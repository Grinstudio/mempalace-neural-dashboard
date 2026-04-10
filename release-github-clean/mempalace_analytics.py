#!/usr/bin/env python3
"""
Shared analytics helpers for MemPalace usage, feedback, and smart-search telemetry.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

DEFAULT_TRANSCRIPTS = (
    r"C:\Users\user\.cursor\projects\d-PROJECTS-Minupidu-FTP\agent-transcripts"
)
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


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_auto_utility(event: Dict) -> Dict:
    """Compute fully automatic utility score for a retrieval event."""
    results = event.get("results", [])
    if not isinstance(results, list):
        results = []

    if not results:
        components = {
            "retrieval_relevance": 0.0,
            "source_diversity": 0.0,
            "consistency": 0.0,
            "actionability": 0.0,
            "execution_outcome": 35.0,
            "stability": 50.0,
        }
        score = 12.0
        band = "low"
        return {
            "score": round(score, 1),
            "band": band,
            "components": components,
            "weights": {
                "retrieval_relevance": 0.30,
                "source_diversity": 0.15,
                "consistency": 0.15,
                "actionability": 0.15,
                "execution_outcome": 0.20,
                "stability": 0.05,
            },
            "channel": "mcp_auto_utility_v1",
            "auto_only": True,
        }

    sem_values: List[float] = []
    unique_sources = set()
    unique_wings = set()
    loc_hits = 0
    snippet_hits = 0
    for row in results:
        sim = _safe_float(row.get("similarity", 0.0), 0.0)
        sem_values.append(_clamp((sim + 1.0) / 2.0, 0.0, 1.0))
        unique_sources.add(str(row.get("source_file", "unknown")))
        unique_wings.add(str(row.get("wing", "unknown")))
        if row.get("source_path") and row.get("line_start") is not None:
            loc_hits += 1
        text = str(row.get("text", "") or row.get("text_preview", "") or "")
        if len(text.strip()) >= 20:
            snippet_hits += 1

    total = max(1, len(results))
    mean_sem = sum(sem_values) / total
    retrieval_relevance = 100.0 * mean_sem

    source_div_raw = len(unique_sources) / total
    wing_div_raw = len(unique_wings) / max(1, min(total, 4))
    source_diversity = 100.0 * _clamp(0.75 * source_div_raw + 0.25 * wing_div_raw, 0.0, 1.0)

    spread = (max(sem_values) - min(sem_values)) if sem_values else 1.0
    consistency = 100.0 * _clamp(1.0 - spread, 0.0, 1.0)

    loc_ratio = loc_hits / total
    snippet_ratio = snippet_hits / total
    actionability = 100.0 * _clamp(0.70 * loc_ratio + 0.30 * snippet_ratio, 0.0, 1.0)

    stickiness = _safe_float(
        (event.get("adaptive", {}) or {}).get("recent_stickiness", event.get("stickiness_score", 50.0)),
        50.0,
    )
    stability = 100.0 * _clamp(1.0 - (stickiness / 100.0), 0.0, 1.0)

    execution_outcome = 100.0 * _clamp(
        0.50 + (mean_sem - 0.50) * 0.60 + (loc_ratio * 0.20),
        0.0,
        1.0,
    )

    weights = {
        "retrieval_relevance": 0.30,
        "source_diversity": 0.15,
        "consistency": 0.15,
        "actionability": 0.15,
        "execution_outcome": 0.20,
        "stability": 0.05,
    }
    components = {
        "retrieval_relevance": round(retrieval_relevance, 1),
        "source_diversity": round(source_diversity, 1),
        "consistency": round(consistency, 1),
        "actionability": round(actionability, 1),
        "execution_outcome": round(execution_outcome, 1),
        "stability": round(stability, 1),
    }
    score = (
        components["retrieval_relevance"] * weights["retrieval_relevance"]
        + components["source_diversity"] * weights["source_diversity"]
        + components["consistency"] * weights["consistency"]
        + components["actionability"] * weights["actionability"]
        + components["execution_outcome"] * weights["execution_outcome"]
        + components["stability"] * weights["stability"]
    )
    if score >= 80.0:
        band = "high"
    elif score >= 55.0:
        band = "medium"
    else:
        band = "low"

    return {
        "score": round(score, 1),
        "band": band,
        "components": components,
        "weights": weights,
        "channel": "mcp_auto_utility_v1",
        "auto_only": True,
    }

