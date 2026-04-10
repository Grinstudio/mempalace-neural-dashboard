# MemPalace Neural Dashboard (Universal)

Local-first toolkit for Cursor + MemPalace with:
- smart retrieval and anti-stickiness,
- automatic utility scoring,
- route telemetry and neural diagnostics dashboard.

This package is neutral and reusable across projects (not tied to Minupidu).

## What Is Included

- `mempalace-smart-search.py`  
  Smart retrieval, adaptive diversity, `auto_utility` scoring, truth telemetry.
- `mempalace-dashboard.py`  
  Route analytics, neural simulator, auto-utility and help-score health views.
- `mempalace-touch.ps1` + `mempalace-route-pulse.py`  
  Tool-touch telemetry and MCP-search bridge to smart-search truth events.
- `.cursor/skills/mempalace-neural-memory/SKILL.md`  
  Memory-first workflow skill.
- `.cursor/rules/mempalace-priority-workflow.mdc`  
  Default operating rule for consistent agent behavior.

## Quick Start (Windows)

1) Preflight check (recommended)

```powershell
.\mempalace-preflight.ps1
```

2) Setup indexing sources

```powershell
.\mempalace-setup-indexing.ps1
```

3) Refresh index

```powershell
.\mempalace-refresh-index.ps1
```

4) Run one smart search

```powershell
.\.venv-mempalace\Scripts\python.exe .\mempalace-smart-search.py "your query" --top-k 10 --candidate-k 40
```

5) Start dashboard

```powershell
.\mempalace-dashboard.ps1
```

Open: [http://localhost:8501](http://localhost:8501)

## Workflow Impact (Practical)

- Fewer wrong edits from ambiguous tasks (clarify-first gating).
- Faster lane selection (debug vs plan vs execute) by task-size + keywords.
- Lower noise and lower CPU/I/O from guarded retrieval behavior.
- Better observability via automatic quality score trends.

## Telemetry Channels

- Smart truth events: `mcp_auto_utility_v1`
- Tool-touch events: `mcp_tool_touch_v1`

`auto_utility` is a technical quality proxy (not a business KPI).

## Optional Skills

Use `skills-config.json` to keep default workflow lean and enable heavier skills only when needed.

## Cursor Integration

One-command install to any target project:

```powershell
.\mempalace-bootstrap-cursor.ps1 -TargetProjectPath "D:\PATH\TO\YOUR\PROJECT"
```

If files already exist and you want overwrite with backup:

```powershell
.\mempalace-bootstrap-cursor.ps1 -TargetProjectPath "D:\PATH\TO\YOUR\PROJECT" -Force
```

Manual copy alternative:
- `.cursor/skills/mempalace-neural-memory/SKILL.md`
- `.cursor/rules/mempalace-priority-workflow.mdc`

## Privacy

- Telemetry is local in `.mempalace-analytics/`
- Vector memory is local in `.mempalace-child/`
- Do not commit private analytics stores or transcripts

## License

MIT (`LICENSE`)
