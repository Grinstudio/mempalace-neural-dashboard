# MemPalace Neural Dashboard for Cursor

**A visual AI memory cockpit for Cursor**: fast search, smarter route selection, anti-stickiness protection, and realtime analytics you can understand at a glance.

## The Story

I built this project on top of the MemPalace idea shared under the `milla-jovovich` project identity, with a clear Fifth Element spirit: turn raw memory into a living, navigable brain map.

I extended that idea into a more practical and more beautiful workflow for real projects:

- clearer memory routing,
- adaptive anti-stickiness logic,
- feedback reinforcement (`helped` / `not helped`),
- a realtime dashboard that shows what the memory system is doing.

## What This Tool Does

I use this toolkit to give Cursor a structured memory layer so it can work better in any project type:

- remember code and chat context across sessions,
- avoid repeating the same narrow memory route,
- surface alternative paths during search,
- learn from outcomes (`helped` and `minutes_saved`),
- explain memory decisions visually in a dashboard.

I use it for:
- software engineering projects of any stack,
- product discovery and whiteboarding sessions,
- architecture planning,
- debugging and incident retrospectives,
- long-running team projects where context continuity matters.

## Why It Matters

Without a memory strategy, AI assistants can:

- forget earlier architecture decisions,
- overfit to one familiar source,
- return technically correct but less useful answers.

I address that with **smart retrieval + reinforcement + observability**.

## Core Features

- **Smart Search Engine**
  - semantic retrieval + `help_score` + recency blending,
  - MMR diversity reranking,
  - source cap,
  - explore injection,
  - adaptive anti-stickiness tuning.

- **Neural Visualization**
  - Neural Map Lite (`wing -> room -> source`),
  - live route stream for the latest query,
  - selected routes vs alternative routes view.

- **Reinforcement Loop ("Dopamine" Model)**
  - `helped=yes` raises route confidence,
  - `helped=no` lowers route confidence,
  - `minutes_saved` increases practical value weighting.

- **Realtime Product Dashboard**
  - stickiness risk trend,
  - alternative-route ratio,
  - adaptive controller state (`relaxed/stable/active/aggressive`),
  - route-level telemetry.

## Very Simple Setup (Windows)

### 1) Refresh project code memory

```powershell
.\mempalace-refresh-child.ps1
```

### 2) Refresh chat memory

```powershell
.\mempalace-refresh-chats.ps1
```

### 3) Run one smart search

```powershell
.\.venv-mempalace\Scripts\python.exe .\mempalace-smart-search.py "your query here" --palace-path "D:\PROJECTS\Minupidu\FTP\.mempalace-child\palace" --top-k 10 --candidate-k 40
```

### 4) Start the dashboard

```powershell
.\mempalace-dashboard.ps1
```

### 5) Open the dashboard in browser

Open:

- [http://localhost:8501](http://localhost:8501)

Dashboard URL: [http://localhost:8501](http://localhost:8501)

### 6) Run automatic maintenance

```powershell
.\mempalace-maintenance.ps1 -Mode auto
```

This checks thresholds and only archives/trims when needed.
I can also manage this directly from the dashboard bottom section:
- live noise score,
- auto-optimization trigger when threshold is exceeded,
- `Optimize database now` manual button.

## How to Use Daily (Non-Technical)

1. I ask Cursor a normal task question.
2. I let smart search propose relevant and alternative memory routes.
3. I check the dashboard when I need to review route quality.
4. After I use the result, I log feedback:

```powershell
.\mempalace-log-feedback.ps1 -Helped yes -MinutesSaved 10 -Note "Correct fix path found quickly"
```

5. I repeat this loop, and the system improves from real outcomes.

## Generic Use Cases

- **Any codebase memory layer**: backend, frontend, mobile, desktop, infra, data workflows.
- **Whiteboarding and ideation**: keep idea evolution, decisions, and alternatives in one retrievable memory graph.
- **Team continuity**: new team members can quickly recover context from previous sessions and decisions.
- **Decision quality tracking**: see whether memory routes are helping or hurting over time.

## File Map

- `mempalace-smart-search.py` - memory retrieval and route selection logic.
- `mempalace-dashboard.py` - realtime visual cockpit.
- `mempalace_analytics.py` - shared analytics helpers.
- `mempalace-feedback.py` - feedback and score updates.
- `mempalace-dashboard.ps1` - dashboard launcher.
- `mempalace-refresh-child.ps1` - project code memory refresh (script name can stay as-is).
- `mempalace-refresh-chats.ps1` - transcript memory refresh.
- `.cursor/skills/mempalace-neural-memory/SKILL.md` - Cursor skill for this workflow.

## Data and Privacy

- Local-first by design.
- Runtime telemetry is stored in `.mempalace-analytics/`.
- Local vector memory lives in `.mempalace-child/`.
- Keep private transcripts and local stores out of public repos.

## License

I publish this project as open source under the MIT License.

You can use, copy, modify, publish, distribute, sublicense, and sell copies of the software, as long as the license notice is included.

See `LICENSE` for full legal terms.
