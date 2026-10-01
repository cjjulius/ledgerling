# Ledgerling

A tiny personal expense tracker CLI. **No dependencies** — just the Python
standard library.

## Install

With [pipx](https://pipx.pypa.io/) (recommended — isolated, on your PATH):

```bash
pipx install .
```

Or with pip into a virtualenv:

```bash
pip install .
```

Both give you a `ledgerling` command. Without installing, you can also run it
from a checkout with `python -m ledgerling` (add `src/` to `PYTHONPATH`).

### Web UI

```bash
ledgerling web              # opens a local UI at http://127.0.0.1:8730
ledgerling web --port 9000 --no-browser
```

The UI is a local, sandboxed web app (stdlib only; binds to `127.0.0.1`). It
opens on a **Dashboard** — this month's income, spending, net, an insights
strip, top categories, budget progress, savings-goal status, an **Upcoming
(30 days)** card of scheduled recurring items (click any row to open the
cash-flow projection), and recent activity at a glance, with a month picker. The
sidebar groups every command (Record / Analyze / Budgets & goals / Recurring /
Data / Settings); it still **generates itself from the CLI**, so every command —
and every command added in the future — appears automatically as a form with its
options, and running it shows the output as text, a table, a chart, or (for
`heatmap`) a calendar. Multi-value arguments (like `split`'s category/amount
pairs) are entered space-separated in one field and tokenized for you. No data
leaves your machine.

Some commands get bespoke visualizations: `heatmap` renders a real calendar,
and `cashflow` renders a **projection chart** — a running-balance line with a
zero baseline, a start/end/lowest summary strip, and a highlighted warning if
the balance is projected to go negative.

It's built to be **keyboard- and screen-reader-friendly**: a skip link, labeled
landmarks, visible focus rings, fully keyboard-operable command list and group
headers (Tab / Enter / Space), form fields with associated labels and
`aria-required`/`aria-describedby`, a theme toggle that reports its state, and a
polite live region that announces command output as it updates.

### Shell completion (optional)

```bash
# bash
ledgerling completion bash > ~/.local/share/bash-completion/completions/ledgerling
# zsh (drop into a directory on your $fpath)
ledgerling completion zsh > "${fpath[1]}/_ledgerling"
```

Then start a new shell and press Tab after `ledgerling`.

## Sandbox rule

Ledgerling only ever reads and writes files **inside one data folder that it
owns**. It never posts, uploads, or pushes anything anywhere. Every write goes
through a guard that refuses any path outside that folder.

- Location: `~/.ledgerling` by default, or `$LEDGERLING_HOME` if you set it.
- Contents: `ledgerling_data.json`, `ledgerling_config.json`, `exports/`,
  `backups/` — all created and owned by the app.

## Requirements

Python 3.7+. Version history is in [CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).

## Usage

```bash
# Record / edit / delete expenses
ledgerling add 12.50 food "lunch burrito"
ledgerling add 45 food "groceries" --date yesterday
ledgerling edit 1 --amount 13.75 --note "lunch (with tip)"
ledgerling delete 2
ledgerling split 1 groceries 70 household 30   # one receipt -> two categories
ledgerling clone 1                    # duplicate entry #1 dated today
ledgerling refund 1                   # record a full refund of expense #1
ledgerling refund 1 --amount 12.50    # a partial refund

# Record income (net + savings rate then show up in stats/report)
ledgerling income 3000 salary "march pay"
ledgerling sources                    # income broken down by source
ledgerling sources --month 2026-09    # scoped to one month

# See recent expenses (filter by category or month); * marks recurring items
ledgerling list                       # expenses only (default)
ledgerling list --income              # income only
ledgerling list --all                 # both, income marked +income
ledgerling list --category food --month 2026-09

# Totals by category, ASCII bar chart (defaults to this month)
ledgerling summary
ledgerling summary --month 2026-08

# Monthly budgets
ledgerling budget --category food --amount 200
ledgerling budget
ledgerling unbudget food               # remove one budget (undoable)
ledgerling unbudget --all              # clear every budget

# Suggest budgets from recent average spending (last 3 months by default)
ledgerling suggest
ledgerling suggest --months 6

# Apply those suggestions as actual budgets in one step (undoable)
ledgerling autobudget --dry-run       # preview
ledgerling autobudget                 # set budgets for categories without one
ledgerling autobudget --replace       # also overwrite existing budgets

# Budget pace: are you ahead or behind, and projected end-of-month?
ledgerling pace

# Budget allowance: how much you can still spend per day to stay on budget
ledgerling allowance

# Monthly savings goal (net vs goal shows up in goal/stats/report)
ledgerling goal --amount 500
ledgerling goal
ledgerling goal --clear

# Export to CSV or JSON (saved in exports/; a path is reduced to its file name)
ledgerling export
ledgerling export --file august.csv --month 2026-08
ledgerling export --format json --file data.json
ledgerling export --start 2026-08-01 --end 2026-08-15   # an arbitrary range

# Import a CSV (looked up in exports/ then the data folder; dedupes automatically)
ledgerling import --file august.csv
ledgerling import --file august.csv --dry-run   # preview counts, import nothing

# Month-over-month trend + budget adherence
ledgerling report
ledgerling report --months 12

# Tags: add #tags in the note; they're parsed automatically
ledgerling add 40 food "client dinner #work #reimbursable"

# Search by keyword (matches note or category) with optional filters
ledgerling search coffee
ledgerling search lunch --month 2026-09
ledgerling search --tag work --month 2026-09
ledgerling search --min 50 --max 200 --category food
ledgerling search coffee --sort amount --desc   # largest matches first

# Entries for a single day (today by default)
ledgerling day
ledgerling day --date yesterday

# This week's spending by day (Mon-Sun); --offset N for weeks back
ledgerling week
ledgerling week --offset 1

# Weekly spending trend over the last N weeks
ledgerling weekly --weeks 8

# No-spend-day streaks for a month
ledgerling streak
ledgerling streak --month 2026-08

# Spending by day of week (which days you spend most)
ledgerling weekday
ledgerling weekday --month 2026-09

# Daily-spending calendar for a month (ASCII grid; a real calendar in the web UI)
ledgerling heatmap
ledgerling heatmap --month 2026-08

# Cumulative (running) spending by day within a month
ledgerling cumulative
ledgerling cumulative --month 2026-08

# One-screen dashboard for a month (income, spend, net, top cats, budgets, goal)
ledgerling month
ledgerling month --month 2026-08

# Plain-language insights about a month (savings, top category, vs last month...)
ledgerling insights
ledgerling insights --month 2026-08

# Totals over an arbitrary date range (end defaults to today)
ledgerling range 2026-08-01 2026-08-15
ledgerling range 2026-09-01            # 2026-09-01 through today

# Quarterly and calendar-year rollups, and a year-end forecast
ledgerling quarter 2026
ledgerling year
ledgerling year 2026
ledgerling years                      # every year, side by side
ledgerling forecast

# Running cumulative net (income - spending) month over month
ledgerling balance

# Monthly savings rate (net / income) trend
ledgerling savings

# Compare two months side by side (defaults to last month vs this month)
ledgerling compare
ledgerling compare 2026-08 2026-09

# Your largest expenses (optionally by month/category)
ledgerling top --limit 10
ledgerling top --month 2026-09 --category food
ledgerling top --income                # your largest income entries
ledgerling top --all                   # largest across expenses and income

# Average spending per day / week / month across your records
ledgerling average

# Histogram of expense sizes ($0-10, $10-25, ... $250+)
ledgerling distribution

# Flag unusually large expenses within each category (statistical outliers)
ledgerling anomalies                        # > 2 SD above the category mean
ledgerling anomalies --z 1.5 --month 2026-09
ledgerling anomalies --category groceries --min-count 6

# Simulate round-up savings (how much you'd set aside rounding each expense up)
ledgerling roundup                          # to the nearest $1.00
ledgerling roundup --to 5 --month 2026-09   # to the nearest $5.00

# Tip calculator and even bill splitter (pure math; touches no stored data)
ledgerling tip 84.50 --pct 20               # tip + total
ledgerling tip 100 --pct 18 --split 3       # split evenly; cents always sum back

# Offline currency converter with your own rates (no network; stays sandboxed)
ledgerling fx set USD 1                      # pick a reference, then set others
ledgerling fx set EUR 1.09                   # 1 EUR = 1.09 reference units
ledgerling fx list
ledgerling fx convert 100 EUR USD            # -> 109.00 USD
ledgerling fx rm EUR

# Monthly spending trend for one category
ledgerling trend food --months 6

# Monthly spending trend for one #tag (spans categories)
ledgerling tagtrend work --months 6

# Category x month spending grid (pivot table)
ledgerling matrix --months 6

# #tag x month spending grid (pivot table)
ledgerling tagmatrix --months 6

# Forecast recurring charges/income coming up (default 30 days)
ledgerling upcoming
ledgerling upcoming --days 60

# Project a running balance forward from your recurring rules (register view);
# flags if/when the balance dips below zero. Starts from your all-time net.
ledgerling cashflow --days 45
ledgerling cashflow --days 60 --start-balance 2500

# How long to reach a lump-sum savings target (uses your recent average net)
ledgerling target 10000
ledgerling target 10000 --monthly 750 --start 2500

# Recurring rules normalized to monthly / annual cost (your fixed obligations)
ledgerling commitments

# Analytics: extremes, averages, and end-of-month projection
ledgerling stats

# Category / tag overviews (all-time, or scoped to a month)
ledgerling categories
ledgerling categories --month 2026-09
ledgerling tags
ledgerling tags --month 2026-09
ledgerling recategorize food dining

# Rename a #tag everywhere; find expenses that still need tags
ledgerling retag work business
ledgerling untagged

# Add or remove #tags on a single entry (without rewriting the note)
ledgerling tag 1 work reimbursable
ledgerling untag 1 reimbursable

# Set, append to, or clear an entry's note (tags re-parsed)
ledgerling note 1 "team lunch #work"
ledgerling note 1 "#reimbursable" --append
ledgerling note 1 --clear

# Find likely double-entered records (same date/amount/category/note)
ledgerling duplicates

# Remove those duplicates (keeps one per group; preview with --dry-run, undoable)
ledgerling dedupe --dry-run
ledgerling dedupe

# Undo the last data change (run it again to redo)
ledgerling undo

# Machine-readable output for piping into other local tools
ledgerling list --json
ledgerling search --tag work --json
ledgerling stats --json

# Version
ledgerling version
ledgerling --version

# Where does my data live? (folder + files with sizes)
ledgerling where

# Settings (currency symbol, default list size)
ledgerling config
ledgerling config --currency "€" --list-limit 50
ledgerling config --currency kr --symbol-position after   # -> 12.50 kr
ledgerling config --reset

# Backup / restore (all copies live in backups/)
ledgerling backup
ledgerling backup --list
ledgerling restore --file ledgerling_data_20260929_173016.json

# Recurring expenses (rent, subscriptions, ...) — or recurring income
ledgerling recur add 1200 rent "apartment" --every month --start 2026-08-01
ledgerling recur add 15 subscriptions "music" --every month
ledgerling recur add 3000 salary "paycheck" --every month --income
ledgerling recur add 450 loan "car" --every month --until 2027-06-30  # fixed term
ledgerling recur from 5 --every month   # turn entry #5 into a recurring rule
ledgerling recur list
ledgerling recur list --json          # structured rows (id, next, until, status)
ledgerling recur edit 1 --amount 1350 --note "rent increase"
ledgerling recur edit 1 --until 2027-01-31   # add/change an end date
ledgerling recur edit 1 --no-until           # make it open-ended again
ledgerling recur remove 1
ledgerling recur run
ledgerling recur skip 1               # skip the next occurrence (e.g. paused)
ledgerling recur skip 1 --date 2026-12-01
ledgerling recur unskip 1 --date 2026-12-01   # cancel that skip
ledgerling recur unskip 1 --all               # clear all skips on the rule
ledgerling recur pause 1              # stop a rule until resumed
ledgerling recur resume 1             # resume (no backfill of the paused gap)
```

## Tests

A small stdlib `unittest` suite lives in `tests/`. File-touching tests redirect
the app's paths to a temp dir, so your real data is never involved.

```bash
python -m unittest discover -s tests -v
```

Continuous integration ([`.github/workflows/ci.yml`](.github/workflows/ci.yml))
runs the suite and a CLI install smoke-test on Linux and Windows across
Python 3.9 / 3.11 / 3.13. It activates automatically once the project is pushed
to GitHub.

Every top-level command is classified in `cli.py` as either a read command
(`CATCHUP_COMMANDS`) or a mutating/meta one (`MUTATING_COMMANDS`), and a guard
test asserts the two sets together cover exactly the parser's commands — so
adding a new command without classifying it fails the suite rather than quietly
changing the recurring catch-up behaviour.

The recurring-occurrence generator takes an optional `since` lower bound so
projections and catch-up don't iterate over years of history for long-running
daily rules; a brute-force equivalence test guards that the fast path returns
exactly the same dates as a full scan. Bulk inserts (recurring catch-up, CSV
import) assign ids from a running counter instead of re-scanning the list per
row, so materializing thousands of entries stays linear.

Config loading is defensive: a stored setting is only accepted when its type
matches the default (a corrupt or hand-edited value falls back to that default
instead of crashing a later command), and mutable defaults are deep-copied so
they can't be aliased and changed process-wide.

## How things behave

- **Recurring rules** auto-catch-up: whenever you `add`, `list`, `summary`,
  `budget`, or `export`, any occurrences due up to today are generated
  automatically (idempotent — never duplicated). `recur run` forces it.
- Removing a recurring rule keeps the expenses it already created.
- A recurring rule with an `--until` end date stops generating after it; `recur
  list` marks such a rule `ENDED`. Already-generated expenses are left as-is.
- **Undo** reverts the last change to your data (add/edit/delete/split/import/
  budget/recategorize/restore/recurring catch-up). Running `undo` again redoes it — it's
  a one-step toggle, stored in the data folder.
- **Recategorize** renames a category across expenses, recurring rules, and the
  budget; if both the old and new categories already have budgets, the new one's
  is kept.
- **Savings goal** is a single monthly target (`goal --amount`). `goal` shows
  this month's net against it with a progress bar; `stats` adds a goal block for
  the current month, and `report` compares net to the cumulative target
  (goal x months in the window).
- **Income** is tracked as a separate entry kind. Spending views (`list`,
  `summary`, `budget`, `categories`) show expenses only by default; `stats` and
  `report` add income, net (income − spending), and a savings rate. Use
  `list`/`search --income` or `--all` to include it, and it round-trips through
  CSV via a `kind` column.
- Adding to a category with a budget shows how much of the month is left / over.
- **Tags** are any `#word` in a note. They're parsed and stored automatically on
  `add`, `edit`, `import`, and recurring generation. Filter with `search --tag`
  and see a per-tag breakdown in `stats`. A tag can span categories (e.g. `#work`
  across food, transit, and travel).
- **Import** dedupes on `(date, amount, category, note)`, so re-importing an
  export is safe; it tolerates a UTF-8 BOM and skips malformed rows, reporting
  added / duplicate / malformed counts. It only reads files from the data folder.
- Dates accept `YYYY-MM-DD`, `today`, or `yesterday`.
- **Restore** validates the backup first and auto-saves your current data to a
  `*_prerestore.json` backup before overwriting, so a restore is always undoable.
- **`--json`** (on `list`, `search`, `stats`) prints clean UTF-8 JSON to stdout
  for piping into other local tools — `list`/`search` emit an array of expenses,
  `stats` a structured object. Note: Windows PowerShell's `|` prepends a UTF-8
  BOM that can break strict JSON parsers; redirect to a file, or pipe from bash/
  cmd, if you hit that.
- **Settings** live in `ledgerling_config.json`: `currency` (used everywhere
  amounts print), `symbol_position` (`before` → `$12.50`, or `after` → `12.50 kr`),
  and `list_limit` (the default `list` size, overridable with `--limit`). A
  missing or corrupted config safely falls back to defaults.
- Writes are atomic, so an interrupted run won't corrupt your data file.
- Delete the data folder (`~/.ledgerling`, or `$LEDGERLING_HOME`) to start over.
