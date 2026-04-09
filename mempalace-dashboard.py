#!/usr/bin/env python3
"""Modern local dashboard for MemPalace usage and impact analytics."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Set

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from mempalace_analytics import (
    DEFAULT_FEEDBACK,
    DEFAULT_HELP_SCORES,
    DEFAULT_SEARCH_EVENTS,
    DEFAULT_TRANSCRIPTS,
    collect_feedback_stats,
    collect_usage_stats,
    load_help_scores,
    load_search_events,
)


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_event_results(event: Dict) -> List[Dict]:
    results = event.get("results", [])
    return results if isinstance(results, list) else []


def _compute_stickiness_metrics(events: List[Dict]) -> pd.DataFrame:
    rows: List[Dict] = []
    prev_sources: Set[str] = set()

    for event in events:
        ts_raw = str(event.get("timestamp", ""))
        ts = _parse_ts(ts_raw)
        results = _safe_event_results(event)
        if not results:
            continue

        source_counter = Counter(str(r.get("source_file", "unknown")) for r in results)
        total = len(results)
        max_source_share = max(source_counter.values()) / total if total else 0.0
        unique_sources = len(source_counter)
        unique_wings = len({str(r.get("wing", "unknown")) for r in results})
        alt_route_ratio = 1.0 - max_source_share

        current_sources = set(source_counter.keys())
        overlap_prev = (
            len(current_sources & prev_sources) / len(current_sources | prev_sources)
            if prev_sources and current_sources
            else 0.0
        )
        prev_sources = current_sources

        diversity_norm = unique_sources / total if total else 0.0
        stickiness_score = 100.0 * (
            0.55 * max_source_share
            + 0.30 * (1.0 - diversity_norm)
            + 0.15 * overlap_prev
        )

        rows.append(
            {
                "timestamp_raw": ts_raw,
                "timestamp": ts if ts else ts_raw,
                "query": str(event.get("query", "")),
                "wing": str(event.get("wing", "all") or "all"),
                "total_results": total,
                "unique_sources": unique_sources,
                "unique_wings": unique_wings,
                "max_source_share": max_source_share,
                "alt_route_ratio": alt_route_ratio,
                "overlap_prev": overlap_prev,
                "stickiness_score": stickiness_score,
                "explore_injected": bool(event.get("explore_injected")),
                "selected_sources": ", ".join(list(source_counter.keys())[:6]),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("timestamp_raw")
        df = df.reset_index(drop=True)
        # Gapless event axis: prevents large visual holes after long idle periods.
        df["event_step"] = df.index + 1
    return df


def _compute_adaptive_metrics(events: List[Dict]) -> pd.DataFrame:
    rows: List[Dict] = []
    for event in events:
        ad = event.get("adaptive", {})
        if not isinstance(ad, dict) or not ad:
            continue
        ts_raw = str(event.get("timestamp", ""))
        rows.append(
            {
                "timestamp_raw": ts_raw,
                "status": str(ad.get("status", "unknown")),
                "enabled": bool(ad.get("enabled", False)),
                "recent_stickiness": float(ad.get("recent_stickiness", 0.0)),
                "trend_delta": float(ad.get("trend_delta", 0.0)),
                "lambda_mmr_used": float(ad.get("lambda_mmr_used", 0.0)),
                "source_cap_used": int(ad.get("source_cap_used", 0)),
                "explore_every_used": int(ad.get("explore_every_used", 0)),
                "adaptation_strength": float(ad.get("adaptation_strength", 0.0)),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("timestamp_raw")
    return df


def _load_json_file(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _compute_noise_metrics(df_stick: pd.DataFrame, events_path: Path) -> Dict:
    if df_stick.empty:
        return {
            "score": 0.0,
            "level": "low",
            "stickiness_recent": 0.0,
            "alt_ratio_recent": 0.0,
            "source_share_recent": 0.0,
            "events_file_mb": (events_path.stat().st_size / (1024 * 1024)) if events_path.exists() else 0.0,
        }

    window = df_stick.tail(80)
    stickiness_recent = float(window["stickiness_score"].mean())
    alt_ratio_recent = float(window["alt_route_ratio"].mean())
    source_share_recent = float(window["max_source_share"].mean())
    events_file_mb = (events_path.stat().st_size / (1024 * 1024)) if events_path.exists() else 0.0

    # Higher means noisier retrieval behavior and more risk of route collapse.
    score = (
        0.55 * stickiness_recent
        + 0.25 * (1.0 - alt_ratio_recent) * 100.0
        + 0.20 * source_share_recent * 100.0
    )
    score = max(0.0, min(100.0, score))
    if score >= 65.0:
        level = "high"
    elif score >= 40.0:
        level = "moderate"
    else:
        level = "low"

    return {
        "score": score,
        "level": level,
        "stickiness_recent": stickiness_recent,
        "alt_ratio_recent": alt_ratio_recent,
        "source_share_recent": source_share_recent,
        "events_file_mb": events_file_mb,
    }


def _run_maintenance(mode: str, analytics_dir: Path) -> Dict:
    script_path = Path(__file__).with_name("mempalace-maintenance.py")
    if not script_path.exists():
        return {"ok": False, "error": f"Maintenance script not found: {script_path}"}

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path), "--analytics-dir", str(analytics_dir), "--mode", mode, "--json"],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "Maintenance command failed").strip()}

    payload = {}
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        payload = {}
    return {"ok": True, "payload": payload}


def _hash_to_xy(seed: str) -> tuple[float, float]:
    """Deterministic pseudo-2D coordinates for stable lightweight constellation plot."""
    h = hashlib.md5(seed.encode("utf-8")).hexdigest()
    a = int(h[:8], 16) / 0xFFFFFFFF
    b = int(h[8:16], 16) / 0xFFFFFFFF
    angle = a * 2.0 * math.pi
    radius = 0.25 + 0.75 * b
    return radius * math.cos(angle), radius * math.sin(angle)


def _build_neural_map_data(events: List[Dict], max_sources: int = 60) -> tuple[pd.DataFrame, pd.DataFrame, Dict[str, List[str]]]:
    """Build lightweight wing->room->source graph data from smart-search events."""
    wing_room = Counter()
    room_source = Counter()
    source_hits = Counter()
    source_wing = {}
    source_queries: Dict[str, List[str]] = {}

    for event in events[-300:]:
        query = str(event.get("query", "")).strip()
        for row in _safe_event_results(event):
            wing = str(row.get("wing", "unknown"))
            room = str(row.get("room", "unknown"))
            source = str(row.get("source_file", "unknown"))
            wing_room[(wing, room)] += 1
            room_source[(room, source)] += 1
            source_hits[source] += 1
            source_wing[source] = wing
            if query:
                source_queries.setdefault(source, [])
                if query not in source_queries[source]:
                    source_queries[source].append(query)

    top_sources = {s for s, _ in source_hits.most_common(max_sources)}
    if not top_sources:
        return pd.DataFrame(), pd.DataFrame(), source_queries

    edge_rows: List[Dict] = []
    for (wing, room), count in wing_room.items():
        edge_rows.append({"src": f"wing:{wing}", "dst": f"room:{room}", "weight": count})
    for (room, source), count in room_source.items():
        if source in top_sources:
            edge_rows.append({"src": f"room:{room}", "dst": f"source:{source}", "weight": count})

    edges_df = pd.DataFrame(edge_rows)

    node_rows: List[Dict] = []
    for source in sorted(top_sources):
        x, y = _hash_to_xy(source)
        node_rows.append(
            {
                "source_file": source,
                "wing": source_wing.get(source, "unknown"),
                "hits": source_hits[source],
                "x": x,
                "y": y,
            }
        )
    nodes_df = pd.DataFrame(node_rows).sort_values("hits", ascending=False)
    return edges_df, nodes_df, source_queries


def _new_cluster_sets(events: List[Dict]) -> tuple[Set[str], Set[str]]:
    seen_rooms: Set[str] = set()
    seen_sources: Set[str] = set()
    for event in events:
        for row in _safe_event_results(event):
            wing = str(row.get("wing", "unknown"))
            room = str(row.get("room", "unknown"))
            source = str(row.get("source_file", "unknown"))
            seen_rooms.add(f"{wing}|{room}")
            seen_sources.add(f"{wing}|{room}|{source}")
    return seen_rooms, seen_sources


def _event_rows(event: Dict, max_rows: int = 24) -> List[Dict]:
    raw_candidates = event.get("candidate_preview", [])
    if isinstance(raw_candidates, list) and raw_candidates:
        return list(raw_candidates)[:max_rows]
    return _safe_event_results(event)[:max_rows]


def _build_live_axis_maps(events_subset: List[Dict]) -> tuple[Dict[str, float], Dict[str, float]]:
    wings: Set[str] = set()
    rooms: Set[str] = set()
    for ev in events_subset:
        for r in _event_rows(ev):
            wing = str(r.get("wing", "unknown"))
            room = str(r.get("room", "unknown"))
            wings.add(wing)
            rooms.add(f"{wing}|{room}")
    wing_y = {w: idx * 1.2 for idx, w in enumerate(sorted(wings))}
    room_y = {rk: idx * 0.55 for idx, rk in enumerate(sorted(rooms))}
    return wing_y, room_y


def _add_axis_nodes(fig: go.Figure, wing_y: Dict[str, float], room_y: Dict[str, float], query_label: str) -> None:
    qx, qy = 0.0, 0.0
    fig.add_trace(
        go.Scatter(
            x=[qx],
            y=[qy],
            mode="markers+text",
            marker=dict(size=14, color="#22d3ee"),
            text=["query"],
            textposition="top center",
            hovertext=[query_label],
            name="query",
        )
    )
    for wing, y in wing_y.items():
        fig.add_trace(
            go.Scatter(
                x=[1.0],
                y=[y],
                mode="markers+text",
                marker=dict(size=10, color="#818cf8"),
                text=[wing],
                textposition="middle right",
                hoverinfo="skip",
                showlegend=False,
            )
        )
    for room_key, y in room_y.items():
        fig.add_trace(
            go.Scatter(
                x=[2.0],
                y=[y],
                mode="markers+text",
                marker=dict(size=8, color="#34d399"),
                text=[room_key.split("|", 1)[1]],
                textposition="middle right",
                hoverinfo="skip",
                showlegend=False,
            )
        )


def _latest_event_stats(rows: List[Dict], seen_rooms: Set[str], seen_sources: Set[str]) -> Dict:
    branch_rooms = len({f"{str(r.get('wing', 'unknown'))}|{str(r.get('room', 'unknown'))}" for r in rows})
    unique_sources = len({str(r.get("source_file", "unknown")) for r in rows})
    selected_routes = sum(1 for r in rows if bool(r.get("selected", True)))
    alternative_routes = max(0, len(rows) - selected_routes)
    new_room_count = 0
    new_source_count = 0
    for r in rows:
        wing = str(r.get("wing", "unknown"))
        room = str(r.get("room", "unknown"))
        source = str(r.get("source_file", "unknown"))
        if f"{wing}|{room}" not in seen_rooms:
            new_room_count += 1
        if f"{wing}|{room}|{source}" not in seen_sources:
            new_source_count += 1
    return {
        "branch_rooms": branch_rooms,
        "unique_sources": unique_sources,
        "selected_routes": selected_routes,
        "alternative_routes": alternative_routes,
        "new_rooms": new_room_count,
        "new_sources": new_source_count,
    }


def _build_live_route_figure(event: Dict, prior_events: List[Dict]) -> tuple[go.Figure, Dict]:
    """Single latest event route graph with branch/new-cluster signals."""
    rows = _event_rows(event)
    wing_y, room_y = _build_live_axis_maps([event])
    seen_rooms, seen_sources = _new_cluster_sets(prior_events)

    fig = go.Figure()
    query_label = (str(event.get("query", "")).strip() or "query")[:80]
    qx, qy = 0.0, 0.0

    for r in rows:
        wing = str(r.get("wing", "unknown"))
        room = str(r.get("room", "unknown"))
        source = str(r.get("source_file", "unknown"))
        room_key = f"{wing}|{room}"
        source_key = f"{wing}|{room}|{source}"
        y_w = wing_y.get(wing, 0.0)
        y_r = room_y.get(room_key, 0.0)
        x_w, x_r, x_s = 1.0, 2.0, 3.0
        y_s = y_r + (hash(source) % 9 - 4) * 0.06

        is_new_room = room_key not in seen_rooms
        is_new_source = source_key not in seen_sources
        is_selected = bool(r.get("selected", True))

        if is_selected and (is_new_room or is_new_source):
            link_color = "rgba(14,165,233,0.82)"
            line_width = 3.2
            dash_style = "solid"
        elif is_selected:
            link_color = "rgba(148,163,184,0.58)"
            line_width = 2.2
            dash_style = "solid"
        else:
            link_color = "rgba(156,163,175,0.38)"
            line_width = 1.3
            dash_style = "dot"

        fig.add_trace(
            go.Scatter(
                x=[qx, x_w, x_r, x_s],
                y=[qy, y_w, y_r, y_s],
                mode="lines",
                line=dict(color=link_color, width=line_width, dash=dash_style),
                hoverinfo="text",
                text=[query_label, f"wing: {wing}", f"room: {room}", f"source: {source}"],
                showlegend=False,
            )
        )

    _add_axis_nodes(fig, wing_y, room_y, query_label)
    fig.update_layout(
        title="Live route for latest search (new clusters highlighted)",
        height=460,
        margin=dict(l=10, r=10, t=50, b=20),
        xaxis=dict(visible=False, range=[-0.3, 3.3]),
        yaxis=dict(visible=False),
    )
    return fig, _latest_event_stats(rows, seen_rooms, seen_sources)


def _build_live_route_overlay_figure(events: List[Dict], last_n: int = 20) -> tuple[go.Figure, Dict]:
    """Overlay last N events: older routes gray/faded, newest bright."""
    subset = events[-max(1, last_n) :]
    latest_event = subset[-1]
    latest_rows = _event_rows(latest_event)
    seen_rooms, seen_sources = _new_cluster_sets(events[:-1])
    wing_y, room_y = _build_live_axis_maps(subset)

    fig = go.Figure()
    qx, qy = 0.0, 0.0
    total_hist = max(1, len(subset) - 1)

    # Older events first (faded gray), so latest appears on top.
    for idx, hist_event in enumerate(subset[:-1]):
        rows = _event_rows(hist_event)
        fade = (idx + 1) / total_hist
        alpha_sel = 0.08 + 0.32 * fade
        alpha_alt = 0.06 + 0.20 * fade
        q_label = (str(hist_event.get("query", "")).strip() or "history query")[:80]
        for r in rows:
            wing = str(r.get("wing", "unknown"))
            room = str(r.get("room", "unknown"))
            source = str(r.get("source_file", "unknown"))
            room_key = f"{wing}|{room}"
            y_w = wing_y.get(wing, 0.0)
            y_r = room_y.get(room_key, 0.0)
            x_w, x_r, x_s = 1.0, 2.0, 3.0
            y_s = y_r + (hash(source) % 9 - 4) * 0.06
            is_selected = bool(r.get("selected", True))

            color = f"rgba(107,114,128,{alpha_sel:.3f})" if is_selected else f"rgba(156,163,175,{alpha_alt:.3f})"
            width = 1.6 if is_selected else 1.0
            dash = "solid" if is_selected else "dot"

            fig.add_trace(
                go.Scatter(
                    x=[qx, x_w, x_r, x_s],
                    y=[qy, y_w, y_r, y_s],
                    mode="lines",
                    line=dict(color=color, width=width, dash=dash),
                    hoverinfo="text",
                    text=[q_label, f"wing: {wing}", f"room: {room}", f"source: {source}"],
                    showlegend=False,
                )
            )

    # Latest event bright and contrasty.
    q_label_latest = (str(latest_event.get("query", "")).strip() or "query")[:80]
    for r in latest_rows:
        wing = str(r.get("wing", "unknown"))
        room = str(r.get("room", "unknown"))
        source = str(r.get("source_file", "unknown"))
        room_key = f"{wing}|{room}"
        source_key = f"{wing}|{room}|{source}"
        y_w = wing_y.get(wing, 0.0)
        y_r = room_y.get(room_key, 0.0)
        x_w, x_r, x_s = 1.0, 2.0, 3.0
        y_s = y_r + (hash(source) % 9 - 4) * 0.06
        is_selected = bool(r.get("selected", True))
        is_new_room = room_key not in seen_rooms
        is_new_source = source_key not in seen_sources

        if is_selected and (is_new_room or is_new_source):
            color = "rgba(14,165,233,0.95)"
            width = 3.5
            dash = "solid"
        elif is_selected:
            color = "rgba(30,41,59,0.82)"
            width = 2.8
            dash = "solid"
        else:
            color = "rgba(71,85,105,0.68)"
            width = 1.8
            dash = "dot"

        fig.add_trace(
            go.Scatter(
                x=[qx, x_w, x_r, x_s],
                y=[qy, y_w, y_r, y_s],
                mode="lines",
                line=dict(color=color, width=width, dash=dash),
                hoverinfo="text",
                text=[q_label_latest, f"wing: {wing}", f"room: {room}", f"source: {source}"],
                showlegend=False,
            )
        )

    _add_axis_nodes(fig, wing_y, room_y, q_label_latest)
    fig.update_layout(
        title="Live route overlay (last 20): newest bright, history faded gray",
        height=500,
        margin=dict(l=10, r=10, t=50, b=20),
        xaxis=dict(visible=False, range=[-0.3, 3.3]),
        yaxis=dict(visible=False),
    )
    stats = _latest_event_stats(latest_rows, seen_rooms, seen_sources)
    stats["overlay_events"] = len(subset)
    return fig, stats


st.set_page_config(
    page_title="MemPalace Analytics",
    page_icon="🧠",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.2rem;}
      /* Make Plotly labels crisp: no stroke/shadow, system thin black text */
      .js-plotly-plot text {
        font-family: "Segoe UI", Arial, sans-serif !important;
        font-weight: 400 !important;
        fill: #111827 !important;
        stroke: none !important;
        paint-order: normal !important;
        text-shadow: none !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧠 MemPalace Analytics")
st.caption("Live usage, impact, diversity, and anti-stickiness telemetry.")

with st.sidebar:
    st.header("Live Controls")
    auto_refresh = st.toggle("Auto refresh", value=True)
    refresh_seconds = st.slider("Refresh interval (sec)", min_value=1, max_value=10, value=2)
    if auto_refresh:
        st_autorefresh(interval=refresh_seconds * 1000, key="mempalace_live_refresh")
    st.caption("Near realtime mode based on local telemetry files.")

    st.header("Data Sources")
    transcripts_path = st.text_input("Transcripts path", DEFAULT_TRANSCRIPTS)
    feedback_path = st.text_input("Feedback file", str(DEFAULT_FEEDBACK))
    events_path = st.text_input("Search events file", str(DEFAULT_SEARCH_EVENTS))
    scores_path = st.text_input("Help scores file", str(DEFAULT_HELP_SCORES))

usage = collect_usage_stats(Path(transcripts_path))
feedback = collect_feedback_stats(Path(feedback_path))
events = load_search_events(Path(events_path))
scores = load_help_scores(Path(scores_path))
df_stick = _compute_stickiness_metrics(events)
df_adaptive = _compute_adaptive_metrics(events)

sessions_total = usage["sessions_total"]
sessions_mem = usage["sessions_with_mempalace"]
session_share = (sessions_mem / sessions_total * 100.0) if sessions_total else 0.0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Sessions scanned", sessions_total)
k2.metric("Sessions with memory", sessions_mem, f"{session_share:.1f}%")
k3.metric("MemPalace calls", usage["mempalace_tool_calls"])
k4.metric("Minutes saved", feedback["minutes_saved_total"])
if not df_stick.empty:
    k5.metric("Stickiness risk", f"{float(df_stick['stickiness_score'].tail(30).mean()):.1f}/100")
else:
    k5.metric("Stickiness risk", "n/a")

if events:
    last_ts = _parse_ts(str(events[-1].get("timestamp", "")))
    if last_ts:
        st.caption(
            f"Live stream active • last event {last_ts.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )

st.markdown("---")

left, right = st.columns([1, 1])
with left:
    st.subheader("Most used memory tools")
    tool_items = usage["mempalace_tools_counter"].most_common(12)
    if tool_items:
        df_tools = pd.DataFrame(tool_items, columns=["tool", "count"])
        fig_tools = px.bar(
            df_tools,
            x="count",
            y="tool",
            orientation="h",
            color="count",
            color_continuous_scale="Tealgrn",
            title="Top MemPalace tools",
        )
        fig_tools.update_layout(height=420, yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_tools, use_container_width=True)
    else:
        st.info("No MemPalace tool usage found yet.")

with right:
    st.subheader("Feedback quality")
    df_fb = pd.DataFrame(
        [
            {"status": "Helped", "count": feedback["helped_yes"]},
            {"status": "Not helped", "count": feedback["helped_no"]},
            {"status": "Unknown", "count": feedback["helped_unknown"]},
        ]
    )
    fig_fb = px.pie(
        df_fb,
        names="status",
        values="count",
        hole=0.58,
        color="status",
        color_discrete_map={
            "Helped": "#0ea5a6",
            "Not helped": "#ef4444",
            "Unknown": "#94a3b8",
        },
        title="Feedback distribution",
    )
    fig_fb.update_layout(height=420)
    st.plotly_chart(fig_fb, use_container_width=True)

st.markdown("---")
st.subheader("Anti-stickiness and alternative routes")

if not df_stick.empty:
    total_events = len(df_stick)
    avg_sources = float(df_stick["unique_sources"].mean())
    avg_wings = float(df_stick["unique_wings"].mean())
    explore_rate = float(df_stick["explore_injected"].mean() * 100.0)
    alt_rate = float(df_stick["alt_route_ratio"].mean() * 100.0)
    stick_recent = float(df_stick["stickiness_score"].tail(40).mean())

    a1, a2, a3, a4, a5 = st.columns(5)
    a1.metric("Smart searches", total_events)
    a2.metric("Avg unique sources", f"{avg_sources:.2f}")
    a3.metric("Explore injection", f"{explore_rate:.1f}%")
    a4.metric("Alt route ratio", f"{alt_rate:.1f}%")
    a5.metric("Avg stickiness", f"{stick_recent:.1f}/100")

    axis_mode = st.radio(
        "Trend axis mode",
        options=["Gapless (event steps)", "Real time", "By day (compressed)"],
        horizontal=True,
        index=0,
        help="Gapless removes idle gaps; By day compresses events into daily averages.",
    )

    trend_df = df_stick.tail(300).copy()
    if axis_mode == "By day (compressed)":
        trend_df["day"] = trend_df["timestamp_raw"].astype(str).str.slice(0, 10)
        trend_df = (
            trend_df.groupby("day", as_index=False)
            .agg(
                stickiness_score=("stickiness_score", "mean"),
                alt_route_ratio=("alt_route_ratio", "mean"),
                events=("event_step", "count"),
            )
            .sort_values("day")
        )
        x_col = "day"
        x_title = "Day"
        hover_cols = ["events"]
    elif axis_mode == "Gapless (event steps)":
        x_col = "event_step"
        x_title = "Event step"
        hover_cols = ["timestamp_raw", "query"]
    else:
        x_col = "timestamp_raw"
        x_title = "Timestamp"
        hover_cols = ["query"]

    c1, c2 = st.columns(2)
    with c1:
        fig_stick = px.line(
            trend_df,
            x=x_col,
            y="stickiness_score",
            title="Stickiness trend (lower is better)",
            markers=True,
            color_discrete_sequence=["#ef4444"],
            hover_data=hover_cols,
        )
        fig_stick.update_layout(height=320, xaxis_title=x_title, yaxis_title="Stickiness (0-100)")
        st.plotly_chart(fig_stick, use_container_width=True)

    with c2:
        fig_alt = px.line(
            trend_df,
            x=x_col,
            y="alt_route_ratio",
            title="Alternative route ratio (higher is better)",
            markers=True,
            color_discrete_sequence=["#06b6d4"],
            hover_data=hover_cols,
        )
        fig_alt.update_layout(height=320, xaxis_title=x_title, yaxis_title="Alt route ratio")
        st.plotly_chart(fig_alt, use_container_width=True)

    gauge_value = float(df_stick["stickiness_score"].tail(30).mean())
    fig_gauge = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=gauge_value,
            title={"text": "Brain Stickiness Risk"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#ef4444"},
                "steps": [
                    {"range": [0, 35], "color": "#10b981"},
                    {"range": [35, 65], "color": "#f59e0b"},
                    {"range": [65, 100], "color": "#ef4444"},
                ],
            },
        )
    )
    fig_gauge.update_layout(height=280, margin=dict(l=20, r=20, t=50, b=20))
    st.plotly_chart(fig_gauge, use_container_width=True)

    st.markdown("**Adaptive anti-stickiness (auto-tuning state)**")
    if not df_adaptive.empty:
        last_ad = df_adaptive.iloc[-1]
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("Adaptive mode", str(last_ad["status"]))
        d2.metric("Adapt strength", f"{float(last_ad['adaptation_strength']) * 100:.0f}%")
        d3.metric("lambda_mmr in use", f"{float(last_ad['lambda_mmr_used']):.2f}")
        d4.metric("source_cap in use", int(last_ad["source_cap_used"]))
        d5.metric("Explore rate", f"1 / {int(last_ad['explore_every_used'])}")

        ad_line = px.line(
            df_adaptive.tail(200),
            x="timestamp_raw",
            y="recent_stickiness",
            color="status",
            title="Adaptive controller: recent stickiness baseline (lower is better)",
            markers=True,
        )
        ad_line.update_layout(height=320, yaxis_title="Recent stickiness baseline")
        st.plotly_chart(ad_line, use_container_width=True)
    else:
        st.caption("Adaptive telemetry appears after new smart-search runs with updated script.")

    st.markdown("**Query → selected routes (recent)**")
    path_table = df_stick.sort_values("timestamp_raw", ascending=False).head(25)[
        [
            "timestamp_raw",
            "query",
            "wing",
            "selected_sources",
            "unique_sources",
            "max_source_share",
            "alt_route_ratio",
            "stickiness_score",
            "explore_injected",
        ]
    ].copy()
    path_table["max_source_share"] = (path_table["max_source_share"] * 100).round(1).astype(str) + "%"
    path_table["alt_route_ratio"] = (path_table["alt_route_ratio"] * 100).round(1).astype(str) + "%"
    path_table["stickiness_score"] = path_table["stickiness_score"].round(1)
    st.dataframe(path_table, use_container_width=True, hide_index=True)
else:
    st.info("No smart-search events yet. Run `mempalace-smart-search.py` to start telemetry.")

st.markdown("---")
st.subheader("Live route stream")

if events:
    live_mode = st.radio(
        "Route view mode",
        options=["Latest only", "Last 20 overlay"],
        horizontal=True,
        index=0,
    )

    if live_mode == "Last 20 overlay":
        live_fig, live_stats = _build_live_route_overlay_figure(events, last_n=20)
    else:
        latest_event = events[-1]
        prior = events[:-1]
        live_fig, live_stats = _build_live_route_figure(latest_event, prior)

    l1, l2, l3, l4, l5, l6 = st.columns(6)
    l1.metric("Branch rooms (latest)", live_stats["branch_rooms"])
    l2.metric("Sources in graph", live_stats["unique_sources"])
    l3.metric("Selected routes", live_stats["selected_routes"])
    l4.metric("Alternative routes", live_stats["alternative_routes"])
    l5.metric("New room clusters", live_stats["new_rooms"])
    l6.metric("New source clusters", live_stats["new_sources"])
    if "overlay_events" in live_stats:
        st.caption(f"Overlay includes last {live_stats['overlay_events']} events.")

    st.plotly_chart(live_fig, use_container_width=True)
    st.caption(
        "Auto-refresh draws a new route when new smart-search event is logged. "
        "In overlay mode, previous routes are gray and progressively faded, while the newest route is high-contrast."
    )
else:
    st.info("Live stream will appear after the first smart-search event.")

st.markdown("---")
st.subheader("Memory graph signals")

if not df_stick.empty:
    wing_df = (
        df_stick.groupby("wing", as_index=False)
        .agg(
            queries=("query", "count"),
            avg_stickiness=("stickiness_score", "mean"),
            avg_alt_ratio=("alt_route_ratio", "mean"),
        )
        .sort_values("queries", ascending=False)
    )

    g1, g2 = st.columns(2)
    with g1:
        fig_wing = px.bar(
            wing_df,
            x="wing",
            y="queries",
            color="avg_stickiness",
            color_continuous_scale="RdYlGn_r",
            title="Wing activity with stickiness heat",
        )
        fig_wing.update_layout(height=320)
        st.plotly_chart(fig_wing, use_container_width=True)
    with g2:
        fig_route = px.bar(
            wing_df,
            x="wing",
            y="avg_alt_ratio",
            color="avg_alt_ratio",
            color_continuous_scale="Blues",
            title="Average alternative-route ratio by wing",
        )
        fig_route.update_layout(height=320, yaxis_title="Alt route ratio")
        st.plotly_chart(fig_route, use_container_width=True)
else:
    st.info("Graph signals will appear after smart-search traffic is logged.")

st.markdown("---")
st.subheader("Neural map lite (fast + stable)")

edges_df, source_nodes_df, source_queries = _build_neural_map_data(events, max_sources=60)
if not edges_df.empty and not source_nodes_df.empty:
    # Sankey graph: wing -> room -> source
    labels = pd.Index(pd.unique(pd.concat([edges_df["src"], edges_df["dst"]], ignore_index=True)))
    label_to_idx = {label: idx for idx, label in enumerate(labels)}
    src_idx = edges_df["src"].map(label_to_idx)
    dst_idx = edges_df["dst"].map(label_to_idx)

    # Keep labels ASCII/plain for crisper text rendering on Windows scaling.
    cleaned_labels = [
        lbl.replace("wing:", "wing: ").replace("room:", "room: ").replace("source:", "src: ")
        for lbl in labels
    ]

    sankey = go.Figure(
        data=[
            go.Sankey(
                node=dict(
                    label=cleaned_labels,
                    pad=12,
                    thickness=14,
                    color="rgba(56,189,248,0.75)",
                ),
                link=dict(
                    source=list(src_idx),
                    target=list(dst_idx),
                    value=list(edges_df["weight"]),
                    color="rgba(148,163,184,0.35)",
                ),
                textfont=dict(
                    size=13,
                    family="Arial, Segoe UI, sans-serif",
                    color="#111827",
                ),
            )
        ]
    )
    sankey.update_layout(height=500, title_text="Wing → Room → Source routes")
    st.plotly_chart(sankey, use_container_width=True)
    st.caption(
        "Map rendering: every logged smart-search event contributes edges "
        "query -> wing -> room -> source; edge thickness reflects selection frequency."
    )

    # Constellation map for top sources
    constellation = px.scatter(
        source_nodes_df.head(80),
        x="x",
        y="y",
        color="wing",
        size="hits",
        hover_data=["source_file", "hits", "wing"],
        title="Route constellation (top sources)",
        color_discrete_sequence=px.colors.qualitative.Set2,
        render_mode="svg",
    )
    constellation.update_layout(height=430, xaxis_visible=False, yaxis_visible=False)
    st.plotly_chart(constellation, use_container_width=True)

    source_options = source_nodes_df["source_file"].tolist()
    selected_source = st.selectbox(
        "Drilldown source",
        options=source_options,
        index=0 if source_options else None,
        help="Select a source to inspect how it participates in memory routes.",
    )

    if selected_source:
        related_queries = source_queries.get(selected_source, [])
        st.markdown(f"**Selected source:** `{selected_source}`")
        if related_queries:
            q_df = pd.DataFrame({"recent_queries": related_queries[-20:]})
            st.dataframe(q_df.iloc[::-1], use_container_width=True, hide_index=True)
        else:
            st.caption("No linked queries found yet for this source.")
else:
    st.info("Neural map will appear after smart-search events accumulate.")

st.markdown("---")
st.subheader("Help score health")

if scores:
    score_rows = []
    for key, value in scores.items():
        score_rows.append(
            {
                "key": key,
                "score": float(value.get("score", 0.0)),
                "used_count": int(value.get("used_count", 0)),
                "positive_count": int(value.get("positive_count", 0)),
                "negative_count": int(value.get("negative_count", 0)),
                "updated_at": value.get("updated_at", ""),
            }
        )
    df_scores = pd.DataFrame(score_rows)
    s1, s2 = st.columns(2)
    with s1:
        fig_hist = px.histogram(
            df_scores,
            x="score",
            nbins=20,
            title="Help score distribution",
            color_discrete_sequence=["#14b8a6"],
        )
        fig_hist.update_layout(height=320)
        st.plotly_chart(fig_hist, use_container_width=True)
    with s2:
        fig_scatter = px.scatter(
            df_scores,
            x="used_count",
            y="score",
            hover_data=["key", "positive_count", "negative_count"],
            title="Score vs usage count",
            color="score",
            color_continuous_scale="RdYlGn",
        )
        fig_scatter.update_layout(height=320)
        st.plotly_chart(fig_scatter, use_container_width=True)
else:
    st.info("No help-score data yet. Log feedback with `mempalace-log-feedback.ps1`.")

st.markdown("---")
st.subheader("Noise control and optimization")

events_file = Path(events_path)
analytics_dir = events_file.parent if events_file.parent else Path(".mempalace-analytics")
noise = _compute_noise_metrics(df_stick, events_file)
maintenance_state_path = analytics_dir / "maintenance-state.json"
maintenance_state = _load_json_file(maintenance_state_path)
last_run_ts = _parse_ts(str(maintenance_state.get("timestamp", "")))

n1, n2, n3, n4, n5 = st.columns(5)
n1.metric("Noise score", f"{noise['score']:.1f}/100")
n2.metric("Noise level", noise["level"])
n3.metric("Recent stickiness", f"{noise['stickiness_recent']:.1f}")
n4.metric("Recent alt-ratio", f"{noise['alt_ratio_recent'] * 100:.1f}%")
n5.metric("Events log size", f"{noise['events_file_mb']:.2f} MB")

c_auto, c_manual = st.columns([2, 1])
with c_auto:
    auto_optimize = st.toggle(
        "Auto optimize when noise is high",
        value=True,
        help="Runs safe maintenance for analytics data when noise score crosses threshold.",
    )
    auto_threshold = st.slider("Auto optimize threshold", min_value=35, max_value=90, value=65)
    auto_cooldown_min = st.slider("Auto optimize cooldown (minutes)", min_value=5, max_value=180, value=60)
with c_manual:
    st.markdown(" ")
    run_now = st.button("Optimize database now", use_container_width=True)

if run_now:
    run_result = _run_maintenance("apply", analytics_dir)
    if run_result.get("ok"):
        st.success("Manual optimization completed. Core memory paths are preserved.")
    else:
        st.error(f"Manual optimization failed: {run_result.get('error', 'unknown error')}")
    maintenance_state = _load_json_file(maintenance_state_path)
    last_run_ts = _parse_ts(str(maintenance_state.get("timestamp", "")))

now_utc = datetime.now(timezone.utc)
if auto_optimize and noise["score"] >= float(auto_threshold):
    last_attempt_ts = st.session_state.get("maintenance_auto_attempt_ts")
    cooldown = timedelta(minutes=int(auto_cooldown_min))
    enough_time_since_last_state = (not last_run_ts) or ((now_utc - last_run_ts) >= cooldown)
    enough_time_since_attempt = (not last_attempt_ts) or ((now_utc - last_attempt_ts) >= timedelta(minutes=3))
    if enough_time_since_last_state and enough_time_since_attempt:
        auto_result = _run_maintenance("auto", analytics_dir)
        st.session_state["maintenance_auto_attempt_ts"] = now_utc
        if auto_result.get("ok"):
            st.info("Auto optimization executed due to high noise level.")
        else:
            st.warning(f"Auto optimization attempted but failed: {auto_result.get('error', 'unknown error')}")
        maintenance_state = _load_json_file(maintenance_state_path)
        last_run_ts = _parse_ts(str(maintenance_state.get("timestamp", "")))

if last_run_ts:
    st.caption(
        "Last maintenance run: "
        f"{last_run_ts.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

state_results = maintenance_state.get("results", {}) if isinstance(maintenance_state, dict) else {}
if state_results:
    s_search = state_results.get("search_events", {})
    s_feedback = state_results.get("feedback", {})
    s_help = state_results.get("help_scores", {})
    st.markdown("**Last maintenance impact**")
    impact_df = pd.DataFrame(
        [
            {
                "target": "search_events",
                "before": int(s_search.get("before_count", 0)),
                "after": int(s_search.get("after_count", 0)),
                "archived": int(s_search.get("archived_count", 0)),
                "trimmed": int(s_search.get("trimmed_count", 0)),
            },
            {
                "target": "feedback",
                "before": int(s_feedback.get("before_count", 0)),
                "after": int(s_feedback.get("after_count", 0)),
                "archived": int(s_feedback.get("archived_count", 0)),
                "trimmed": int(s_feedback.get("trimmed_count", 0)),
            },
            {
                "target": "help_scores",
                "before": int(s_help.get("before_count", 0)),
                "after": int(s_help.get("after_count", 0)),
                "archived": 0,
                "trimmed": int(s_help.get("pruned_count", 0)),
            },
        ]
    )
    st.dataframe(impact_df, use_container_width=True, hide_index=True)

st.caption(
    "Optimization is safe: it archives and trims analytics telemetry. "
    "Core memory index and main retrieval paths remain intact."
)
