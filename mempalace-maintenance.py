#!/usr/bin/env python3
"""
Automatic maintenance for local MemPalace analytics data.

Goals:
- keep telemetry responsive as data grows,
- archive old records instead of deleting blindly,
- trim only when thresholds are exceeded,
- persist maintenance state for tracking.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from mempalace_analytics import DEFAULT_ANALYTICS_DIR, ensure_analytics_dir


DEFAULT_CONFIG = {
    "max_search_events_lines": 20000,
    "search_events_archive_days": 60,
    "max_feedback_lines": 8000,
    "feedback_archive_days": 120,
    "max_analytics_size_mb": 120,
    "max_help_scores_entries": 12000,
    "help_scores_prune_days": 240,
    "help_scores_prune_used_count_lte": 0,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def save_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def read_jsonl_lines(path: Path) -> List[Tuple[str, Dict]]:
    rows: List[Tuple[str, Dict]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append((line, obj))
    return rows


def write_jsonl(path: Path, objects: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for obj in objects:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def dedupe_by_line(rows: List[Tuple[str, Dict]]) -> List[Dict]:
    seen = set()
    out: List[Dict] = []
    for raw_line, obj in rows:
        if raw_line in seen:
            continue
        seen.add(raw_line)
        out.append(obj)
    return out


def split_by_age(rows: List[Dict], ts_key: str, cutoff: datetime) -> Tuple[List[Dict], List[Dict]]:
    keep: List[Dict] = []
    archive: List[Dict] = []
    for row in rows:
        ts = parse_ts(str(row.get(ts_key, "")))
        if ts and ts < cutoff:
            archive.append(row)
        else:
            keep.append(row)
    return keep, archive


def append_archive(archive_path: Path, rows: List[Dict], apply_changes: bool) -> None:
    if not rows or not apply_changes:
        return
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def total_size_mb(folder: Path) -> float:
    total = 0
    if not folder.exists():
        return 0.0
    for path in folder.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total / (1024 * 1024)


def maintain_jsonl(
    *,
    file_path: Path,
    archive_path: Path,
    timestamp_key: str,
    archive_days: int,
    max_lines: int,
    apply_changes: bool,
) -> Dict:
    before_rows = read_jsonl_lines(file_path)
    before_count = len(before_rows)

    deduped = dedupe_by_line(before_rows)
    deduped_count = len(deduped)

    cutoff = utc_now() - timedelta(days=max(1, archive_days))
    kept_rows, archived_rows = split_by_age(deduped, timestamp_key, cutoff)

    trimmed_rows = kept_rows
    trimmed = 0
    if len(trimmed_rows) > max_lines:
        trimmed = len(trimmed_rows) - max_lines
        trimmed_rows = trimmed_rows[-max_lines:]

    if apply_changes:
        append_archive(archive_path, archived_rows, apply_changes=True)
        write_jsonl(file_path, trimmed_rows)

    return {
        "file": str(file_path),
        "before_count": before_count,
        "after_count": len(trimmed_rows),
        "deduped_removed": max(0, before_count - deduped_count),
        "archived_count": len(archived_rows),
        "trimmed_count": trimmed,
        "changed": (before_count != len(trimmed_rows)) or bool(archived_rows),
    }


def maintain_help_scores(
    *,
    help_scores_path: Path,
    max_entries: int,
    prune_days: int,
    prune_used_count_lte: int,
    apply_changes: bool,
) -> Dict:
    data = load_json(help_scores_path)
    before_count = len(data)
    if not data:
        return {
            "file": str(help_scores_path),
            "before_count": 0,
            "after_count": 0,
            "pruned_count": 0,
            "changed": False,
        }

    cutoff = utc_now() - timedelta(days=max(1, prune_days))
    candidates: List[Tuple[str, datetime]] = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        used_count = int(value.get("used_count", 0))
        updated_at = parse_ts(str(value.get("updated_at", ""))) or parse_ts(str(value.get("last_positive", "")))
        if used_count <= prune_used_count_lte and updated_at and updated_at < cutoff:
            candidates.append((key, updated_at))

    overflow = max(0, len(data) - max_entries)
    candidates.sort(key=lambda x: x[1])
    prune_keys = [k for k, _ in candidates[:overflow]] if overflow > 0 else []

    if apply_changes and prune_keys:
        for key in prune_keys:
            data.pop(key, None)
        save_json(help_scores_path, data)

    return {
        "file": str(help_scores_path),
        "before_count": before_count,
        "after_count": len(data) - 0 if apply_changes else before_count - len(prune_keys),
        "pruned_count": len(prune_keys),
        "changed": bool(prune_keys),
    }


def decide_auto_apply(config: Dict, analytics_dir: Path) -> Tuple[bool, Dict]:
    search_events_path = analytics_dir / "search_events.jsonl"
    feedback_path = analytics_dir / "feedback.jsonl"
    help_scores_path = analytics_dir / "help_scores.json"

    checks = {
        "search_events_over_limit": len(read_jsonl_lines(search_events_path)) > int(config["max_search_events_lines"]),
        "feedback_over_limit": len(read_jsonl_lines(feedback_path)) > int(config["max_feedback_lines"]),
        "analytics_size_over_limit": total_size_mb(analytics_dir) > float(config["max_analytics_size_mb"]),
        "help_scores_over_limit": len(load_json(help_scores_path)) > int(config["max_help_scores_entries"]),
    }
    should_apply = any(checks.values())
    return should_apply, checks


def load_or_create_config(config_path: Path) -> Dict:
    if config_path.exists():
        cfg = load_json(config_path)
        merged = dict(DEFAULT_CONFIG)
        merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
        return merged
    save_json(config_path, DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto maintenance for MemPalace analytics data.")
    parser.add_argument("--analytics-dir", default=str(DEFAULT_ANALYTICS_DIR))
    parser.add_argument("--config", default=None, help="Path to maintenance config json.")
    parser.add_argument(
        "--mode",
        choices=["monitor", "auto", "apply"],
        default="auto",
        help="monitor: report only, auto: apply on threshold exceed, apply: always apply",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args()

    analytics_dir = Path(args.analytics_dir)
    ensure_analytics_dir(analytics_dir)

    config_path = Path(args.config) if args.config else (analytics_dir / "maintenance-config.json")
    config = load_or_create_config(config_path)

    auto_apply, checks = decide_auto_apply(config, analytics_dir)
    apply_changes = args.mode == "apply" or (args.mode == "auto" and auto_apply)

    archive_dir = analytics_dir / "archive"
    stamp = utc_now().strftime("%Y%m")
    search_archive_path = archive_dir / f"search_events-{stamp}.jsonl"
    feedback_archive_path = archive_dir / f"feedback-{stamp}.jsonl"

    search_result = maintain_jsonl(
        file_path=analytics_dir / "search_events.jsonl",
        archive_path=search_archive_path,
        timestamp_key="timestamp",
        archive_days=int(config["search_events_archive_days"]),
        max_lines=int(config["max_search_events_lines"]),
        apply_changes=apply_changes,
    )
    feedback_result = maintain_jsonl(
        file_path=analytics_dir / "feedback.jsonl",
        archive_path=feedback_archive_path,
        timestamp_key="timestamp",
        archive_days=int(config["feedback_archive_days"]),
        max_lines=int(config["max_feedback_lines"]),
        apply_changes=apply_changes,
    )
    help_result = maintain_help_scores(
        help_scores_path=analytics_dir / "help_scores.json",
        max_entries=int(config["max_help_scores_entries"]),
        prune_days=int(config["help_scores_prune_days"]),
        prune_used_count_lte=int(config["help_scores_prune_used_count_lte"]),
        apply_changes=apply_changes,
    )

    report = {
        "timestamp": utc_now().isoformat(),
        "mode": args.mode,
        "apply_changes": apply_changes,
        "checks": checks,
        "config_path": str(config_path),
        "config": config,
        "results": {
            "search_events": search_result,
            "feedback": feedback_result,
            "help_scores": help_result,
        },
        "analytics_size_mb": round(total_size_mb(analytics_dir), 2),
    }

    state_path = analytics_dir / "maintenance-state.json"
    if apply_changes or args.mode == "monitor":
        save_json(state_path, report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("MemPalace maintenance report")
        print(f"Mode: {args.mode}")
        print(f"Apply changes: {'yes' if apply_changes else 'no'}")
        print("")
        print("Threshold checks:")
        for key, value in checks.items():
            print(f"- {key}: {'triggered' if value else 'ok'}")
        print("")
        for name, block in report["results"].items():
            print(
                f"{name}: before={block.get('before_count', 0)} "
                f"after={block.get('after_count', 0)} "
                f"archived={block.get('archived_count', 0)} "
                f"trimmed={block.get('trimmed_count', 0)} "
                f"pruned={block.get('pruned_count', 0)}"
            )
        print("")
        print(f"Analytics size: {report['analytics_size_mb']} MB")
        print(f"State written to: {state_path}")


if __name__ == "__main__":
    main()
