#!/usr/bin/env python3
"""MemPalace usage and impact stats for this project."""

from __future__ import annotations

import argparse
from pathlib import Path

from mempalace_analytics import (
    DEFAULT_SEARCH_EVENTS,
    DEFAULT_TRANSCRIPTS,
    compute_auto_utility,
    collect_usage_stats,
    load_search_events,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="MemPalace usage and impact stats.")
    parser.add_argument("--transcripts", default=DEFAULT_TRANSCRIPTS)
    parser.add_argument("--events", default=str(DEFAULT_SEARCH_EVENTS))
    args = parser.parse_args()

    transcripts_root = Path(args.transcripts)
    events_path = Path(args.events)

    if not transcripts_root.exists():
        print(f"ERROR: transcripts path not found: {transcripts_root}")
        raise SystemExit(1)

    usage = collect_usage_stats(transcripts_root)
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

    events = load_search_events(events_path)
    if events:
        total = len(events)
        smart = sum(1 for e in events if str(e.get("event_kind", "smart_search")) == "smart_search")
        touch = sum(1 for e in events if str(e.get("event_kind", "")) == "tool_touch")
        truth_events = [
            e
            for e in events
            if str(e.get("event_kind", "smart_search")) == "smart_search"
            and isinstance(e.get("results", []), list)
            and len(e.get("results", [])) > 0
        ]
        truth_total = len(truth_events)
        avg_sources = (sum(e.get("unique_sources", 0) for e in truth_events) / truth_total) if truth_total else 0.0
        avg_wings = (sum(e.get("unique_wings", 0) for e in truth_events) / truth_total) if truth_total else 0.0
        explore = (sum(1 for e in truth_events if e.get("explore_injected")) / truth_total * 100.0) if truth_total else 0.0
        print("")
        print("Anti-stickiness:")
        print(f"  Route events logged: {total} (smart: {smart}, touch: {touch})")
        print(f"  Truth vector events: {truth_total}")
        print(f"  Avg unique sources per search: {avg_sources:.2f}")
        print(f"  Avg unique wings per search: {avg_wings:.2f}")
        print(f"  Explore injection rate: {explore:.1f}%")
        auto_rows = []
        for event in truth_events:
            auto = event.get("auto_utility")
            if not isinstance(auto, dict):
                auto = compute_auto_utility(event)
            auto_rows.append(auto)
        if auto_rows:
            avg_score = sum(float(a.get("score", 0.0)) for a in auto_rows) / len(auto_rows)
            high = sum(1 for a in auto_rows if str(a.get("band", "")) == "high")
            medium = sum(1 for a in auto_rows if str(a.get("band", "")) == "medium")
            low = sum(1 for a in auto_rows if str(a.get("band", "")) == "low")
            print("")
            print("Auto utility:")
            print(f"  Avg score: {avg_score:.1f}/100")
            print(f"  Band distribution: high={high}, medium={medium}, low={low}")


if __name__ == "__main__":
    main()
