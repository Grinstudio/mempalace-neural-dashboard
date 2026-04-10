---
name: mempalace-neural-memory
description: Operates a generic MemPalace smart-memory workflow with anti-stickiness search, realtime dashboard telemetry, and automatic utility scoring. Use when the user mentions MemPalace, memory search, neural map, dashboard, anti-stickiness, alternatives/routes, auto utility quality, or request-to-code traceability.
---

# MemPalace Neural Memory Workflow

## Quick Start

Use this skill when tasks involve memory retrieval quality, memory analytics, or automatic utility tracking.

Default sequence:
1. Start with `mempalace_status` once per session to confirm palace health.
2. Run smart search first (not plain search) for mixed relevance + diversity.
3. Use top results, but keep at least one alternative route in reasoning.
4. Use memory output to narrow file reads (open only likely files first).
5. After non-search MemPalace calls, emit route pulse so the live map updates.
6. Ensure smart-search events include `auto_utility` telemetry for quality monitoring.
7. Validate auto-utility trends in dashboard before changing retrieval settings.

Memory-first, files-second policy:
- Do not start with broad codebase browsing when memory can answer path discovery.
- For code modifications, still confirm exact lines in the target file before editing.
- If user gives an exact file path or stack trace, inspect that directly, then return to memory flow.

Do not use this skill for:
- Pure UI text edits that do not need memory retrieval.
- One-file changes where user already gave exact file + symbol.
- Repeating the same query immediately without new context.

## Core Paths

- Palace path: set by your local MCP env or `MEMPALACE_PALACE_PATH`
- Smart search: `mempalace-smart-search.py`
- Dashboard: `mempalace-dashboard.py`
- Route pulse logger: `mempalace-touch.ps1` / `mempalace-route-pulse.py`
- Analytics store: `.mempalace-analytics/`
- Trace cards: any project folder like `memory-cards/`

## Retrieval Policy

Prefer:
- project-code wings for implementation details.
- transcript/chat wings for rationale/history/decision context.
- mixed retrieval when a request needs both code + prior discussion.

MCP server naming:
- Prefer `user-mempalace` when calling MCP tools from Cursor.

When user asks "why are you reading files":
- Explain that memory is used for route discovery and context ranking.
- Explain that exact file reads are still required before safe edits.
- Continue with memory-first order in the next steps.

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

## Automatic Utility Principle

Apply this model as an automatic quality loop:
- Score utility from retrieval signals only (no human input required).
- Track 6 components: relevance, diversity, consistency, actionability, execution proxy, stability.
- Use utility bands (`high`, `medium`, `low`) for monitoring trend and regressions.

This is implemented through:
- `auto_utility` telemetry in smart-search events (`telemetry_channel = mcp_auto_utility_v1`).
- automatic `help_scores` updates from auto-utility (no manual feedback required).
- bridge flow for plain MCP search (`mempalace_search`) via `mempalace-touch.ps1` -> smart-search truth event.

Interpretation guardrail:
- Treat auto utility as a technical quality proxy, not a direct business KPI.
- Use trends over multiple events; avoid decisions from a single low/high score.

## Performance Guardrails

- Avoid duplicate smart-search calls for the same query in short intervals.
- Prefer targeted `--wing` / `--room` filters before increasing candidate depth.
- Use larger `candidate-k` only when current retrieval quality is clearly insufficient.
- Keep route pulse for non-search tools only; smart-search already writes truth telemetry.

## Dashboard Use

Open:
- `http://localhost:8501`

Check:
- Anti-stickiness trend
- Alternative route ratio
- Live route stream
- Adaptive anti-stickiness indicators
- Auto utility trend and band distribution
- Help score health (should populate automatically from smart-search runs)
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
