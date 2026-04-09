# MemPalace Neural Dashboard for Cursor

A lightweight, production-friendly memory layer for Cursor workflows with realtime analytics, anti-stickiness search logic, and visual route mapping for AI memory decisions.

This toolkit helps teams store and retrieve useful project context from both code and chat transcripts, then inspect how memory routes are selected over time.

## Why This Project

AI assistants can lose context across sessions, repeat suboptimal paths, or overfit to a small set of memory chunks. This project solves that by combining:

- targeted project memory indexing,
- smart reranking with diversity controls,
- feedback-driven reinforcement (`help_score`),
- visual telemetry for route quality and exploration behavior.

## Key Features

- Realtime Streamlit dashboard with auto-refresh.
- Neural Map Lite (`wing -> room -> source`) for route observability.
- Live route stream for the latest query path.
- Alternative-route tracking and anti-stickiness metrics.
- Smart search blending:
  - semantic similarity,
  - help score reinforcement,
  - recency boost,
  - MMR-based diversity,
  - source-cap control,
  - deterministic explore-injection.
- Feedback loop to log usefulness and minutes saved.
- Child-theme-first memory workflow (`themes/listeo-child`).
- Cursor transcript mining support for linking request-to-code context.

## Architecture Overview

- `mempalace-smart-search.py` - smart retrieval + telemetry writer.
- `mempalace-dashboard.py` - realtime analytics UI (Streamlit + Plotly).
- `mempalace_analytics.py` - shared analytics helpers.
- `mempalace-feedback.py` - feedback ingestion and score updates.
- `mempalace-refresh-child.ps1` - refresh child-theme memory index.
- `mempalace-refresh-chats.ps1` - refresh Cursor transcript memory index.
- `themes/listeo-child/memory-cards/` - request-to-change-to-outcome trace cards.

## Requirements

- Windows PowerShell
- Python 3.9+
- Local MemPalace environment (`.venv-mempalace`)
- Streamlit dependencies (`streamlit`, `plotly`, `pandas`, `streamlit-autorefresh`)

## Quick Start

1. Refresh child-theme memory:

```powershell
.\mempalace-refresh-child.ps1
```

2. Refresh Cursor chat memory:

```powershell
.\mempalace-refresh-chats.ps1
```

3. Run a smart search (writes telemetry events):

```powershell
.\.venv-mempalace\Scripts\python.exe .\mempalace-smart-search.py "your query here" --palace-path "D:\PROJECTS\Minupidu\FTP\.mempalace-child\palace" --top-k 10 --candidate-k 40
```

4. Launch dashboard:

```powershell
.\mempalace-dashboard.ps1
```

5. Open:

- [http://localhost:8501](http://localhost:8501)

## Dashboard Highlights

- Session and memory usage KPIs.
- Helpfulness and minutes-saved tracking.
- Stickiness risk gauge (lower is better).
- Alternative-route ratio trend (higher is better).
- Query-to-route table for recent selections.
- Neural map and route constellation for memory graph exploration.
- Live route rendering with selected vs alternative paths.

## Dopamine Reinforcement Loop (Practical)

This project uses a practical reinforcement loop to improve retrieval quality over time:

- If memory **helped**, route confidence increases.
- If memory **did not help**, route confidence decreases.
- `minutes_saved` adds practical value weighting.

These signals update `help_score`, which is blended with semantic similarity and recency during smart search reranking. The goal is simple: improve future answers based on real outcomes, not only text similarity.

## Data and Privacy Notes

- Designed for local-first operation.
- Runtime telemetry is written to `.mempalace-analytics/`.
- Local memory indexes (`.mempalace-child/`) should stay out of public repos.
- Use `.gitignore` to avoid pushing private transcripts, logs, and local stores.

## SEO Keywords

Cursor memory system, AI memory dashboard, MemPalace integration, vector memory search, smart retrieval reranking, anti-stickiness AI search, Streamlit AI analytics, WordPress AI development workflow.

## Use Cases

- Long-running WordPress projects with frequent iterative changes.
- Teams needing traceability from request to code outcome.
- AI-assisted coding workflows that require context persistence.
- Performance-aware memory routing with exploration safeguards.

## License

This project is open source and available to everyone under the MIT License.

You are free to use, copy, modify, merge, publish, distribute, sublicense, and sell
copies of the software, provided that the copyright notice and license text are included.

For full legal terms, see the `LICENSE` file.
