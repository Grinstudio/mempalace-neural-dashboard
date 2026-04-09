# MemPalace Analytics

This folder stores lightweight local analytics for memory usage.

## Files

- `feedback.jsonl` (created automatically): manual impact feedback entries.
- `search_events.jsonl` (auto): smart-search telemetry.
- `help_scores.json` (auto): learned usefulness scores per memory source.

## Commands

### 1) Usage + impact report

```powershell
python .\mempalace-stats.py
```

This shows:

- how many sessions used MemPalace tools,
- which MemPalace tools were used most,
- feedback totals: helped/not helped/estimated minutes saved.

### 2) Log one feedback entry

```powershell
.\mempalace-log-feedback.ps1 -SessionId "585e59b5-2567-4b0e-b891-a4c6c6cfa81e" -Helped yes -MinutesSaved 12 -Note "Found previous decision quickly"
```

`-Helped` accepts: `yes`, `no`, `unknown`.

### 3) Smart anti-stickiness search

```powershell
python .\mempalace-smart-search.py "submit flow package step" --palace-path "D:\PROJECTS\Minupidu\FTP\.mempalace-child\palace" --wing cursor_chats --top-k 8
```

### 4) Visual dashboard

```powershell
.\mempalace-dashboard.ps1
```

Opens a modern local dashboard with usage, impact, diversity, and help-score trends.

Stage 1 dashboard now includes near realtime updates and anti-stickiness visuals:

- auto-refresh (1-10 sec),
- stickiness risk gauge (0-100),
- alternative-route ratio trend,
- query -> selected routes table,
- wing-level route diversity panels.

### 5) Auto-maintenance (tracking + cleanup)

Monitor only (no changes):

```powershell
.\mempalace-maintenance.ps1 -Mode monitor
```

Auto mode (applies cleanup only if thresholds are exceeded):

```powershell
.\mempalace-maintenance.ps1 -Mode auto
```

Force apply immediately:

```powershell
.\mempalace-maintenance.ps1 -Mode apply
```

Optional: create Windows scheduled task:

```powershell
.\mempalace-maintenance-schedule.ps1 -Frequency Daily -Time 03:00
```

State report is written to:

- `.mempalace-analytics/maintenance-state.json`
