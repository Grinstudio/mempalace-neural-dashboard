#!/usr/bin/env python3
"""
Log impact feedback and update help scores for smart reranking.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from mempalace_analytics import (
    DEFAULT_FEEDBACK,
    DEFAULT_HELP_SCORES,
    DEFAULT_LAST_SEARCH,
    ensure_analytics_dir,
    load_help_scores,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def write_json(path: Path, obj: Dict) -> None:
    ensure_analytics_dir(path.parent)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def append_jsonl(path: Path, obj: Dict) -> None:
    ensure_analytics_dir(path.parent)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_last_search(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def apply_feedback_to_scores(help_scores: Dict[str, Dict], results: List[Dict], helped: str) -> None:
    if helped == "unknown":
        return

    for idx, item in enumerate(results):
        key = str(item.get("key", "")).strip()
        if not key:
            continue
        entry = help_scores.get(key, {})
        current = float(entry.get("score", 0.0))

        # Top items receive stronger signal.
        if idx < 3:
            delta = 0.06 if helped == "yes" else -0.05
        else:
            delta = 0.03 if helped == "yes" else -0.02

        updated = clamp(current + delta, -1.0, 1.0)
        entry["score"] = round(updated, 4)
        entry["used_count"] = int(entry.get("used_count", 0)) + 1
        if helped == "yes":
            entry["positive_count"] = int(entry.get("positive_count", 0)) + 1
            entry["last_positive"] = utc_now_iso()
        elif helped == "no":
            entry["negative_count"] = int(entry.get("negative_count", 0)) + 1
            entry["last_negative"] = utc_now_iso()
        entry["updated_at"] = utc_now_iso()
        help_scores[key] = entry


def main() -> None:
    parser = argparse.ArgumentParser(description="Log feedback and update MemPalace help scores.")
    parser.add_argument("--helped", choices=["yes", "no", "unknown"], default="unknown")
    parser.add_argument("--minutes-saved", type=int, default=0)
    parser.add_argument("--note", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--feedback-path", default=str(DEFAULT_FEEDBACK))
    parser.add_argument("--help-scores-path", default=str(DEFAULT_HELP_SCORES))
    parser.add_argument("--last-search-path", default=str(DEFAULT_LAST_SEARCH))
    parser.add_argument("--skip-score-update", action="store_true")
    args = parser.parse_args()

    feedback_path = Path(args.feedback_path)
    scores_path = Path(args.help_scores_path)
    last_search_path = Path(args.last_search_path)

    feedback_obj = {
        "timestamp": utc_now_iso(),
        "session_id": args.session_id,
        "helped": True if args.helped == "yes" else False if args.helped == "no" else None,
        "minutes_saved": int(args.minutes_saved),
        "note": args.note,
    }
    append_jsonl(feedback_path, feedback_obj)

    if not args.skip_score_update:
        help_scores = load_help_scores(scores_path)
        last_search = load_last_search(last_search_path)
        results = last_search.get("results", []) if isinstance(last_search, dict) else []
        if isinstance(results, list) and results:
            apply_feedback_to_scores(help_scores, results, args.helped)
            write_json(scores_path, help_scores)

    print(f"Feedback logged: {feedback_path}")
    if args.skip_score_update:
        print("Help score update: skipped")
    else:
        print(f"Help score store: {scores_path}")


if __name__ == "__main__":
    main()
