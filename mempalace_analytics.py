#!/usr/bin/env python3
"""
Shared analytics helpers for MemPalace usage, feedback, and smart-search telemetry.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

# Default to Cursor projects root for portability.
# You can override this path from the dashboard sidebar.
DEFAULT_TRANSCRIPTS = str(Path.home() / ".cursor" / "projects")
DEFAULT_ANALYTICS_DIR = Path(r".mempalace-analytics")
DEFAULT_FEEDBACK = DEFAULT_ANALYTICS_DIR / "feedback.jsonl"
DEFAULT_SEARCH_EVENTS = DEFAULT_ANALYTICS_DIR / "search_events.jsonl"
DEFAULT_HELP_SCORES = DEFAULT_ANALYTICS_DIR / "help_scores.json"
DEFAULT_LAST_SEARCH = DEFAULT_ANALYTICS_DIR / "last_search.json"


def ensure_analytics_dir(analytics_dir: Path = DEFAULT_ANALYTICS_DIR) -> None:
    analytics_dir.mkdir(parents=True, exist_ok=True)


def iter_parent_transcripts(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.jsonl"):
        if "subagents" in {part.lower() for part in path.parts}:
            continue
        yield path


def extract_tool_uses(message_obj: Dict) -> List[str]:
    names: List[str] = []
    if message_obj.get("role") != "assistant":
        return names

    message = message_obj.get("message", {})
    content = message.get("content", [])
    if not isinstance(content, list):
        return names

    for item in content:
        if not (isinstance(item, dict) and item.get("type") == "tool_use"):
            continue

        name = str(item.get("name", "")).strip()
        if not name:
            continue

        if name.startswith("mempalace_"):
            names.append(name)
            continue

        if name == "CallMcpTool":
            tool_input = item.get("input", {})
            server = str(tool_input.get("server", "")).strip().lower()
            tool_name = str(tool_input.get("toolName", "")).strip()
            if server in {"user-mempalace", "mempalace"} and tool_name:
                names.append(f"mempalace::{tool_name}")
                continue

        if name == "Shell":
            tool_input = item.get("input", {})
            command = str(tool_input.get("command", "")).strip().lower()
            if "mempalace" in command:
                names.append("mempalace::cli")
                continue

        names.append(name)

    return names


def collect_usage_stats(transcripts_root: Path) -> Dict:
    sessions_total = 0
    sessions_with_mempalace = 0
    mempalace_tool_calls = 0
    all_tool_calls = 0
    tool_counter: Counter = Counter()
    mempalace_tools_counter: Counter = Counter()
    session_call_counts: Dict[str, int] = defaultdict(int)

    for transcript in iter_parent_transcripts(transcripts_root):
        sessions_total += 1
        session_id = transcript.stem
        session_used_mempalace = False

        with transcript.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for tool_name in extract_tool_uses(obj):
                    all_tool_calls += 1
                    tool_counter[tool_name] += 1
                    if tool_name.startswith("mempalace_") or tool_name.startswith("mempalace::"):
                        mempalace_tool_calls += 1
                        mempalace_tools_counter[tool_name] += 1
                        session_call_counts[session_id] += 1
                        session_used_mempalace = True

        if session_used_mempalace:
            sessions_with_mempalace += 1

    return {
        "sessions_total": sessions_total,
        "sessions_with_mempalace": sessions_with_mempalace,
        "mempalace_tool_calls": mempalace_tool_calls,
        "all_tool_calls": all_tool_calls,
        "tool_counter": tool_counter,
        "mempalace_tools_counter": mempalace_tools_counter,
        "session_call_counts": session_call_counts,
    }


def collect_feedback_stats(feedback_path: Path) -> Dict:
    if not feedback_path.exists():
        return {
            "entries": 0,
            "helped_yes": 0,
            "helped_no": 0,
            "helped_unknown": 0,
            "minutes_saved_total": 0,
            "minutes_saved_avg": 0.0,
        }

    helped_yes = 0
    helped_no = 0
    helped_unknown = 0
    minutes_saved_total = 0
    entries = 0

    with feedback_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries += 1
            helped = obj.get("helped")
            if helped is True:
                helped_yes += 1
            elif helped is False:
                helped_no += 1
            else:
                helped_unknown += 1
            try:
                minutes_saved_total += int(obj.get("minutes_saved", 0))
            except (TypeError, ValueError):
                pass

    avg = (minutes_saved_total / entries) if entries else 0.0
    return {
        "entries": entries,
        "helped_yes": helped_yes,
        "helped_no": helped_no,
        "helped_unknown": helped_unknown,
        "minutes_saved_total": minutes_saved_total,
        "minutes_saved_avg": avg,
    }


def load_search_events(path: Path = DEFAULT_SEARCH_EVENTS) -> List[Dict]:
    if not path.exists():
        return []
    events: List[Dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def load_help_scores(path: Path = DEFAULT_HELP_SCORES) -> Dict[str, Dict]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}

