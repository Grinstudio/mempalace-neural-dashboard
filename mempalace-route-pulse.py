#!/usr/bin/env python3
"""Write truthful heartbeat events for non-retrieval MemPalace tool touches."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from mempalace_analytics import DEFAULT_SEARCH_EVENTS, ensure_analytics_dir


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
    args = parser.parse_args()
    query = args.query.strip() or f"tool:{args.tool}"
    wing = str(args.wing or "tool_touch")
    room = str(args.room or "tooling")

    event = {
        "timestamp": utc_now_iso(),
        "event_kind": "tool_touch",
        "tool_name": args.tool,
        "query": query,
        "wing": wing,
        "room": room,
        # Source-of-truth rule:
        # non-retrieval tool touches do not invent vector routes.
        "candidate_k": 0,
        "top_k": 0,
        "adaptive": {},
        "explore_injected": False,
        "unique_sources": 0,
        "unique_wings": 0,
        "candidate_preview": [],
        "results": [],
        "vector_truth": False,
        "note": "heartbeat-only event; no retrieval vectors in this tool call",
    }

    write_jsonl(Path(args.events_path), event)
    print(f"Route pulse written: tool={args.tool}, query={query}")


if __name__ == "__main__":
    main()
