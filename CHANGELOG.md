# Changelog

All notable changes to Ledgerling are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.44.0] - 2026-09-29

### Added

- Web UI dashboard: an **Insights** strip (the top observations from the
  `insights` command) and a **Recent activity** card (the latest entries for the
  month, newest first, income marked) now appear on the home view, alongside the
  existing stat/category/budget/goal cards.

### Fixed

- Web UI: `run_cli` now serializes command execution with a lock. It redirects
  the process-wide stdout/stderr to capture output, so concurrent requests (e.g.
  the dashboard fetching several commands at once) could previously clobber each
  other's captured output. Commands are fast and local, so serializing them has
  no practical cost.

## [1.43.0] - 2026-09-29

### Added

- `matrix` command — a category x month spending grid (pivot table) over the
  last N months (`--months`, default 6), with per-month and per-category totals
  and a grand total; categories are sorted by their window total. Rows are
  objects (`category` + one field per month + `total`), so the web UI renders a
  clean pivot table. Supports `--json`.

## [1.42.0] - 2026-09-29

### Added

- `range START [END]` — totals over an arbitrary date range (not just a calendar
  month): income, spending, net, per-day average, and a per-category breakdown,
  over the inclusive `START`..`END` window (`END` defaults to today; reversed
  dates are swapped). Dates accept `YYYY-MM-DD`, `today`, or `yesterday`.
  Supports `--json`. This is the first range-based summary — every other rollup
  is month-bound.

## [1.41.0] - 2026-09-29

### Added

- `tagtrend TAG` — a monthly spending trend for a single `#tag` over the last N
  months (`--months`, default 6), with a bar chart, average, and total. Mirrors
  `trend` (which charts a category) but for tags, which can span categories.
  Series-shaped, so the web UI renders it as a table/chart. Supports `--json`.

## [1.40.0] - 2026-09-29

### Added

- `insights` command — plain-language observations about a month: savings (or
  overspend) and savings rate, your biggest category and its share,
  month-over-month spending change, budget breaches (or "all on track"), the
  largest single expense, and no-spend days. Supports `--json` (`insights` list
  plus a `metrics` object).
- Web UI: a bespoke **Insights** view for that command — each observation as its
  own card in a clean list, shown as the preferred tab (the raw metrics chart is
  suppressed for it). Its second per-feature hand-built element, after the
  heatmap calendar.

## [1.39.0] - 2026-09-29

### Changed

- **Web UI redesign.** The app now opens on a **Dashboard** instead of a raw
  command form: this month's income / spending / net as stat cards, top
  categories and budget progress as bars, and savings-goal status, with a month
  picker — all built from `month --json`. The sidebar now groups commands
  (Record / Analyze / Budgets & goals / Recurring / Data / Settings) with a
  quick "New expense" action, replacing the flat list; any command not in a
  group still appears automatically under "More", so the nav stays
  comprehensive. A card-based visual pass (typography, spacing, pill tabs,
  zebra tables, light/dark polish) applies throughout. The parser-introspection
  contract is unchanged — new commands still generate their own form, and the
  Text / Table / Chart / Calendar output views are preserved.
- `/api/describe` now also reports the configured currency symbol, so the
  dashboard formats amounts correctly.

## [1.38.0] - 2026-09-29

### Added

- `suggest` command — recommends a monthly budget per category from recent
  average spending (`--months`, default 3; averaged over the months in that
  window that actually had spend), rounded up to a friendly figure with a little
  headroom. Shows your current budget alongside each suggestion, and prints the
  `budget` command to set it. Series-shaped, so the web UI renders a
  table/chart. Supports `--json`.

## [1.37.0] - 2026-09-29

### Added

- `heatmap` command — a daily-spending calendar for a month. In the terminal it
  prints a Mon-Sun calendar grid with intensity glyphs (`. : + * #`) plus the
  month total and busiest day; `--json` emits per-day spending.
- Web UI: a bespoke **Calendar** view for `heatmap` — a real month grid whose
  cells are shaded by spending intensity (with a less-to-more legend and the
  peak day), shown as its own tab alongside Text / Table / Chart. This is the
  first command with a hand-built UI element beyond the auto-generated views.

## [1.36.0] - 2026-09-29

### Added

- `dedupe` command — removes duplicate entries (identical date, amount,
  category, note, and kind), keeping the lowest-id entry in each group. Pairs
  with `duplicates` (which only reports them). `--dry-run` previews the ids it
  would remove without changing anything; a real run is undoable with `undo`.
  Supports `--json`.

## [1.35.0] - 2026-09-29

### Added

- `savings` command — a monthly savings-rate trend: for each month with data,
  income, spending, net, and the savings rate (net / income, `null` when there
  was no income that month), with an ASCII bar. Series-shaped, so the web UI
  renders it as a table/chart with a metric selector (income / spending / net /
  rate). Supports `--json`.

## [1.34.0] - 2026-09-29

### Added

- `commitments` command — normalizes every recurring rule to its
  monthly-equivalent and annual cost (day × 365/12, week × 52/12, month × 1),
  split into expense vs income with monthly and annual net. Shows your fixed
  obligations at a glance. Series-shaped, so the web UI renders it as a
  table/chart. Supports `--json`.

## [1.33.0] - 2026-09-29

### Added

- `balance` command — a running cumulative net (income − spending) accumulated
  month over month, showing how your tracked balance has evolved over time.
  Series-shaped, so it inherits the web UI's Table and Chart views; the chart
  defaults to the cumulative balance line. Supports `--json`.

## [1.32.0] - 2026-09-29

### Added

- Web UI: chart view now also covers **map-shaped** results - objects keyed by
  name whose values are numbers (`stats.by_tag`) or objects with numeric fields
  (`categories`, `sources`, `tags`) render as bar charts, with the metric
  selector defaulting to the most meaningful field (total over count).

## [1.31.0] - 2026-09-29

### Added

- `forecast` command — projects the current year's spending, income, and net to
  year-end from the run rate so far (day-of-year elapsed), shown next to the
  amounts so far. Supports `--json`.

## [1.30.0] - 2026-09-29

### Added

- `quarter [YYYY]` command — quarterly rollup (Q1-Q4) of spending, income, and
  net for a year. Series-shaped, so it inherits the web UI's Table and Chart
  views automatically. Supports `--json`.

## [1.29.0] - 2026-09-29

### Added

- Web UI: **Chart** view. Series-shaped JSON now renders as a horizontal bar
  chart with a Text / Table / Chart toggle - covers `trend`, `distribution`,
  `week`, `year`, `weekday`, and any future series command. When a series has
  several numeric fields, a metric selector switches between them (e.g. weekday
  total / count / average).

## [1.28.0] - 2026-09-29

### Added

- `sources` command — income broken down by source (category) with entry counts
  and totals, mirroring `categories` for the income side. Supports `--json`, so
  the web UI renders it as a table automatically.

## [1.27.0] - 2026-09-29

### Added

- Web UI: rich result rendering. Command output now offers a **Text / Table**
  toggle - JSON-capable commands render as a columnar table (arrays like
  `list`/`search`/`top`/`categories`) or a field table (objects like
  `stats`/`month`/`year`), fetched automatically alongside the text output.

## [1.26.0] - 2026-09-29

### Added

- **Web UI** (`web` command) — a local, sandboxed single-page app (stdlib
  `http.server`, binds to 127.0.0.1) that drives every Ledgerling command. It
  generates itself from the CLI parser, so all current commands — and any added
  later — show up automatically as forms; running one shows its output. No shell
  access and no network egress; data stays in the Ledgerling folder.

## [1.25.0] - 2026-09-29

### Added

- `distribution` command — a histogram of expense sizes bucketed into
  $0-10 / $10-25 / $25-50 / $50-100 / $100-250 / $250+, with counts and totals
  (optionally filtered by `--month`). Supports `--json`.

## [1.24.0] - 2026-09-29

### Added

- `average` command — average spending per day, week, and month across the full
  recorded date range (first to last expense). Supports `--json`.

## [1.23.0] - 2026-09-29

### Added

- `untagged` command — lists expenses that have no `#tags` (optionally filtered
  by `--month`), to help you find and tag them. Supports `--json`.

## [1.22.0] - 2026-09-29

### Added

- `year [YYYY]` command — a calendar-year rollup: per-month spending (Jan-Dec)
  with a bar chart, plus annual income, spending, net, and average/month.
  Supports `--json`.

## [1.21.0] - 2026-09-29

### Added

- `export --format {csv,json}` — export can now write JSON (full fields
  including tags, kind, and ids) in addition to CSV; default remains CSV.

## [1.20.0] - 2026-09-29

### Added

- `day` command — lists the entries for a single day (today by default, or
  `--date`) with spending, income, and net. Supports `--json`.

## [1.19.0] - 2026-09-29

### Added

- `retag OLD NEW` — rename a `#tag` across all entries and recurring rules
  (case-insensitive match, rewrites the note text and re-parses tags).

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

[1.44.0]: #1440---2026-09-29
[1.43.0]: #1430---2026-09-29
[1.42.0]: #1420---2026-09-29
[1.41.0]: #1410---2026-09-29
[1.40.0]: #1400---2026-09-29
[1.39.0]: #1390---2026-09-29
[1.38.0]: #1380---2026-09-29
[1.37.0]: #1370---2026-09-29
[1.36.0]: #1360---2026-09-29
[1.35.0]: #1350---2026-09-29
[1.34.0]: #1340---2026-09-29
[1.33.0]: #1330---2026-09-29
[1.32.0]: #1320---2026-09-29
[1.31.0]: #1310---2026-09-29
[1.30.0]: #1300---2026-09-29
[1.29.0]: #1290---2026-09-29
[1.28.0]: #1280---2026-09-29
[1.27.0]: #1270---2026-09-29
[1.26.0]: #1260---2026-09-29
[1.25.0]: #1250---2026-09-29
[1.24.0]: #1240---2026-09-29
[1.23.0]: #1230---2026-09-29
[1.22.0]: #1220---2026-09-29
[1.21.0]: #1210---2026-09-29
[1.20.0]: #1200---2026-09-29
[1.19.0]: #1190---2026-09-29
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
