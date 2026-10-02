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

### Desktop app

```bash
ledgerling gui              # open the native desktop window
ledgerling gui --theme light
```

Ledgerling ships a **native desktop application** built on Tkinter (still
standard library only — no extra dependencies). It opens a real OS window with:

- two polished **themes** — a light theme with a **green accent**, and a deep
  **green dark theme with a light (mint) accent** — toggled with an animated
  colour crossfade, plus a gently **animated header** and an animated run
  spinner so the app feels alive rather than flat;
- a **menu bar** — File (Run, open web UI, Quit), a **Commands** menu with every
  command organized into submenus, View (toggle light/dark, focus search), and
  Help;
- a **toolbar** of quick-access buttons for the common actions;
- a **sidebar** of **icon buttons** — every command, grouped (Record / Analyze /
  Budgets & goals / Calculators / Recurring / Data / Settings / Almanac), each
  with an icon, a hover animation, and a tooltip of its help — with a live
  **filter** box and a pulsing active-command highlight. The filter is
  keyboard-first: **Ctrl+K** focuses it, **Enter** opens the first match,
  **↑/↓** step through matches, and **Esc** clears it;
- a **Pinned bar** with **drag-and-drop**: drag any command from the sidebar
  onto it to pin a favourite, drag the chips to reorder, right-click to unpin;
  your pins persist between launches;
- **multiple windows** — File → New window (Ctrl+N) opens another command
  window so you can run things side by side, and "Pop out current command"
  detaches the one you're viewing; the windows share the main window's
  schema-driven form logic, so they behave identically;
- a **getting-started assistant** — a short guided tour that greets new users on
  first launch and is reopenable any time from Help → Getting started (or the
  toolbar's **? guide**);
- it **opens where you left off** — the app reopens your last-viewed command
  (new users land on today's briefing, auto-run so real numbers show right
  away), and the window title reflects the current command;
- a **schema-driven form** for the selected command (it generates itself from
  the CLI, so every current and future command appears automatically), a
  results area with an **Output** tab (text, with a Copy button) and a
  **Table** tab, and a **status bar**;
- a **sortable results table**: for any command that supports `--json`, the
  Table tab fills from its structured output — money and percent columns are
  formatted, and clicking a column header sorts by it (numeric-aware);
- it **remembers** your theme and window size/position between launches;
- **keyboard shortcuts**: `Ctrl+Enter` to run, `Ctrl+K` to focus the filter,
  `Ctrl+T` to toggle the theme, `Ctrl+Q` to quit — and Enter in any field runs
  the command.

When built as an executable (see below), this is the **double-clickable app**:
`ledgerling-gui.exe` launches straight into the window with no console.

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
sidebar groups every command (Record / Analyze / Budgets & goals / Calculators /
Recurring / Data / Settings); it still **generates itself from the CLI**, so every command —
and every command added in the future — appears automatically as a form with its
options, and running it shows the output as text, a table, a chart, or (for
`heatmap`) a calendar. Multi-value arguments (like `split`'s category/amount
pairs) are entered space-separated in one field and tokenized for you. The
selected command is reflected in the URL hash, so you can reload, bookmark, or
use the browser's back/forward buttons to return to a specific command. No data
leaves your machine.

Some commands get bespoke visualizations: `heatmap` renders a real calendar,
and `cashflow` renders a **projection chart** — a running-balance line with a
zero baseline, a start/end/lowest summary strip, and a highlighted warning if
the balance is projected to go negative.

Each command's output has a **Copy** button to grab the result (text or JSON)
in one click. Auto-rendered tables format columns by meaning — money as money,
rates and `_pct` columns as percentages — and are **sortable**: click (or focus
and press Enter/Space on) any column header to sort by it, numeric-aware, with
the direction reflected in `aria-sort` for screen readers.

It's built to be **keyboard- and screen-reader-friendly**: a skip link, labeled
landmarks, visible focus rings, fully keyboard-operable command list and group
headers (Tab / Enter / Space), form fields with associated labels and
`aria-required`/`aria-describedby`, a theme toggle that reports its state, and a
polite live region that announces command output as it updates (held quiet with
`aria-busy` while a command runs, so the finished result is announced once
rather than the interim state). A successful run is confirmed on its own polite
status line (e.g. "Summary completed — 6 lines of output"), and a command
failure is surfaced on a dedicated assertive status line so screen-reader users
hear it immediately. The
result views (Text / Table / Chart / …) are a proper ARIA tablist — arrow keys
plus Home/End move between tabs with a roving focus, and each tab is wired to its
panel. Data tables mark their header cells with `scope` (col/row) so screen
readers announce the right header for each cell. The calendar heatmap is a
labeled grid whose every day cell carries its own accessible name (e.g.
"2026-03-10: $40.00", or "no spending"), with the decorative day numbers and
weekday headers hidden from assistive tech. The whole UI honours
`prefers-reduced-motion`, dropping transitions and animations for users who ask
for less motion.

### Standalone executable

Build self-contained binaries (no Python install needed to run them):

```bash
python scripts/build_exe.py
```

With [PyInstaller](https://pyinstaller.org/) available this produces **two**
one-file executables under `dist/`:

- **`ledgerling`** (`ledgerling.exe` on Windows) — the console CLI; run any
  command, including `ledgerling gui` and `ledgerling web`.
- **`ledgerling-gui`** (`ledgerling-gui.exe`) — the **double-clickable desktop
  app**: a windowed build with no console that opens straight into the native
  UI.

Without PyInstaller it falls back to a stdlib `zipapp` (`dist/ledgerling.pyz`).
Everything is written only to `build/`/`dist/` (git-ignored); nothing is
uploaded. Your data lives in your home folder, so the executable can be moved
anywhere and keeps the same ledger.

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
owns**. It never posts, uploads, or pushes your data anywhere. Every write goes
through a guard that refuses any path outside that folder.

- Location: `~/.ledgerling` by default, or `$LEDGERLING_HOME` if you set it.
- Contents: `ledgerling_data.json`, `ledgerling_config.json`, `gui_state.json`,
  `exports/`, `backups/` — all created and owned by the app.

**One network exception:** the `weather` command, when given a place name,
makes an outbound request to [Open-Meteo](https://open-meteo.com/) (a free,
keyless, public weather API) to fetch current conditions — it sends only the
place name you type, never any ledger data, and falls back to an offline
estimate if the network is unavailable or you pass `--offline`. No other command
touches the network.

## Requirements

Python 3.7+. Version history is in [CHANGELOG.md](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).

## Usage

```bash
# Record / edit / delete expenses
ledgerling add 12.50 food "lunch burrito"
ledgerling add 45 food "groceries" --date yesterday
ledgerling config --home-code USD      # one-time: your home currency's fx code
ledgerling add 100 travel "paris" --in EUR   # entered in EUR, stored in home $
ledgerling edit 1 --amount 13.75 --note "lunch (with tip)"
ledgerling delete 2
ledgerling split 1 groceries 70 household 30   # one receipt -> two categories
ledgerling split 1 --pct groceries 60 household 40   # ...or split by percentage
ledgerling clone 1                    # duplicate entry #1 dated today
ledgerling refund 1                   # record a full refund of expense #1
ledgerling refund 1 --amount 12.50    # a partial refund

# Quick-entry templates: save presets for common expenses, then record in one
# step (distinct from recurring rules, which auto-generate on a schedule)
ledgerling template add coffee 4.50 food "flat white #treat"
ledgerling template add paycheck 3000 salary "monthly pay" --income
ledgerling template list
ledgerling template use coffee                       # records a $4.50 food entry today
ledgerling template use coffee --qty 3               # 3 coffees -> one $13.50 entry
ledgerling template use coffee --amount 5 --date yesterday  # override for one entry
ledgerling template rename coffee espresso           # rename, keeping its fields
ledgerling template remove coffee

# Record income (net + savings rate then show up in stats/report)
ledgerling income 3000 salary "march pay"
ledgerling sources                    # income broken down by source
ledgerling sources --month 2026-09    # scoped to one month

# See recent expenses (filter by category or month); * marks recurring items
ledgerling list                       # expenses only (default)
ledgerling list --income              # income only
ledgerling list --all                 # both, income marked +income
ledgerling list --category food --month 2026-09
ledgerling list --tag work                       # filter by a #tag
ledgerling list --sort amount --desc --limit 5   # your 5 biggest recent entries

# Totals by category, ASCII bar chart (defaults to this month)
ledgerling summary
ledgerling summary --month 2026-08
ledgerling summary --json             # category breakdown as JSON

# Monthly budgets
ledgerling budget --category food --amount 200
ledgerling budget                      # usage per category: spent / limit / left
ledgerling budget --month 2026-09 --json
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

# Budget breaches across your whole history: applying your current budgets to
# every month, which categories went over and by how much. (report shows only
# the latest month; pace shows only the current one.)
ledgerling overbudget
ledgerling overbudget --month 2026-09        # just one month
ledgerling overbudget --category food        # just one category

# Monthly savings goal (net vs goal shows up in goal/stats/report)
ledgerling goal --amount 500
ledgerling goal
ledgerling goal --clear

# Savings pots (sinking funds): save toward named targets with progress bars
ledgerling pot vacation --target 2000   # create/set a target
ledgerling pot vacation --by 2027-06-01 # set a target date (shows $/mo needed)
ledgerling pot vacation --target 2000 --by 2027-06-01   # both in one call
ledgerling pot vacation --add 500       # contribute
ledgerling pot vacation --take 100      # withdraw (never below zero)
ledgerling pot vacation --clear-by      # drop the target date
ledgerling pot                          # list all pots + total saved
ledgerling pot vacation --remove
ledgerling transfer 150 vacation laptop # move money between two pots

# Net worth: track manual account balances (assets and debts) and see your
# net worth, with the ledger's all-time cash position shown for context.
ledgerling networth --set checking --amount 2500
ledgerling networth --set "car loan" --amount 12000 --debt   # a liability
ledgerling networth                                          # the summary
ledgerling networth --remove "car loan"
ledgerling networth --json

# Record today's net worth to build a history, then chart it over time
ledgerling networth --snapshot      # one per day (same-day re-snapshots replace)
ledgerling worthtrend               # net worth per snapshot, with the change
ledgerling worthtrend --json

# Export to CSV or JSON (saved in exports/; a path is reduced to its file name).
# The CSV includes a `cleared` column, and both formats round-trip the
# cleared/pending reconciliation status back through `import`.
ledgerling export
ledgerling export --file august.csv --month 2026-08
ledgerling export --format json --file data.json
ledgerling export --start 2026-08-01 --end 2026-08-15   # an arbitrary range
ledgerling export --income --format json                # only income entries
ledgerling export --category rent --file rent.csv       # only one category

# Import a CSV or JSON file (looked up in exports/ then the data folder; dedupes)
ledgerling import --file august.csv
ledgerling import --file data.json              # re-import a JSON export
ledgerling import --file august.csv --dry-run   # preview counts, import nothing

# Month-over-month trend + budget adherence
ledgerling report
ledgerling report --months 12
ledgerling report --json              # trend/income/net as JSON

# Tags: add #tags in the note; they're parsed automatically
ledgerling add 40 food "client dinner #work #reimbursable"

# Search by keyword (matches note or category) with optional filters
ledgerling search coffee
ledgerling search lunch --month 2026-09
ledgerling search --tag work --month 2026-09
ledgerling search --min 50 --max 200 --category food
ledgerling search --since 2026-08-01 --until 2026-08-15   # an arbitrary range
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

# A financial-health grade (A-F) for a month, with a scored breakdown
# (savings rate / budget adherence / spending habits) and one tip
ledgerling scorecard
ledgerling scorecard --month 2026-08

# The same grade charted over the last N months (is it trending up or down?)
ledgerling scoretrend
ledgerling scoretrend --months 12

# Income, expenses, net and savings rate (all-time, or one month)
ledgerling net
ledgerling net --month 2026-09

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
ledgerling fx convert 100 USD                # omit the target -> every currency
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

# Export those upcoming charges as an iCalendar (.ics) file you can import
# into any calendar app (Google / Apple / Outlook). Each charge becomes an
# all-day event with a stable id, so re-importing updates rather than
# duplicates. The file is written inside the data folder's exports/ dir.
ledgerling upcoming --days 60 --ics            # -> exports/upcoming.ics
ledgerling upcoming --days 90 --ics bills.ics  # custom filename

# Project a running balance forward from your recurring rules (register view);
# flags if/when the balance dips below zero. Starts from your all-time net.
ledgerling cashflow --days 45
ledgerling cashflow --days 60 --start-balance 2500

# How long to reach a lump-sum savings target (uses your recent average net)
ledgerling target 10000
ledgerling target 10000 --monthly 750 --start 2500

# Runway: how long a balance lasts at your average monthly net (burn rate)
ledgerling runway
ledgerling runway --balance 8000 --monthly-net -1200

# Compound-growth / future-value calculator (pure math; not investment advice)
ledgerling interest 10000 --rate 6 --years 20
ledgerling interest 10000 --rate 6 --years 20 --monthly 200

# Loan payment / amortization estimate (monthly payment + total interest)
ledgerling loan 25000 --rate 7.5 --years 6

# Recurring rules normalized to monthly / annual cost (your fixed obligations)
ledgerling commitments

# Detect subscription-like charges from your actual spending history
# (a payee billed on a regular cadence with a stable amount), with an
# estimated monthly/annual cost -- surfaces recurring spend you never
# formalized as a recurring rule.
ledgerling subscriptions
ledgerling subscriptions --min-count 4   # require more charges before flagging

# A consolidated monthly statement (income, spending by category, budget
# adherence, largest expenses, savings rate). Prints by default; --json for the
# structured form; --save writes a shareable Markdown file to exports/.
ledgerling statement
ledgerling statement --month 2026-09
ledgerling statement --month 2026-09 --save   # -> exports/statement_2026-09.md

# Analytics: extremes, averages, and end-of-month projection
ledgerling stats

# Category / tag overviews (all-time, or scoped to a month)
ledgerling categories
ledgerling categories --month 2026-09
ledgerling category food               # drill into one category: total, share,
                                       # avg/median, min/max, span, monthly trend
ledgerling category food --months 12   # longer trend window
ledgerling tags
ledgerling tags --month 2026-09
ledgerling recategorize food dining

# Rank spending by payee/merchant (the note, #tags stripped; category when the
# note is blank) with count, total, average and first/last seen -- the merchant
# complement to the category-based `categories`/`top`.
ledgerling payees
ledgerling payees --month 2026-09 --limit 10

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

# Daily briefing: this month so far, what's due in the next few days, any
# budgets already over, and a fortune for the day (ties the fun modes to real
# data). --days sets the look-ahead window (default 7).
ledgerling today
ledgerling today --days 14 --json

# Version
ledgerling version
ledgerling --version

# Where does my data live? (folder + files with sizes)
ledgerling where

# Scan your data for integrity problems (duplicate ids, bad dates, orphans,
# invalid budgets, malformed accounts / net-worth snapshots, ...)
ledgerling check          # in the web UI this renders as a grouped Health panel
ledgerling check --fix    # repair the safe ones (undoable); leaves judgment calls

# Settings (currency symbol, default list size)
ledgerling config
ledgerling config --currency "€" --list-limit 50
ledgerling config --currency kr --symbol-position after   # -> 12.50 kr
# Negative amounts print the sign first: -$5.00 (or -5.00 kr), matching the web UI
ledgerling config --reset

# Reconcile against your bank: mark entries cleared (posted) vs pending, then
# see the cleared balance, what's still outstanding, and the projected total.
ledgerling clear 12 13 14      # mark these entry ids cleared
ledgerling unclear 13          # put one back to pending
ledgerling reconcile
ledgerling reconcile --json
ledgerling list --pending      # find what still needs clearing (✓ marks cleared)
ledgerling search rent --cleared   # filters also work on search

# Backup / restore (all copies live in backups/)
ledgerling backup
ledgerling backup --list
ledgerling restore --file ledgerling_data_20260929_173016.json

# Recurring expenses (rent, subscriptions, ...) — or recurring income
ledgerling recur add 1200 rent "apartment" --every month --start 2026-08-01
ledgerling recur add 15 subscriptions "music" --every month
ledgerling recur add 3000 salary "paycheck" --every month --income
ledgerling recur add 450 loan "car" --every month --until 2027-06-30  # fixed term
ledgerling recur add 450 loan "car" --every month --count 12          # 12 payments
ledgerling recur from 5 --every month   # turn entry #5 into a recurring rule
ledgerling recur list
ledgerling recur list --json          # structured rows (id, next, until, status)
ledgerling recur edit 1 --amount 1350 --note "rent increase"
ledgerling recur edit 1 --until 2027-01-31   # add/change an end date
ledgerling recur edit 1 --no-until           # make it open-ended again
ledgerling recur edit 1 --count 24           # or cap by number of occurrences
ledgerling recur edit 1 --no-count           # remove the count cap
ledgerling recur remove 1
ledgerling recur run
ledgerling recur skip 1               # skip the next occurrence (e.g. paused)
ledgerling recur skip 1 --date 2026-12-01
ledgerling recur unskip 1 --date 2026-12-01   # cancel that skip
ledgerling recur unskip 1 --all               # clear all skips on the rule
ledgerling recur pause 1              # stop a rule until resumed
ledgerling recur resume 1             # resume (no backfill of the paused gap)

# Almanac — daily companion readings. The fortune/horoscope/eightball modes are
# self-contained (no network, no stored data touched) and deterministic given
# --seed; the daily ones otherwise vary by date.
ledgerling fortune                    # a daily fortune + lucky numbers
ledgerling horoscope leo              # finance-flavoured daily horoscope
ledgerling eightball "will I save money this month"   # yes/no decision helper
ledgerling fortune --seed 42 --json   # reproducible; all support --json

# weather is the one command that may use the network: with a place name it
# fetches live current conditions from Open-Meteo (free, no API key); with
# --offline (or no place) it gives a local deterministic estimate instead.
ledgerling weather --where "Dublin"
ledgerling weather --where "Tokyo" --json
ledgerling weather --offline          # never touches the network
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
changing the recurring catch-up behaviour. A second guard checks every
top-level command is listed in the module's `--help` command summary, so the
docs can't silently drift from the parser either. A third runs every
no-argument command against an empty store, so adding a command that crashes on
empty data (division by zero, `max()` of nothing, ...) fails the suite. A fourth
checks every read command offers `--json` (so it works for scripting and the
web UI), with `export` exempt since it writes files via `--format`. A fifth
checks every command is placed in a web-sidebar group, so none silently lands
in the catch-all "More".

The recurring-occurrence generator takes an optional `since` lower bound so
projections and catch-up don't iterate over years of history for long-running
daily rules; a brute-force equivalence test guards that the fast path returns
exactly the same dates as a full scan. Bulk inserts (recurring catch-up, CSV
import) assign ids from a running counter instead of re-scanning the list per
row, so materializing thousands of entries stays linear.

Shared derived values and small routines live in single helpers rather than
being re-implemented per command — e.g. the all-time net that `cashflow`,
`target`, and `runway` use as a default balance, a category's spend in a month
(`budget`/`pace`/`allowance`/`report`), the date-range bounds for
`export`/`search`, the `--in` currency conversion for `add`/`income`, the
per-period income/spending accumulation that `savings` and `years` share, the
optional `--month` row filter that a dozen read commands apply, the
month income/spending/net totals that `compare`, `today`, and `statement` share,
the category-totals accumulation (`{category: total}` with running 2dp rounding)
that `summary`, `insights`, `scorecard`, and `range` share,
and the formatted entry line (`#id date amount [category] …` with its markers)
that `list` and `search` share.

Config loading is defensive: a stored setting is only accepted when its type
matches the default (a corrupt or hand-edited value falls back to that default
instead of crashing a later command), and mutable defaults are deep-copied so
they can't be aliased and changed process-wide. Data loading is defensive the
same way: every container section (expenses, budgets, recurring, accounts,
net-worth history, pots, templates) is declared in one table and, on load, filled if
missing and reset to an empty container if its stored value is the wrong type —
so a corrupt file can't crash a command, and bad *contents* are surfaced by
`check`.

## How things behave

- **Recurring rules** auto-catch-up: whenever you `add`, `list`, `summary`,
  `budget`, or `export`, any occurrences due up to today are generated
  automatically (idempotent — never duplicated). `recur run` forces it.
- Removing a recurring rule keeps the expenses it already created.
- A recurring rule can stop on its own by date (`--until`) or after a fixed
  number of occurrences (`--count`, e.g. 12 loan payments); `recur list` marks a
  finished rule `ENDED`. Already-generated expenses are left as-is. A skipped
  occurrence still counts toward `--count` (the rule fires N times; a skipped
  one just isn't recorded).
- `upcoming` and `cashflow` only forecast charges that will actually happen —
  paused rules and skipped dates are excluded.
- **Undo** reverts the last change to your data (add/edit/delete/split/import/
  budget/recategorize/restore/`check --fix`/recurring catch-up). Running `undo` again redoes it — it's
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
