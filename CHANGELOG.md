# Changelog

All notable changes to Ledgerling are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.18.0] - 2026-09-29

### Added

- `weekday` command — spending aggregated by day of week (Mon-Sun) with totals,
  counts, and averages, over all data or a single month. Supports `--json`.

## [1.17.0] - 2026-09-29

### Added

- `streak` command — no-spend-day metrics for a month: spend days, no-spend
  days, and the longest and current no-spend streaks. Supports `--json`.

## [1.16.0] - 2026-09-29

### Added

- `week` command — this week's spending broken down by day (Mon-Sun) with a bar
  chart, plus income and net. `--offset N` looks back N weeks; supports `--json`.

## [1.15.0] - 2026-09-29

### Added

- `clone ID` — duplicate an existing entry (amount, category, note, tags, and
  kind), dated today by default or via `--date`. Handy for re-entering a
  frequent purchase without retyping.

## [1.14.0] - 2026-09-29

### Added

- `duplicates` command — finds likely double-entered records (identical date,
  amount, category, note, and kind) and lists each group's ids so you can
  `delete` the extras. Supports `--json`.

## [1.13.0] - 2026-09-29

### Added

- `pace` command — budget pace for a month: spent vs the day-adjusted expected
  amount, plus projected end-of-month per category. Partial for the current
  month, full for any past month. Supports `--json`.

## [1.12.0] - 2026-09-29

### Added

- `top` command — lists your largest expenses (`--limit`, default 10), with
  optional `--month` and `--category` filters. Supports `--json`.

## [1.11.0] - 2026-09-29

### Added

- `trend CATEGORY` — a monthly spending trend (bar chart) for a single category
  over the last N months (`--months`, default 6), with average and total.
  Supports `--json`.

## [1.10.0] - 2026-09-29

### Added

- `recur edit ID` — change an existing recurring rule's amount, category, note,
  frequency, or kind (`--income`/`--expense`), instead of deleting and re-adding.
  Already-generated expenses are left unchanged.

## [1.9.0] - 2026-09-29

### Added

- `compare` command — compares two months side by side (income, spending, net,
  and per-category spending deltas). Defaults to last month vs this month;
  supports `--json`.

## [1.8.0] - 2026-09-29

### Added

- `tags` command — lists `#tags` with item counts and totals (a tag can span
  categories), mirroring `categories`. Supports `--json`.

## [1.7.0] - 2026-09-29

### Added

- `upcoming` command — forecasts recurring charges and income due in the next
  N days (`--days`, default 30), with expense / income / net totals. Supports
  `--json`.

## [1.6.0] - 2026-09-29

### Added

- `completion` command — prints a `bash` or `zsh` tab-completion script,
  introspected from the parser so it always matches the current subcommands and
  options.

## [1.5.0] - 2026-09-29

### Added

- `month` command — a one-screen dashboard for a given month (`--month`,
  default current): income, spending, net, top categories, budget status, and
  savings-goal progress in a single view. Supports `--json`.

## [1.4.0] - 2026-09-29

### Added

- `goal` command — set, view, or clear a monthly savings target. It shows this
  month's net (income - spending) against the goal with a progress bar.
- `stats` now includes a savings-goal block for the current month (and `goal` /
  `goal_month_net` in `--json`); `report` compares net to the cumulative target
  (goal x months in the window).

## [1.3.0] - 2026-09-29

### Added

- **Income tracking.** New `income` command records inflows as a separate entry
  kind; `recur add --income` supports recurring income (e.g. salary).
- `stats` and `report` now show income, net (income − spending), and a savings
  rate. `list` and `search` gained `--income` and `--all` scoping.
- CSV export/import carries a `kind` column so income round-trips (and dedupe
  distinguishes income from an otherwise-identical expense).

### Changed

- Spending views (`list`, `summary`, `budget`, `categories`) show expenses only
  by default; income no longer counts toward spending totals or budgets.
- Expenses now carry an explicit `kind: "expense"`; existing records without the
  field are treated as expenses (backward compatible).

## [1.2.0] - 2026-09-29

### Added

- `categories` — list categories with item counts and totals (categories that
  only have a budget are included); supports `--json`.
- `recategorize OLD NEW` — rename a category across expenses, recurring rules,
  and budgets in one step.
- `undo` — revert the last change to your data, with a one-step redo toggle.
  Every data write now records a snapshot in the data folder first.

## [1.1.0] - 2026-09-29

Packaged as an installable CLI.

### Added

- Packaging via `pyproject.toml` with a `ledgerling` console-script entry point,
  installable with `pipx install .` (or `pip install .`). Also runnable as
  `python -m ledgerling`.
- `LEDGERLING_HOME` environment variable to choose the data folder.

### Changed

- **Data location.** Data now lives in a dedicated per-user folder the app owns
  (`~/.ledgerling` by default, or `$LEDGERLING_HOME`) instead of next to the
  script — required so an installed CLI does not write into its own package
  directory. The sandbox guarantee is unchanged: all reads and writes stay
  inside that one folder, and nothing is ever posted or pushed.
- Source reorganized into a `src/ledgerling/` package (`cli.py`).

## [1.0.0] - 2026-09-29

First release. A tiny, zero-dependency personal expense tracker: one Python
file, standard library only, strictly confined to its own folder.

### Added

- **Core records** — `add`, `edit`, `delete`, and `list` (filter by category or
  month; per-viewer default size configurable).
- **Search** — `search` by keyword (matches note or category), with
  `--category`, `--tag`, `--month`, and `--min`/`--max` amount filters.
- **Tags** — any `#word` in a note is parsed and stored automatically (on `add`,
  `edit`, `import`, and recurring generation); filter with `search --tag` and see
  a per-tag breakdown in `stats`.
- **Budgets** — `budget` sets/views monthly per-category limits; `add` warns when
  a category goes over.
- **Recurring expenses** — `recur add/list/remove/run` with daily/weekly/monthly
  schedules; occurrences auto-catch-up (idempotently) on read/report commands.
- **Reporting** — `summary` (category totals with an ASCII chart), `report`
  (month-over-month trend and budget adherence), and `stats` (extremes,
  averages, median, per-tag totals, and an end-of-month projection).
- **Data exchange** — `export` to CSV and `import` from CSV (dedupes on
  date/amount/category/note; tolerates a UTF-8 BOM; skips malformed rows).
- **Safety** — `backup` and `restore`, where restore validates the file first and
  auto-saves a pre-restore copy of current data before overwriting.
- **Settings** — `config` for currency symbol and default `list` size, stored in
  `ledgerling_config.json`; a missing or corrupted config falls back to defaults.
- **Scripting** — `--json` output on `list`, `search`, and `stats`.
- **Version** — `version` subcommand and `--version` flag.
- **Tests** — a 20-case stdlib `unittest` suite covering pure logic, the
  recurring engine, file round-trips, and the CLI end to end.

### Security

- **Sandbox** — the app only ever reads and writes files inside its own folder,
  and never posts, uploads, or pushes anything. Every write is routed through a
  guard (`_within_app`) that refuses any path escaping the app folder; imports
  and restores are reduced to a basename and looked up only within the folder.

[1.18.0]: #1180---2026-09-29
[1.17.0]: #1170---2026-09-29
[1.16.0]: #1160---2026-09-29
[1.15.0]: #1150---2026-09-29
[1.14.0]: #1140---2026-09-29
[1.13.0]: #1130---2026-09-29
[1.12.0]: #1120---2026-09-29
[1.11.0]: #1110---2026-09-29
[1.10.0]: #1100---2026-09-29
[1.9.0]: #190---2026-09-29
[1.8.0]: #180---2026-09-29
[1.7.0]: #170---2026-09-29
[1.6.0]: #160---2026-09-29
[1.5.0]: #150---2026-09-29
[1.4.0]: #140---2026-09-29
[1.3.0]: #130---2026-09-29
[1.2.0]: #120---2026-09-29
[1.1.0]: #110---2026-09-29
[1.0.0]: #100---2026-09-29
