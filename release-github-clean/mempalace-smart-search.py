#!/usr/bin/env python3
"""
Smart MemPalace search with anti-stickiness protections:
- blended relevance (semantic + help score + recency),
- diversity via light MMR,
- optional exploration slot,
- telemetry logging for dashboard stats.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from mempalace.searcher import search_memories

from mempalace_analytics import (
    compute_auto_utility,
    DEFAULT_HELP_SCORES,
    DEFAULT_LAST_SEARCH,
    DEFAULT_SEARCH_EVENTS,
    ensure_analytics_dir,
    load_help_scores,
)


TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{3,}")
MAX_SOURCE_MATCH_FILES = 24
MAX_SOURCE_SCAN_BYTES = 1_500_000
MAX_EVENTS_TAIL_LINES = 220
MAX_EVENTS_TAIL_BYTES = 262_144


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def tokenize(text: str) -> set:
    return set(TOKEN_RE.findall((text or "").lower()))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a.intersection(b))
    union = len(a.union(b))
    return inter / union if union else 0.0


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def result_key(item: Dict) -> str:
    wing = str(item.get("wing", "unknown"))
    room = str(item.get("room", "unknown"))
    source = str(item.get("source_file", "unknown"))
    return f"{wing}|{room}|{source}"


def short_text(value: str, max_len: int = 320) -> str:
    text = (value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def _line_number_from_pos(text: str, pos: int) -> int:
    if pos <= 0:
        return 1
    return text.count("\n", 0, pos) + 1


def _load_recent_events_tail(path: Path, max_lines: int = MAX_EVENTS_TAIL_LINES, max_bytes: int = MAX_EVENTS_TAIL_BYTES) -> List[Dict]:
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


def infer_source_location(item: Dict, workspace_root: Path, file_cache: Dict[str, List[Path]]) -> Dict:
    source_file = str(item.get("source_file", "") or "")
    if not source_file:
        return {}

    file_name = Path(source_file).name
    if not file_name:
        return {}
    key = file_name.lower()
    if key not in file_cache:
        try:
            matches: List[Path] = []
            for idx, p in enumerate(workspace_root.rglob(file_name)):
                matches.append(p)
                if idx + 1 >= MAX_SOURCE_MATCH_FILES:
                    break
            file_cache[key] = matches
        except Exception:
            file_cache[key] = []

    snippet = str(item.get("text", "") or "")
    snippet_line = ""
    for ln in snippet.splitlines():
        clean = ln.strip()
        if clean:
            snippet_line = clean
            break

    for path in file_cache.get(key, []):
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_SCAN_BYTES:
                continue
        except OSError:
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        pos = -1
        if snippet_line:
            pos = body.find(snippet_line)
        if pos < 0 and snippet:
            pos = body.find(snippet[: min(len(snippet), 90)])
        if pos < 0:
            continue
        start_line = _line_number_from_pos(body, pos)
        line_span = max(0, snippet.count("\n"))
        return {
            "source_path": str(path),
            "line_start": int(start_line),
            "line_end": int(start_line + line_span),
        }
    return {}


def recency_bonus(entry: Dict) -> float:
    last_positive = parse_ts(str(entry.get("last_positive", "")))
    if not last_positive:
        return 0.5
    days = (datetime.now(timezone.utc) - last_positive).total_seconds() / 86400.0
    return float(math.exp(-days / 45.0))


def blended_score(item: Dict, help_scores: Dict[str, Dict], sem_w: float, help_w: float, rec_w: float) -> float:
    # MemPalace similarity can be negative; map [-1,1] -> [0,1].
    sem_raw = float(item.get("similarity", 0.0))
    sem = clamp((sem_raw + 1.0) / 2.0, 0.0, 1.0)
    key = result_key(item)
    entry = help_scores.get(key, {})
    raw_help = clamp(float(entry.get("score", 0.0)), -1.0, 1.0)
    help_norm = (raw_help + 1.0) / 2.0
    rec = recency_bonus(entry)
    return sem_w * sem + help_w * help_norm + rec_w * rec


def mmr_select(
    candidates: List[Dict],
    top_k: int,
    lambda_mmr: float,
) -> List[Dict]:
    if not candidates:
        return []

    selected: List[Dict] = []
    token_cache = [tokenize(c.get("text", "")) for c in candidates]

    while candidates and len(selected) < top_k:
        best_idx = None
        best_score = -1e9

        for idx, cand in enumerate(candidates):
            relevance = float(cand.get("_blended", 0.0))
            if not selected:
                mmr_val = relevance
            else:
                max_sim = 0.0
                cand_tokens = token_cache[idx]
                for sel in selected:
                    sim = jaccard(cand_tokens, tokenize(sel.get("text", "")))
                    if sim > max_sim:
                        max_sim = sim
                mmr_val = lambda_mmr * relevance - (1.0 - lambda_mmr) * max_sim
            if mmr_val > best_score:
                best_score = mmr_val
                best_idx = idx

        if best_idx is None:
            break

        selected.append(candidates.pop(best_idx))
        token_cache.pop(best_idx)

    return selected


def enforce_source_cap(items: List[Dict], cap: int) -> List[Dict]:
    if cap <= 0:
        return items
    counts: Dict[str, int] = {}
    output: List[Dict] = []
    overflow: List[Dict] = []
    for item in items:
        src = str(item.get("source_file", "unknown"))
        if counts.get(src, 0) < cap:
            output.append(item)
            counts[src] = counts.get(src, 0) + 1
        else:
            overflow.append(item)
    output.extend(overflow)
    return output


def event_stickiness(event: Dict, prev_sources: set | None) -> Tuple[float, set]:
    results = event.get("results", [])
    if not isinstance(results, list) or not results:
        return 0.0, set()

    source_counter: Dict[str, int] = {}
    for r in results:
        src = str(r.get("source_file", "unknown"))
        source_counter[src] = source_counter.get(src, 0) + 1

    total = len(results)
    max_source_share = max(source_counter.values()) / total if total else 0.0
    current_sources = set(source_counter.keys())
    overlap_prev = (
        len(current_sources & prev_sources) / len(current_sources | prev_sources)
        if prev_sources and current_sources
        else 0.0
    )
    diversity_norm = len(current_sources) / total if total else 0.0
    stickiness = 100.0 * (
        0.55 * max_source_share
        + 0.30 * (1.0 - diversity_norm)
        + 0.15 * overlap_prev
    )
    return stickiness, current_sources


def compute_adaptive_settings(
    recent_events: List[Dict],
    base_lambda_mmr: float,
    base_source_cap: int,
    adaptive_window: int,
) -> Dict:
    if not recent_events:
        return {
            "enabled": False,
            "status": "bootstrap",
            "recent_stickiness": 0.0,
            "trend_delta": 0.0,
            "lambda_mmr_used": base_lambda_mmr,
            "source_cap_used": base_source_cap,
            "explore_every_used": 8,
            "adaptation_strength": 0.0,
        }

    window = recent_events[-max(3, adaptive_window) :]
    values: List[float] = []
    prev_sources: set = set()
    for event in window:
        v, prev_sources = event_stickiness(event, prev_sources)
        if v > 0:
            values.append(v)

    if not values:
        return {
            "enabled": False,
            "status": "bootstrap",
            "recent_stickiness": 0.0,
            "trend_delta": 0.0,
            "lambda_mmr_used": base_lambda_mmr,
            "source_cap_used": base_source_cap,
            "explore_every_used": 8,
            "adaptation_strength": 0.0,
        }

    recent_stickiness = sum(values) / len(values)
    midpoint = max(1, len(values) // 2)
    first_avg = sum(values[:midpoint]) / len(values[:midpoint])
    last_avg = sum(values[midpoint:]) / len(values[midpoint:])
    trend_delta = last_avg - first_avg

    lambda_used = base_lambda_mmr
    source_cap_used = base_source_cap
    explore_every_used = 8
    status = "stable"
    strength = 0.0

    if recent_stickiness >= 62.0 or trend_delta >= 8.0:
        lambda_used = clamp(base_lambda_mmr - 0.13, 0.55, 0.9)
        source_cap_used = max(2, base_source_cap - 1)
        explore_every_used = 4
        status = "aggressive"
        strength = 1.0
    elif recent_stickiness >= 48.0 or trend_delta >= 3.0:
        lambda_used = clamp(base_lambda_mmr - 0.07, 0.58, 0.92)
        source_cap_used = max(2, base_source_cap)
        explore_every_used = 6
        status = "active"
        strength = 0.55
    elif recent_stickiness <= 30.0 and trend_delta < -2.0:
        lambda_used = clamp(base_lambda_mmr + 0.02, 0.6, 0.95)
        source_cap_used = max(3, base_source_cap)
        explore_every_used = 10
        status = "relaxed"
        strength = 0.2

    return {
        "enabled": True,
        "status": status,
        "recent_stickiness": round(recent_stickiness, 2),
        "trend_delta": round(trend_delta, 2),
        "lambda_mmr_used": round(lambda_used, 3),
        "source_cap_used": int(source_cap_used),
        "explore_every_used": int(explore_every_used),
        "adaptation_strength": float(round(strength, 2)),
    }


def maybe_inject_explore(
    selected: List[Dict],
    original_candidates: List[Dict],
    top_k: int,
    query: str,
    explore_every: int,
) -> Tuple[List[Dict], bool]:
    if not selected:
        return selected, False

    # Deterministic low-rate exploration: every ~8th query hash bucket.
    bucket = max(2, int(explore_every))
    inject = (abs(hash(query)) % bucket) == 0
    if not inject:
        return selected, False

    used_keys = {result_key(i) for i in selected}
    median_help = sorted(i.get("_help_raw", 0.0) for i in original_candidates)[len(original_candidates) // 2]
    explore_pool = [
        i
        for i in original_candidates
        if result_key(i) not in used_keys and float(i.get("_help_raw", 0.0)) <= float(median_help)
    ]
    explore_pool.sort(key=lambda x: float(x.get("similarity", 0.0)), reverse=True)
    if not explore_pool:
        return selected, False

    explore_item = explore_pool[0]
    # Replace last slot to keep output size stable.
    if len(selected) >= top_k:
        selected = selected[: top_k - 1] + [explore_item]
    else:
        selected.append(explore_item)
    return selected, True


def apply_auto_utility_to_help_scores(
    help_scores: Dict[str, Dict],
    selected: List[Dict],
    auto_utility: Dict,
) -> None:
    """Update help-score store from automatic utility signal (no human feedback)."""
    if not selected:
        return

    score = float(auto_utility.get("score", 50.0))
    # Center around neutral 50; keep updates small and stable.
    base_delta = clamp((score - 50.0) / 100.0 * 0.10, -0.05, 0.05)

    for idx, item in enumerate(selected):
        key = result_key(item)
        if not key:
            continue
        entry = help_scores.get(key, {})
        current = clamp(float(entry.get("score", 0.0)), -1.0, 1.0)
        rank_weight = 1.0 if idx < 3 else 0.7
        delta = base_delta * rank_weight
        updated = clamp(current + delta, -1.0, 1.0)

        entry["score"] = round(updated, 4)
        entry["used_count"] = int(entry.get("used_count", 0)) + 1
        if score >= 60.0:
            entry["positive_count"] = int(entry.get("positive_count", 0)) + 1
            entry["last_positive"] = utc_now_iso()
        elif score < 45.0:
            entry["negative_count"] = int(entry.get("negative_count", 0)) + 1
            entry["last_negative"] = utc_now_iso()
        entry["updated_at"] = utc_now_iso()
        help_scores[key] = entry


def write_jsonl(path: Path, obj: Dict) -> None:
    ensure_analytics_dir(path.parent)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def write_json(path: Path, obj: Dict) -> None:
    ensure_analytics_dir(path.parent)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart anti-stickiness MemPalace search.")
    parser.add_argument("query")
    parser.add_argument("--palace-path", required=True)
    parser.add_argument("--wing", default=None)
    parser.add_argument("--room", default=None)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-k", type=int, default=40)
    parser.add_argument("--lambda-mmr", type=float, default=0.75)
    parser.add_argument("--source-cap", type=int, default=4)
    parser.add_argument("--adaptive-window", type=int, default=30)
    parser.add_argument("--no-adaptive", action="store_true", help="Disable auto-adaptive anti-stickiness tuning.")
    parser.add_argument("--events-path", default=str(DEFAULT_SEARCH_EVENTS))
    parser.add_argument("--help-scores-path", default=str(DEFAULT_HELP_SCORES))
    parser.add_argument("--last-search-path", default=str(DEFAULT_LAST_SEARCH))
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    raw = search_memories(
        query=args.query,
        palace_path=args.palace_path,
        wing=args.wing,
        room=args.room,
        n_results=max(args.candidate_k, args.top_k),
    )

    if "error" in raw:
        print(json.dumps(raw, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    results = list(raw.get("results", []))
    if not results:
        print("No results.")
        return

    workspace_root = Path(__file__).resolve().parent
    file_cache: Dict[str, List[Path]] = {}

    help_scores = load_help_scores(Path(args.help_scores_path))

    sem_w, help_w, rec_w = 0.70, 0.20, 0.10
    for item in results:
        key = result_key(item)
        hs = help_scores.get(key, {})
        item["_help_raw"] = clamp(float(hs.get("score", 0.0)), -1.0, 1.0)
        item["_blended"] = blended_score(item, help_scores, sem_w, help_w, rec_w)

    recent_events = _load_recent_events_tail(
        Path(args.events_path),
        max_lines=max(MAX_EVENTS_TAIL_LINES, int(args.adaptive_window) * 4),
        max_bytes=MAX_EVENTS_TAIL_BYTES,
    )
    adaptive = compute_adaptive_settings(
        recent_events=recent_events,
        base_lambda_mmr=float(args.lambda_mmr),
        base_source_cap=int(args.source_cap),
        adaptive_window=int(args.adaptive_window),
    )
    if args.no_adaptive:
        adaptive["enabled"] = False
        adaptive["status"] = "disabled"
        adaptive["lambda_mmr_used"] = float(args.lambda_mmr)
        adaptive["source_cap_used"] = int(args.source_cap)
        adaptive["explore_every_used"] = 8
        adaptive["adaptation_strength"] = 0.0

    lambda_mmr_used = float(adaptive["lambda_mmr_used"])
    source_cap_used = int(adaptive["source_cap_used"])
    explore_every_used = int(adaptive["explore_every_used"])

    # Pre-sort by blended relevance before MMR.
    candidates = sorted(results, key=lambda x: float(x["_blended"]), reverse=True)
    mmr_out = mmr_select(candidates.copy(), top_k=args.top_k, lambda_mmr=lambda_mmr_used)
    capped = enforce_source_cap(mmr_out, cap=source_cap_used)[: args.top_k]
    reranked, explore_injected = maybe_inject_explore(
        capped,
        results,
        args.top_k,
        args.query,
        explore_every=explore_every_used,
    )
    selected_keys = {result_key(i) for i in reranked}

    candidate_preview = []
    for item in candidates[: min(len(candidates), max(args.top_k * 2, 16))]:
        key = result_key(item)
        loc = infer_source_location(item, workspace_root=workspace_root, file_cache=file_cache)
        candidate_preview.append(
            {
                "key": key,
                "source_file": item.get("source_file"),
                "wing": item.get("wing"),
                "room": item.get("room"),
                "similarity": item.get("similarity"),
                "help_score": item.get("_help_raw", 0.0),
                "blended_score": item.get("_blended", 0.0),
                "text": short_text(str(item.get("text", "") or "")),
                "source_path": loc.get("source_path"),
                "line_start": loc.get("line_start"),
                "line_end": loc.get("line_end"),
                "selected": key in selected_keys,
            }
        )

    output = {
        "query": args.query,
        "wing": args.wing,
        "room": args.room,
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "adaptive": adaptive,
        "explore_injected": explore_injected,
        "results": [
            {
                "rank": idx + 1,
                "wing": item.get("wing"),
                "room": item.get("room"),
                "source_file": item.get("source_file"),
                "similarity": item.get("similarity"),
                "semantic_norm": round(clamp((float(item.get("similarity", 0.0)) + 1.0) / 2.0, 0.0, 1.0), 3),
                "help_score": round(float(item.get("_help_raw", 0.0)), 3),
                "blended_score": round(float(item.get("_blended", 0.0)), 4),
                "text": item.get("text"),
            }
            for idx, item in enumerate(reranked)
        ],
    }

    event_results = []
    for i in reranked:
        loc = infer_source_location(i, workspace_root=workspace_root, file_cache=file_cache)
        full_text = str(i.get("text", "") or "")
        event_results.append(
            {
                "key": result_key(i),
                "source_file": i.get("source_file"),
                "wing": i.get("wing"),
                "room": i.get("room"),
                "similarity": i.get("similarity"),
                "help_score": i.get("_help_raw", 0.0),
                "blended_score": i.get("_blended", 0.0),
                # Keep full snippet for node popup expand/collapse.
                "text": full_text,
                "text_preview": short_text(full_text),
                "source_path": loc.get("source_path"),
                "line_start": loc.get("line_start"),
                "line_end": loc.get("line_end"),
            }
        )

    event = {
        "timestamp": utc_now_iso(),
        "event_kind": "smart_search",
        "telemetry_channel": "mcp_auto_utility_v1",
        "query": args.query,
        "wing": args.wing,
        "room": args.room,
        "candidate_k": args.candidate_k,
        "top_k": args.top_k,
        "adaptive": adaptive,
        "explore_injected": explore_injected,
        "unique_sources": len({i.get("source_file") for i in reranked}),
        "unique_wings": len({i.get("wing") for i in reranked}),
        "candidate_preview": candidate_preview,
        "results": event_results,
        "vector_truth": True,
        "route_replay": False,
    }
    event["auto_utility"] = compute_auto_utility(event)
    apply_auto_utility_to_help_scores(help_scores, reranked, event["auto_utility"])

    write_jsonl(Path(args.events_path), event)
    write_json(Path(args.last_search_path), event)
    write_json(Path(args.help_scores_path), help_scores)

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f'Smart results for: "{args.query}"')
        if args.wing:
            print(f"Wing filter: {args.wing}")
        if args.room:
            print(f"Room filter: {args.room}")
        ad = output.get("adaptive", {})
        if ad:
            print(
                "Adaptive anti-stickiness: "
                f"{ad.get('status')} | "
                f"lambda_mmr={ad.get('lambda_mmr_used')} | "
                f"source_cap={ad.get('source_cap_used')} | "
                f"explore_every={ad.get('explore_every_used')}"
            )
        print(f"Explore injected: {'yes' if explore_injected else 'no'}")
        auto = event.get("auto_utility", {})
        if isinstance(auto, dict):
            print(
                "Auto utility: "
                f"score={auto.get('score', 'n/a')} "
                f"band={str(auto.get('band', 'n/a')).upper()} "
                f"channel={auto.get('channel', 'mcp_auto_utility_v1')}"
            )
        print("")
        for row in output["results"]:
            print(
                f"[{row['rank']}] {row['wing']} / {row['room']} | "
                f"{row['source_file']} | sem={row['similarity']} | "
                f"sem_norm={row['semantic_norm']} | "
                f"help={row['help_score']} | score={row['blended_score']}"
            )
            text = (row.get("text") or "").strip().splitlines()
            preview = text[0] if text else ""
            print(f"    {preview[:180]}")
            print("")


if __name__ == "__main__":
    main()
