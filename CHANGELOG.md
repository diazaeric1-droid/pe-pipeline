# Changelog

PE Pipeline is the suite **orchestrator / fleet-triage hub** — it chains the individually
versioned apps, so it tracks notable changes by date rather than its own semver.

## 2026-06-07
### Changed
- **Light theme** — adopted the suite-wide light palette (shared `theme.py` + `config.toml`
  vendored byte-identical: white surfaces, `plotly_white` charts, navy/blue accents retained;
  transparent fixed header so the title never clips). `runtime.txt` pinned to Python 3.12.
