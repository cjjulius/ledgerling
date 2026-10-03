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

- three **themes** — light, dark, and a high-contrast theme for low-vision
  readability — cycled with Ctrl+T through a short colour crossfade;
- a **menu bar** — File (Run, open web UI, Quit), a **Commands** menu with every
  command organized into submenus, View (toggle light/dark, focus search), and
  Help;
- a **toolbar** of quick-access buttons for the common actions;
- a **sidebar** listing every command, grouped (Record / Analyze /
  Budgets & goals / Calculators / Recurring / Data / Settings / Almanac), with a
  clear accent marker on the active one and a tooltip of each command's help,
  plus a live **filter** box. The filter is keyboard-first: **Ctrl+K** focuses
  it, **Enter** opens the first match, **↑/↓** step through matches, and **Esc**
  clears it;
- a **Pinned bar** with **drag-and-drop**: drag any command from the sidebar
  onto it to pin a favourite, drag the chips to reorder, right-click to unpin;
  your pins persist between launches;
- **multiple windows** — File → New window (Ctrl+N) opens another command
  window so you can run things side by side, and "Pop out current command"
  detaches the one you're viewing; the windows share the main window's
  schema-driven form logic, so they behave identically;
- a **getting-started assistant** — a short guided tour that greets new users on
  first launch and is reopenable any time from Help → Getting started (or the
  toolbar's **Guide** button);
- it **opens where you left off** — the app reopens your last-viewed command
  (new users land on today's briefing, auto-run so real numbers show right
  away), and the window title reflects the current command;
- a **schema-driven form** for the selected command (it generates itself from
  the CLI, so every current and future command appears automatically), a
  results area with an **Output** tab (text, with a Copy button) and a
  **Table** tab, and a **status bar** whose right side always shows an
  at-a-glance summary of the current month (spent, net, entry count) that
  refreshes after every command;
- a **sortable results table**: for any command that supports `--json`, the
  Table tab fills from its structured output — money and percent columns are
  formatted, and clicking a column header sorts by it (numeric-aware);
- it **remembers** your theme and window size/position between launches;
- **keyboard shortcuts**: `Ctrl+Enter` to run, `Ctrl+K` to focus the filter,
  `Ctrl+T` to cycle the theme, `Ctrl+Q` to quit — and Enter in any field runs
  the command;
- **adjustable text size** for readability: set a scale with
  `ledgerling config --ui-scale 1.25` (anywhere from 0.5 to 3.0) and the app
  sizes all of its text to match on the next launch.

When built as an executable (see below), this is the **double-clickable app**:
`ledgerling-gui.exe` launches straight into the window with no console.

### Web UI (deprecated, maintenance mode)

The web UI is **deprecated**. It stays stable and will keep working, but it no
longer receives new features. Our users prefer the desktop app, so that is now
the recommended interface; reach for `ledgerling gui` for day-to-day use.

```bash
ledgerling web              # opens a local UI at http://127.0.0.1:8730
ledgerling web --port 9000 --no-browser
```

It remains a local, sandboxed web app (standard library only, binds to
`127.0.0.1`, nothing leaves your machine). Because it generates itself from the
CLI, new commands still appear in it automatically, but that automatic coverage
is the extent of its upkeep. It still offers a dashboard, forms for every
command, sortable tables, a few charts, and solid keyboard and screen-reader
support.

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

## Using Ledgerling

New here? These four cover most days:

```bash
ledgerling add 12.50 food "lunch #work"   # record a spend (notes can carry #tags)
ledgerling month                          # this month at a glance
ledgerling budget --category food --amount 400   # set a monthly budget
ledgerling list                           # recent entries
```

Everything else is discoverable without hunting through this file. Run
`ledgerling` on its own for the full command list, and `ledgerling <command>
--help` for a command's options. Prefer not to type? `ledgerling gui` opens the
desktop app and `ledgerling web` opens the browser version, and both expose
every command.

### What it can do

Commands are grouped by theme (the same groups the desktop and web interfaces
use):

- **Record** your money: `add`, `income`, `edit`, `delete`, `split`, `clone`,
  `refund`, and reusable quick-entry `template`s.
- **Analyze** it: a month `dashboard`, plain-language `insights`, an A-to-F
  health `scorecard` and its `scoretrend`, per-`category` and per-`tag`
  profiles, an `onthisday` flashback, your highest-spending days (`topdays`), a
  printable `receipt` for any day or entry, a playful `persona` read of your
  habits, `search`, and rollups by week, month, quarter, year, and weekday.
- **Budgets and goals**: monthly `budget`s with `pace` and daily `allowance`, a
  savings `goal`, `networth` tracking, savings `pot`s (sinking funds) with
  optional target dates and a combined `savingsplan`, gamified savings
  `challenge`s (52-week, no-spend, round-up jar), and `achievements` badges
  you unlock from your history.
- **Recurring and bills**: define `recur`ring charges or income, see what is due
  soon (`upcoming`) or scheduled across a month (`bills`), and export them to a
  calendar file.
- **Calculators**: `tip` splitting, `loan` and compound-`interest` estimates,
  a `fire` (financial-independence) number and the `rule72` doubling-time rule,
  a `lattefactor` habit-cost projector, an `inflation` adjuster for comparing
  money across years, a `words` helper that spells an amount out as on a cheque,
  a `countdown` to any date or a savings pot's target, `roundup` savings, a
  `target`-date planner, `runway`, and an offline currency converter (`fx`).
- **Data**: `export` and `import` CSV or JSON (export filters by
  month/range/category/kind/amount), `backup` and `restore`,
  `reconcile` against your bank, find and remove `duplicates`, and `undo` the
  last change. `check` scans your data for problems and repairs the safe ones
  with `check --fix`.

Most read commands accept `--json` (handy for piping into other local tools),
and most accept `--month YYYY-MM` to scope to a single month.

### The almanac

A few light touches round out the app: a `fortune`, a `horoscope`, an
`eightball`, local `weather`, and `mascot` - a small ASCII companion whose mood
tracks your month's health. The readings are self-contained and deterministic
given a `--seed`. `weather` is the only command that may reach the network (the
free, keyless Open-Meteo service), and only when you pass a place name;
`--offline` keeps it fully local.

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
the drill-down summary stats (total, share, count, average/median, min/max,
span) that `category` and `tags NAME` share,
the common entry filters (kind scope, category, tag, month, cleared/pending)
that `list` and `search` share, the date-range and amount-bound filters
(`--since`/`--until`/`--min`/`--max`) that `list`, `search`, and `top` share,
the formatted entry line (`#id date amount [category] …` with its markers)
that `list` and `search` share,
and the current-month value (`YYYY-MM`) that the many commands defaulting to
this month share.

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
