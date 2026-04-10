#!/usr/bin/env python3
"""Modern local dashboard for MemPalace usage and impact analytics."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import html
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Set

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from mempalace_analytics import (
    DEFAULT_HELP_SCORES,
    DEFAULT_SEARCH_EVENTS,
    DEFAULT_TRANSCRIPTS,
    compute_auto_utility,
    collect_usage_stats,
    load_help_scores,
    load_search_events,
)


def _run_performance_fix() -> tuple[bool, str]:
    """Keep one dashboard/MCP process and stop duplicates."""
    script = Path(__file__).with_name("mempalace-clean-duplicates.ps1")
    if not script.exists():
        return False, f"Cleanup script not found: {script}"
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-KeepDashboardPid",
            str(os.getpid()),
        ]
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        output = "\n".join(
            part.strip() for part in [completed.stdout or "", completed.stderr or ""] if part.strip()
        )
        if completed.returncode != 0:
            return False, output or f"Cleanup failed (exit {completed.returncode})."
        return True, output or "Duplicate cleanup completed."
    except Exception as exc:
        return False, f"Cleanup failed: {exc}"


def _file_stamp(path: Path) -> tuple[bool, int, int]:
    """Fast change marker for cache invalidation."""
    try:
        if not path.exists():
            return (False, 0, 0)
        stt = path.stat()
        return (True, int(stt.st_mtime_ns), int(stt.st_size))
    except Exception:
        return (False, 0, 0)


def _transcripts_stamp(transcripts_root: Path) -> tuple[int, int, int]:
    """Directory-level stamp without parsing file contents."""
    if not transcripts_root.exists():
        return (0, 0, 0)
    count = 0
    newest_mtime = 0
    total_size = 0
    try:
        for path in transcripts_root.rglob("*.jsonl"):
            if "subagents" in {part.lower() for part in path.parts}:
                continue
            try:
                stt = path.stat()
            except OSError:
                continue
            count += 1
            newest_mtime = max(newest_mtime, int(stt.st_mtime_ns))
            total_size += int(stt.st_size)
    except Exception:
        return (count, newest_mtime, total_size)
    return (count, newest_mtime, total_size)


@st.cache_data(show_spinner=False)
def _cached_usage_stats(transcripts_path_value: str, transcripts_signature: tuple[int, int, int]) -> Dict:
    _ = transcripts_signature
    return collect_usage_stats(Path(transcripts_path_value))


@st.cache_data(show_spinner=False)
def _cached_search_events(events_path_value: str, events_signature: tuple[bool, int, int]) -> List[Dict]:
    _ = events_signature
    return load_search_events(Path(events_path_value))


@st.cache_data(show_spinner=False)
def _cached_help_scores(scores_path_value: str, scores_signature: tuple[bool, int, int]) -> Dict[str, Dict]:
    _ = scores_signature
    return load_help_scores(Path(scores_path_value))


def _load_recent_events_tail(path: Path, max_lines: int = 200, max_bytes: int = 262_144) -> List[Dict]:
    """Read only recent JSONL lines to keep watcher lightweight."""
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        read_size = min(max_bytes, max(0, size))
        with path.open("rb") as fh:
            if read_size > 0:
                fh.seek(-read_size, 2)
            raw = fh.read()
    except OSError:
        return []

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    tail_lines = lines[-max_lines:] if len(lines) > max_lines else lines
    events: List[Dict] = []
    for line in tail_lines:
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


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
                "event_kind": str(event.get("event_kind", "smart_search") or "smart_search"),
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
        df = df.reset_index(drop=True)
        # Gapless event axis for adaptive controller trend.
        df["event_step"] = df.index + 1
    return df


def _compute_auto_utility_metrics(events: List[Dict]) -> pd.DataFrame:
    rows: List[Dict] = []
    for event in events:
        if not _is_truth_vector_event(event):
            continue
        auto = event.get("auto_utility")
        if not isinstance(auto, dict):
            auto = compute_auto_utility(event)
        ts_raw = str(event.get("timestamp", ""))
        comps = auto.get("components", {}) if isinstance(auto.get("components"), dict) else {}
        rows.append(
            {
                "timestamp_raw": ts_raw,
                "query": str(event.get("query", "")),
                "score": float(auto.get("score", 0.0)),
                "band": str(auto.get("band", "low")),
                "retrieval_relevance": float(comps.get("retrieval_relevance", 0.0)),
                "source_diversity": float(comps.get("source_diversity", 0.0)),
                "consistency": float(comps.get("consistency", 0.0)),
                "actionability": float(comps.get("actionability", 0.0)),
                "execution_outcome": float(comps.get("execution_outcome", 0.0)),
                "stability": float(comps.get("stability", 0.0)),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("timestamp_raw")
        df = df.reset_index(drop=True)
        df["event_step"] = df.index + 1
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


def _is_truth_vector_event(event: Dict) -> bool:
    # Truth mode: show only real vector retrieval routes.
    # Exclude replayed touch routes to avoid stale/foreign paths.
    if len(_event_rows(event, max_rows=1)) == 0:
        return False
    if bool(event.get("route_replay", False)):
        return False
    if "vector_truth" in event:
        return bool(event.get("vector_truth"))
    # Backward compatibility for historical logs without vector_truth flag.
    return str(event.get("event_kind", "smart_search") or "smart_search") == "smart_search"


@st.fragment(run_every=2)
def _watch_neural_truth_events(events_path_value: str) -> None:
    """Poll smart-search truth events and rerun app only on new event."""
    events_path_obj = Path(events_path_value)
    live_events = _load_recent_events_tail(events_path_obj)
    truth_events = [ev for ev in live_events if _is_truth_vector_event(ev)]
    if not truth_events:
        return

    latest = truth_events[-1]
    latest_ts = str(latest.get("timestamp", ""))
    latest_kind = str(latest.get("event_kind", "smart_search") or "smart_search")
    latest_query = str(latest.get("query", ""))
    latest_signature = f"{latest_ts}::{latest_kind}::{latest_query}"

    prev_signature = st.session_state.get("neural_last_signature")
    if prev_signature is None:
        st.session_state["neural_last_signature"] = latest_signature
        st.session_state["neural_last_update_utc"] = datetime.now(timezone.utc).strftime("%H:%M:%S")
        return

    if prev_signature != latest_signature:
        st.session_state["neural_last_signature"] = latest_signature
        st.session_state["neural_last_update_utc"] = datetime.now(timezone.utc).strftime("%H:%M:%S")
        st.rerun()


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


def _source_label(value: str) -> str:
    name = _source_name(value)
    if len(name) <= 24:
        return name
    return f"{name[:11]}...{name[-10:]}"


def _source_name(value: str) -> str:
    if not value:
        return "unknown"
    return value.replace("\\", "/").split("/")[-1]


def _short_text(value: str, max_len: int = 30) -> str:
    text = (value or "").strip()
    if not text:
        return "unknown"
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def _expand_row_text(row: Dict, max_chars: int = 4000) -> str:
    """Return best available full snippet for popup (supports old truncated events)."""
    text = str(row.get("text", "") or "")
    if not text:
        return ""
    # New events already store full text.
    if not text.endswith("..."):
        return text

    source_path_raw = row.get("source_path")
    if not source_path_raw:
        return text
    source_path = Path(str(source_path_raw))
    if not source_path.exists() or not source_path.is_file():
        return text

    try:
        body = source_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return text

    anchor = text[:-3].strip()
    if anchor:
        pos = body.find(anchor)
        if pos >= 0:
            return body[pos : pos + max_chars]

    line_start_raw = row.get("line_start")
    try:
        line_start = int(line_start_raw) if line_start_raw is not None else None
    except (TypeError, ValueError):
        line_start = None
    if line_start and line_start > 0:
        lines = body.splitlines(keepends=True)
        idx = min(max(0, line_start - 1), max(0, len(lines) - 1))
        chunk = "".join(lines[idx : idx + 40])
        return chunk[:max_chars] if chunk else text

    return text


def _vector_strength(row: Dict) -> float:
    if "semantic_norm" in row:
        try:
            return max(0.0, min(1.0, float(row.get("semantic_norm", 0.0))))
        except Exception:
            return 0.5
    try:
        sim = float(row.get("similarity", 0.0))
        return max(0.0, min(1.0, (sim + 1.0) / 2.0))
    except Exception:
        return 0.5


def _build_neural_sim_payload(events: List[Dict]) -> Dict:
    vector_events = [ev for ev in events if _is_truth_vector_event(ev)]
    if not vector_events:
        return {}

    window = [vector_events[-1]]
    latest = window[-1]
    latest_query = str(latest.get("query", "Memory route simulation"))
    latest_rows = _event_rows(latest, max_rows=18)

    max_sources = 20
    source_union: List[str] = []
    target_total_routes = 10
    max_alt_routes = 4

    def _push_source(src_value: str) -> None:
        src_name = str(src_value or "unknown")
        if src_name not in source_union:
            source_union.append(src_name)

    # Priority 1: current query routes (selected first, then alternatives).
    for row in latest_rows:
        if bool(row.get("selected", True)):
            _push_source(str(row.get("source_file", "unknown")))
    for row in latest_rows:
        if not bool(row.get("selected", True)):
            _push_source(str(row.get("source_file", "unknown")))

    source_union = source_union[:max_sources]
    if not source_union:
        source_union = ["unknown"]

    source_strength: Dict[str, float] = {}
    source_details: Dict[str, Dict] = {}
    for row in latest_rows:
        src = str(row.get("source_file", "unknown"))
        strength = _vector_strength(row)
        prev = source_strength.get(src, 0.0)
        if strength > prev:
            source_strength[src] = strength
        if src not in source_details:
            source_details[src] = {
                "wing": str(row.get("wing", "unknown")),
                "room": str(row.get("room", "unknown")),
                "similarity": row.get("similarity"),
                "text": _expand_row_text(row),
                "text_preview": str(row.get("text_preview", "") or ""),
                "source_path": row.get("source_path"),
                "line_start": row.get("line_start"),
                "line_end": row.get("line_end"),
            }
    for src in source_union:
        source_strength.setdefault(src, 0.45)

    y_positions = [0.2 + i * (0.6 / max(1, len(source_union) - 1)) for i in range(len(source_union))]
    source_map = {src: y_positions[idx] for idx, src in enumerate(source_union)}

    history_routes = []
    for idx, ev in enumerate(window):
        rows = _event_rows(ev, max_rows=14)
        selected_raw: List[str] = []
        alternatives_raw: List[str] = []
        ordered_raw: List[Dict[str, str]] = []
        ordered_seen: Set[str] = set()
        for row in rows:
            src = str(row.get("source_file", "unknown"))
            if src not in source_map:
                continue
            is_sel = bool(row.get("selected", True))
            target = selected_raw if is_sel else alternatives_raw
            if src not in target:
                target.append(src)
            if src not in ordered_seen:
                ordered_seen.add(src)
                ordered_raw.append({"src": src, "kind": ("selected" if is_sel else "alt")})

        alternatives = alternatives_raw[:max_alt_routes]

        selected_cap = max(1, target_total_routes - len(alternatives))
        selected = selected_raw[:selected_cap]
        allowed = set(selected).union(set(alternatives))
        ordered = [item for item in ordered_raw if str(item.get("src", "")) in allowed]

        history_routes.append(
            {
                "age": idx,
                "selected": selected,
                "alternatives": alternatives,
                "ordered": ordered,
                "query": str(ev.get("query", ""))[:90],
            }
        )

    latest_selected = len(history_routes[-1]["selected"]) if history_routes else 0
    latest_alt = len(history_routes[-1]["alternatives"]) if history_routes else 0
    latest_selected_ids = history_routes[-1]["selected"] if history_routes else []
    target_id = latest_selected_ids[0] if latest_selected_ids else (source_union[0] if source_union else "target")

    stickiness = 0
    if latest_rows:
        counts = Counter(str(r.get("source_file", "unknown")) for r in latest_rows)
        total = len(latest_rows)
        max_share = max(counts.values()) / total if total else 0.0
        unique_sources = len(counts)
        stickiness = int(
            100
            * (
                0.55 * max_share
                + 0.30 * (1.0 - (unique_sources / total if total else 0.0))
                + 0.15 * 0.0
            )
        )

    adaptive = latest.get("adaptive", {}) if isinstance(latest.get("adaptive"), dict) else {}
    controller_state = str(adaptive.get("status", "active")).upper()
    alt_ratio = f"{max(1, latest_alt)}:{max(1, latest_selected)}"
    mean_strength = sum(source_strength.values()) / max(1, len(source_strength))

    return {
        "viewMode": "Latest only",
        "query": latest_query[:120],
        "latestEventTs": str(latest.get("timestamp", "")),
        "latestEventKind": str(latest.get("event_kind", "smart_search") or "smart_search"),
        "labels": {
            "source": _short_text(f"Q: {latest_query}", 34),
            "router": _short_text(f"Route: {latest_query}", 34),
            "target": _short_text(f"Answer: {_source_label(target_id)}", 34),
        },
        "labels_full": {
            "source": f"Q: {latest_query}",
            "router": f"Route: {latest_query}",
            "target": f"Answer: {_source_name(target_id)}",
        },
        "controllerState": controller_state,
        "stickinessRisk": max(1, min(99, stickiness)),
        "altRouteRatio": alt_ratio,
        "meanVectorStrength": round(float(mean_strength), 3),
        "targetStrength": round(float(source_strength.get(target_id, mean_strength)), 3),
        "sources": [
            {
                "id": src,
                "label": _source_label(src),
                "full_label": _source_name(src),
                "y": source_map[src],
                "strength": round(float(source_strength.get(src, 0.45)), 3),
            }
            for src in source_union
        ],
        "sourceDetails": source_details,
        "historyRoutes": history_routes,
    }


def _render_neural_simulator(payload: Dict, component_key: str) -> None:
    if not payload:
        st.info("No route data yet. Run smart search to populate neural paths.")
        return

    payload_json = json.dumps(payload, ensure_ascii=False)
    html = f"""
<div id="neural-wrap" style="position:relative;width:100%;height:1240px;background:#030504;border:1px solid #0f2f1a;border-radius:12px;overflow:hidden;">
  <canvas id="neural-canvas" style="position:absolute;inset:0;width:100%;height:100%;"></canvas>
  <div style="position:absolute;left:16px;top:16px;width:320px;background:rgba(0,20,0,.72);border:1px solid #1eff8c55;border-radius:10px;padding:14px;color:#b9ffd8;font-family:Consolas, monospace;z-index:2;backdrop-filter: blur(3px);">
    <div style="font-size:14px;font-weight:700;color:#7dffbc;margin-bottom:8px;">NEURAL PATH OBSERVATORY</div>
    <div style="font-size:12px;margin-bottom:8px;opacity:.9;">Controller: <b id="ctl-state" style="color:#79ffb1">ACTIVE</b></div>
    <div style="font-size:12px;margin-bottom:8px;">Stickiness Risk: <b id="stick-val" style="color:#79ffb1">14%</b></div>
    <div style="font-size:12px;margin-bottom:12px;">Alternative Route Ratio: <b id="ratio-val" style="color:#ffb347">3:1</b></div>
    <div style="font-size:11px;opacity:.85;margin-bottom:8px;">Current Query</div>
    <input id="query-input" readonly style="width:100%;box-sizing:border-box;background:#021208;border:1px solid #1eff8c55;color:#b9ffd8;border-radius:8px;padding:8px;font-family:Consolas, monospace;" />
    <div style="margin-top:10px;font-size:11px;">Vector Strength: <b id="vector-strength" style="color:#79ffb1">0.00</b></div>
    <div style="margin-top:4px;height:6px;background:rgba(30,80,55,.5);border:1px solid rgba(30,120,70,.6);border-radius:999px;overflow:hidden;">
      <div id="vector-bar" style="height:100%;width:0%;background:linear-gradient(90deg,#1ccf74,#64ffb3);"></div>
    </div>
    <div style="margin-top:10px;font-size:10px;opacity:.82;">Mini diagnostics</div>
    <div id="mini-diag" style="margin-top:4px;font-size:10px;line-height:1.35;opacity:.92;white-space:normal;overflow-wrap:anywhere;">
      mode: - | nodes: - | selected: - | alt: -
    </div>
    <div id="node-info" style="margin-top:10px;font-size:11px;opacity:.9;min-height:18px;white-space:normal;overflow-wrap:anywhere;">Drag nodes to tune path layout.</div>
  </div>
  <div id="node-popup" style="display:none;position:absolute;right:16px;top:18px;width:360px;max-width:46%;background:rgba(2,16,11,.90);border:1px solid #33e19e66;border-radius:10px;padding:12px 12px 10px;color:#d7ffeb;font-family:Consolas,monospace;z-index:3;box-shadow:0 6px 24px rgba(0,0,0,.38);">
    <div id="node-popup-title" style="font-size:12px;font-weight:700;color:#91ffc9;margin-bottom:8px;">Node details</div>
    <div id="node-popup-meta" style="font-size:10px;opacity:.8;margin-bottom:8px;white-space:normal;overflow-wrap:anywhere;">file: -</div>
    <div style="font-size:10px;opacity:.8;margin-bottom:4px;">IN</div>
    <div id="node-popup-in" style="font-size:11px;line-height:1.35;min-height:18px;margin-bottom:9px;white-space:normal;overflow-wrap:anywhere;">-</div>
    <div style="font-size:10px;opacity:.8;margin-bottom:4px;">OUT</div>
    <div id="node-popup-codewrap" style="display:grid;grid-template-columns:42px 1fr;max-height:220px;overflow-y:hidden;overflow-x:hidden;border:1px solid #2dcf9033;border-radius:8px;background:#011109;margin-bottom:9px;">
      <pre id="node-popup-lines" style="margin:0;padding:8px 6px 8px 8px;font-size:10px;line-height:1.35;color:#77c79f;background:rgba(8,38,25,.45);border-right:1px solid #2dcf9028;text-align:right;user-select:none;pointer-events:none;">-</pre>
      <pre id="node-popup-out" style="margin:0;padding:8px 10px;font-size:11px;line-height:1.35;color:#d7ffeb;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;user-select:text;cursor:text;">-</pre>
    </div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px;">
      <button id="node-popup-toggle" style="cursor:pointer;font-size:10px;background:transparent;border:1px solid #2dcf9055;color:#98ffca;border-radius:6px;padding:3px 8px;">Show full content</button>
      <button id="node-popup-copy" style="cursor:pointer;font-size:10px;background:transparent;border:1px solid #38bdf855;color:#7dd3fc;border-radius:6px;padding:3px 8px;">Copy</button>
    </div>
  </div>
</div>
<script>
(() => {{
  const data = {payload_json};
  const wrap = document.getElementById('neural-wrap');
  const canvas = document.getElementById('neural-canvas');
  const ctx = canvas.getContext('2d');
  const queryInput = document.getElementById('query-input');
  const vectorStrengthEl = document.getElementById('vector-strength');
  const vectorBarEl = document.getElementById('vector-bar');
  const nodeInfo = document.getElementById('node-info');
  const ctl = document.getElementById('ctl-state');
  const stick = document.getElementById('stick-val');
  const ratio = document.getElementById('ratio-val');
  const nodePopup = document.getElementById('node-popup');
  const nodePopupTitle = document.getElementById('node-popup-title');
  const nodePopupMeta = document.getElementById('node-popup-meta');
  const nodePopupIn = document.getElementById('node-popup-in');
  const nodePopupCodeWrap = document.getElementById('node-popup-codewrap');
  const nodePopupLines = document.getElementById('node-popup-lines');
  const nodePopupOut = document.getElementById('node-popup-out');
  const nodePopupToggle = document.getElementById('node-popup-toggle');
  const nodePopupCopy = document.getElementById('node-popup-copy');
  const miniDiag = document.getElementById('mini-diag');

  queryInput.value = data.query || "Simulate memory route";
  ctl.textContent = data.controllerState || "ACTIVE";
  stick.textContent = String(data.stickinessRisk || 14) + "%";
  ratio.textContent = data.altRouteRatio || "3:1";

  let w = 0, h = 0;
  let matrixDrops = [];
  let particles = [];
  let animProgress = 0;
  let lastFrameTs = 0;
  let isTabHidden = document.visibilityState === "hidden";
  let interactionBoostUntil = Date.now() + 2500;
  let speed = Math.max(0.45, Math.min(1.45, Number(data.meanVectorStrength || 0.5) * 1.65));
  let clickedLabel = "";
  let dragNode = null;
  let isPanning = false;
  let selectedNode = null;
  let activeRouteSnapshot = null;
  let popupExpanded = false;
  let popupContentFull = "";
  let popupLineStart = 1;
  const view = {{ scale: 1.0, tx: 0, ty: 0 }};
  let currentStrength = Math.max(0, Math.min(1, Number(data.meanVectorStrength || 0.5)));
  let targetStrength = currentStrength;
  const historyRoutes = data.historyRoutes || [];
  const latestRouteIndex = historyRoutes.length ? historyRoutes.length - 1 : -1;

  function markInteractive(ms=2200) {{
    interactionBoostUntil = Date.now() + Math.max(400, Number(ms || 0));
  }}

  function frameIntervalMs() {{
    if (isTabHidden) return 700;   // ~1.4 FPS when tab hidden
    if (Date.now() < interactionBoostUntil) return 16; // ~60 FPS while user interacts
    return 33; // ~30 FPS idle
  }}

  document.addEventListener("visibilitychange", () => {{
    isTabHidden = document.visibilityState === "hidden";
    if (!isTabHidden) {{
      markInteractive(1800);
    }}
  }});

  const nodeModel = {{
    source: {{ x: 0.08, y: 0.5, tx: 0.08, ty: 0.5 }},
    router: {{ x: 0.34, y: 0.5, tx: 0.34, ty: 0.5 }},
    target: {{ x: 0.9, y: 0.5, tx: 0.9, ty: 0.5 }},
    candidates: {{}},
  }};
  const _sources = data.sources || [];
  const _candidateColOffsets = [-0.030, 0.0, 0.030];
  const _clamp01 = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const _hash01 = (text) => {{
    let h = 0;
    const t = String(text || "");
    for (let i = 0; i < t.length; i++) {{
      h = ((h << 5) - h + t.charCodeAt(i)) | 0;
    }}
    return (Math.abs(h) % 1000) / 1000;
  }};
  _sources.forEach((s, idx) => {{
    const lane = idx % _candidateColOffsets.length;
    const colShift = _candidateColOffsets[lane];
    const hash = _hash01(s.id);
    const jitterX = (hash - 0.5) * 0.016; // subtle horizontal spread
    const jitterY = (((hash * 1.7) % 1) - 0.5) * 0.020; // subtle vertical spread
    const waveY = Math.sin((idx + 1) * 0.95) * 0.010; // soft anti-overlap wave
    const x = _clamp01(0.56 + colShift + jitterX, 0.50, 0.69);
    const y = _clamp01(Number(s.y || 0.5) + jitterY + waveY, 0.08, 0.92);
    nodeModel.candidates[s.id] = {{ x, y, tx: x, ty: y, label: s.label, fullLabel: s.full_label || s.label }};
  }});

  const chars = "01ABCDEF#$%";
  const sourceStrengthMap = {{}};
  const sourceYMap = {{}};
  const sourceLabelMap = {{}};
  const sourceDetails = data.sourceDetails || {{}};
  for (const s of data.sources || []) {{
    sourceStrengthMap[s.id] = Number(s.strength || 0.45);
    sourceYMap[s.id] = Number(s.y || 0.5);
    sourceLabelMap[s.id] = s.full_label || s.label || s.id;
  }}

  function labelForSource(src) {{
    if (!src) return "unknown";
    return sourceLabelMap[src] || String(src);
  }}

  function closeNodePopup() {{
    nodePopup.style.display = "none";
    popupExpanded = false;
    popupContentFull = "";
    popupLineStart = 1;
    nodePopupToggle.textContent = "Show full content";
    nodePopupCopy.textContent = "Copy";
    nodePopupCodeWrap.style.overflowY = "hidden";
  }}

  function _short(v, n=220) {{
    const t = String(v || "").trim();
    if (!t) return "";
    return t.length > n ? (t.slice(0, n - 1) + "...") : t;
  }}

  function nodeDataForPopup(node, route) {{
    if (!node) return "-";
    if (node.key === "candidate" && node.id) {{
      const d = sourceDetails[node.id] || {{}};
      return d;
    }}
    if (node.key === "router") {{
      const selected = Array.from(new Set((route && route.selected) || []));
      for (const src of selected) {{
        const d = sourceDetails[src] || {{}};
        if (String(d.text || "").trim()) return d;
      }}
      return {{}};
    }}
    if (node.key === "target" || node.key === "source") {{
      const selected = Array.from(new Set((route && route.selected) || []));
      for (const src of selected) {{
        const d = sourceDetails[src] || {{}};
        if (String(d.text || "").trim()) return d;
      }}
      return {{}};
    }}
    return {{}};
  }}

  function renderPopupContent() {{
    if (!popupContentFull) {{
      nodePopupOut.textContent = "No extracted content in this event.";
      nodePopupLines.textContent = "-";
      nodePopupToggle.style.display = "none";
      nodePopupCopy.style.display = "none";
      nodePopupCodeWrap.style.overflowY = "hidden";
      return;
    }}
    const allLines = String(popupContentFull).replace(/\\r/g, "").split("\\n");
    const maxPreviewLines = 3;
    const previewLineMaxChars = 140;
    const truncateLine = (line) => {{
      const s = String(line || "");
      if (s.length <= previewLineMaxChars) return s;
      return s.slice(0, previewLineMaxChars - 3) + "...";
    }};
    const visibleLinesRaw = popupExpanded ? allLines : allLines.slice(0, maxPreviewLines);
    const visibleLines = popupExpanded ? visibleLinesRaw : visibleLinesRaw.map(truncateLine);
    let codeText = visibleLines.join("\\n");
    if (!popupExpanded && allLines.length > maxPreviewLines) {{
      codeText += "\\n...";
    }}
    nodePopupOut.textContent = codeText;

    const lineNumbers = [];
    for (let i = 0; i < visibleLines.length; i++) {{
      lineNumbers.push(String((popupLineStart || 1) + i));
    }}
    if (!popupExpanded && allLines.length > maxPreviewLines) {{
      lineNumbers.push("...");
    }}
    nodePopupLines.textContent = lineNumbers.join("\\n");
    nodePopupToggle.style.display = "inline-block";
    nodePopupCopy.style.display = "inline-block";
    nodePopupToggle.textContent = popupExpanded ? "Show less" : "Show full content";
    nodePopupCodeWrap.style.overflowY = popupExpanded ? "auto" : "hidden";
  }}

  function renderMiniDiag(route) {{
    const selected = Array.from(new Set((route && route.selected) || []));
    const alternatives = Array.from(new Set((route && route.alternatives) || []));
    const ts = String(data.latestEventTs || "").replace("T", " ").replace("+00:00", " UTC");
    const kind = String(data.latestEventKind || "smart_search");
    miniDiag.textContent =
      "mode: Latest only | kind: " + kind +
      " | nodes: " + String((data.sources || []).length) +
      " | selected: " + String(selected.length) +
      " | alt: " + String(alternatives.length) +
      " | ts: " + (ts || "-");
  }}

  function openNodePopup(node, route) {{
    if (!node || !route) return;
    const d = nodeDataForPopup(node, route);
    popupExpanded = false;
    popupContentFull = String(d.text || d.text_preview || "").trim();
    const lineStart = d.line_start ?? null;
    popupLineStart = (lineStart && Number(lineStart) > 0) ? Number(lineStart) : 1;
    const fileInfo = String(d.source_path || d.source_file || node.id || "-");
    nodePopupTitle.textContent = "Node: " + (node.label || "unknown");
    nodePopupMeta.textContent = "file: " + fileInfo;
    nodePopupIn.textContent = String(data.query || "-");
    renderPopupContent();
    nodePopup.style.display = "block";
  }}

  nodePopupToggle.addEventListener("click", () => {{
    popupExpanded = !popupExpanded;
    renderPopupContent();
  }});

  nodePopupCopy.addEventListener("click", async () => {{
    const textToCopy = String(popupContentFull || "").trim();
    if (!textToCopy) return;
    try {{
      await navigator.clipboard.writeText(textToCopy);
      nodePopupCopy.textContent = "Copied";
      setTimeout(() => {{ nodePopupCopy.textContent = "Copy"; }}, 1200);
    }} catch (e) {{
      nodePopupCopy.textContent = "Copy failed";
      setTimeout(() => {{ nodePopupCopy.textContent = "Copy"; }}, 1400);
    }}
  }});

  function setTargetStrength(strength) {{
    targetStrength = Math.max(0, Math.min(1, Number(strength || 0)));
  }}

  function renderStrength() {{
    vectorStrengthEl.textContent = currentStrength.toFixed(2);
    vectorBarEl.style.width = Math.round(currentStrength * 100) + "%";
    speed = Math.max(0.45, Math.min(1.5, currentStrength * 1.7));
  }}

  function animateStrength() {{
    const d = targetStrength - currentStrength;
    if (Math.abs(d) < 0.001) {{
      currentStrength = targetStrength;
      renderStrength();
      return;
    }}
    currentStrength += d * 0.18;
    renderStrength();
  }}

  function strengthForNode(node) {{
    if (!node) return Number(data.meanVectorStrength || 0.5);
    if (node.key === "candidate" && node.id) return Number(sourceStrengthMap[node.id] || data.meanVectorStrength || 0.5);
    if (node.key === "target") return Number(data.targetStrength || data.meanVectorStrength || 0.5);
    return Number(data.meanVectorStrength || 0.5);
  }}

  function laneFactorForSource(src) {{
    const y = Math.max(0, Math.min(1, Number(sourceYMap[src] ?? 0.5)));
    // Keep top lanes slightly faster but avoid extreme jumps.
    return 1.22 - (0.62 * y); // y=0 -> 1.22, y=1 -> 0.60
  }}

  function visualStrength(src) {{
    const s = Math.max(0, Math.min(1, Number(sourceStrengthMap[src] ?? data.meanVectorStrength ?? 0.5)));
    // Increase contrast so thickness/intensity differences are obvious.
    return Math.max(0, Math.min(1, Math.pow(s, 0.72)));
  }}

  function _routeByIndex(idx) {{
    if (!historyRoutes.length) return {{selected: [], alternatives: []}};
    const safeIdx = Math.max(0, Math.min(historyRoutes.length - 1, idx));
    return historyRoutes[safeIdx] || {{selected: [], alternatives: []}};
  }}

  function _sourceJitterPx(src) {{
    const text = String(src || "");
    let h = 0;
    for (let i = 0; i < text.length; i++) {{
      h = ((h << 5) - h + text.charCodeAt(i)) | 0;
    }}
    return ((Math.abs(h) % 9) - 4) * 0.85;
  }}

  function _updateActiveRoute() {{
    if (!historyRoutes.length) {{
      return {{selected: [], alternatives: []}};
    }}
    const latestRoute = _routeByIndex(latestRouteIndex);
    return latestRoute;
  }}

  renderStrength();
  if (latestRouteIndex >= 0) {{
    const latestRouteSeed = _routeByIndex(latestRouteIndex);
    const focusSrc = (latestRouteSeed.selected && latestRouteSeed.selected[0]) || (latestRouteSeed.alternatives && latestRouteSeed.alternatives[0]) || null;
    if (focusSrc) {{
      setTargetStrength(visualStrength(focusSrc));
    }}
  }}

  function resize() {{
    const rect = wrap.getBoundingClientRect();
    w = Math.max(200, Math.floor(rect.width));
    h = Math.max(280, Math.floor(rect.height));
    canvas.width = w;
    canvas.height = h;
    const cols = Math.floor(w / 14);
    matrixDrops = Array.from({{ length: cols }}, () => 1 + Math.random() * 40);
  }}

  function pt(xNorm, yNorm) {{
    return {{ x: xNorm * w, y: yNorm * h }};
  }}
  function toWorld(mx, my) {{
    return {{
      x: (mx - view.tx) / view.scale,
      y: (my - view.ty) / view.scale,
    }};
  }}

  function clamp(v, lo, hi) {{ return Math.max(lo, Math.min(hi, v)); }}

  function dragBounds(key) {{
    if (key === "source") return {{ xMin: 0.03, xMax: 0.23, yMin: 0.08, yMax: 0.92 }};
    if (key === "router") return {{ xMin: 0.24, xMax: 0.48, yMin: 0.08, yMax: 0.92 }};
    if (key === "target") return {{ xMin: 0.79, xMax: 0.97, yMin: 0.08, yMax: 0.92 }};
    return {{ xMin: 0.50, xMax: 0.72, yMin: 0.08, yMax: 0.92 }};
  }}

  function nodePos(key, id=null) {{
    if (key === "candidate" && id && nodeModel.candidates[id]) return nodeModel.candidates[id];
    return nodeModel[key];
  }}

  function sourcePoint() {{ const p = nodePos("source"); return pt(p.x, p.y); }}
  function splitPoint() {{ const p = nodePos("router"); return pt(p.x, p.y); }}
  function targetPoint() {{ const p = nodePos("target"); return pt(p.x, p.y); }}
  function candidatePoint(id) {{
    const p = nodePos("candidate", id) || {{ x: 0.56, y: 0.5 }};
    return pt(p.x, p.y);
  }}

  function drawMatrix() {{
    ctx.fillStyle = "rgba(2,6,3,0.10)";
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "rgba(43,185,84,0.12)";
    ctx.font = "12px Consolas, monospace";
    for (let i = 0; i < matrixDrops.length; i++) {{
      const ch = chars[Math.floor(Math.random() * chars.length)];
      const y = matrixDrops[i] * 14;
      ctx.fillText(ch, i * 14, y);
      if (y > h && Math.random() > 0.975) matrixDrops[i] = 0;
      matrixDrops[i] += 0.25;
    }}
  }}

  function drawNode(p, color, label, alpha=1) {{
    ctx.save();
    ctx.globalAlpha = Math.max(0, Math.min(1, Number(alpha || 0)));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    ctx.shadowBlur = 14;
    ctx.shadowColor = color;
    const rw = 52, rh = 34;
    ctx.strokeRect(p.x - rw / 2, p.y - rh / 2, rw, rh);
    for (let i = 0; i < 3; i++) {{
      const yy = p.y - 10 + i * 10;
      ctx.beginPath();
      ctx.moveTo(p.x - 16, yy);
      ctx.lineTo(p.x + 16, yy);
      ctx.stroke();
    }}
    ctx.shadowBlur = 0;
    ctx.fillStyle = color;
    ctx.font = "10px Consolas, monospace";
    ctx.textAlign = "center";
    ctx.fillText(label, p.x, p.y + 29);
    ctx.restore();
  }}

  function drawSegment(a, b, progress, color, width, dash=[], bendShift=0) {{
    const tMax = Math.max(0, Math.min(1, progress));
    if (tMax <= 0) return;
    const dx = b.x - a.x;
    const c1 = {{ x: a.x + dx * 0.38, y: a.y + bendShift }};
    const c2 = {{ x: a.x + dx * 0.62, y: b.y - bendShift }};
    const pointAt = (t) => {{
      const u = 1 - t;
      return {{
        x: (u * u * u * a.x) + (3 * u * u * t * c1.x) + (3 * u * t * t * c2.x) + (t * t * t * b.x),
        y: (u * u * u * a.y) + (3 * u * u * t * c1.y) + (3 * u * t * t * c2.y) + (t * t * t * b.y),
      }};
    }};
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.shadowBlur = 2;
    ctx.shadowColor = color;
    ctx.beginPath();
    const steps = Math.max(12, Math.floor(48 * tMax));
    const p0 = pointAt(0);
    ctx.moveTo(p0.x, p0.y);
    for (let i = 1; i <= steps; i++) {{
      const t = (tMax * i) / steps;
      const p = pointAt(t);
      ctx.lineTo(p.x, p.y);
    }}
    ctx.stroke();
    ctx.restore();
  }}

  function spawnParticles(latest) {{
    if (!latest) return;
    if (Math.random() < (0.10 + 0.14 * speed / 1.5) && latest.selected.length) {{
      const src = latest.selected[Math.floor(Math.random() * latest.selected.length)];
      const srcStrength = visualStrength(src);
      const lane = laneFactorForSource(src);
      particles.push({{type:"main", src, t:0, speed:(0.0025 + 0.010 * srcStrength) * speed * lane * 0.5}});
    }}
    if (Math.random() < (0.05 + 0.10 * speed / 1.5) && latest.alternatives.length) {{
      const src = latest.alternatives[Math.floor(Math.random() * latest.alternatives.length)];
      const srcStrength = visualStrength(src);
      const lane = laneFactorForSource(src);
      particles.push({{type:"alt", src, t:0, speed:(0.0015 + 0.0058 * srcStrength) * speed * lane * 0.5, life:1.0}});
    }}
  }}

  function drawParticle(p) {{
    const s = sourcePoint();
    const split = splitPoint();
    const cand = candidatePoint(p.src);
    const target = targetPoint();
    const qPoint = (a, b, t) => {{
      const dx = b.x - a.x;
      const c1 = {{ x: a.x + dx * 0.38, y: a.y }};
      const c2 = {{ x: a.x + dx * 0.62, y: b.y }};
      const u = 1 - t;
      return {{
        x: (u * u * u * a.x) + (3 * u * u * t * c1.x) + (3 * u * t * t * c2.x) + (t * t * t * b.x),
        y: (u * u * u * a.y) + (3 * u * u * t * c1.y) + (3 * u * t * t * c2.y) + (t * t * t * b.y),
      }};
    }};
    let pos = {{x:s.x, y:s.y}};
    if (p.type === "main") {{
      if (p.t < .33) {{
        const k = p.t / .33; pos = qPoint(s, split, k);
      }} else if (p.t < .66) {{
        const k = (p.t-.33)/.33; pos = qPoint(split, cand, k);
      }} else {{
        const k = (p.t-.66)/.34; pos = qPoint(cand, target, k);
      }}
      ctx.fillStyle = "rgba(74,228,153,0.72)";
      ctx.shadowColor = "rgba(74,228,153,0.55)";
      ctx.shadowBlur = 6;
      ctx.beginPath(); ctx.arc(pos.x, pos.y, 2.6, 0, Math.PI*2); ctx.fill();
    }} else {{
      if (p.t < .6) {{
        const k = p.t/.6; pos = qPoint(split, cand, k);
      }} else {{
        const k = (p.t-.6)/.4; const end = pt(0.76, nodePos("candidate", p.src)?.y || 0.5);
        pos = qPoint(cand, end, k);
      }}
      const alpha = Math.max(0.05, p.life * 0.78);
      ctx.fillStyle = `rgba(244,158,66,${{alpha.toFixed(3)}})`;
      ctx.shadowColor = ctx.fillStyle;
      ctx.shadowBlur = 4;
      ctx.beginPath(); ctx.arc(pos.x, pos.y, 1.95, 0, Math.PI*2); ctx.fill();
    }}
    ctx.shadowBlur = 0;
  }}

  function orderedEntriesForRoute(route) {{
    const out = [];
    const seen = new Set();
    const rawOrdered = (route && Array.isArray(route.ordered)) ? route.ordered : [];
    for (const row of rawOrdered) {{
      const src = String((row && row.src) || "");
      if (!src || seen.has(src)) continue;
      seen.add(src);
      const kind = (String((row && row.kind) || "selected") === "alt") ? "alt" : "selected";
      out.push({{ src: src, kind: kind }});
    }}
    if (!out.length) {{
      for (const src of Array.from(new Set((route && route.selected) || []))) {{
        if (!src || seen.has(src)) continue;
        seen.add(src);
        out.push({{ src: src, kind: "selected" }});
      }}
      for (const src of Array.from(new Set((route && route.alternatives) || []))) {{
        if (!src || seen.has(src)) continue;
        seen.add(src);
        out.push({{ src: src, kind: "alt" }});
      }}
    }}
    return out;
  }}

  function drawLatestAnimated(latest) {{
    if (!latest) return;
    const s = sourcePoint(), split = splitPoint(), target = targetPoint();
    const ordered = orderedEntriesForRoute(latest);
    const uniqueSelected = ordered.filter(x => x.kind === "selected").map(x => x.src);
    const selectedRoutes = uniqueSelected.length ? uniqueSelected : (ordered.length ? [ordered[0].src] : []);
    const trunkStrength = uniqueSelected.length
      ? Math.max(...uniqueSelected.map(src => visualStrength(src)))
      : Number(data.meanVectorStrength || 0.5);
    const trunkAlpha = (0.30 + 0.14 * trunkStrength) * 0.5;
    const trunkW = 0.95 + 0.95 * trunkStrength;
    // Layer 1 (Latest only): single animated semi-transparent line.
    drawSegment(
      s,
      split,
      Math.max(0, Math.min(1, animProgress * 1.20)),
      `rgba(78,250,170,${{trunkAlpha.toFixed(3)}})`,
      trunkW
    );
    const totalOrdered = Math.max(1, ordered.length);
    ordered.forEach((item, idx) => {{
      const src = item.src;
      const kind = item.kind;
      const cand = candidatePoint(src);
      const srcStrength = visualStrength(src);
      const delay = Math.min(0.72, 0.08 + (idx / totalOrdered) * 0.58);
      const phase = Math.max(0, Math.min(1, (animProgress - delay) / Math.max(0.08, 1 - delay)));
      if (kind === "selected") {{
        const alpha = (0.28 + 0.18 * srcStrength) * (0.55 + 0.45 * phase) * 0.5;
        const mainW = 0.85 + 0.95 * srcStrength;
        const bend = _sourceJitterPx(src) * 0.35;
        drawSegment(split, cand, phase, `rgba(90,252,181,${{alpha.toFixed(3)}})`, mainW, [], bend);
        const phase2 = Math.max(0, Math.min(1, (phase - 0.12) / 0.88));
        drawSegment(cand, target, phase2, `rgba(90,252,181,${{alpha.toFixed(3)}})`, mainW + 0.10, [], bend * 0.45);
      }} else {{
        const end = pt(0.76, nodePos("candidate", src)?.y || 0.5);
        const alpha = (0.18 + 0.18 * srcStrength) * (0.52 + 0.48 * phase);
        const altW = 0.70 + 0.78 * srcStrength;
        const bend = _sourceJitterPx(src) * 0.55;
        drawSegment(split, cand, phase, `rgba(244,158,66,${{alpha.toFixed(3)}})`, altW, [6,4], bend);
        const phase2 = Math.max(0, Math.min(1, (phase - 0.10) / 0.90));
        drawSegment(cand, end, phase2, `rgba(244,158,66,${{(alpha*0.90).toFixed(3)}})`, Math.max(0.58, altW - 0.10), [6,4], bend * 0.72);
      }}
    }});
    // Latest-only mode: only animated paths and particles.
  }}

  function drawNodes(latest) {{
    const latestRoute = latest || {{selected: [], alternatives: []}};
    const ordered = orderedEntriesForRoute(latestRoute);
    const orderIndex = new Map();
    ordered.forEach((item, idx) => orderIndex.set(item.src, idx));
    const totalOrdered = Math.max(1, ordered.length);
    const phaseByDelay = (delay) => Math.max(0, Math.min(1, (animProgress - delay) / Math.max(0.08, 1 - delay)));
    const sourcePhase = phaseByDelay(0.00);
    const routerPhase = phaseByDelay(0.06);
    const targetPhase = phaseByDelay(0.12);
    if (sourcePhase > 0.02) drawNode(sourcePoint(), "#43ff9c", data.labels?.source || "QUERY", sourcePhase);
    if (routerPhase > 0.02) drawNode(splitPoint(), "#43ff9c", data.labels?.router || "ROUTE", routerPhase);
    if (targetPhase > 0.02) drawNode(targetPoint(), "#43ff9c", data.labels?.target || "ANSWER", targetPhase);
    for (const s of data.sources || []) {{
      const isHot = (latestRoute.selected || []).includes(s.id);
      const isAlt = (latestRoute.alternatives || []).includes(s.id);
      const color = isHot ? "#43ff9c" : (isAlt ? "#ffb04f" : "#67b68e");
      const idx = orderIndex.has(s.id) ? Number(orderIndex.get(s.id)) : -1;
      const delay = idx >= 0 ? Math.min(0.84, 0.18 + (idx / totalOrdered) * 0.62) : 0.86;
      const phase = phaseByDelay(delay);
      if (phase <= 0.02) continue;
      const alpha = idx >= 0 ? (0.25 + 0.75 * phase) : (0.10 + 0.40 * phase);
      drawNode(candidatePoint(s.id), color, s.label, alpha);
    }}
  }}

  function updateParticles(latest) {{
    spawnParticles(latest);
    particles = particles.filter(p => p.t <= 1.0 && (p.type !== "alt" || p.life > 0.05));
    for (const p of particles) {{
      p.t += p.speed;
      if (p.type === "alt" && p.t > 0.58) p.life -= 0.025 * speed;
      drawParticle(p);
    }}
  }}

  function smoothNodes() {{
    const k = 0.22;
    for (const key of ["source", "router", "target"]) {{
      const n = nodeModel[key];
      n.x += (n.tx - n.x) * k;
      n.y += (n.ty - n.y) * k;
    }}
    for (const id of Object.keys(nodeModel.candidates)) {{
      const n = nodeModel.candidates[id];
      n.x += (n.tx - n.x) * k;
      n.y += (n.ty - n.y) * k;
    }}
  }}

  function animate(ts) {{
    const nowTs = Number(ts || performance.now());
    const minDelta = frameIntervalMs();
    if ((nowTs - lastFrameTs) < minDelta) {{
      requestAnimationFrame(animate);
      return;
    }}
    lastFrameTs = nowTs;
    drawMatrix();
    smoothNodes();
    animateStrength();
    const activeRoute = _updateActiveRoute();
    activeRouteSnapshot = activeRoute;
    renderMiniDiag(activeRoute);
    ctx.save();
    ctx.translate(view.tx, view.ty);
    ctx.scale(view.scale, view.scale);
    const grad = ctx.createLinearGradient(0, h*0.5, w, h*0.5);
    grad.addColorStop(0, "rgba(54,255,153,.02)");
    grad.addColorStop(0.5, "rgba(54,255,153,.05)");
    grad.addColorStop(1, "rgba(54,255,153,.02)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, h*0.495, w, h*0.010);
    drawLatestAnimated(activeRoute);
    drawNodes(activeRoute);
    updateParticles(activeRoute);
    ctx.restore();
    animProgress = Math.min(1, animProgress + 0.008 * speed);
    requestAnimationFrame(animate);
  }}

  function simulate() {{
    animProgress = 0;
    particles = [];
    const base = Number(data.stickinessRisk || 14);
    const jitter = Math.max(1, Math.min(99, base + Math.floor((Math.random() * 11) - 5)));
    stick.textContent = jitter + "%";
    const modes = ["RELAXED", "STABLE", "ACTIVE", "AGGRESSIVE"];
    ctl.textContent = modes[Math.floor(Math.random() * modes.length)];
  }}

  function pickNode(mx, my) {{
    const all = [
      {{ key: "source", id: null, label: data.labels_full?.source || data.labels?.source || "query", p: sourcePoint() }},
      {{ key: "router", id: null, label: data.labels_full?.router || data.labels?.router || "route", p: splitPoint() }},
      {{ key: "target", id: null, label: data.labels_full?.target || data.labels?.target || "answer", p: targetPoint() }},
    ];
    for (const s of data.sources || []) {{
      all.push({{ key: "candidate", id: s.id, label: s.full_label || s.label, p: candidatePoint(s.id) }});
    }}
    for (const n of all) {{
      if (Math.hypot(mx - n.p.x, my - n.p.y) < 38) return n;
    }}
    return null;
  }}

  function setDraggedNodePosition(mx, my) {{
    if (!dragNode) return;
    const nx = clamp(mx / w, 0.01, 0.99);
    const ny = clamp(my / h, 0.05, 0.95);
    const key = dragNode.key;
    const b = dragBounds(key);
    if (key === "candidate" && dragNode.id && nodeModel.candidates[dragNode.id]) {{
      nodeModel.candidates[dragNode.id].tx = clamp(nx, b.xMin, b.xMax);
      nodeModel.candidates[dragNode.id].ty = clamp(ny, b.yMin, b.yMax);
    }} else {{
      nodeModel[key].tx = clamp(nx, b.xMin, b.xMax);
      nodeModel[key].ty = clamp(ny, b.yMin, b.yMax);
    }}
  }}

  canvas.addEventListener("mousedown", (e) => {{
    markInteractive(2400);
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const my = e.clientY - r.top;
    const world = toWorld(mx, my);
    const picked = pickNode(world.x, world.y);
    if (picked) {{
      dragNode = picked;
      selectedNode = picked;
      setTargetStrength(strengthForNode(picked));
      clickedLabel = picked.label;
      nodeInfo.textContent = "Dragging node: " + picked.label + " | strength " + Number(strengthForNode(picked)).toFixed(2);
      isPanning = false;
      return;
    }}
    // Close right popup by clicking empty left canvas area.
    if (world.x < w * 0.46) {{
      closeNodePopup();
    }}
    isPanning = true;
    clickedLabel = "";
    nodeInfo.textContent = "Panning map...";
  }});

  canvas.addEventListener("dblclick", (e) => {{
    markInteractive(2400);
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const my = e.clientY - r.top;
    const world = toWorld(mx, my);
    const picked = pickNode(world.x, world.y);
    if (picked) {{
      openNodePopup(picked, activeRouteSnapshot || _updateActiveRoute());
      e.preventDefault();
      return;
    }}
    if (world.x < w * 0.46) {{
      closeNodePopup();
    }}
  }});

  canvas.addEventListener("mousemove", (e) => {{
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const my = e.clientY - r.top;
    if (dragNode) {{
      markInteractive(2200);
      const world = toWorld(mx, my);
      setDraggedNodePosition(world.x, world.y);
      return;
    }}
    if (isPanning && (e.buttons & 1) === 1) {{
      markInteractive(2200);
      view.tx += e.movementX;
      view.ty += e.movementY;
    }}
  }});

  window.addEventListener("mouseup", () => {{
    markInteractive(1500);
    if (dragNode) {{
      selectedNode = dragNode;
      const s = strengthForNode(selectedNode);
      setTargetStrength(s);
      nodeInfo.textContent = "Selected node: " + dragNode.label + " | strength " + Number(s).toFixed(2);
    }}
    dragNode = null;
    if (isPanning) {{
      nodeInfo.textContent = "Map moved.";
    }}
    isPanning = false;
  }});

  resize();
  window.addEventListener("resize", resize);
  simulate();
  animate();
}})();
</script>
"""
    # `key` is not supported by older Streamlit `components.html` versions.
    # Embed key marker into HTML so content still changes when signature changes.
    html = f"<!-- sim-key:{component_key} -->\n" + html
    components.html(html, height=1240, scrolling=False)


def _render_help_title(
    title: str,
    tooltip: str,
    section_key: str,
    what_it_is: str,
    examples: List[str],
    impact: str,
    watch_for: str = "",
    meaning: str = "",
    checklist: List[str] | None = None,
) -> None:
    tooltip_safe = html.escape(tooltip, quote=True)
    title_safe = html.escape(title)
    what_safe = html.escape(what_it_is)
    impact_safe = html.escape(impact)
    watch_for_safe = html.escape(watch_for) if watch_for else ""
    meaning_text = meaning.strip() if meaning else f"In plain words: {what_it_is}"
    meaning_safe = html.escape(meaning_text)
    checklist_items = checklist or [
        "Check trend over multiple events, not one isolated spike.",
        "Compare with related metrics before changing settings.",
        "If issue persists, inspect recent routes in the route table.",
    ]
    checklist_html = "".join(f"<li>{html.escape(item)}</li>" for item in checklist_items)
    example_items = "".join(f"<li>{html.escape(ex)}</li>" for ex in examples)
    modal_id = f"mp-help-modal-{section_key}"
    st.markdown(
        f"""
        <div class="mp-help-wrap">
          <input id="{modal_id}" class="mp-help-modal-toggle" type="checkbox" />
          <div class="mp-help-title">
            <span class="mp-help-title-text">{title_safe}</span>
            <span class="mp-help-q">?
              <span class="mp-help-tooltip">
                <span class="mp-help-tooltip-text">{tooltip_safe}</span>
                <label for="{modal_id}" class="mp-help-more-btn">More</label>
              </span>
            </span>
          </div>
          <label for="{modal_id}" class="mp-help-modal-backdrop"></label>
          <div class="mp-help-modal">
            <div class="mp-help-modal-header">
              <span>{title_safe}</span>
              <label for="{modal_id}" class="mp-help-close">Close</label>
            </div>
            <div class="mp-help-modal-body">
              <p><strong>What this shows</strong></p>
              <p>{what_safe}</p>
              <p><strong>Simple examples</strong></p>
              <ul>{example_items}</ul>
              <p><strong>What it affects</strong></p>
              <p>{impact_safe}</p>
              <p><strong>How this connects to MemPalace</strong></p>
              <p>{impact_safe}</p>
              <p><strong>What this means</strong></p><p>{meaning_safe}</p>
              {f"<p><strong>What to watch first</strong></p><p>{watch_for_safe}</p>" if watch_for_safe else ""}
              <p><strong>Checklist</strong></p>
              <ul>{checklist_html}</ul>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_inline_help(
    label: str,
    tooltip: str,
    section_key: str,
    what_it_is: str,
    examples: List[str],
    impact: str,
    watch_for: str = "",
    meaning: str = "",
    checklist: List[str] | None = None,
) -> None:
    tooltip_safe = html.escape(tooltip, quote=True)
    label_safe = html.escape(label)
    what_safe = html.escape(what_it_is)
    impact_safe = html.escape(impact)
    watch_for_safe = html.escape(watch_for) if watch_for else ""
    meaning_text = meaning.strip() if meaning else f"In plain words: {what_it_is}"
    meaning_safe = html.escape(meaning_text)
    example_items = "".join(f"<li>{html.escape(ex)}</li>" for ex in examples)
    checklist_items = checklist or [
        "Check trend over multiple events, not one isolated spike.",
        "Compare with related metrics before changing settings.",
        "If issue persists, inspect recent routes in the route table.",
    ]
    checklist_html = "".join(f"<li>{html.escape(item)}</li>" for item in checklist_items)
    modal_id = f"mp-help-modal-inline-{section_key}"
    st.markdown(
        f"""
        <div class="mp-help-wrap mp-help-inline-wrap">
          <input id="{modal_id}" class="mp-help-modal-toggle" type="checkbox" />
          <div class="mp-help-title mp-help-inline-title">
            <span class="mp-help-title-text mp-help-inline-text">{label_safe}</span>
            <span class="mp-help-q">?
              <span class="mp-help-tooltip">
                <span class="mp-help-tooltip-text">{tooltip_safe}</span>
                <label for="{modal_id}" class="mp-help-more-btn">More</label>
              </span>
            </span>
          </div>
          <label for="{modal_id}" class="mp-help-modal-backdrop"></label>
          <div class="mp-help-modal">
            <div class="mp-help-modal-header">
              <span>{label_safe}</span>
              <label for="{modal_id}" class="mp-help-close">Close</label>
            </div>
            <div class="mp-help-modal-body">
              <p><strong>What this shows</strong></p>
              <p>{what_safe}</p>
              <p><strong>Simple examples</strong></p>
              <ul>{example_items}</ul>
              <p><strong>What it affects</strong></p>
              <p>{impact_safe}</p>
              <p><strong>How this connects to MemPalace</strong></p>
              <p>{impact_safe}</p>
              <p><strong>What this means</strong></p><p>{meaning_safe}</p>
              {f"<p><strong>What to watch first</strong></p><p>{watch_for_safe}</p>" if watch_for_safe else ""}
              <p><strong>Checklist</strong></p>
              <ul>{checklist_html}</ul>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.set_page_config(
    page_title="MemPalace Analytics",
    page_icon="🧠",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.2rem;}
      .mp-help-title {
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 0.15rem 0 0.35rem 0;
        position: relative;
      }
      .mp-help-title-text {
        font-size: 1.1rem;
        font-weight: 700;
        color: #0f172a;
      }
      .mp-help-inline-title {
        margin: 0.05rem 0 0.15rem 0;
      }
      .mp-help-inline-text {
        font-size: 0.86rem;
        font-weight: 600;
        color: #334155;
      }
      .mp-help-q {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 18px;
        height: 18px;
        border-radius: 999px;
        border: 1px solid #94a3b8;
        color: #334155;
        background: #f8fafc;
        font-size: 12px;
        font-weight: 700;
        cursor: help;
        user-select: none;
        position: relative;
      }
      .mp-help-tooltip {
        display: none;
        position: absolute;
        left: 24px;
        top: -2px;
        min-width: 220px;
        max-width: 320px;
        padding: 8px;
        border-radius: 8px;
        border: 1px solid #cbd5e1;
        background: #ffffff;
        box-shadow: 0 6px 18px rgba(15, 23, 42, 0.18);
        color: #0f172a;
        z-index: 20;
        pointer-events: auto;
      }
      /* Hover bridge: keeps tooltip open while moving cursor from ? to tooltip */
      .mp-help-tooltip::before {
        content: "";
        position: absolute;
        left: -20px;
        top: -6px;
        width: 24px;
        height: calc(100% + 12px);
        background: transparent;
      }
      .mp-help-tooltip-text {
        display: block;
        font-size: 12px;
        line-height: 1.35;
        font-weight: 500;
        margin-bottom: 8px;
      }
      .mp-help-q:hover .mp-help-tooltip {
        display: block;
      }
      .mp-help-more-btn {
        display: inline-block;
        font-size: 11px;
        font-weight: 600;
        color: #0f766e;
        border: 1px solid #99f6e4;
        background: #f0fdfa;
        border-radius: 999px;
        padding: 3px 8px;
        cursor: pointer;
      }
      .mp-help-modal-toggle {
        display: none;
      }
      .mp-help-modal-backdrop {
        display: none;
        position: fixed;
        inset: 0;
        background: rgba(2, 6, 23, 0.86);
        backdrop-filter: blur(2px);
        z-index: 900;
      }
      .mp-help-modal {
        display: none;
        position: fixed;
        left: 50%;
        top: 50%;
        transform: translate(-50%, -50%);
        width: min(760px, 92vw);
        max-height: 84vh;
        overflow: auto;
        background: #ffffff;
        border: 1px solid #cbd5e1;
        border-radius: 12px;
        box-shadow: 0 18px 48px rgba(15, 23, 42, 0.25);
        z-index: 901;
      }
      .mp-help-modal-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 14px;
        border-bottom: 1px solid #e2e8f0;
        font-weight: 700;
        color: #0f172a;
      }
      .mp-help-modal-body {
        padding: 12px 14px;
        color: #1e293b;
        font-size: 14px;
        line-height: 1.45;
      }
      .mp-help-modal-body p {
        margin: 0.35rem 0;
      }
      .mp-help-modal-body ul {
        margin: 0.25rem 0 0.6rem 1rem;
      }
      .mp-help-close {
        font-size: 12px;
        font-weight: 600;
        color: #0f766e;
        border: 1px solid #99f6e4;
        background: #f0fdfa;
        border-radius: 999px;
        padding: 3px 9px;
        cursor: pointer;
      }
      .mp-help-modal-toggle:checked ~ .mp-help-modal-backdrop,
      .mp-help-modal-toggle:checked ~ .mp-help-modal {
        display: block;
      }
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
_render_help_title(
    title="Dashboard overview",
    tooltip="Quick orientation for what this page tracks.",
    section_key="overview",
    what_it_is="This dashboard is a live health panel for memory quality. Think of it like a car dashboard: speed, fuel, and warning lights, but for AI memory retrieval.",
    examples=[
        "If stickiness goes up, memory keeps choosing same sources too often.",
        "If alternative-route ratio goes up, retrieval explores more paths.",
    ],
    impact="Helps you decide when to trust current retrieval behavior and when to tune indexing/feedback.",
)

with st.sidebar:
    _render_help_title(
        title="Dashboard controls",
        tooltip="Manual controls for page actions.",
        section_key="live-controls",
        what_it_is="Use these actions when needed: refresh this page now or clean duplicate background processes.",
        examples=[
            "Refresh now = manual one-time update.",
            "Fix performance = keeps only one active dashboard/service instance.",
        ],
        impact="Avoids background page reruns and reduces accidental UI lag.",
    )
    manual_refresh = st.button("Refresh now", use_container_width=True)
    if manual_refresh:
        st.rerun()
    run_perf_fix = st.button("Fix performance (clean duplicates)", use_container_width=True)
    if run_perf_fix:
        ok, perf_msg = _run_performance_fix()
        if ok:
            st.success("Performance cleanup finished.")
        else:
            st.error("Performance cleanup failed.")
        if perf_msg:
            st.code(perf_msg, language="text")
    st.caption("Manual mode: no automatic page refresh.")

    _render_help_title(
        title="Data sources",
        tooltip="Paths of files used for charts and metrics.",
        section_key="data-sources",
        what_it_is="These paths tell the dashboard where to read logs and score stores.",
        examples=[
            "Search events file powers route charts and simulator.",
            "Help scores file powers score health plots.",
        ],
        impact="Wrong paths mean empty or misleading charts.",
    )
    transcripts_path = st.text_input("Transcripts path", DEFAULT_TRANSCRIPTS)
    events_path = st.text_input("Search events file", str(DEFAULT_SEARCH_EVENTS))
    scores_path = st.text_input("Help scores file", str(DEFAULT_HELP_SCORES))

transcripts_path_obj = Path(transcripts_path)
events_path_obj = Path(events_path)
scores_path_obj = Path(scores_path)

usage = _cached_usage_stats(transcripts_path, _transcripts_stamp(transcripts_path_obj))
events = _cached_search_events(events_path, _file_stamp(events_path_obj))
scores = _cached_help_scores(scores_path, _file_stamp(scores_path_obj))
df_stick = _compute_stickiness_metrics(events)
df_adaptive = _compute_adaptive_metrics(events)
df_auto_utility = _compute_auto_utility_metrics(events)

sessions_total = usage["sessions_total"]
sessions_mem = usage["sessions_with_mempalace"]
session_share = (sessions_mem / sessions_total * 100.0) if sessions_total else 0.0

_render_help_title(
    title="Core KPI snapshot",
    tooltip="High-level metrics for usage, memory coverage, and risk.",
    section_key="kpi-snapshot",
    what_it_is="A compact summary of activity and quality. Like your daily vital signs.",
    examples=[
        "Sessions with memory (unique) = how many chats used memory at least once.",
        "Stickiness risk = chance retrieval is repeating itself too much.",
    ],
    impact="Fast way to spot whether quality is improving or drifting.",
)

k1, k2, k3, k4, k5 = st.columns(5)
with k1:
    _render_inline_help(
        label="Sessions scanned",
        tooltip="How many chat sessions were analyzed from transcripts.",
        section_key="metric_sessions_scanned",
        what_it_is="Total number of sessions the dashboard scanned from your transcript source.",
        examples=[
            "If this is 120, the board analyzed 120 chat sessions.",
            "If this suddenly drops after path changes, source path may be wrong.",
        ],
        impact="This controls how representative your analytics are. Low coverage can mislead all charts.",
        watch_for="If expected sessions are missing, first verify the transcript path and file availability.",
        meaning="Это ваш «размер выборки». Чем больше сессий реально учтено, тем правдивее картина по всему дашборду.",
        checklist=[
            "Сверьте число сессий с ожидаемым объёмом ваших чатов.",
            "Если число резко упало — проверьте путь к transcripts и доступ к файлам.",
            "Не делайте выводы по качеству, пока покрытие сессий слишком маленькое.",
        ],
    )
    k1.metric(" ", sessions_total)
with k2:
    _render_inline_help(
        label="Sessions with memory (unique)",
        tooltip="Sessions where MemPalace was actually used at least once.",
        section_key="metric_sessions_with_memory",
        what_it_is="Counts sessions where the memory tool was called, not just opened chats.",
        examples=[
            "80/120 means memory participated in about two-thirds of sessions.",
            "A very low number can mean memory hooks are not being triggered often.",
        ],
        impact="Shows real adoption of MemPalace retrieval in practice.",
        watch_for="Track ratio vs Sessions scanned. If ratio falls, check tool invocation flow.",
        meaning="Показывает, в скольких сессиях MemPalace реально участвовал, а не просто был установлен.",
        checklist=[
            "Смотрите долю относительно Sessions scanned, а не только абсолютное число.",
            "Если доля падает — проверьте, вызываются ли memory-инструменты в рабочем потоке.",
            "Если доля растёт, а качество не растёт — анализируйте stickiness и auto utility.",
        ],
    )
    k2.metric(" ", sessions_mem, f"{session_share:.1f}%")
with k3:
    _render_inline_help(
        label="MemPalace calls",
        tooltip="Total count of memory tool invocations.",
        section_key="metric_calls_total",
        what_it_is="How many times memory-related tools were called across scanned sessions.",
        examples=[
            "High calls with high auto utility usually means healthy usage.",
            "High calls with low auto utility may indicate noisy retrieval.",
        ],
        impact="Affects load, telemetry density, and how fast adaptive logic gets reliable signal.",
        watch_for="Use together with Auto utility quality and Stickiness risk, not in isolation.",
    )
    k3.metric(" ", usage["mempalace_tool_calls"])
with k4:
    _render_inline_help(
        label="Auto utility (avg)",
        tooltip="Automatic utility score based on retrieval quality signals.",
        section_key="metric_auto_utility_avg",
        what_it_is="A 0-100 automatic quality estimate from relevance, diversity, consistency, actionability, execution proxy, and stability.",
        examples=[
            "80+ usually means retrieval quality is strong and stable.",
            "Below 55 means route quality likely needs tuning.",
        ],
        impact="Shows machine-estimated usefulness without manual feedback input.",
        watch_for="Track trend over last 30 truth events, not one isolated event.",
    )
    if not df_auto_utility.empty:
        k4.metric(" ", f"{float(df_auto_utility['score'].tail(30).mean()):.1f}/100")
    else:
        k4.metric(" ", "n/a")
with k5:
    _render_inline_help(
        label="Stickiness risk",
        tooltip="How strongly retrieval is repeating same sources.",
        section_key="metric_stickiness_risk",
        what_it_is="A 0-100 risk score: higher means memory keeps choosing the same context too often.",
        examples=[
            "20-35 is generally healthy.",
            "70+ means route diversity likely needs correction.",
        ],
        impact="Directly impacts answer diversity and chance of tunnel-vision responses.",
        watch_for="If rising for many events, review alt-route ratio and adaptive controller state.",
    )
    if not df_stick.empty:
        k5.metric(" ", f"{float(df_stick['stickiness_score'].tail(30).mean()):.1f}/100")
    else:
        k5.metric(" ", "n/a")

if events:
    last_ts = _parse_ts(str(events[-1].get("timestamp", "")))
    last_kind = str(events[-1].get("event_kind", "smart_search") or "smart_search")
    if last_ts:
        age_min = max(
            0,
            int((datetime.now(timezone.utc) - last_ts.astimezone(timezone.utc)).total_seconds() // 60),
        )
        st.caption(
            "Live stream active • last route event "
            f"{last_ts.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC "
            f"({age_min} min ago), kind: `{last_kind}`."
        )
    st.caption(
        "Note: route simulator and route-event counters use "
        "`.mempalace-analytics/search_events.jsonl` only."
    )

st.markdown("---")

left, right = st.columns([1, 1])
with left:
    _render_help_title(
        title="Most used memory tools",
        tooltip="Shows which MemPalace tools are called most often.",
        section_key="most-used-tools",
        what_it_is="A frequency chart of tool usage. Similar to seeing which buttons are pressed most in an app.",
        examples=[
            "If one tool dominates too much, workflow may be unbalanced.",
            "A new tool appearing means adoption has started.",
        ],
        impact="Helps prioritize optimization and documentation for frequently used tools.",
    )
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
    _render_help_title(
        title="Auto utility quality",
        tooltip="Distribution of automatic utility bands.",
        section_key="auto-utility-quality",
        what_it_is="This chart summarizes machine-estimated utility bands from truth retrieval events.",
        examples=[
            "More 'High' means retrieval quality is likely improving.",
            "Growing 'Low' means you should inspect recent routes.",
        ],
        impact="Directly shows whether automatic retrieval quality is moving in the right direction.",
    )
    if not df_auto_utility.empty:
        df_band = (
            df_auto_utility["band"]
            .value_counts()
            .reindex(["high", "medium", "low"], fill_value=0)
            .rename_axis("band")
            .reset_index(name="count")
        )
        fig_band = px.pie(
            df_band,
            names="band",
            values="count",
            hole=0.58,
            color="band",
            color_discrete_map={
                "high": "#16a34a",
                "medium": "#f59e0b",
                "low": "#ef4444",
            },
            title="Auto utility distribution",
        )
        fig_band.update_layout(height=420)
        st.plotly_chart(fig_band, use_container_width=True)
    else:
        st.info("No auto-utility data yet. Run smart search to produce truth events.")

st.markdown("---")
_render_help_title(
    title="Anti-stickiness and alternative routes",
    tooltip="Tracks diversity of retrieval paths and repetition risk.",
    section_key="anti-stickiness",
    what_it_is="This section checks whether memory explores enough sources instead of getting stuck in one familiar path.",
    examples=[
        "High stickiness + low alternatives = tunnel vision.",
        "Lower stickiness + higher alt-route ratio = healthier exploration.",
    ],
    impact="Improves robustness of answers and reduces repetitive context selection.",
)

total_events = len(events)
smart_events = sum(1 for e in events if str(e.get("event_kind", "smart_search") or "smart_search") == "smart_search")
touch_events = sum(1 for e in events if str(e.get("event_kind", "")) == "tool_touch")

if not df_stick.empty:
    avg_sources = float(df_stick["unique_sources"].mean())
    avg_wings = float(df_stick["unique_wings"].mean())
    explore_rate = float(df_stick["explore_injected"].mean() * 100.0)
    alt_rate = float(df_stick["alt_route_ratio"].mean() * 100.0)
    stick_recent = float(df_stick["stickiness_score"].tail(40).mean())

    a1, a2, a3, a4, a5 = st.columns(5)
    with a1:
        _render_inline_help(
            label="Route events",
            tooltip="How many route events were logged (smart + touch).",
            section_key="metric_route_events",
            what_it_is="Total number of routing events used by charts and simulator.",
            examples=[
                "smart events = real retrieval routes.",
                "touch events = heartbeat/update pings without vector routes.",
            ],
            impact="Defines how much fresh behavior data the dashboard can analyze.",
            watch_for="If total grows but smart is low, visual updates may occur with little true retrieval signal.",
            meaning="Это «пульс» маршрутизации. Мало smart-событий = мало реальных данных о поиске в памяти.",
            checklist=[
                "Сравнивайте smart и touch: smart должен стабильно расти при реальной работе.",
                "Если touch много, а smart мало — проверьте, реально ли запускается smart-search.",
                "Перед выводами по графикам убедитесь, что накопилось достаточно smart-событий.",
            ],
        )
        a1.metric(" ", total_events, f"smart {smart_events} / touch {touch_events}")
    with a2:
        _render_inline_help(
            label="Avg unique sources",
            tooltip="Average number of distinct sources used per route.",
            section_key="metric_avg_unique_sources",
            what_it_is="How many different files/sources are usually involved in one retrieval event.",
            examples=[
                "Higher value often means broader context coverage.",
                "Very low value can indicate over-concentration.",
            ],
            impact="Affects robustness of final answers and resistance to narrow context bias.",
            watch_for="Sudden drops often correlate with rising stickiness.",
        )
        a2.metric(" ", f"{avg_sources:.2f}")
    with a3:
        _render_inline_help(
            label="Explore injection",
            tooltip="How often diversity boost injected less-obvious candidates.",
            section_key="metric_explore_injection",
            what_it_is="Percent of events where anti-stickiness deliberately injected exploratory candidates.",
            examples=[
                "0% means no forced exploration.",
                "Moderate values can help avoid repetitive paths.",
            ],
            impact="Improves chance to discover relevant context outside dominant sources.",
            watch_for="If always high, relevance may become noisy; if always low during high stickiness, adaptation may be too weak.",
        )
        a3.metric(" ", f"{explore_rate:.1f}%")
    with a4:
        _render_inline_help(
            label="Alt route ratio",
            tooltip="Share of alternatives among selected routing outcomes.",
            section_key="metric_alt_route_ratio",
            what_it_is="How much retrieval branches away from the single strongest route.",
            examples=[
                "Higher ratio usually means better diversity.",
                "Very low ratio may mean repetitive routing.",
            ],
            impact="Strong predictor of whether answers include broader evidence.",
            watch_for="Track this alongside stickiness; falling ratio + rising stickiness is a red flag.",
        )
        a4.metric(" ", f"{alt_rate:.1f}%")
    with a5:
        _render_inline_help(
            label="Avg stickiness",
            tooltip="Recent average of repetition pressure score.",
            section_key="metric_avg_stickiness",
            what_it_is="Rolling average of stickiness over recent events.",
            examples=[
                "Lower average means healthier memory exploration.",
                "Higher average means repeated source reuse.",
            ],
            impact="Feeds your operational decision to tune or auto-optimize.",
            watch_for="Persistent rise over many events matters more than one isolated spike.",
        )
        a5.metric(" ", f"{stick_recent:.1f}/100")

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
        _render_help_title(
            title="Stickiness trend chart",
            tooltip="Lower line is better.",
            section_key="stickiness-trend-chart",
            what_it_is="Shows how repetitive retrieval behavior changes over time/events.",
            examples=[
                "Downward trend = memory is diversifying.",
                "Upward spikes = repeated source dominance in recent searches.",
            ],
            impact="Used to decide when to adjust source cap, MMR, or indexing balance.",
        )
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
        _render_help_title(
            title="Alternative-route ratio chart",
            tooltip="Higher line is better.",
            section_key="alt-route-chart",
            what_it_is="Shows how often retrieval uses alternatives instead of only one primary route.",
            examples=[
                "0.70 means alternatives are common and healthy.",
                "0.20 means the system rarely branches.",
            ],
            impact="Higher values usually improve resilience and reduce overfitting to one source.",
        )
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
    _render_help_title(
        title="Brain stickiness risk gauge",
        tooltip="Traffic-light style risk indicator.",
        section_key="stickiness-gauge",
        what_it_is="A quick red/yellow/green dial for repetition risk.",
        examples=[
            "Green (0-35): healthy route diversity.",
            "Red (65-100): strong repetition pressure.",
        ],
        impact="Fast executive signal for whether auto-optimization should kick in.",
    )
    st.plotly_chart(fig_gauge, use_container_width=True)

    _render_help_title(
        title="Adaptive anti-stickiness (auto-tuning state)",
        tooltip="Shows current controller settings and adaptation force.",
        section_key="adaptive-controller",
        what_it_is="This is the autopilot that nudges retrieval settings when repetition risk grows.",
        examples=[
            "Higher adapt strength = stronger correction against repeated paths.",
            "Lower lambda_mmr may increase diversity pressure.",
        ],
        impact="Directly changes retrieval behavior without manual tuning each time.",
    )
    if not df_adaptive.empty:
        last_ad = df_adaptive.iloc[-1]
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("Adaptive mode", str(last_ad["status"]))
        d2.metric("Adapt strength", f"{float(last_ad['adaptation_strength']) * 100:.0f}%")
        d3.metric("lambda_mmr in use", f"{float(last_ad['lambda_mmr_used']):.2f}")
        d4.metric("source_cap in use", int(last_ad["source_cap_used"]))
        d5.metric("Explore rate", f"1 / {int(last_ad['explore_every_used'])}")

        ad_trend_df = df_adaptive.tail(200).copy()
        if axis_mode == "By day (compressed)":
            ad_trend_df["day"] = ad_trend_df["timestamp_raw"].astype(str).str.slice(0, 10)
            ad_trend_df = (
                ad_trend_df.groupby("day", as_index=False)
                .agg(
                    recent_stickiness=("recent_stickiness", "mean"),
                    events=("event_step", "count"),
                )
                .sort_values("day")
            )
            ad_x_col = "day"
            ad_x_title = "Day"
            ad_hover = ["events"]
        elif axis_mode == "Gapless (event steps)":
            ad_x_col = "event_step"
            ad_x_title = "Event step"
            ad_hover = ["timestamp_raw", "status"]
        else:
            ad_x_col = "timestamp_raw"
            ad_x_title = "Timestamp"
            ad_hover = ["status"]

        ad_line = px.line(
            ad_trend_df,
            x=ad_x_col,
            y="recent_stickiness",
            title="Adaptive controller: recent stickiness baseline (lower is better)",
            markers=True,
            color="status" if "status" in ad_trend_df.columns else None,
            hover_data=ad_hover,
        )
        ad_line.update_layout(height=320, xaxis_title=ad_x_title, yaxis_title="Recent stickiness baseline")
        st.plotly_chart(ad_line, use_container_width=True)
    else:
        st.caption("Adaptive telemetry appears after new smart-search runs with updated script.")

    _render_help_title(
        title="Query -> selected routes (recent)",
        tooltip="Recent route table for audit and debugging.",
        section_key="query-routes-table",
        what_it_is="A trace table that shows which sources were chosen for recent queries.",
        examples=[
            "If same file repeats on unrelated queries, that may signal stickiness.",
            "Explore-injected=True shows diversity forcing was applied.",
        ],
        impact="Main audit surface for understanding why specific answers were built.",
    )
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
_render_help_title(
    title="Neural path simulator",
    tooltip="Interactive route visualization for latest query events.",
    section_key="neural-simulator",
    what_it_is="A live map of how a query moves through source selection. Think of it as air-traffic control for memory routes.",
    examples=[
        "Green routes = selected primary memory paths.",
        "Orange routes = alternative branches.",
    ],
    impact="Makes hidden retrieval decisions visible and easier to debug.",
)

_watch_neural_truth_events(events_path)
truth_events_for_sim = [ev for ev in events if _is_truth_vector_event(ev)]
if truth_events_for_sim:
    sim_payload = _build_neural_sim_payload(truth_events_for_sim)
    latest_truth = truth_events_for_sim[-1]
    latest_truth_ts = str(latest_truth.get("timestamp", ""))
    latest_truth_kind = str(latest_truth.get("event_kind", "smart_search") or "smart_search")
    latest_truth_query = str(latest_truth.get("query", ""))
    latest_truth_signature = f"{latest_truth_ts}::{latest_truth_kind}::{latest_truth_query}"
    sim_key = f"neural-sim::latest-only::{latest_truth_signature}"
    _render_neural_simulator(sim_payload, component_key=sim_key)
    st.caption(
        "Neural window auto-updates on new smart-search events only. "
        f"Latest event: `{latest_truth_kind}` at `{latest_truth_ts}`."
    )
else:
    st.info("Run smart search to populate the neural simulator.")
st.caption(
    "Interactive matrix view: primary green routes run to target, "
    "alternative orange routes branch and terminate."
)

st.markdown("---")
_render_help_title(
    title="Memory graph signals",
    tooltip="Wing-level activity and quality patterns.",
    section_key="memory-graph-signals",
    what_it_is="Aggregated view by wing to compare where traffic and quality are concentrated.",
    examples=[
        "Wing with high queries + high stickiness may need re-indexing.",
        "Wing with better alt ratio often has richer source coverage.",
    ],
    impact="Helps prioritize which project areas need memory quality work first.",
)

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
_render_help_title(
    title="Help score health",
    tooltip="Distribution and behavior of learned usefulness scores.",
    section_key="help-score-health",
    what_it_is="Shows how feedback-based scores are spread and how they relate to usage.",
    examples=[
        "Many low-score heavily used items can hurt answer quality.",
        "Balanced spread suggests stable learning.",
    ],
    impact="Controls long-term ranking quality through feedback reinforcement.",
)

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
    st.info("No help-score data yet. Run smart search to generate ranking signals.")

st.markdown("---")
_render_help_title(
    title="Noise control and optimization",
    tooltip="Monitors telemetry noise and runs safe maintenance.",
    section_key="noise-optimization",
    what_it_is="This section is housekeeping for analytics logs: detect noise, trim safely, keep board responsive.",
    examples=[
        "Auto optimize when score exceeds threshold.",
        "Manual optimize for immediate cleanup after heavy testing.",
    ],
    impact="Keeps analytics useful over time without touching core memory index data.",
)

events_file = Path(events_path)
analytics_dir = events_file.parent if events_file.parent else Path(".mempalace-analytics")
noise = _compute_noise_metrics(df_stick, events_file)
maintenance_state_path = analytics_dir / "maintenance-state.json"
maintenance_state = _load_json_file(maintenance_state_path)
last_run_ts = _parse_ts(str(maintenance_state.get("timestamp", "")))

n1, n2, n3, n4, n5 = st.columns(5)
with n1:
    _render_inline_help(
        label="Noise score",
        tooltip="Composite score of telemetry noise and repetition risk.",
        section_key="metric_noise_score",
        what_it_is="A combined score built from stickiness, alternative ratio, and source concentration.",
        examples=[
            "High score means logs are noisy and route behavior may be collapsing.",
            "Lower score means cleaner telemetry signal.",
        ],
        impact="Used as trigger for auto-maintenance/optimization decisions.",
        watch_for="When consistently above threshold, let auto-optimize run or trigger manual optimization.",
    )
    n1.metric(" ", f"{noise['score']:.1f}/100")
with n2:
    _render_inline_help(
        label="Noise level",
        tooltip="Simple state derived from noise score: low/moderate/high.",
        section_key="metric_noise_level",
        what_it_is="Human-friendly category for the current noise score.",
        examples=[
            "Low = healthy signal.",
            "High = quality drift risk and potential analytics clutter.",
        ],
        impact="Quick status for non-technical users to know whether maintenance is needed now.",
        watch_for="If it stays 'high', inspect route diversity and run optimization.",
        meaning="Это светофор состояния: low = спокойно, moderate = наблюдаем, high = пора действовать.",
        checklist=[
            "Если high держится долго — запустите Optimize database now или авто-оптимизацию.",
            "Проверьте рядом Recent stickiness и Recent alt-ratio, чтобы понять причину.",
            "После оптимизации убедитесь, что уровень снизился в следующих событиях.",
        ],
    )
    n2.metric(" ", noise["level"])
with n3:
    _render_inline_help(
        label="Recent stickiness",
        tooltip="Recent average repetition component used by noise model.",
        section_key="metric_recent_stickiness_noise",
        what_it_is="The stickiness component that feeds the overall noise score.",
        examples=[
            "Rising value often means more repeated source picks.",
            "Falling value indicates recovery after tuning.",
        ],
        impact="Major contributor to whether the controller sees routing as healthy or stuck.",
        watch_for="Watch trend direction over multiple events, not one point.",
    )
    n3.metric(" ", f"{noise['stickiness_recent']:.1f}")
with n4:
    _render_inline_help(
        label="Recent alt-ratio",
        tooltip="Recent alternative-route share used by noise model.",
        section_key="metric_recent_alt_ratio_noise",
        what_it_is="The alternative-route component used in the noise calculation.",
        examples=[
            "Higher percentage usually supports lower noise.",
            "Low percentage can indicate route collapse.",
        ],
        impact="Helps the model estimate whether retrieval remains diverse enough.",
        watch_for="If this drops while stickiness rises, prioritize controller/maintenance checks.",
    )
    n4.metric(" ", f"{noise['alt_ratio_recent'] * 100:.1f}%")
with n5:
    _render_inline_help(
        label="Events log size",
        tooltip="Current size of route telemetry file on disk.",
        section_key="metric_events_log_size",
        what_it_is="Physical size of the events log powering many dashboard charts.",
        examples=[
            "Growth is normal with active usage.",
            "Very large files may slow processing if never maintained.",
        ],
        impact="Affects dashboard responsiveness and maintenance urgency.",
        watch_for="If size grows fast with high noise, schedule or allow auto-optimization.",
    )
    n5.metric(" ", f"{noise['events_file_mb']:.2f} MB")

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
