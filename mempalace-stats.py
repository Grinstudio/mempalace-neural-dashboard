#!/usr/bin/env python3
"""MemPalace usage and impact stats for this project."""

from __future__ import annotations

import argparse
from pathlib import Path

from mempalace_analytics import (
    DEFAULT_FEEDBACK,
    DEFAULT_SEARCH_EVENTS,
    DEFAULT_TRANSCRIPTS,
    collect_feedback_stats,
    collect_usage_stats,
    load_search_events,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="MemPalace usage and impact stats.")
    parser.add_argument("--transcripts", default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--feedback", default=str(DEFAULT_FEEDBACK))
    parser.add_argument("--events", default=str(DEFAULT_SEARCH_EVENTS))
    args = parser.parse_args()

    transcripts_root = Path(args.transcripts)
    feedback_path = Path(args.feedback)
    events_path = Path(args.events)

    if not transcripts_root.exists():
        print(f"ERROR: transcripts path not found: {transcripts_root}")
        raise SystemExit(1)

    usage = collect_usage_stats(transcripts_root)
    feedback = collect_feedback_stats(feedback_path)

    sessions_total = usage["sessions_total"]
    sessions_with_mem = usage["sessions_with_mempalace"]
    mem_share = (sessions_with_mem / sessions_total * 100.0) if sessions_total else 0.0

    print("MemPalace Stats")
    print("==============")
    print(f"Transcripts scanned: {sessions_total}")
    print(
        f"Sessions with MemPalace usage: {sessions_with_mem} "
        f"({mem_share:.1f}%)"
    )
    print(f"Total MemPalace tool calls: {usage['mempalace_tool_calls']}")
    print(f"Total tool calls overall: {usage['all_tool_calls']}")
    print("")
    print("Top MemPalace tools:")
    for name, count in usage["mempalace_tools_counter"].most_common(10):
        print(f"  - {name}: {count}")

    print("")
    print("Impact feedback:")
    print(f"  Entries: {feedback['entries']}")
    print(f"  Helped (yes): {feedback['helped_yes']}")
    print(f"  Helped (no): {feedback['helped_no']}")
    print(f"  Helped (unknown): {feedback['helped_unknown']}")
    print(f"  Minutes saved total: {feedback['minutes_saved_total']}")
    print(f"  Minutes saved average: {feedback['minutes_saved_avg']:.1f}")

    events = load_search_events(events_path)
    if events:
        total = len(events)
        avg_sources = sum(e.get("unique_sources", 0) for e in events) / total
        avg_wings = sum(e.get("unique_wings", 0) for e in events) / total
        explore = sum(1 for e in events if e.get("explore_injected")) / total * 100.0
        print("")
        print("Anti-stickiness:")
        print(f"  Smart searches logged: {total}")
        print(f"  Avg unique sources per search: {avg_sources:.2f}")
        print(f"  Avg unique wings per search: {avg_wings:.2f}")
        print(f"  Explore injection rate: {explore:.1f}%")


if __name__ == "__main__":
    main()
