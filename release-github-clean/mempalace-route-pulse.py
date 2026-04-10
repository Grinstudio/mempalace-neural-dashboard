#!/usr/bin/env python3
"""Write heartbeat events for non-retrieval MemPalace tool touches."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from mempalace_analytics import DEFAULT_LAST_SEARCH, DEFAULT_SEARCH_EVENTS, ensure_analytics_dir


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def write_jsonl(path: Path, obj: Dict) -> None:
    ensure_analytics_dir(path.parent)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Write route pulse event for dashboard updates.")
    parser.add_argument("--tool", required=True, help="Tool name that triggered this pulse.")
    parser.add_argument("--query", default="", help="Optional query/context for this touch.")
    parser.add_argument("--wing", default=None)
    parser.add_argument("--room", default=None)
    parser.add_argument("--events-path", default=str(DEFAULT_SEARCH_EVENTS))
    parser.add_argument("--last-search-path", default=str(DEFAULT_LAST_SEARCH))
    parser.add_argument(
        "--replay-last-route",
        action="store_true",
        help="Replay last route into touch event (legacy behavior, off by default).",
    )
    args = parser.parse_args()
    query = args.query.strip() or f"tool:{args.tool}"
    wing = str(args.wing or "tool_touch")
    room = str(args.room or "tooling")

    replay_rows = []
    replay_candidates = []
    replay = load_json(Path(args.last_search_path)) if args.replay_last_route else {}
    if isinstance(replay.get("results"), list):
        replay_rows = list(replay.get("results") or [])[:16]
    if isinstance(replay.get("candidate_preview"), list):
        replay_candidates = list(replay.get("candidate_preview") or [])[:24]
    if not replay_candidates and replay_rows:
        replay_candidates = [dict(r, selected=True) for r in replay_rows]

    unique_sources = len({str(r.get("source_file", "unknown")) for r in replay_rows}) if replay_rows else 0
    unique_wings = len({str(r.get("wing", "unknown")) for r in replay_rows}) if replay_rows else 0

    event = {
        "timestamp": utc_now_iso(),
        "event_kind": "tool_touch",
        "telemetry_channel": "mcp_tool_touch_v1",
        "tool_name": args.tool,
        "query": query,
        "wing": wing,
        "room": room,
        # Touch events are not vector retrieval events.
        # Keep rows empty by default so dashboard truth-path stays honest.
        # Optional replay is available only with --replay-last-route.
        "candidate_k": 0,
        "top_k": 0,
        "adaptive": {},
        "explore_injected": False,
        "unique_sources": unique_sources,
        "unique_wings": unique_wings,
        "candidate_preview": replay_candidates,
        "results": replay_rows,
        "vector_truth": False,
        "route_replay": bool(replay_rows),
        "note": (
            "touch event with replayed last route (legacy mode)"
            if replay_rows
            else "touch event without route data (truth mode)"
        ),
    }

    write_jsonl(Path(args.events_path), event)
    print(f"Route pulse written: tool={args.tool}, query={query}")


if __name__ == "__main__":
    main()
