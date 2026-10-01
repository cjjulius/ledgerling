# Changelog

All notable changes to Ledgerling are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.88.0] - 2026-09-30

### Added

- `split` — replace one entry with several category/amount parts that sum back
  to the original (e.g. a single receipt that was groceries + household). The
  parts inherit the original's date, note, tags and kind; the total must match
  the original to the cent or the split is rejected. Undoable like any change,
  and exposed in the self-generating web UI.

## [1.87.0] - 2026-09-30

### Changed

- Performance: the recurring-occurrence generator (`_occurrences`) now takes an
  optional `since` lower bound and fast-forwards to the first relevant date
  instead of iterating from the rule's start every time. Projections
  (`upcoming`, `cashflow`), the `recur list` next-due lookup, `recur skip`, and
  the post-`last` part of the catch-up no longer loop over years of history for
  long-running daily/weekly rules. Output is identical (a new brute-force
  equivalence test guards this); only the wasted iteration is gone.

## [1.86.0] - 2026-09-30

### Added

- `tip` — a tip calculator and even bill splitter. Give a bill amount and an
  optional `--pct` (default 18) and `--split` N; it reports the tip, total, and
  per-person share. Pure arithmetic (touches no stored data) done in integer
  cents, so an uneven split always sums back to the total — the leftover cents
  are spread one each across the first few people, and the breakdown is shown.
  Supports `--json`, and the self-generating web UI picks it up automatically.

## [1.85.0] - 2026-09-30

### Added

- Web UI: `cashflow` now renders a dedicated **Projection** view instead of a
  generic bar chart — an SVG running-balance line with a dashed zero baseline
  and area fill, a start / end / net-change / lowest summary strip, and a
  highlighted banner when the balance is projected to go negative. Points are
  red below zero and carry hover tooltips. The chart is theme-aware and marked
  up with an `img` role and label for assistive tech.

## [1.84.0] - 2026-09-30

### Added

- `cashflow` — project a running balance forward over the next N days from your
  recurring rules, shown as a register (each scheduled income/expense with the
  resulting balance). Starts from your all-time net by default, overridable with
  `--start-balance`; reports the end balance, net change, lowest point, and
  flags if/when the balance goes negative. Paused rules and skipped occurrences
  are excluded, and same-day income is applied before expenses. Supports
  `--days` and `--json`, and is picked up automatically by the web UI.

## [1.83.0] - 2026-09-30

### Changed

- Internal: the recurring catch-up in `main()` no longer relies on an inline
  command list that had to be hand-edited for every new read command. Commands
  are now classified in two explicit module-level sets, `CATCHUP_COMMANDS` and
  `MUTATING_COMMANDS`, and a new guard test asserts the two together cover
  exactly the parser's top-level commands (and never overlap). Adding a command
  without classifying it now fails the test suite instead of silently skipping
  or mis-applying catch-up. No behaviour change for users.

## [1.82.0] - 2026-09-30

### Added

- `roundup` — simulate a round-up savings rule: how much you'd set aside if
  every expense were rounded up to the nearest `--to` dollars (default 1.0).
  Reports total saved, average per item, and the largest single bump, using
  exact integer-cents arithmetic. Supports `--month` and `--json`, and is
  picked up automatically by the self-generating web UI.

## [1.81.0] - 2026-09-30

### Changed

- Web UI accessibility pass. Added a "Skip to main content" link, proper
  landmark labels (`nav`, command-output live region), and a visible
  keyboard-focus ring on every control. Command list items and group headers
  are now real keyboard controls (focusable, Enter/Space to activate, with
  `aria-current`/`aria-expanded`). Every generated form field now has its
  label programmatically associated with its input, required fields expose
  `aria-required`, and help text is linked via `aria-describedby`. The theme
  toggle reports its state with `aria-pressed`, and command output is announced
  politely to screen readers as it updates.

## [1.80.0] - 2026-09-30

### Added

- `anomalies` — flag expenses that are statistical outliers within their own
  category. For each category with enough history (`--min-count`, default 4),
  it computes the mean and standard deviation of amounts and reports any entry
  more than `--z` standard deviations above the mean (default 2.0). Supports
  `--month`, `--category`, and `--json`; results are sorted most-extreme first
  and the self-generating web UI picks it up automatically.

## [1.79.0] - 2026-09-30

### Added

- `config --symbol-position {before,after}` — choose whether the currency symbol
  prints before (`$12.50`) or after (`12.50 kr`) the amount, for currencies that
  trail the number. Applies everywhere amounts show, in the CLI and the web UI;
  `config` now also prints a formatted sample.

## [1.78.0] - 2026-09-30

### Changed

- Web UI dashboard: Budget rows are now clickable to drill into that category's
  expenses (like Top categories), and the Net stat card shows the savings rate
  (e.g. "75% of income saved").

## [1.77.0] - 2026-09-30

### Added

- `recur from ID` — create a recurring rule from an existing entry (its amount,
  category, note, and kind), so you can turn a one-off like rent or a paycheck
  into a monthly rule without retyping. `--every` sets the frequency; `--start`
  defaults to the entry's own date. Catches up immediately and is undoable.

## [1.76.0] - 2026-09-30

### Changed

- Web UI: while a command runs, the Run button is disabled and shows "Running…",
  then restores — clear feedback on slower operations and no accidental
  double-submits.

## [1.75.0] - 2026-09-30

### Added

- `years` command — a multi-year rollup: spending, income, and net for every
  year that has data, oldest first. The long-term counterpart to `year` (one
  year by month) and `quarter`. Series-shaped, so the web UI renders a
  table/chart. Supports `--json`.

## [1.74.0] - 2026-09-30

### Changed

- Web UI: pressing Enter in the command filter opens the first matching command,
  and Escape clears the filter. With the `/` shortcut, you can now jump to any
  command entirely from the keyboard (`/`, type, Enter).

## [1.73.0] - 2026-09-30

### Added

- `import --dry-run` — preview how many rows a CSV would add, skip as
  duplicates, and reject as malformed, without importing anything. Lets you
  sanity-check a file before committing it.

## [1.72.0] - 2026-09-30

### Added

- Web UI: a light/dark theme toggle in the header. The app still follows the OS
  by default, but the toggle forces light or dark and remembers the choice
  across reloads (the color tokens were restructured so a manual pick overrides
  the system preference).

## [1.71.0] - 2026-09-30

### Added

- `top --income` / `top --all` — `top` can now rank income entries, or expenses
  and income together, not just expenses (using the same kind scoping as `list`
  and `search`). Handy for finding your largest paychecks or refunds.

## [1.70.0] - 2026-09-30

### Changed

- Web UI Chart view now distinguishes negative values: negative bars and their
  value labels are red (positives stay green), so signed series like `balance`,
  `savings` net, or `compare` deltas read correctly at a glance.

## [1.69.0] - 2026-09-30

### Added

- `weekly` command — a weekly spending trend: total spend for each of the last N
  weeks (`--weeks`, default 8; weeks start Monday), with a bar chart, weekly
  average, and total. Complements `week` (a single week by day). Series-shaped,
  so the web UI renders a table/chart. Supports `--json`.

## [1.68.0] - 2026-09-30

### Changed

- Web UI rapid entry: after a successful `add` or `income`, the form clears and
  re-focuses the amount field so you can enter the next one immediately (the
  confirmation still shows). Press `/` anywhere to jump to the command filter.

## [1.67.0] - 2026-09-30

### Added

- `search --sort {date,amount,category}` and `--desc` — sort search results by
  date (default), amount, or category, ascending or descending. Handy for
  finding the largest matching transactions (`search coffee --sort amount
  --desc`).

## [1.66.0] - 2026-09-30

### Changed

- Web UI sidebar groups are now collapsible: click a group header (Record /
  Analyze / ...) to fold or unfold it, with each header showing its command
  count and the collapsed state remembered across reloads. Filtering still
  reveals every matching command regardless of collapse state.

## [1.65.0] - 2026-09-30

### Added

- `sources --month YYYY-MM` — the income-by-source breakdown can now be scoped
  to a single month, matching the `--month` option on `categories` and `tags`.

## [1.64.0] - 2026-09-30

### Added

- Web UI dashboard drill-downs: click a row in **Top categories** to jump to
  that category's expenses (`list --category`, run automatically), or a **Recent
  activity** entry to open its Edit form with the id pre-filled (ready to change,
  not auto-run). Rows show a hover highlight to signal they're clickable.

## [1.63.0] - 2026-09-30

### Added

- `recur pause ID` / `recur resume ID` — pause a recurring rule indefinitely (it
  generates nothing while paused) and resume it later. Resuming advances the
  rule to today so the paused gap is not backfilled. `recur list` marks paused
  rules. Both are undoable. Complements `recur skip` (a single occurrence).

## [1.62.0] - 2026-09-30

### Added

- Web UI: a "+ New income" quick-action button in the sidebar (next to
  "+ New expense") that jumps straight to the income form. Recording income was
  previously buried in the command list.

## [1.61.0] - 2026-09-30

### Changed

- Web UI Chart view now formats value labels like the tables: money metrics show
  as currency ($62.00) and rate/share as %, and the metric selector lists
  humanized names (Total, Count, Average) instead of raw field keys.

## [1.60.0] - 2026-09-30

### Changed

- Web UI Table view is much more readable. Numeric columns are right-aligned and
  monospaced; money columns render as currency ($62.00, not 62.0) and rate/share
  columns get a %; column headers are humanized. Crucially, a result object with
  a nested array (e.g. `matrix.rows`, `year.months`, `heatmap.days`) now renders
  that array as a proper table with the scalar fields shown as a "Summary"
  below, instead of dumping the array as raw JSON.

## [1.59.0] - 2026-09-30

### Changed

- Web UI polish: command names show as friendly Title Case in the sidebar and
  page heading ("Recur Skip", not `recur skip`) while search still matches the
  raw name; the Run button reads "Run Add" etc.; and opening a command now
  auto-focuses its first field so you can type right away.

## [1.58.0] - 2026-09-30

### Changed

- Web UI forms are much friendlier. Fields now use human labels ("List limit",
  not `--list-limit`); the internal `--json` flag is hidden (the UI fetches JSON
  itself); amounts get a currency-prefixed number field; `--month` renders as a
  month picker and `--date`/`--start`/`--end` as date pickers; and category
  fields autocomplete from your existing categories (kept current as you add
  entries). Commands with nothing to fill in now say "No options - just run it."
  The parser-introspection contract is unchanged — every command still generates
  its own form automatically.

## [1.57.0] - 2026-09-30

### Added

- `tagmatrix` command — a `#tag` x month spending grid (pivot table) over the
  last N months (`--months`, default 6), mirroring `matrix` for tags. Rows are
  objects (`tag` + one field per month + `total`), sorted by window total, so
  the web UI renders a clean pivot table. Supports `--json`.

## [1.56.0] - 2026-09-29

### Added

- `recur unskip ID --date` (or `--all`) — cancel a skip set with `recur skip`,
  restoring that occurrence. Undoable.
- `recur list` now shows a rule's upcoming skipped dates and picks the next
  *non-skipped* occurrence as its "next" charge.

## [1.55.0] - 2026-09-29

### Added

- `recur skip ID` — skip a recurring rule's next occurrence (or a specific one
  with `--date`), so a paused subscription cycle or a one-off holiday doesn't
  generate an expense. Skipped dates are stored on the rule; the recurring
  engine passes over them (and still advances past them, so catch-up stays
  idempotent). Undoable.

## [1.54.0] - 2026-09-29

### Added

- `where` command — prints the data folder Ledgerling owns and the paths to its
  data file, config, `exports/`, and `backups/`, each with a size (or file
  count) or "not created yet". A quick way to find your data, exports, and
  backups. Supports `--json`.

## [1.53.0] - 2026-09-29

### Added

- `refund ID` — record a refund for an expense as an offsetting income entry in
  the same category (noted `refund of #ID`), so net and savings reflect it.
  Defaults to the full expense amount; `--amount` records a partial refund and
  `--date` sets the refund date. Refusing to "refund" an income entry. Undoable.

## [1.52.0] - 2026-09-29

### Added

- `export --start DATE --end DATE` — export an arbitrary date range (either bound
  optional; reversed bounds are swapped), in addition to the existing
  `--month`. Dates accept `YYYY-MM-DD`, `today`, or `yesterday`. Passing both
  `--month` and a range bound is an error.

## [1.51.0] - 2026-09-29

### Added

- `allowance` command — how much of your budgets is left this month and, dividing
  the total remaining by the days still to come (today included), how much you
  can spend per day to stay on budget. Per-category remaining is shown too, with
  over-budget categories flagged. The forward-looking companion to `pace` (which
  projects end-of-month). Supports `--json`; the daily figure is `null` for a
  month that is already over.

## [1.50.0] - 2026-09-29

### Added

- `cumulative` command — running (cumulative) spending by day within a month: a
  burn-up curve of month-to-date spend. Series-shaped, so the web UI renders it
  as a table/chart with the metric defaulting to the cumulative line. The
  current month runs through today; a past month covers all its days. Supports
  `--json`.

## [1.49.0] - 2026-09-29

### Added

- `note ID [text]` — set, append to (`--append`), or clear (`--clear`) an entry's
  note without the full `edit` command; with no text it just prints the current
  note. Tags are re-parsed from the new note, and the change is undoable. Rounds
  out per-entry editing alongside `tag`/`untag`.

## [1.48.0] - 2026-09-29

### Added

- `unbudget CATEGORY` — removes a single category's budget (there was previously
  no way to clear a budget once set, only overwrite it). `unbudget --all` clears
  every budget. Both are undoable with `undo`.

## [1.47.0] - 2026-09-29

### Added

- `autobudget` command — sets monthly budgets for each category from recent
  average spending in one step (the actionable counterpart to `suggest`, which
  only prints them). By default it only sets categories that don't already have
  a budget; `--replace` also overwrites existing ones, `--dry-run` previews, and
  `--months` sets the averaging window (default 3). Undoable with `undo`.
  Supports `--json`.

### Changed

- `suggest` and `autobudget` share the recent-average computation
  (`_recent_category_averages`). `suggest --json` no longer includes the
  redundant `window` field.

## [1.46.0] - 2026-09-29

### Added

- `categories --month YYYY-MM` and `tags --month YYYY-MM` — both breakdowns can
  now be scoped to a single month, not just all-time. (In the month view,
  `categories` lists only categories with spend that month, omitting the
  budget-only placeholder rows; budgets still show for matched categories.)

## [1.45.0] - 2026-09-29

### Added

- `tag ID NAME...` and `untag ID NAME...` — add or remove one or more `#tags` on
  an existing entry directly, instead of rewriting the whole note with `edit`.
  `tag` appends only tags not already present; `untag` strips the `#name` tokens
  from the note and tidies whitespace. Both re-parse the entry's tags and are
  undoable. Pairs with `untagged` for cleaning up.

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

[1.88.0]: #1880---2026-09-30
[1.87.0]: #1870---2026-09-30
[1.86.0]: #1860---2026-09-30
[1.85.0]: #1850---2026-09-30
[1.84.0]: #1840---2026-09-30
[1.83.0]: #1830---2026-09-30
[1.82.0]: #1820---2026-09-30
[1.81.0]: #1810---2026-09-30
[1.80.0]: #1800---2026-09-30
[1.79.0]: #1790---2026-09-30
[1.78.0]: #1780---2026-09-30
[1.77.0]: #1770---2026-09-30
[1.76.0]: #1760---2026-09-30
[1.75.0]: #1750---2026-09-30
[1.74.0]: #1740---2026-09-30
[1.73.0]: #1730---2026-09-30
[1.72.0]: #1720---2026-09-30
[1.71.0]: #1710---2026-09-30
[1.70.0]: #1700---2026-09-30
[1.69.0]: #1690---2026-09-30
[1.68.0]: #1680---2026-09-30
[1.67.0]: #1670---2026-09-30
[1.66.0]: #1660---2026-09-30
[1.65.0]: #1650---2026-09-30
[1.64.0]: #1640---2026-09-30
[1.63.0]: #1630---2026-09-30
[1.62.0]: #1620---2026-09-30
[1.61.0]: #1610---2026-09-30
[1.60.0]: #1600---2026-09-30
[1.59.0]: #1590---2026-09-30
[1.58.0]: #1580---2026-09-30
[1.57.0]: #1570---2026-09-30
[1.56.0]: #1560---2026-09-29
[1.55.0]: #1550---2026-09-29
[1.54.0]: #1540---2026-09-29
[1.53.0]: #1530---2026-09-29
[1.52.0]: #1520---2026-09-29
[1.51.0]: #1510---2026-09-29
[1.50.0]: #1500---2026-09-29
[1.49.0]: #1490---2026-09-29
[1.48.0]: #1480---2026-09-29
[1.47.0]: #1470---2026-09-29
[1.46.0]: #1460---2026-09-29
[1.45.0]: #1450---2026-09-29
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
