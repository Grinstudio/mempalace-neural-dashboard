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
    DEFAULT_HELP_SCORES,
    DEFAULT_LAST_SEARCH,
    DEFAULT_SEARCH_EVENTS,
    ensure_analytics_dir,
    load_help_scores,
)


TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{3,}")


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


def maybe_inject_explore(
    selected: List[Dict],
    original_candidates: List[Dict],
    top_k: int,
    query: str,
) -> Tuple[List[Dict], bool]:
    if not selected:
        return selected, False

    # Deterministic low-rate exploration: every ~8th query hash bucket.
    inject = (abs(hash(query)) % 8) == 0
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

    help_scores = load_help_scores(Path(args.help_scores_path))

    sem_w, help_w, rec_w = 0.70, 0.20, 0.10
    for item in results:
        key = result_key(item)
        hs = help_scores.get(key, {})
        item["_help_raw"] = clamp(float(hs.get("score", 0.0)), -1.0, 1.0)
        item["_blended"] = blended_score(item, help_scores, sem_w, help_w, rec_w)

    # Pre-sort by blended relevance before MMR.
    candidates = sorted(results, key=lambda x: float(x["_blended"]), reverse=True)
    mmr_out = mmr_select(candidates.copy(), top_k=args.top_k, lambda_mmr=args.lambda_mmr)
    capped = enforce_source_cap(mmr_out, cap=args.source_cap)[: args.top_k]
    reranked, explore_injected = maybe_inject_explore(capped, results, args.top_k, args.query)
    selected_keys = {result_key(i) for i in reranked}

    candidate_preview = []
    for item in candidates[: min(len(candidates), max(args.top_k * 2, 16))]:
        key = result_key(item)
        candidate_preview.append(
            {
                "key": key,
                "source_file": item.get("source_file"),
                "wing": item.get("wing"),
                "room": item.get("room"),
                "similarity": item.get("similarity"),
                "help_score": item.get("_help_raw", 0.0),
                "blended_score": item.get("_blended", 0.0),
                "selected": key in selected_keys,
            }
        )

    output = {
        "query": args.query,
        "wing": args.wing,
        "room": args.room,
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
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

    event = {
        "timestamp": utc_now_iso(),
        "query": args.query,
        "wing": args.wing,
        "room": args.room,
        "candidate_k": args.candidate_k,
        "top_k": args.top_k,
        "explore_injected": explore_injected,
        "unique_sources": len({i.get("source_file") for i in reranked}),
        "unique_wings": len({i.get("wing") for i in reranked}),
        "candidate_preview": candidate_preview,
        "results": [
            {
                "key": result_key(i),
                "source_file": i.get("source_file"),
                "wing": i.get("wing"),
                "room": i.get("room"),
                "similarity": i.get("similarity"),
                "help_score": i.get("_help_raw", 0.0),
                "blended_score": i.get("_blended", 0.0),
            }
            for i in reranked
        ],
    }

    write_jsonl(Path(args.events_path), event)
    write_json(Path(args.last_search_path), event)

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f'Smart results for: "{args.query}"')
        if args.wing:
            print(f"Wing filter: {args.wing}")
        if args.room:
            print(f"Room filter: {args.room}")
        print(f"Explore injected: {'yes' if explore_injected else 'no'}")
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
