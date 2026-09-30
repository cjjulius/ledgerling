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
**generates itself from the CLI**, so every command — and every command added
in the future — appears automatically as a form with its options, and running
it shows the command's output. No data leaves your machine.

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
ledgerling clone 1                    # duplicate entry #1 dated today

# Record income (net + savings rate then show up in stats/report)
ledgerling income 3000 salary "march pay"
ledgerling sources                    # income broken down by source

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

# Budget pace: are you ahead or behind, and projected end-of-month?
ledgerling pace

# Monthly savings goal (net vs goal shows up in goal/stats/report)
ledgerling goal --amount 500
ledgerling goal
ledgerling goal --clear

# Export to CSV or JSON (saved in exports/; a path is reduced to its file name)
ledgerling export
ledgerling export --file august.csv --month 2026-08
ledgerling export --format json --file data.json

# Import a CSV (looked up in exports/ then the data folder; dedupes automatically)
ledgerling import --file august.csv

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

# Entries for a single day (today by default)
ledgerling day
ledgerling day --date yesterday

# This week's spending by day (Mon-Sun); --offset N for weeks back
ledgerling week
ledgerling week --offset 1

# No-spend-day streaks for a month
ledgerling streak
ledgerling streak --month 2026-08

# Spending by day of week (which days you spend most)
ledgerling weekday
ledgerling weekday --month 2026-09

# One-screen dashboard for a month (income, spend, net, top cats, budgets, goal)
ledgerling month
ledgerling month --month 2026-08

# Quarterly and calendar-year rollups, and a year-end forecast
ledgerling quarter 2026
ledgerling year
ledgerling year 2026
ledgerling forecast

# Compare two months side by side (defaults to last month vs this month)
ledgerling compare
ledgerling compare 2026-08 2026-09

# Your largest expenses (optionally by month/category)
ledgerling top --limit 10
ledgerling top --month 2026-09 --category food

# Average spending per day / week / month across your records
ledgerling average

# Histogram of expense sizes ($0-10, $10-25, ... $250+)
ledgerling distribution

# Monthly spending trend for one category
ledgerling trend food --months 6

# Forecast recurring charges/income coming up (default 30 days)
ledgerling upcoming
ledgerling upcoming --days 60

# Analytics: extremes, averages, and end-of-month projection
ledgerling stats

# Category / tag overviews, and bulk-rename a category everywhere
ledgerling categories
ledgerling tags
ledgerling recategorize food dining

# Rename a #tag everywhere; find expenses that still need tags
ledgerling retag work business
ledgerling untagged

# Find likely double-entered records (same date/amount/category/note)
ledgerling duplicates

# Undo the last data change (run it again to redo)
ledgerling undo

# Machine-readable output for piping into other local tools
ledgerling list --json
ledgerling search --tag work --json
ledgerling stats --json

# Version
ledgerling version
ledgerling --version

# Settings (currency symbol, default list size)
ledgerling config
ledgerling config --currency "€" --list-limit 50
ledgerling config --reset

# Backup / restore (all copies live in backups/)
ledgerling backup
ledgerling backup --list
ledgerling restore --file ledgerling_data_20260929_173016.json

# Recurring expenses (rent, subscriptions, ...) — or recurring income
ledgerling recur add 1200 rent "apartment" --every month --start 2026-08-01
ledgerling recur add 15 subscriptions "music" --every month
ledgerling recur add 3000 salary "paycheck" --every month --income
ledgerling recur list
ledgerling recur edit 1 --amount 1350 --note "rent increase"
ledgerling recur remove 1
ledgerling recur run
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

## How things behave

- **Recurring rules** auto-catch-up: whenever you `add`, `list`, `summary`,
  `budget`, or `export`, any occurrences due up to today are generated
  automatically (idempotent — never duplicated). `recur run` forces it.
- Removing a recurring rule keeps the expenses it already created.
- **Undo** reverts the last change to your data (add/edit/delete/import/budget/
  recategorize/restore/recurring catch-up). Running `undo` again redoes it — it's
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
  amounts print) and `list_limit` (the default `list` size, overridable with
  `--limit`). A missing or corrupted config safely falls back to defaults.
- Writes are atomic, so an interrupted run won't corrupt your data file.
- Delete the data folder (`~/.ledgerling`, or `$LEDGERLING_HOME`) to start over.
