---
name: mempalace-neural-memory
description: Operates a generic MemPalace smart-memory workflow with anti-stickiness search, realtime dashboard telemetry, and feedback-driven help_score reinforcement. Use when the user mentions MemPalace, memory search, neural map, dashboard, anti-stickiness, alternatives/routes, helped or not helped feedback, minutes saved, or request-to-code traceability.
---

# MemPalace Neural Memory Workflow

## Quick Start

Use this skill when tasks involve memory retrieval quality, memory analytics, or "did memory help?" tracking.

Default sequence:
1. Run smart search first (not plain search) for mixed relevance + diversity.
2. Use top results, but keep at least one alternative route in reasoning.
3. After every MemPalace tool touch, write route pulse so live map updates immediately.
4. Log feedback (`helped` and `minutes_saved`) after meaningful outcomes.
5. Validate trends in dashboard before changing retrieval settings.

## Core Paths

- Palace path: set by your local MCP env or `MEMPALACE_PALACE_PATH`
- Smart search: `mempalace-smart-search.py`
- Dashboard: `mempalace-dashboard.py`
- Feedback logger: `mempalace-log-feedback.ps1`
- Route pulse logger: `mempalace-touch.ps1` / `mempalace-route-pulse.py`
- Analytics store: `.mempalace-analytics/`
- Trace cards: any project folder like `memory-cards/`

## Retrieval Policy

Prefer:
- project-code wings for implementation details.
- transcript/chat wings for rationale/history/decision context.
- mixed retrieval when a request needs both code + prior discussion.

Use this default command:

```powershell
.\.venv-mempalace\Scripts\python.exe .\mempalace-smart-search.py "<query>" --top-k 10 --candidate-k 40
```

After any non-search MemPalace tool call:

```powershell
.\mempalace-touch.ps1 -Tool "mempalace_status" -Query "memory health check"
```

## Anti-Stickiness Rules

- Do not rely on one source cluster only.
- Keep alternatives visible in final reasoning.
- Respect adaptive controller outputs (`relaxed`, `stable`, `active`, `aggressive`).
- If stickiness remains high across multiple searches, bias toward diversity instead of only highest similarity.

## Dopamine Reinforcement Principle

Apply this model as a practical learning loop:
- `helped=yes` increases confidence for selected memory routes (positive reinforcement).
- `helped=no` decreases confidence for those routes (negative reinforcement).
- `minutes_saved` estimates practical value and helps prioritize truly useful memory.

This is implemented through `help_score` updates and blended ranking, so future retrieval improves based on outcome quality, not only semantic similarity.

## Feedback Logging

After meaningful tasks, record feedback:

```powershell
.\mempalace-log-feedback.ps1 -Helped yes -MinutesSaved 12 -Note "Found correct route for listing package logic"
```

Allowed values:
- `-Helped yes|no|unknown`
- `-MinutesSaved <int>`
- `-Note "<short outcome>"`

## Dashboard Use

Open:
- `http://localhost:8501`

Check:
- Anti-stickiness trend
- Alternative route ratio
- Live route stream
- Adaptive anti-stickiness indicators
- Neural Map Lite route topology

If trends worsen, adjust retrieval behavior first (query specificity, wing targeting, alternatives), then tune parameters.

## Traceability Requirement

For non-trivial changes, create/update a memory card in your chosen project path, for example:
- `memory-cards/`

Include:
- Request summary
- Code changes
- Outcome and validation
- Follow-up decisions

Then refresh memory index:

```powershell
.\mempalace-refresh-index.ps1
```
