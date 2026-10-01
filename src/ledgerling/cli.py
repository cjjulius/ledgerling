#!/usr/bin/env python3
"""Ledgerling - a tiny, zero-dependency personal expense tracker.

One file, standard library only.

SANDBOX RULE (hard constraint)
------------------------------
Ledgerling only ever reads and writes files INSIDE one data folder that it owns
(`~/.ledgerling` by default, or $LEDGERLING_HOME if set). It never posts,
uploads, or pushes anything anywhere. Reading external, read-only inputs (CPU
usage, the web, ...) would be fine, but the app is not allowed to alter anything
outside the files it owns. Every write goes through `_within_home()`, which
refuses any path that would escape the data folder.

Data lives in `<data folder>/ledgerling_data.json`; exports go to
`<data folder>/exports/`.

Commands:
    add       Record an expense (supports #tags in the note)
    income    Record an income entry
    sources   Income broken down by source
    list      Show recent expenses (with optional filters)
    edit      Change fields on an existing expense
    delete    Remove an expense by id
    clone     Duplicate an entry (defaults to today's date)
    refund    Record a refund for an expense (as offsetting income)
    search    Find expenses by keyword, #tag, category, month, or amount range
    summary   Totals by category with an ASCII bar chart
    report    Month-over-month trend and budget adherence
    stats     Analytics: extremes, averages, per-tag totals, projection
    day       Entries for a single day (today by default)
    week      This week's spending by day (Mon-Sun), income and net
    weekly    Weekly spending trend over the last N weeks
    streak    No-spend-day streaks for a month
    weekday   Spending by day of week (which days you spend most)
    heatmap   Daily-spending calendar for a month (with a web calendar view)
    cumulative  Cumulative spending by day within a month
    month     One-screen dashboard for a month (income, spend, net, budgets)
    insights  Plain-language observations about a month
    range     Totals over an arbitrary date range (start [end])
    forecast  Project this year's spending/income/net to year-end
    quarter   Quarterly rollup (Q1-Q4) for a year
    balance   Running cumulative net (income - spending) month over month
    savings   Monthly savings rate (net / income) trend
    year      Calendar-year rollup by month (spending, income, net)
    years     Multi-year rollup (spending, income, net per year)
    compare   Compare two months side by side (with per-category deltas)
    trend     Monthly spending trend for one category
    tagtrend  Monthly spending trend for one #tag
    matrix    Category x month spending grid (pivot table)
    tagmatrix  #tag x month spending grid (pivot table)
    top       List your largest expenses (optionally by month/category)
    average   Average spending per day / week / month
    distribution  Histogram of expense sizes
    anomalies  Flag unusually large expenses within each category
    roundup   Simulate round-up savings (round each expense up to $N)
    upcoming  Forecast recurring charges/income due in the next N days
    commitments  Recurring rules normalized to monthly/annual cost
    suggest   Suggest per-category budgets from recent average spending
    autobudget  Apply suggested budgets from recent spending (undoable)
    categories  List categories with counts and totals
    tags      List #tags with counts and totals
    untagged  List expenses that have no #tags
    recategorize  Rename a category across all records
    retag     Rename a #tag across all records
    tag       Add #tag(s) to an existing entry
    untag     Remove #tag(s) from an existing entry
    note      Set, append to, or clear an entry's note
    duplicates  Find likely double-entered records
    dedupe    Remove duplicate entries (keeps one per group; undoable)
    undo      Revert the last data change (toggles redo)
    budget    Set / view monthly budgets
    unbudget  Remove a category's budget (or --all)
    pace      Budget pace: spent vs day-adjusted expected, projected EOM
    allowance  How much you can still spend per day to stay on budget
    goal      Set / view a monthly savings goal
    recur     Recurring rules (add/from/edit/list/remove/run/skip/unskip/pause/resume)
    export    Write expenses to a CSV file (inside the data folder)
    import    Read expenses back from a CSV (deduped)
    backup    Save a timestamped copy of your data
    restore   Restore data from a backup (with a pre-restore safety copy)
    config    View or change settings (currency symbol, default list limit)
    version   Show the version (also `--version`)
    where     Show the data folder and its files
    completion  Print a bash/zsh tab-completion script
    web       Launch a local web UI covering every command

Run `python ledgerling.py --help` or `<command> --help` for details.
"""

import argparse
import calendar
import csv
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, date, timedelta

__version__ = "1.82.0"

# --------------------------------------------------------------------------- #
# Sandbox + storage
# --------------------------------------------------------------------------- #

def _resolve_home():
    """The one folder Ledgerling reads and writes: $LEDGERLING_HOME or ~/.ledgerling."""
    env = os.environ.get("LEDGERLING_HOME")
    base = env if env else os.path.join(os.path.expanduser("~"), ".ledgerling")
    return os.path.abspath(base)


HOME_DIR = _resolve_home()
DATA_FILE = os.path.join(HOME_DIR, "ledgerling_data.json")
CONFIG_FILE = os.path.join(HOME_DIR, "ledgerling_config.json")
UNDO_FILE = os.path.join(HOME_DIR, ".undo.json")
EXPORT_DIR = os.path.join(HOME_DIR, "exports")
BACKUP_DIR = os.path.join(HOME_DIR, "backups")

# When True, save() does not record an undo snapshot (used while performing an
# undo, so undo/redo can toggle without clobbering the snapshot).
_SUPPRESS_UNDO = False

DEFAULT_DATA = {"expenses": [], "budgets": {}, "recurring": []}
DEFAULT_CONFIG = {"currency": "$", "list_limit": 20, "symbol_position": "before"}

# Live settings, loaded from CONFIG_FILE at startup (see main()). Kept as a
# module-level dict so helpers like money() can read it without threading it
# through every call.
_CONFIG = dict(DEFAULT_CONFIG)


def _within_home(path):
    """Return the real path if it is inside the data folder, else refuse.

    This is the single choke point that enforces the sandbox rule for writes.
    """
    base = os.path.realpath(HOME_DIR)
    real = os.path.realpath(path)
    try:
        inside = os.path.commonpath([real, base]) == base
    except ValueError:
        inside = False  # different drives on Windows -> definitely outside
    if not inside:
        sys.exit(f"error: refusing to access '{path}' - outside the data folder")
    return real


def load():
    if not os.path.exists(DATA_FILE):
        return {"expenses": [], "budgets": {}, "recurring": [], "goal": None}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        sys.exit(f"error: could not read {DATA_FILE}: {exc}")
    data.setdefault("expenses", [])
    data.setdefault("budgets", {})
    data.setdefault("recurring", [])
    data.setdefault("goal", None)
    return data


def _atomic_write_json(path, obj):
    """Write obj as JSON atomically, only inside the data folder."""
    _within_home(path)
    os.makedirs(HOME_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=HOME_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        sys.exit(f"error: could not write {path}: {exc}")


def save(data):
    """Write the data file atomically. Records a one-step undo snapshot of the
    previous contents first (unless suppressed, e.g. during an undo)."""
    if not _SUPPRESS_UNDO and os.path.exists(DATA_FILE):
        try:
            shutil.copy2(DATA_FILE, UNDO_FILE)
        except OSError:
            pass  # a failed snapshot must never block the actual save
    _atomic_write_json(DATA_FILE, data)


def load_config():
    """Return settings merged over defaults (unknown keys ignored)."""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                cfg.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG})
        except (json.JSONDecodeError, OSError):
            pass  # a broken config falls back to defaults rather than crashing
    return cfg


def save_config(cfg):
    _atomic_write_json(CONFIG_FILE, cfg)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def parse_date(text):
    """Accept YYYY-MM-DD, or 'today'/'yesterday'. Returns an ISO date string."""
    text = (text or "today").strip().lower()
    if text == "today":
        return date.today().isoformat()
    if text == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        sys.exit(f"error: '{text}' is not a valid date (use YYYY-MM-DD)")


def month_of(iso_date):
    return iso_date[:7]


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def check_month(m):
    """Exit with a clear message if a --month filter is malformed."""
    if m is not None and not _MONTH_RE.match(m):
        sys.exit(f"error: '{m}' is not a valid month (use YYYY-MM)")


def clean_category(raw):
    """Normalize a category and reject an empty one."""
    c = raw.strip().lower()
    if not c:
        sys.exit("error: category cannot be empty")
    return c


def kind_of(entry):
    """An entry is an expense unless explicitly marked income (back-compatible)."""
    return entry.get("kind", "expense")


def expenses_only(rows):
    return [e for e in rows if kind_of(e) == "expense"]


def income_only(rows):
    return [e for e in rows if kind_of(e) == "income"]


_TAG_RE = re.compile(r"#(\w+)")


def parse_tags(note):
    """Extract unique, lowercased #tags from a note (without the '#')."""
    return sorted({m.lower() for m in _TAG_RE.findall(note or "")})


def money(amount):
    cur = _CONFIG.get("currency", "$")
    if _CONFIG.get("symbol_position") == "after":
        return f"{amount:,.2f} {cur}"
    return f"{cur}{amount:,.2f}"


def bar(fraction, width=24):
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "#" * filled + "-" * (width - filled)


def next_id(items):
    return max((i["id"] for i in items), default=0) + 1


def find(items, item_id):
    for i in items:
        if i["id"] == item_id:
            return i
    return None


def add_months(d, n):
    """Add n calendar months to a date, clamping the day to a valid value."""
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def budget_status_line(data, category, ref_iso):
    """Return a one-line budget status for a category/month, or None."""
    limit = data["budgets"].get(category)
    if not limit:
        return None
    spent = sum(e["amount"] for e in data["expenses"]
                if kind_of(e) == "expense" and e["category"] == category
                and month_of(e["date"]) == month_of(ref_iso))
    left = limit - spent
    status = "OVER" if left < 0 else "left"
    return (f"  budget: {money(spent)} of {money(limit)} this month "
            f"({money(abs(left))} {status})")


# --------------------------------------------------------------------------- #
# Recurring engine
# --------------------------------------------------------------------------- #

def _occurrences(rule, today):
    """All dates this rule should have fired on, from start through today."""
    start = date.fromisoformat(rule["start"])
    every = rule["every"]
    out = []
    k = 0
    while True:
        if every == "day":
            d = start + timedelta(days=k)
        elif every == "week":
            d = start + timedelta(weeks=k)
        else:  # month
            d = add_months(start, k)
        if d > today:
            break
        out.append(d)
        k += 1
    return out


def apply_recurring(data):
    """Materialize any due recurring occurrences into real expenses.

    Idempotent: each rule tracks the last date it generated, so running this
    repeatedly never duplicates. Returns the number of expenses created.
    """
    today = date.today()
    created = 0
    for rule in data["recurring"]:
        if rule.get("paused"):
            continue  # a paused rule generates nothing until resumed
        last = date.fromisoformat(rule["last"]) if rule.get("last") else None
        latest = last
        skips = set(rule.get("skips", []))
        for d in _occurrences(rule, today):
            if last is not None and d <= last:
                continue
            if d.isoformat() in skips:
                # honour a `recur skip`: don't generate, but move past it
                if latest is None or d > latest:
                    latest = d
                continue
            data["expenses"].append({
                "id": next_id(data["expenses"]),
                "amount": rule["amount"],
                "category": rule["category"],
                "note": rule["note"],
                "date": d.isoformat(),
                "tags": parse_tags(rule["note"]),
                "kind": rule.get("kind", "expense"),
                "recur_id": rule["id"],
            })
            created += 1
            if latest is None or d > latest:
                latest = d
        if latest is not None:
            rule["last"] = latest.isoformat()
    return created


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_add(args):
    data = load()
    if args.amount <= 0:
        sys.exit("error: amount must be greater than zero")
    note = args.note.strip()
    entry = {
        "id": next_id(data["expenses"]),
        "amount": round(args.amount, 2),
        "category": clean_category(args.category),
        "note": note,
        "date": parse_date(args.date),
        "tags": parse_tags(note),
        "kind": "expense",
    }
    data["expenses"].append(entry)
    save(data)
    print(f"added #{entry['id']}: {money(entry['amount'])} "
          f"[{entry['category']}] {entry['note']} on {entry['date']}")
    line = budget_status_line(data, entry["category"], entry["date"])
    if line:
        print(line)


def cmd_income(args):
    data = load()
    if args.amount <= 0:
        sys.exit("error: amount must be greater than zero")
    note = args.note.strip()
    entry = {
        "id": next_id(data["expenses"]),
        "amount": round(args.amount, 2),
        "category": clean_category(args.category),
        "note": note,
        "date": parse_date(args.date),
        "tags": parse_tags(note),
        "kind": "income",
    }
    data["expenses"].append(entry)
    save(data)
    print(f"recorded income #{entry['id']}: {money(entry['amount'])} "
          f"[{entry['category']}] {entry['note']} on {entry['date']}")


def _scope_by_kind(rows, args):
    """Apply --income / --all scoping; default is expenses only."""
    if getattr(args, "all", False):
        return rows
    if getattr(args, "income", False):
        return income_only(rows)
    return expenses_only(rows)


def cmd_list(args):
    check_month(args.month)
    data = load()
    rows = _scope_by_kind(data["expenses"], args)
    if args.category:
        rows = [e for e in rows if e["category"] == args.category.strip().lower()]
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    rows = sorted(rows, key=lambda e: (e["date"], e["id"]))
    limit = args.limit if args.limit is not None else _CONFIG["list_limit"]
    if limit and limit > 0:
        rows = rows[-limit:]

    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print("no matching entries")
        return

    for e in rows:
        note = f" - {e['note']}" if e["note"] else ""
        tag = " *" if e.get("recur_id") else ""
        mark = " +income" if kind_of(e) == "income" else ""
        print(f"#{e['id']:<4} {e['date']}  {money(e['amount']):>12}  "
              f"[{e['category']}]{note}{tag}{mark}")
    print("-" * 50)
    exp_sum = sum(e["amount"] for e in rows if kind_of(e) == "expense")
    inc_sum = sum(e["amount"] for e in rows if kind_of(e) == "income")
    if inc_sum and exp_sum:
        print(f"{len(rows)} item(s) - spent {money(exp_sum)}, "
              f"income {money(inc_sum)}   (* = recurring)")
    elif inc_sum:
        print(f"{len(rows)} income item(s), total {money(inc_sum)}"
              f"   (* = from a recurring rule)")
    else:
        print(f"{len(rows)} item(s), total {money(exp_sum)}"
              f"   (* = from a recurring rule)")


def cmd_edit(args):
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no expense with id #{args.id}")
    if all(v is None for v in (args.amount, args.category, args.note, args.date)):
        sys.exit("error: nothing to change - pass --amount/--category/--note/--date")
    if args.amount is not None:
        if args.amount <= 0:
            sys.exit("error: amount must be greater than zero")
        e["amount"] = round(args.amount, 2)
    if args.category is not None:
        e["category"] = clean_category(args.category)
    if args.note is not None:
        e["note"] = args.note.strip()
        e["tags"] = parse_tags(e["note"])
    if args.date is not None:
        e["date"] = parse_date(args.date)
    save(data)
    note = f" - {e['note']}" if e["note"] else ""
    print(f"updated #{e['id']}: {money(e['amount'])} [{e['category']}]{note} "
          f"on {e['date']}")


def cmd_clone(args):
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no entry with id #{args.id}")
    note = e["note"]
    entry = {
        "id": next_id(data["expenses"]),
        "amount": e["amount"],
        "category": e["category"],
        "note": note,
        "date": parse_date(args.date),
        "tags": parse_tags(note),
        "kind": kind_of(e),
    }
    data["expenses"].append(entry)
    save(data)
    what = "income" if entry["kind"] == "income" else "expense"
    print(f"cloned #{e['id']} -> {what} #{entry['id']}: {money(entry['amount'])} "
          f"[{entry['category']}] on {entry['date']}")


def cmd_refund(args):
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no entry with id #{args.id}")
    if kind_of(e) != "expense":
        sys.exit(f"error: #{args.id} is income, not an expense to refund")
    amount = round(args.amount, 2) if args.amount is not None else e["amount"]
    if amount <= 0:
        sys.exit("error: refund amount must be greater than zero")
    orig = e["note"] or e["category"]
    note = f"refund of #{e['id']} ({orig})"
    entry = {
        "id": next_id(data["expenses"]),
        "amount": amount,
        "category": e["category"],
        "note": note,
        "date": parse_date(args.date),
        "tags": parse_tags(note),
        "kind": "income",
    }
    data["expenses"].append(entry)
    save(data)
    part = "" if amount == e["amount"] else " (partial)"
    print(f"refunded #{e['id']} -> income #{entry['id']}: {money(amount)}{part} "
          f"[{entry['category']}] on {entry['date']}")


def cmd_delete(args):
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no expense with id #{args.id}")
    data["expenses"] = [x for x in data["expenses"] if x["id"] != args.id]
    save(data)
    print(f"deleted #{e['id']}: {money(e['amount'])} [{e['category']}] "
          f"on {e['date']}")


def cmd_summary(args):
    check_month(args.month)
    data = load()
    period = args.month or date.today().isoformat()[:7]
    rows = [e for e in expenses_only(data["expenses"])
            if month_of(e["date"]) == period]
    if not rows:
        print(f"no expenses for {period}")
        return

    totals = {}
    for e in rows:
        totals[e["category"]] = totals.get(e["category"], 0) + e["amount"]
    grand = sum(totals.values())
    biggest = max(totals.values())

    print(f"Summary for {period}")
    print("=" * 50)
    for cat, amt in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
        share = amt / grand if grand else 0
        print(f"{cat:<14} {money(amt):>12}  {bar(amt / biggest)} "
              f"{share * 100:4.0f}%")
    print("=" * 50)
    print(f"{'TOTAL':<14} {money(grand):>12}")


def cmd_budget(args):
    check_month(args.month)
    data = load()

    # Setting a budget needs both --category and --amount; one alone is an error
    # rather than a silent fall-through to the view.
    if args.category is not None or args.amount is not None:
        if args.category is None or args.amount is None:
            sys.exit("error: to set a budget, provide both --category and --amount")
        cat = clean_category(args.category)
        if args.amount < 0:
            sys.exit("error: budget cannot be negative")
        data["budgets"][cat] = round(args.amount, 2)
        save(data)
        print(f"set monthly budget for [{cat}] to {money(args.amount)}")
        return

    if not data["budgets"]:
        print("no budgets set. Try: budget --category food --amount 400")
        return

    period = args.month or date.today().isoformat()[:7]
    print(f"Budgets for {period}")
    print("=" * 54)
    for cat, limit in sorted(data["budgets"].items()):
        spent = sum(e["amount"] for e in data["expenses"]
                    if kind_of(e) == "expense" and e["category"] == cat
                    and month_of(e["date"]) == period)
        frac = spent / limit if limit else 0
        flag = "  <-- OVER" if spent > limit else ""
        print(f"{cat:<14} {money(spent):>10} / {money(limit):<10} "
              f"{bar(frac)} {frac * 100:4.0f}%{flag}")


def cmd_unbudget(args):
    data = load()
    if getattr(args, "all", False):
        n = len(data["budgets"])
        if not n:
            print("no budgets set")
            return
        data["budgets"] = {}
        save(data)
        print(f"cleared all {n} budget(s).  undo with `undo`.")
        return
    if not args.category:
        sys.exit("error: give a category, or --all to clear every budget")
    cat = clean_category(args.category)
    if cat not in data["budgets"]:
        sys.exit(f"error: no budget set for [{cat}]")
    amount = data["budgets"].pop(cat)
    save(data)
    print(f"removed the {money(amount)} budget for [{cat}].  undo with `undo`.")


def cmd_export(args):
    check_month(args.month)
    arg_start = getattr(args, "start", None)
    arg_end = getattr(args, "end", None)
    if args.month and (arg_start or arg_end):
        sys.exit("error: use --month or --start/--end, not both")
    data = load()
    rows = sorted(data["expenses"], key=lambda e: (e["date"], e["id"]))
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    elif arg_start or arg_end:
        start = parse_date(arg_start) if arg_start else "0000-01-01"
        end = parse_date(arg_end) if arg_end else "9999-12-31"
        if end < start:
            start, end = end, start
        rows = [e for e in rows if start <= e["date"] <= end]

    fmt = args.format
    if args.file:
        # Force the export to stay inside the data folder, ignoring any path
        # components the user supplied.
        target = os.path.join(EXPORT_DIR, os.path.basename(args.file))
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(EXPORT_DIR, f"expenses_{stamp}.{fmt}")

    os.makedirs(EXPORT_DIR, exist_ok=True)
    _within_home(target)
    try:
        if fmt == "json":
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, indent=2)
        else:
            with open(target, "w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["id", "date", "amount", "category", "note",
                                 "kind", "recurring"])
                for e in rows:
                    writer.writerow([e["id"], e["date"], f"{e['amount']:.2f}",
                                     e["category"], e["note"], kind_of(e),
                                     "yes" if e.get("recur_id") else "no"])
    except OSError as exc:
        sys.exit(f"error: could not write {target}: {exc}")
    print(f"exported {len(rows)} expense(s) to {target}")


def cmd_import(args):
    name = os.path.basename(args.file)  # keep the read inside the data folder
    candidates = [os.path.join(EXPORT_DIR, name), os.path.join(HOME_DIR, name)]
    path = next((c for c in candidates if os.path.exists(c)), None)
    if not path:
        sys.exit(f"error: '{name}' not found in exports/ or the data folder")
    _within_home(path)

    data = load()
    seen = {(e["date"], round(e["amount"], 2), e["category"], e["note"],
             kind_of(e)) for e in data["expenses"]}

    added = skipped = bad = 0
    try:
        # utf-8-sig tolerates a BOM (Excel / PowerShell often add one).
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                try:
                    d = datetime.strptime(row["date"].strip(),
                                          "%Y-%m-%d").date().isoformat()
                    amount = round(float(row["amount"]), 2)
                    category = row["category"].strip().lower()
                    note = (row.get("note") or "").strip()
                    kind = (row.get("kind") or "expense").strip().lower()
                    if kind not in ("expense", "income"):
                        kind = "expense"
                    if amount <= 0 or not category:
                        raise ValueError
                except (KeyError, ValueError, AttributeError):
                    bad += 1
                    continue
                key = (d, amount, category, note, kind)
                if key in seen:
                    skipped += 1
                    continue
                seen.add(key)
                if not getattr(args, "dry_run", False):
                    data["expenses"].append({
                        "id": next_id(data["expenses"]),
                        "amount": amount, "category": category,
                        "note": note, "date": d, "tags": parse_tags(note),
                        "kind": kind,
                    })
                added += 1
    except OSError as exc:
        sys.exit(f"error: could not read {path}: {exc}")

    if getattr(args, "dry_run", False):
        print(f"dry run of {path} (nothing imported)")
        print(f"  would add {added}, skip {skipped} duplicate(s), "
              f"{bad} malformed row(s)")
        return

    save(data)
    print(f"imported from {path}")
    print(f"  added {added}, skipped {skipped} duplicate(s), "
          f"{bad} malformed row(s)")


def cmd_report(args):
    data = load()
    months = args.months
    if months < 1:
        sys.exit("error: --months must be at least 1")

    first_this = date.today().replace(day=1)
    keys = [month_of(add_months(first_this, -i).isoformat())
            for i in range(months - 1, -1, -1)]

    totals = {k: 0.0 for k in keys}
    income = {k: 0.0 for k in keys}
    for e in data["expenses"]:
        m = month_of(e["date"])
        if m in totals:
            if kind_of(e) == "income":
                income[m] += e["amount"]
            else:
                totals[m] += e["amount"]

    peak = max(totals.values()) if any(totals.values()) else 0

    print(f"Spending trend (last {months} month(s))")
    print("=" * 56)
    prev = None
    for k in keys:
        amt = totals[k]
        chart = bar(amt / peak) if peak else bar(0)
        if prev is None or prev == 0:
            change = "   -  "
        else:
            pct = (amt - prev) / prev * 100
            change = f"{pct:+5.0f}%"
        print(f"{k}  {money(amt):>12}  {chart}  {change}")
        prev = amt

    active = [k for k in keys if totals[k] > 0]
    avg = (sum(totals[k] for k in active) / len(active)) if active else 0
    print("-" * 56)
    print(f"{'average/mo':<10} {money(avg):>12}  (months with spend)")

    # Income + net over the window, only if any income was recorded.
    total_income = sum(income.values())
    total_spent = sum(totals.values())
    net = total_income - total_spent
    if total_income:
        rate = (net / total_income * 100) if total_income else 0
        print()
        print(f"{'income':<10} {money(total_income):>12}")
        print(f"{'spending':<10} {money(total_spent):>12}")
        print(f"{'net':<10} {money(net):>12}   (saved {rate:.0f}% of income)")

    goal = data.get("goal")
    if goal is not None:
        target = goal * months
        status = "on track" if net >= target else f"{money(target - net)} behind"
        print()
        print(f"{'goal':<10} {money(target):>12}   ({money(goal)}/mo x {months})")
        print(f"{'vs net':<10} {money(net):>12}   {status}")

    # Budget adherence for the most recent month shown.
    latest = keys[-1]
    if data["budgets"]:
        print()
        print(f"Budget adherence for {latest}")
        print("=" * 56)
        over = 0
        for cat, limit in sorted(data["budgets"].items()):
            spent = sum(e["amount"] for e in data["expenses"]
                        if kind_of(e) == "expense" and e["category"] == cat
                        and month_of(e["date"]) == latest)
            frac = spent / limit if limit else 0
            flag = "  <-- OVER" if spent > limit else ""
            if spent > limit:
                over += 1
            print(f"{cat:<14} {money(spent):>10} / {money(limit):<10} "
                  f"{bar(frac)} {frac * 100:4.0f}%{flag}")
        print("-" * 56)
        verdict = "all within budget" if over == 0 else f"{over} category(ies) over"
        print(f"result: {verdict}")


def cmd_search(args):
    check_month(args.month)
    if args.min is not None and args.max is not None and args.min > args.max:
        sys.exit("error: --min cannot be greater than --max")
    data = load()
    rows = _scope_by_kind(data["expenses"], args)
    kw = (args.keyword or "").strip().lower()
    if kw:
        rows = [e for e in rows
                if kw in e["note"].lower() or kw in e["category"].lower()]
    if args.category:
        rows = [e for e in rows if e["category"] == args.category.strip().lower()]
    if args.tag:
        want = args.tag.strip().lstrip("#").lower()
        rows = [e for e in rows if want in e.get("tags", [])]
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    if args.min is not None:
        rows = [e for e in rows if e["amount"] >= args.min]
    if args.max is not None:
        rows = [e for e in rows if e["amount"] <= args.max]

    sort = getattr(args, "sort", None) or "date"
    keyfn = {
        "date": lambda e: (e["date"], e["id"]),
        "amount": lambda e: (e["amount"], e["date"]),
        "category": lambda e: (e["category"], e["date"]),
    }[sort]
    rows = sorted(rows, key=keyfn, reverse=getattr(args, "desc", False))

    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print("no matching entries")
        return

    for e in rows:
        note = f" - {e['note']}" if e["note"] else ""
        tag = " *" if e.get("recur_id") else ""
        mark = " +income" if kind_of(e) == "income" else ""
        print(f"#{e['id']:<4} {e['date']}  {money(e['amount']):>12}  "
              f"[{e['category']}]{note}{tag}{mark}")
    print("-" * 50)
    print(f"{len(rows)} match(es), total {money(sum(e['amount'] for e in rows))}")


def _median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def cmd_stats(args):
    data = load()
    exp = expenses_only(data["expenses"])
    inc = income_only(data["expenses"])
    want_json = getattr(args, "json", False)
    if not exp and not inc:
        if want_json:
            print(json.dumps({"expenses": 0}, indent=2))
        else:
            print("nothing recorded yet - nothing to analyze")
        return

    # --- expense-derived metrics (only meaningful when expenses exist) --------
    total = sum(e["amount"] for e in exp)
    amounts = [e["amount"] for e in exp]
    biggest = max(exp, key=lambda e: e["amount"]) if exp else None

    by_day = {}
    for e in exp:
        by_day.setdefault(e["date"], []).append(e["amount"])
    busy_date = max(by_day, key=lambda d: sum(by_day[d])) if by_day else None

    cats = {}
    for e in exp:
        cats.setdefault(e["category"], []).append(e["amount"])
    cat_stats = {c: {"count": len(v), "total": round(sum(v), 2),
                     "average": round(sum(v) / len(v), 2)}
                 for c, v in cats.items()}
    tag_tot = {}
    for e in exp:
        for t in e.get("tags", []):
            tag_tot[t] = round(tag_tot.get(t, 0) + e["amount"], 2)

    today = date.today()
    period = today.isoformat()[:7]
    spent = sum(e["amount"] for e in exp if month_of(e["date"]) == period)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed = today.day
    budget_total = sum(data["budgets"].values())
    projection = None
    if spent > 0:
        rate = spent / elapsed
        projection = {
            "month": period, "spent": round(spent, 2),
            "elapsed_days": elapsed, "days_in_month": days_in_month,
            "run_rate_per_day": round(rate, 2),
            "projected_eom": round(rate * days_in_month, 2),
            "budget_total": round(budget_total, 2) if budget_total else None,
        }

    # --- income + net --------------------------------------------------------
    total_income = sum(e["amount"] for e in inc)
    net = total_income - total
    savings_rate = round(net / total_income * 100, 1) if total_income else None

    goal = data.get("goal")
    this_month_net = month_net(data["expenses"], period)

    if want_json:
        print(json.dumps({
            "expenses": len(exp),
            "total": round(total, 2),
            "average": round(total / len(exp), 2) if exp else 0,
            "median": round(_median(amounts), 2),
            "biggest": biggest,
            "busiest_day": ({"date": busy_date,
                             "total": round(sum(by_day[busy_date]), 2),
                             "count": len(by_day[busy_date])}
                            if busy_date else None),
            "by_category": cat_stats,
            "by_tag": tag_tot,
            "projection": projection,
            "income": round(total_income, 2),
            "net": round(net, 2),
            "savings_rate": savings_rate,
            "goal": goal,
            "goal_month_net": round(this_month_net, 2) if goal is not None else None,
        }, indent=2))
        return

    print("Ledgerling stats")
    print("=" * 52)
    if exp:
        print(f"{'expenses recorded':<20} {len(exp)}")
        print(f"{'total spent':<20} {money(total)}")
        print(f"{'average expense':<20} {money(total / len(exp))}")
        print(f"{'median expense':<20} {money(_median(amounts))}")
        bnote = f" - {biggest['note']}" if biggest["note"] else ""
        print(f"{'biggest expense':<20} {money(biggest['amount'])} "
              f"[{biggest['category']}]{bnote} ({biggest['date']})")
        print(f"{'busiest day':<20} {busy_date}  ({money(sum(by_day[busy_date]))} "
              f"across {len(by_day[busy_date])} item(s))")

        print()
        print("Average by category")
        print("-" * 52)
        for cat, vals in sorted(cats.items(), key=lambda kv: sum(kv[1]),
                                reverse=True):
            print(f"{cat:<14} avg {money(sum(vals) / len(vals)):>10}   "
                  f"({len(vals)} item(s), {money(sum(vals))} total)")

        if tag_tot:
            print()
            print("Spending by tag")
            print("-" * 52)
            for t, amt in sorted(tag_tot.items(), key=lambda kv: kv[1],
                                 reverse=True):
                print(f"#{t:<13} {money(amt):>10}")
    else:
        print("(no expenses recorded)")

    if total_income:
        print()
        print("Income & net (all time)")
        print("-" * 52)
        print(f"{'income':<16} {money(total_income)}")
        print(f"{'spending':<16} {money(total)}")
        print(f"{'net':<16} {money(net)}   (saved {savings_rate:.0f}% of income)")

    if goal is not None:
        print()
        print(f"Savings goal ({period})")
        print("-" * 52)
        status = (f"met (+{money(this_month_net - goal)})" if this_month_net >= goal
                  else f"{money(goal - this_month_net)} to go")
        print(f"{'goal':<16} {money(goal)}")
        print(f"{'net so far':<16} {money(this_month_net)}   {status}")

    if exp:
        print()
        print(f"This month ({period}) projection")
        print("-" * 52)
        if projection is None:
            print("no spending recorded this month yet")
        else:
            print(f"{'spent so far':<16} {money(spent)}  "
                  f"(day {elapsed} of {days_in_month})")
            print(f"{'run rate':<16} {money(projection['run_rate_per_day'])}/day")
            print(f"{'projected EOM':<16} {money(projection['projected_eom'])}")
            if budget_total:
                diff = projection["projected_eom"] - budget_total
                verb = "over" if diff > 0 else "under"
                print(f"{'vs budgets':<16} {money(budget_total)} total  "
                      f"-> projected {money(abs(diff))} {verb}")


def _make_backup(label=""):
    """Copy the current data file into backups/. Returns the backup path."""
    if not os.path.exists(DATA_FILE):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    target = os.path.join(BACKUP_DIR, f"ledgerling_data_{stamp}{suffix}.json")
    _within_home(target)
    shutil.copy2(DATA_FILE, target)
    return target


def _list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    files = [f for f in os.listdir(BACKUP_DIR) if f.endswith(".json")]
    return sorted(files)


def _print_backups():
    files = _list_backups()
    if not files:
        print("no backups yet. Try: backup")
        return
    print("Backups (in backups/)")
    print("=" * 50)
    for f in files:
        size = os.path.getsize(os.path.join(BACKUP_DIR, f))
        print(f"{f}   ({size:,} bytes)")


def cmd_backup(args):
    if args.list:
        _print_backups()
        return
    path = _make_backup()
    if not path:
        sys.exit("error: no data file to back up yet")
    print(f"backed up to {path}")


def cmd_restore(args):
    if args.list:
        _print_backups()
        return
    if not args.file:
        sys.exit("error: restore needs --file NAME (see `restore --list`)")

    name = os.path.basename(args.file)  # keep the read inside the data folder
    candidates = [os.path.join(BACKUP_DIR, name), os.path.join(HOME_DIR, name)]
    path = next((c for c in candidates if os.path.exists(c)), None)
    if not path:
        sys.exit(f"error: '{name}' not found in backups/ or the data folder")
    _within_home(path)

    # Validate the backup before touching live data.
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        sys.exit(f"error: '{name}' is not a valid backup: {exc}")
    if not isinstance(data, dict) or "expenses" not in data:
        sys.exit(f"error: '{name}' does not look like Ledgerling data")

    # Safety net: back up whatever is live now before overwriting it.
    safety = _make_backup(label="prerestore")
    data.setdefault("expenses", [])
    data.setdefault("budgets", {})
    data.setdefault("recurring", [])
    data.setdefault("goal", None)
    save(data)

    print(f"restored from {path}")
    print(f"  {len(data['expenses'])} expense(s), "
          f"{len(data['recurring'])} recurring rule(s)")
    if safety:
        print(f"  previous data saved to {safety}")


def cmd_undo(args):
    """Revert the last change to the data file. Running it again redoes, because
    undo swaps the current state into the snapshot slot as it restores."""
    global _SUPPRESS_UNDO
    if not os.path.exists(UNDO_FILE):
        print("nothing to undo")
        return
    try:
        with open(UNDO_FILE, "r", encoding="utf-8") as fh:
            restore = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        sys.exit(f"error: undo snapshot is unreadable: {exc}")

    current = None
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as fh:
                current = json.load(fh)
        except (json.JSONDecodeError, OSError):
            current = None

    _SUPPRESS_UNDO = True
    try:
        restore.setdefault("expenses", [])
        restore.setdefault("budgets", {})
        restore.setdefault("recurring", [])
        restore.setdefault("goal", None)
        save(restore)
    finally:
        _SUPPRESS_UNDO = False

    # Put the just-replaced state into the slot so the next undo redoes it.
    if current is not None:
        _atomic_write_json(UNDO_FILE, current)
    else:
        try:
            os.remove(UNDO_FILE)
        except OSError:
            pass
    print(f"undid the last change - now {len(restore['expenses'])} expense(s). "
          "Run `undo` again to redo.")


def _period_totals(rows, period):
    exp = sum(e["amount"] for e in rows
              if kind_of(e) == "expense" and month_of(e["date"]) == period)
    inc = sum(e["amount"] for e in rows
              if kind_of(e) == "income" and month_of(e["date"]) == period)
    return {"income": round(inc, 2), "spending": round(exp, 2),
            "net": round(inc - exp, 2)}


def _signed(v):
    return ("+" if v >= 0 else "-") + money(abs(v))


def cmd_compare(args):
    check_month(args.month_a)
    check_month(args.month_b)
    data = load()
    first = date.today().replace(day=1)
    a = args.month_a or month_of(add_months(first, -1).isoformat())
    b = args.month_b or month_of(first.isoformat())

    rows = data["expenses"]
    ta, tb = _period_totals(rows, a), _period_totals(rows, b)

    ca, cb = {}, {}
    for e in expenses_only(rows):
        if month_of(e["date"]) == a:
            ca[e["category"]] = round(ca.get(e["category"], 0) + e["amount"], 2)
        if month_of(e["date"]) == b:
            cb[e["category"]] = round(cb.get(e["category"], 0) + e["amount"], 2)
    cats = sorted(set(ca) | set(cb), key=lambda c: cb.get(c, 0), reverse=True)
    by_cat = {c: {"a": ca.get(c, 0.0), "b": cb.get(c, 0.0),
                  "delta": round(cb.get(c, 0) - ca.get(c, 0), 2)} for c in cats}

    if getattr(args, "json", False):
        print(json.dumps({"a": a, "b": b, "a_totals": ta, "b_totals": tb,
                          "by_category": by_cat}, indent=2))
        return

    print(f"Compare  {a}  ->  {b}")
    print("=" * 58)
    print(f"{'':<12}{a:>14}{b:>14}{'delta':>16}")
    for key in ("income", "spending", "net"):
        print(f"{key:<12}{money(ta[key]):>14}{money(tb[key]):>14}"
              f"{_signed(tb[key] - ta[key]):>16}")

    if by_cat:
        print()
        print("By category (spending)")
        print("-" * 58)
        for c, v in by_cat.items():
            print(f"{c:<12}{money(v['a']):>14}{money(v['b']):>14}"
                  f"{_signed(v['delta']):>16}")


def cmd_duplicates(args):
    data = load()
    groups = {}
    for e in data["expenses"]:
        key = (e["date"], round(e["amount"], 2), e["category"],
               e["note"], kind_of(e))
        groups.setdefault(key, []).append(e["id"])
    dupes = [{"date": k[0], "amount": k[1], "category": k[2], "note": k[3],
              "kind": k[4], "ids": sorted(ids)}
             for k, ids in groups.items() if len(ids) > 1]
    dupes.sort(key=lambda g: (g["date"], g["category"]))

    if getattr(args, "json", False):
        print(json.dumps(dupes, indent=2))
        return
    if not dupes:
        print("no duplicates found")
        return

    extra = sum(len(g["ids"]) - 1 for g in dupes)
    print("Potential duplicates")
    print("=" * 58)
    for g in dupes:
        note = f" - {g['note']}" if g["note"] else ""
        mark = " +income" if g["kind"] == "income" else ""
        ids = ", ".join(f"#{i}" for i in g["ids"])
        print(f"{g['date']}  {money(g['amount']):>12}  [{g['category']}]{note}"
              f"{mark}   ({ids})")
    print("-" * 58)
    print(f"{len(dupes)} group(s), {extra} extra entr{'y' if extra == 1 else 'ies'}."
          "  Remove with `delete <id>` or `dedupe`.")


def cmd_dedupe(args):
    data = load()
    groups = {}
    for e in data["expenses"]:
        key = (e["date"], round(e["amount"], 2), e["category"],
               e["note"], kind_of(e))
        groups.setdefault(key, []).append(e)
    # in each duplicate group keep the lowest id, drop the rest
    remove_ids = []
    for entries in groups.values():
        if len(entries) > 1:
            entries.sort(key=lambda e: e["id"])
            remove_ids.extend(e["id"] for e in entries[1:])
    remove_ids.sort()

    dry = getattr(args, "dry_run", False)
    if getattr(args, "json", False):
        print(json.dumps({"removed": remove_ids, "count": len(remove_ids),
                          "dry_run": dry}, indent=2))
        if remove_ids and not dry:
            data["expenses"] = [e for e in data["expenses"]
                                if e["id"] not in set(remove_ids)]
            save(data)
        return

    if not remove_ids:
        print("no duplicates to remove")
        return
    ids = ", ".join(f"#{i}" for i in remove_ids)
    if dry:
        print(f"would remove {len(remove_ids)} duplicate entr"
              f"{'y' if len(remove_ids) == 1 else 'ies'}: {ids}")
        print("(dry run - nothing changed; rerun without --dry-run to apply)")
        return
    data["expenses"] = [e for e in data["expenses"]
                        if e["id"] not in set(remove_ids)]
    save(data)
    print(f"removed {len(remove_ids)} duplicate entr"
          f"{'y' if len(remove_ids) == 1 else 'ies'}: {ids}")
    print("undo with `undo`.")


def cmd_pace(args):
    check_month(args.month)
    data = load()
    if not data["budgets"]:
        print("no budgets set. Try: budget --category food --amount 400")
        return
    today = date.today()
    period = args.month or today.isoformat()[:7]
    year, mon = (int(x) for x in period.split("-"))
    days_in_month = calendar.monthrange(year, mon)[1]
    # elapsed days: partial for the current month, full for any other month
    elapsed = today.day if period == today.isoformat()[:7] else days_in_month
    frac_time = elapsed / days_in_month

    cats = {}
    for cat, limit in sorted(data["budgets"].items()):
        spent = sum(e["amount"] for e in data["expenses"]
                    if kind_of(e) == "expense" and e["category"] == cat
                    and month_of(e["date"]) == period)
        expected = round(limit * frac_time, 2)
        projected = round(spent / elapsed * days_in_month, 2) if elapsed else 0.0
        cats[cat] = {"spent": round(spent, 2), "limit": round(limit, 2),
                     "expected": expected, "projected": projected,
                     "on_pace": spent <= expected}

    if getattr(args, "json", False):
        print(json.dumps({"month": period, "elapsed_days": elapsed,
                          "days_in_month": days_in_month,
                          "categories": cats}, indent=2))
        return

    print(f"Budget pace for {period} (day {elapsed} of {days_in_month})")
    print("=" * 62)
    for cat, c in cats.items():
        pace = "on pace" if c["on_pace"] else "over pace"
        proj_flag = " OVER" if c["projected"] > c["limit"] else ""
        print(f"{cat:<12} spent {money(c['spent']):>10}  vs expected "
              f"{money(c['expected']):>10}  {pace}")
        print(f"{'':<12} projected EOM {money(c['projected'])} / "
              f"{money(c['limit'])}{proj_flag}")


def cmd_allowance(args):
    check_month(args.month)
    data = load()
    if not data["budgets"]:
        print("no budgets set. Try: budget --category food --amount 400")
        return
    today = date.today()
    period = args.month or today.isoformat()[:7]
    cur = today.isoformat()[:7]
    year, mon = (int(x) for x in period.split("-"))
    days_in_month = calendar.monthrange(year, mon)[1]
    # days still to come, inclusive of today, for the current month
    if period == cur:
        days_left = days_in_month - today.day + 1
    elif period > cur:
        days_left = days_in_month
    else:
        days_left = 0

    cats = {}
    total_remaining = 0.0
    for cat, limit in sorted(data["budgets"].items()):
        spent = sum(e["amount"] for e in data["expenses"]
                    if kind_of(e) == "expense" and e["category"] == cat
                    and month_of(e["date"]) == period)
        remaining = round(limit - spent, 2)
        total_remaining = round(total_remaining + remaining, 2)
        cats[cat] = {"limit": round(limit, 2), "spent": round(spent, 2),
                     "remaining": remaining}
    daily = round(total_remaining / days_left, 2) if days_left > 0 else None

    if getattr(args, "json", False):
        print(json.dumps({"month": period, "days_left": days_left,
                          "categories": cats,
                          "total_remaining": total_remaining,
                          "daily_allowance": daily}, indent=2))
        return

    print(f"Budget allowance for {period}")
    print("=" * 54)
    for cat, c in cats.items():
        flag = "  OVER" if c["remaining"] < 0 else ""
        print(f"{cat:<14} {money(c['spent']):>10} / {money(c['limit']):<10}"
              f"  left {money(c['remaining']):>10}{flag}")
    print("-" * 54)
    print(f"total left {money(total_remaining)}")
    if daily is not None:
        print(f"{days_left} day(s) left -> spend up to {money(daily)}/day "
              "to stay on budget")


_DIST_EDGES = [10, 25, 50, 100, 250]
_DIST_LABELS = ["$0-10", "$10-25", "$25-50", "$50-100", "$100-250", "$250+"]


def _dist_bucket(amount):
    for i, edge in enumerate(_DIST_EDGES):
        if amount < edge:
            return i
    return len(_DIST_EDGES)


def cmd_distribution(args):
    check_month(args.month)
    data = load()
    rows = expenses_only(data["expenses"])
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]

    buckets = [{"label": _DIST_LABELS[i], "count": 0, "total": 0.0}
               for i in range(len(_DIST_LABELS))]
    for e in rows:
        b = buckets[_dist_bucket(e["amount"])]
        b["count"] += 1
        b["total"] = round(b["total"] + e["amount"], 2)

    if getattr(args, "json", False):
        print(json.dumps({"buckets": buckets}, indent=2))
        return
    if not rows:
        print("no expenses to chart")
        return

    peak = max(b["count"] for b in buckets) or 0
    print(f"Expense size distribution ({args.month or 'all time'})")
    print("=" * 52)
    for b in buckets:
        chart = bar(b["count"] / peak, width=18) if peak else bar(0, width=18)
        print(f"{b['label']:<10} {b['count']:>4}  {money(b['total']):>12}  {chart}")


def cmd_anomalies(args):
    """Flag expenses that are statistical outliers within their category.

    For each category with enough history, compute the mean and (population)
    standard deviation of its expense amounts and flag any entry that sits more
    than --z standard deviations above the mean. Purely a read/analytics view.
    """
    check_month(args.month)
    z = args.z if args.z and args.z > 0 else 2.0
    min_count = args.min_count if args.min_count and args.min_count > 1 else 4
    data = load()
    rows = expenses_only(data["expenses"])
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    if args.category:
        cat = clean_category(args.category)
        rows = [e for e in rows if e["category"] == cat]

    groups = {}
    for e in rows:
        groups.setdefault(e["category"], []).append(e)

    flagged = []
    for cat, items in groups.items():
        if len(items) < min_count:
            continue
        amounts = [e["amount"] for e in items]
        n = len(amounts)
        mean = sum(amounts) / n
        std = (sum((a - mean) ** 2 for a in amounts) / n) ** 0.5
        if std == 0:
            continue
        threshold = mean + z * std
        for e in items:
            if e["amount"] > threshold:
                flagged.append({
                    "id": e["id"],
                    "date": e["date"],
                    "category": cat,
                    "amount": e["amount"],
                    "note": e.get("note", ""),
                    "category_mean": round(mean, 2),
                    "category_std": round(std, 2),
                    "deviations": round((e["amount"] - mean) / std, 2),
                })

    flagged.sort(key=lambda f: f["deviations"], reverse=True)

    if getattr(args, "json", False):
        print(json.dumps({"threshold_z": z, "min_count": min_count,
                          "anomalies": flagged}, indent=2))
        return

    scope = args.month or "all time"
    print(f"Spending anomalies ({scope}, > {z:g} SD above category mean)")
    print("=" * 60)
    if not flagged:
        print("no anomalies found")
        return
    for f in flagged:
        note = f" - {f['note']}" if f["note"] else ""
        print(f"#{f['id']:<4} {money(f['amount']):>12}  {f['date']}  "
              f"[{f['category']}]  +{f['deviations']:.1f} SD{note}")
    print("-" * 60)
    cats = {f["category"] for f in flagged}
    print(f"{len(flagged)} anomal{'y' if len(flagged) == 1 else 'ies'} "
          f"across {len(cats)} categor{'y' if len(cats) == 1 else 'ies'} "
          f"(category mean +/- SD shown in --json)")


def cmd_roundup(args):
    """Simulate a round-up savings rule: how much you'd set aside if every
    expense were rounded up to the nearest --to dollars.

    Uses integer-cents arithmetic so the bump per expense is exact.
    """
    check_month(args.month)
    step = args.to if args.to and args.to > 0 else 1.0
    step_cents = round(step * 100)
    if step_cents <= 0:
        sys.exit("error: --to must be greater than 0")

    data = load()
    rows = expenses_only(data["expenses"])
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]

    count = len(rows)
    total_cents = 0
    largest_cents = 0
    for e in rows:
        cents = round(e["amount"] * 100)
        bump = (-cents) % step_cents  # 0 when already on a step boundary
        total_cents += bump
        if bump > largest_cents:
            largest_cents = bump
    total = round(total_cents / 100, 2)
    average = round((total_cents / count) / 100, 2) if count else 0.0
    largest = round(largest_cents / 100, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "to": step, "expenses": count, "total_saved": total,
            "average": average, "largest": largest,
        }, indent=2))
        return

    scope = args.month or "all time"
    print(f"Round-up savings ({scope}, to nearest {money(step)})")
    print("=" * 48)
    if not count:
        print("no expenses in range")
        return
    print(f"{'expenses':<16} {count}")
    print(f"{'total saved':<16} {money(total)}")
    print(f"{'average / item':<16} {money(average)}")
    print(f"{'largest bump':<16} {money(largest)}")


def cmd_average(args):
    data = load()
    exp = expenses_only(data["expenses"])
    if not exp:
        if getattr(args, "json", False):
            print(json.dumps({"total": 0}, indent=2))
        else:
            print("no expenses to average")
        return

    dates = [date.fromisoformat(e["date"]) for e in exp]
    first, last = min(dates), max(dates)
    span = (last - first).days + 1
    total = round(sum(e["amount"] for e in exp), 2)
    per_day = total / span
    per_week = round(per_day * 7, 2)
    per_month = round(per_day * 30.44, 2)  # avg calendar-month length
    per_day = round(per_day, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "first": first.isoformat(), "last": last.isoformat(),
            "days": span, "total": total, "per_day": per_day,
            "per_week": per_week, "per_month": per_month,
        }, indent=2))
        return

    print("Average spending")
    print("=" * 48)
    print(f"{'range':<12} {first.isoformat()} to {last.isoformat()} "
          f"({span} day(s))")
    print(f"{'total':<12} {money(total)}")
    print(f"{'per day':<12} {money(per_day)}")
    print(f"{'per week':<12} {money(per_week)}")
    print(f"{'per month':<12} {money(per_month)}")


def cmd_top(args):
    check_month(args.month)
    data = load()
    rows = _scope_by_kind(data["expenses"], args)
    if args.category:
        rows = [e for e in rows if e["category"] == args.category.strip().lower()]
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    rows = sorted(rows, key=lambda e: e["amount"], reverse=True)
    limit = args.limit if args.limit and args.limit > 0 else 10
    rows = rows[:limit]

    if getattr(args, "all", False):
        noun, nounp = "entry", "entries"
    elif getattr(args, "income", False):
        noun, nounp = "income entry", "income entries"
    else:
        noun, nounp = "expense", "expenses"

    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("no matching entries")
        return

    print(f"Top {len(rows)} {noun if len(rows) == 1 else nounp}")
    print("=" * 56)
    for rank, e in enumerate(rows, 1):
        note = f" - {e['note']}" if e["note"] else ""
        mark = " +income" if kind_of(e) == "income" else ""
        print(f"{rank:>2}. {money(e['amount']):>12}  {e['date']}  "
              f"[{e['category']}]{note}{mark}")
    print("-" * 56)
    print(f"shown total {money(sum(e['amount'] for e in rows))}")


def cmd_trend(args):
    cat = clean_category(args.category)
    months = args.months
    if months < 1:
        sys.exit("error: --months must be at least 1")
    data = load()
    first = date.today().replace(day=1)
    keys = [month_of(add_months(first, -i).isoformat())
            for i in range(months - 1, -1, -1)]

    totals = {k: 0.0 for k in keys}
    for e in expenses_only(data["expenses"]):
        if e["category"] == cat:
            m = month_of(e["date"])
            if m in totals:
                totals[m] = round(totals[m] + e["amount"], 2)

    window_total = round(sum(totals.values()), 2)
    average = round(window_total / months, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "category": cat,
            "months": [{"month": k, "total": totals[k]} for k in keys],
            "total": window_total,
            "average": average,
        }, indent=2))
        return

    if window_total == 0:
        print(f"no spending on [{cat}] in the last {months} month(s)")
        return

    peak = max(totals.values())
    print(f"Trend for [{cat}] (last {months} month(s))")
    print("=" * 52)
    for k in keys:
        print(f"{k}  {money(totals[k]):>12}  {bar(totals[k] / peak)}")
    print("-" * 52)
    print(f"{'average/mo':<10} {money(average):>12}   "
          f"total {money(window_total)}")


def cmd_matrix(args):
    months = args.months
    if months < 1:
        sys.exit("error: --months must be at least 1")
    data = load()
    first = date.today().replace(day=1)
    keys = [month_of(add_months(first, -i).isoformat())
            for i in range(months - 1, -1, -1)]

    grid = {}  # category -> {month: total}
    for e in expenses_only(data["expenses"]):
        m = month_of(e["date"])
        if m in keys:
            g = grid.setdefault(e["category"], {k: 0.0 for k in keys})
            g[m] = round(g[m] + e["amount"], 2)

    cats = sorted(grid, key=lambda c: sum(grid[c].values()), reverse=True)
    rows = []
    for c in cats:
        row = {"category": c}
        row.update({k: grid[c][k] for k in keys})
        row["total"] = round(sum(grid[c].values()), 2)
        rows.append(row)
    totals = {k: round(sum(grid[c][k] for c in cats), 2) for k in keys}
    grand = round(sum(totals.values()), 2)

    if getattr(args, "json", False):
        print(json.dumps({"months": keys, "rows": rows,
                          "totals": totals, "total": grand}, indent=2))
        return
    if not rows:
        print(f"no spending in the last {months} month(s)")
        return

    w = 11
    print(f"Category x month ({keys[0]} to {keys[-1]})")
    print("=" * (16 + w * (len(keys) + 1)))
    header = f"{'category':<14}" + "".join(f"{k[2:]:>{w}}" for k in keys) + \
        f"{'total':>{w}}"
    print(header)
    print("-" * len(header))
    for r in rows:
        line = f"{r['category']:<14}" + \
            "".join(f"{money(r[k]):>{w}}" for k in keys) + \
            f"{money(r['total']):>{w}}"
        print(line)
    print("-" * len(header))
    print(f"{'total':<14}" + "".join(f"{money(totals[k]):>{w}}" for k in keys) +
          f"{money(grand):>{w}}")


def cmd_tagmatrix(args):
    months = args.months
    if months < 1:
        sys.exit("error: --months must be at least 1")
    data = load()
    first = date.today().replace(day=1)
    keys = [month_of(add_months(first, -i).isoformat())
            for i in range(months - 1, -1, -1)]

    grid = {}  # tag -> {month: total}
    for e in expenses_only(data["expenses"]):
        m = month_of(e["date"])
        if m in keys:
            for t in e.get("tags", []):
                g = grid.setdefault(t, {k: 0.0 for k in keys})
                g[m] = round(g[m] + e["amount"], 2)

    tags = sorted(grid, key=lambda t: sum(grid[t].values()), reverse=True)
    rows = []
    for t in tags:
        row = {"tag": t}
        row.update({k: grid[t][k] for k in keys})
        row["total"] = round(sum(grid[t].values()), 2)
        rows.append(row)
    totals = {k: round(sum(grid[t][k] for t in tags), 2) for k in keys}
    grand = round(sum(totals.values()), 2)

    if getattr(args, "json", False):
        print(json.dumps({"months": keys, "rows": rows,
                          "totals": totals, "total": grand}, indent=2))
        return
    if not rows:
        print(f"no tagged spending in the last {months} month(s)")
        return

    w = 11
    print(f"Tag x month ({keys[0]} to {keys[-1]})")
    print("=" * (16 + w * (len(keys) + 1)))
    header = f"{'tag':<14}" + "".join(f"{k[2:]:>{w}}" for k in keys) + \
        f"{'total':>{w}}"
    print(header)
    print("-" * len(header))
    for r in rows:
        line = f"{('#' + r['tag']):<14}" + \
            "".join(f"{money(r[k]):>{w}}" for k in keys) + \
            f"{money(r['total']):>{w}}"
        print(line)
    print("-" * len(header))
    print(f"{'total':<14}" + "".join(f"{money(totals[k]):>{w}}" for k in keys) +
          f"{money(grand):>{w}}")


def cmd_tagtrend(args):
    tag = args.tag.strip().lstrip("#").lower()
    if not tag:
        sys.exit("error: tag must not be empty")
    months = args.months
    if months < 1:
        sys.exit("error: --months must be at least 1")
    data = load()
    first = date.today().replace(day=1)
    keys = [month_of(add_months(first, -i).isoformat())
            for i in range(months - 1, -1, -1)]

    totals = {k: 0.0 for k in keys}
    for e in expenses_only(data["expenses"]):
        if tag in e.get("tags", []):
            m = month_of(e["date"])
            if m in totals:
                totals[m] = round(totals[m] + e["amount"], 2)

    window_total = round(sum(totals.values()), 2)
    average = round(window_total / months, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "tag": tag,
            "months": [{"month": k, "total": totals[k]} for k in keys],
            "total": window_total,
            "average": average,
        }, indent=2))
        return

    if window_total == 0:
        print(f"no spending tagged #{tag} in the last {months} month(s)")
        return

    peak = max(totals.values())
    print(f"Trend for #{tag} (last {months} month(s))")
    print("=" * 52)
    for k in keys:
        print(f"{k}  {money(totals[k]):>12}  {bar(totals[k] / peak)}")
    print("-" * 52)
    print(f"{'average/mo':<10} {money(average):>12}   "
          f"total {money(window_total)}")


def cmd_sources(args):
    check_month(args.month)
    data = load()
    rows = income_only(data["expenses"])
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    agg = {}
    for e in rows:
        a = agg.setdefault(e["category"], {"count": 0, "total": 0.0})
        a["count"] += 1
        a["total"] = round(a["total"] + e["amount"], 2)

    if getattr(args, "json", False):
        print(json.dumps(agg, indent=2))
        return
    if not agg:
        where = f" in {args.month}" if args.month else ""
        print(f"no income recorded{where} yet. Try: income 3000 salary \"march pay\"")
        return

    print("Income by source" + (f" ({args.month})" if args.month else ""))
    print("=" * 48)
    total = 0.0
    for cat, v in sorted(agg.items(), key=lambda kv: kv[1]["total"], reverse=True):
        total += v["total"]
        print(f"{cat:<16} {v['count']:>3} entr(y/ies)  {money(v['total']):>12}")
    print("-" * 48)
    print(f"{'TOTAL':<16} {'':>3}              {money(round(total, 2)):>12}")


def cmd_untagged(args):
    check_month(args.month)
    data = load()
    rows = [e for e in expenses_only(data["expenses"]) if not e.get("tags")]
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    rows = sorted(rows, key=lambda e: (e["date"], e["id"]))

    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("no untagged expenses")
        return

    print("Untagged expenses")
    print("=" * 52)
    for e in rows:
        note = f" - {e['note']}" if e["note"] else ""
        print(f"#{e['id']:<4} {e['date']}  {money(e['amount']):>12}  "
              f"[{e['category']}]{note}")
    print("-" * 52)
    print(f"{len(rows)} untagged, total {money(sum(e['amount'] for e in rows))}")


def cmd_tags(args):
    check_month(args.month)
    data = load()
    rows = expenses_only(data["expenses"])
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    agg = {}
    for e in rows:
        for t in e.get("tags", []):
            a = agg.setdefault(t, {"count": 0, "total": 0.0})
            a["count"] += 1
            a["total"] = round(a["total"] + e["amount"], 2)

    if getattr(args, "json", False):
        print(json.dumps(agg, indent=2))
        return
    if not agg:
        where = f" in {args.month}" if args.month else ""
        print(f"no tags{where} yet - add #tags in a note, "
              "e.g. add 40 food \"dinner #work\"")
        return

    print("Tags" + (f" ({args.month})" if args.month else ""))
    print("=" * 48)
    for tag, v in sorted(agg.items(), key=lambda kv: kv[1]["total"], reverse=True):
        print(f"#{tag:<14} {v['count']:>3} item(s)  {money(v['total']):>12}")


def cmd_upcoming(args):
    days = args.days
    if days < 1:
        sys.exit("error: --days must be at least 1")
    data = load()
    today = date.today()
    horizon = today + timedelta(days=days)

    items = []
    for rule in data["recurring"]:
        for d in _occurrences(rule, horizon):
            if d > today:
                items.append({
                    "date": d.isoformat(),
                    "amount": rule["amount"],
                    "category": rule["category"],
                    "kind": rule.get("kind", "expense"),
                    "note": rule["note"],
                    "recur_id": rule["id"],
                })
    items.sort(key=lambda i: (i["date"], i["category"]))

    exp_total = sum(i["amount"] for i in items if i["kind"] == "expense")
    inc_total = sum(i["amount"] for i in items if i["kind"] == "income")

    if getattr(args, "json", False):
        print(json.dumps({
            "days": days,
            "until": horizon.isoformat(),
            "items": items,
            "expense_total": round(exp_total, 2),
            "income_total": round(inc_total, 2),
            "net": round(inc_total - exp_total, 2),
        }, indent=2))
        return

    if not items:
        print(f"nothing scheduled in the next {days} day(s)")
        return

    print(f"Upcoming (next {days} day(s), through {horizon.isoformat()})")
    print("=" * 52)
    for i in items:
        note = f" - {i['note']}" if i["note"] else ""
        mark = " +income" if i["kind"] == "income" else ""
        print(f"{i['date']}  {money(i['amount']):>12}  [{i['category']}]{note}{mark}")
    print("-" * 52)
    print(f"expenses {money(exp_total)}, income {money(inc_total)}, "
          f"net {money(inc_total - exp_total)}")


def _nice_budget(avg):
    """Round an average up to a friendly budget figure (nearest 5/10/25)."""
    if avg <= 0:
        return 0.0
    target = avg * 1.1  # a little headroom
    step = 5 if target < 100 else (10 if target < 500 else 25)
    return float(int((target + step - 0.01) // step * step))


def _recent_category_averages(data, months):
    """Average monthly spend per category over the last `months` months,
    averaged only over the months in that window that actually had spend."""
    today = date.today()
    window = {add_months(today, -i).isoformat()[:7] for i in range(months)}
    agg = {}
    for e in expenses_only(data["expenses"]):
        m = month_of(e["date"])
        if m in window:
            a = agg.setdefault(e["category"], {"total": 0.0, "months": set()})
            a["total"] = round(a["total"] + e["amount"], 2)
            a["months"].add(m)
    return {c: round(v["total"] / (len(v["months"]) or 1), 2)
            for c, v in agg.items()}


def cmd_suggest(args):
    months = args.months
    if months < 1:
        sys.exit("error: --months must be at least 1")
    data = load()
    averages = _recent_category_averages(data, months)

    suggestions = []
    for cat in sorted(averages):
        avg = averages[cat]
        suggestions.append({
            "category": cat,
            "average": avg,
            "suggested": _nice_budget(avg),
            "current": round(data["budgets"].get(cat, 0.0), 2)
            if cat in data["budgets"] else None,
        })

    if getattr(args, "json", False):
        print(json.dumps({"months": months, "suggestions": suggestions}, indent=2))
        return
    if not suggestions:
        print(f"no spending in the last {months} month(s) to base budgets on")
        return

    print(f"Suggested budgets (avg of last {months} month(s) with spend)")
    print("=" * 60)
    for s in suggestions:
        cur = "  (no budget)" if s["current"] is None else \
            f"  (now {money(s['current'])})"
        print(f"{s['category']:<16} avg {money(s['average']):>11}   "
              f"suggest {money(s['suggested']):>11}{cur}")
    print("-" * 60)
    print("set one with:  budget --category CAT --amount N  "
          "(or apply all with `autobudget`)")


def cmd_autobudget(args):
    months = args.months
    if months < 1:
        sys.exit("error: --months must be at least 1")
    data = load()
    averages = _recent_category_averages(data, months)

    planned = []  # (category, current, suggested)
    for cat in sorted(averages):
        suggested = _nice_budget(averages[cat])
        if suggested <= 0:
            continue
        current = data["budgets"].get(cat)
        if current is not None and not args.replace:
            continue  # keep an existing budget unless --replace
        if current == suggested:
            continue
        planned.append((cat, current, suggested))

    dry = getattr(args, "dry_run", False)
    if getattr(args, "json", False):
        print(json.dumps({
            "months": months, "replace": args.replace, "dry_run": dry,
            "set": [{"category": c, "from": cur, "to": s}
                    for c, cur, s in planned],
        }, indent=2))
        if planned and not dry:
            for c, _, s in planned:
                data["budgets"][c] = s
            save(data)
        return

    if not planned:
        print(f"no budgets to set from the last {months} month(s)"
              + ("" if args.replace else " (existing budgets kept; "
                 "use --replace to overwrite)"))
        return

    verb = "Would set" if dry else "Set"
    print(f"{verb} {len(planned)} budget(s) from the last {months} month(s):")
    for cat, cur, s in planned:
        note = "" if cur is None else f"  (was {money(cur)})"
        print(f"  {cat:<16} {money(s):>11}{note}")
    if dry:
        print("(dry run - nothing changed; rerun without --dry-run to apply)")
    else:
        for c, _, s in planned:
            data["budgets"][c] = s
        save(data)
        print("undo with `undo`.")


def cmd_categories(args):
    check_month(args.month)
    data = load()
    rows = expenses_only(data["expenses"])
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    agg = {}
    for e in rows:
        a = agg.setdefault(e["category"], {"count": 0, "total": 0.0})
        a["count"] += 1
        a["total"] = round(a["total"] + e["amount"], 2)
    # include categories that only have a budget set (all-time view only)
    if not args.month:
        for cat in data["budgets"]:
            agg.setdefault(cat, {"count": 0, "total": 0.0})

    result = {c: {**v, "budget": data["budgets"].get(c)}
              for c, v in agg.items()}

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return
    if not result:
        print(f"no categories{f' in {args.month}' if args.month else ''} yet")
        return

    print("Categories" + (f" ({args.month})" if args.month else ""))
    print("=" * 56)
    for cat, v in sorted(result.items(), key=lambda kv: kv[1]["total"],
                         reverse=True):
        budget = f"  budget {money(v['budget'])}" if v["budget"] else ""
        print(f"{cat:<14} {v['count']:>3} item(s)  {money(v['total']):>12}{budget}")


_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def cmd_weekday(args):
    check_month(args.month)
    data = load()
    rows = expenses_only(data["expenses"])
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]

    agg = {i: {"total": 0.0, "count": 0} for i in range(7)}
    for e in rows:
        wd = date.fromisoformat(e["date"]).weekday()
        agg[wd]["total"] = round(agg[wd]["total"] + e["amount"], 2)
        agg[wd]["count"] += 1

    weekdays = [{"day": _WEEKDAY_NAMES[i], "total": agg[i]["total"],
                 "count": agg[i]["count"],
                 "average": round(agg[i]["total"] / agg[i]["count"], 2)
                 if agg[i]["count"] else 0.0}
                for i in range(7)]

    if getattr(args, "json", False):
        print(json.dumps({"scope": args.month or "all time",
                          "weekdays": weekdays}, indent=2))
        return

    if not rows:
        scope = args.month or "your records"
        print(f"no expenses in {scope}")
        return

    peak = max(w["total"] for w in weekdays) or 0
    print(f"Spending by weekday ({args.month or 'all time'})")
    print("=" * 52)
    for w in weekdays:
        chart = bar(w["total"] / peak, width=18) if peak else bar(0, width=18)
        print(f"{w['day']}  {money(w['total']):>12}  ({w['count']:>3})  {chart}")


_HEATMAP_GLYPHS = [".", ":", "+", "*", "#"]  # none, low, med, high, peak


def cmd_heatmap(args):
    check_month(args.month)
    data = load()
    period = args.month or date.today().isoformat()[:7]
    year, mon = (int(x) for x in period.split("-"))
    days_in_month = calendar.monthrange(year, mon)[1]

    spend = {d: 0.0 for d in range(1, days_in_month + 1)}
    for e in expenses_only(data["expenses"]):
        if month_of(e["date"]) == period:
            d = int(e["date"][8:10])
            spend[d] = round(spend[d] + e["amount"], 2)

    days = [{"date": f"{period}-{d:02d}", "day": d, "spending": spend[d]}
            for d in range(1, days_in_month + 1)]
    total = round(sum(spend.values()), 2)
    peak = max(spend.values()) if spend else 0.0
    busiest = None
    if peak > 0:
        bd = max(spend, key=lambda d: spend[d])
        busiest = {"date": f"{period}-{bd:02d}", "spending": spend[bd]}

    if getattr(args, "json", False):
        print(json.dumps({"month": period, "days": days, "total": total,
                          "max": round(peak, 2), "busiest": busiest}, indent=2))
        return

    def glyph(amount):
        if peak <= 0 or amount <= 0:
            return _HEATMAP_GLYPHS[0]
        return _HEATMAP_GLYPHS[1 + min(3, int(amount / peak * 3.999))]

    print(f"Spending heatmap - {period}")
    print("=" * 36)
    print(" Mo  Tu  We  Th  Fr  Sa  Su")
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, mon):
        cells = []
        for d in week:
            cells.append("    " if d == 0 else f"{d:2d}{glyph(spend[d])} ")
        print("".join(cells).rstrip())
    print("-" * 36)
    legend = "  ".join(f"{g} {lbl}" for g, lbl in zip(
        _HEATMAP_GLYPHS, ["none", "low", "med", "high", "peak"]))
    print(legend)
    print(f"total {money(total)}" + (
        f"   busiest {busiest['date']} ({money(busiest['spending'])})"
        if busiest else ""))


def cmd_cumulative(args):
    check_month(args.month)
    data = load()
    today = date.today()
    period = args.month or today.isoformat()[:7]
    year, mon = (int(x) for x in period.split("-"))
    dim = calendar.monthrange(year, mon)[1]
    last_day = today.day if period == today.isoformat()[:7] else dim

    per_day = {d: 0.0 for d in range(1, last_day + 1)}
    for e in expenses_only(data["expenses"]):
        if month_of(e["date"]) == period:
            d = int(e["date"][8:10])
            if d in per_day:
                per_day[d] = round(per_day[d] + e["amount"], 2)

    rows, running = [], 0.0
    for d in range(1, last_day + 1):
        running = round(running + per_day[d], 2)
        rows.append({"date": f"{period}-{d:02d}", "spending": per_day[d],
                     "cumulative": running})
    total = running

    if getattr(args, "json", False):
        print(json.dumps({"month": period, "days": rows, "total": total},
                         indent=2))
        return
    if total == 0:
        print(f"no spending in {period}")
        return

    print(f"Cumulative spending - {period}")
    print("=" * 52)
    for r in rows:
        if r["spending"]:
            bar_w = bar(r["cumulative"] / total) if total else ""
            print(f"{r['date']}  +{money(r['spending']):>10}  "
                  f"={money(r['cumulative']):>11}  {bar_w}")
    print("-" * 52)
    print(f"total {money(total)} over {last_day} day(s)")


def cmd_streak(args):
    check_month(args.month)
    data = load()
    today = date.today()
    period = args.month or today.isoformat()[:7]
    year, mon = (int(x) for x in period.split("-"))
    days_in_month = calendar.monthrange(year, mon)[1]
    last = today.day if period == today.isoformat()[:7] else days_in_month

    spend_dates = {e["date"] for e in expenses_only(data["expenses"])
                   if month_of(e["date"]) == period}

    spend_days = no_spend_days = longest = current = 0
    for day in range(1, last + 1):
        iso = f"{year:04d}-{mon:02d}-{day:02d}"
        if iso in spend_dates:
            spend_days += 1
            current = 0
        else:
            no_spend_days += 1
            current += 1
            longest = max(longest, current)

    if getattr(args, "json", False):
        print(json.dumps({
            "month": period, "days_considered": last,
            "spend_days": spend_days, "no_spend_days": no_spend_days,
            "longest_no_spend": longest, "current_no_spend": current,
        }, indent=2))
        return

    print(f"Spending streak for {period} (through day {last})")
    print("=" * 48)
    print(f"{'days considered':<24} {last}")
    print(f"{'spend days':<24} {spend_days}")
    print(f"{'no-spend days':<24} {no_spend_days}")
    print(f"{'longest no-spend streak':<24} {longest} day(s)")
    print(f"{'current no-spend streak':<24} {current} day(s)")


def cmd_balance(args):
    data = load()
    per_month = {}
    for e in data["expenses"]:
        m = month_of(e["date"])
        delta = e["amount"] if kind_of(e) == "income" else -e["amount"]
        per_month[m] = round(per_month.get(m, 0) + delta, 2)

    rows, running = [], 0.0
    for m in sorted(per_month):
        running = round(running + per_month[m], 2)
        rows.append({"month": m, "net": per_month[m], "balance": running})

    if getattr(args, "json", False):
        print(json.dumps({"months": rows}, indent=2))
        return
    if not rows:
        print("nothing recorded yet")
        return

    print("Running balance (cumulative net)")
    print("=" * 52)
    for r in rows:
        print(f"{r['month']}   net {money(r['net']):>12}   "
              f"balance {money(r['balance']):>12}")


# monthly-equivalent multipliers for each recurring frequency
_MONTHLY_FACTOR = {"day": 365 / 12, "week": 52 / 12, "month": 1.0}


def cmd_commitments(args):
    data = load()
    rules = []
    for r in data["recurring"]:
        factor = _MONTHLY_FACTOR.get(r["every"], 1.0)
        monthly = round(r["amount"] * factor, 2)
        rules.append({
            "id": r["id"],
            "category": r["category"],
            "note": r["note"],
            "every": r["every"],
            "amount": r["amount"],
            "kind": r.get("kind", "expense"),
            "monthly": monthly,
            "annual": round(monthly * 12, 2),
        })
    rules.sort(key=lambda x: (x["kind"], -x["monthly"]))

    m_exp = round(sum(x["monthly"] for x in rules if x["kind"] == "expense"), 2)
    m_inc = round(sum(x["monthly"] for x in rules if x["kind"] == "income"), 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "rules": rules,
            "monthly_expense": m_exp,
            "monthly_income": m_inc,
            "monthly_net": round(m_inc - m_exp, 2),
            "annual_expense": round(m_exp * 12, 2),
            "annual_income": round(m_inc * 12, 2),
            "annual_net": round((m_inc - m_exp) * 12, 2),
        }, indent=2))
        return

    if not rules:
        print("no recurring rules. Try: recur add 1200 rent --every month")
        return

    print("Recurring commitments (normalized to monthly)")
    print("=" * 60)
    for x in rules:
        note = f" - {x['note']}" if x["note"] else ""
        mark = " +income" if x["kind"] == "income" else ""
        print(f"#{x['id']:<3} {money(x['monthly']):>12}/mo  "
              f"({money(x['amount'])}/{x['every']})  [{x['category']}]{note}{mark}")
    print("-" * 60)
    print(f"monthly: expense {money(m_exp)}, income {money(m_inc)}, "
          f"net {money(m_inc - m_exp)}")
    print(f"annual:  expense {money(m_exp * 12)}, income {money(m_inc * 12)}, "
          f"net {money((m_inc - m_exp) * 12)}")


def cmd_savings(args):
    data = load()
    agg = {}
    for e in data["expenses"]:
        m = month_of(e["date"])
        a = agg.setdefault(m, {"income": 0.0, "spending": 0.0})
        if kind_of(e) == "income":
            a["income"] = round(a["income"] + e["amount"], 2)
        else:
            a["spending"] = round(a["spending"] + e["amount"], 2)

    rows = []
    for m in sorted(agg):
        inc, spend = agg[m]["income"], agg[m]["spending"]
        net = round(inc - spend, 2)
        rate = round(net / inc * 100, 1) if inc > 0 else None
        rows.append({"month": m, "income": inc, "spending": spend,
                     "net": net, "rate": rate})

    if getattr(args, "json", False):
        print(json.dumps({"months": rows}, indent=2))
        return
    if not rows:
        print("nothing recorded yet")
        return

    print("Monthly savings rate (net / income)")
    print("=" * 60)
    for r in rows:
        rate = "  n/a" if r["rate"] is None else f"{r['rate']:>5.1f}%"
        bar = ""
        if r["rate"] is not None:
            filled = max(0, min(20, int(round(r["rate"] / 5))))
            bar = "  " + "#" * filled
        print(f"{r['month']}  income {money(r['income']):>11}  "
              f"net {money(r['net']):>11}  {rate}{bar}")


def cmd_forecast(args):
    data = load()
    today = date.today()
    year = today.year
    ys = f"{year:04d}"
    elapsed = today.timetuple().tm_yday
    days_in_year = (date(year, 12, 31) - date(year, 1, 1)).days + 1
    spend = sum(e["amount"] for e in data["expenses"]
                if e["date"][:4] == ys and kind_of(e) == "expense")
    income = sum(e["amount"] for e in data["expenses"]
                 if e["date"][:4] == ys and kind_of(e) == "income")

    def proj(v):
        return round(v / elapsed * days_in_year, 2) if elapsed else 0.0

    ps, pi = proj(spend), proj(income)
    result = {
        "year": year, "day_of_year": elapsed, "days_in_year": days_in_year,
        "spending": round(spend, 2), "income": round(income, 2),
        "net": round(income - spend, 2),
        "projected_spending": ps, "projected_income": pi,
        "projected_net": round(pi - ps, 2),
    }

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return

    print(f"Year-end forecast ({year})")
    print("=" * 52)
    print(f"{'':<10}{'so far':>16}{'projected':>18}")
    print(f"{'spending':<10}{money(spend):>16}{money(ps):>18}")
    print(f"{'income':<10}{money(income):>16}{money(pi):>18}")
    print(f"{'net':<10}{money(income - spend):>16}{money(pi - ps):>18}")
    print("-" * 52)
    print(f"day {elapsed} of {days_in_year}")


def cmd_quarter(args):
    data = load()
    year = args.year if args.year is not None else date.today().year
    ys = f"{year:04d}"
    spend = [0.0, 0.0, 0.0, 0.0]
    inc = [0.0, 0.0, 0.0, 0.0]
    for e in data["expenses"]:
        if e["date"][:4] == ys:
            qi = (int(e["date"][5:7]) - 1) // 3
            if kind_of(e) == "income":
                inc[qi] += e["amount"]
            else:
                spend[qi] += e["amount"]
    quarters = [{"quarter": f"Q{i + 1}", "spending": round(spend[i], 2),
                 "income": round(inc[i], 2),
                 "net": round(inc[i] - spend[i], 2)} for i in range(4)]

    if getattr(args, "json", False):
        print(json.dumps({"year": year, "quarters": quarters}, indent=2))
        return
    if not any(spend) and not any(inc):
        print(f"nothing recorded in {year}")
        return

    print(f"{year} by quarter")
    print("=" * 52)
    print(f"{'':<6}{'spending':>14}{'income':>14}{'net':>14}")
    for q in quarters:
        print(f"{q['quarter']:<6}{money(q['spending']):>14}"
              f"{money(q['income']):>14}{money(q['net']):>14}")


def cmd_years(args):
    data = load()
    agg = {}
    for e in data["expenses"]:
        y = e["date"][:4]
        a = agg.setdefault(y, {"spending": 0.0, "income": 0.0})
        if kind_of(e) == "income":
            a["income"] = round(a["income"] + e["amount"], 2)
        else:
            a["spending"] = round(a["spending"] + e["amount"], 2)

    rows = []
    for y in sorted(agg):
        s, i = agg[y]["spending"], agg[y]["income"]
        rows.append({"year": y, "spending": s, "income": i,
                     "net": round(i - s, 2)})

    if getattr(args, "json", False):
        print(json.dumps({"years": rows}, indent=2))
        return
    if not rows:
        print("nothing recorded yet")
        return

    print("By year")
    print("=" * 56)
    print(f"{'year':<8}{'spending':>14}{'income':>14}{'net':>14}")
    for r in rows:
        print(f"{r['year']:<8}{money(r['spending']):>14}"
              f"{money(r['income']):>14}{money(r['net']):>14}")


def cmd_year(args):
    data = load()
    year = args.year if args.year is not None else date.today().year
    year_str = f"{year:04d}"
    months = [f"{year_str}-{m:02d}" for m in range(1, 13)]

    spend = {k: 0.0 for k in months}
    income = 0.0
    for e in data["expenses"]:
        if e["date"][:4] == year_str:
            if kind_of(e) == "income":
                income += e["amount"]
            else:
                spend[month_of(e["date"])] = round(
                    spend[month_of(e["date"])] + e["amount"], 2)
    spending = round(sum(spend.values()), 2)
    income = round(income, 2)
    net = round(income - spending, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "year": year,
            "months": [{"month": k, "spending": spend[k]} for k in months],
            "spending": spending, "income": income, "net": net,
            "average": round(spending / 12, 2),
        }, indent=2))
        return

    if spending == 0 and income == 0:
        print(f"nothing recorded in {year}")
        return

    peak = max(spend.values()) or 0
    print(f"Year {year}")
    print("=" * 48)
    for k in months:
        chart = bar(spend[k] / peak, width=18) if peak else bar(0, width=18)
        print(f"{k}  {money(spend[k]):>12}  {chart}")
    print("-" * 48)
    print(f"spending {money(spending)}, income {money(income)}, net {money(net)}")
    print(f"average/mo {money(round(spending / 12, 2))}")


def cmd_day(args):
    data = load()
    d = parse_date(args.date)
    rows = sorted((e for e in data["expenses"] if e["date"] == d),
                  key=lambda e: e["id"])
    spending = round(sum(e["amount"] for e in rows
                         if kind_of(e) == "expense"), 2)
    income = round(sum(e["amount"] for e in rows
                       if kind_of(e) == "income"), 2)

    if getattr(args, "json", False):
        print(json.dumps({"date": d, "entries": rows, "spending": spending,
                          "income": income, "net": round(income - spending, 2)},
                         indent=2))
        return

    weekday = datetime.strptime(d, "%Y-%m-%d").strftime("%A")
    print(f"{d} ({weekday})")
    print("=" * 50)
    if not rows:
        print("no entries")
        return
    for e in rows:
        note = f" - {e['note']}" if e["note"] else ""
        mark = " +income" if kind_of(e) == "income" else ""
        print(f"#{e['id']:<4} {money(e['amount']):>12}  [{e['category']}]"
              f"{note}{mark}")
    print("-" * 50)
    print(f"spending {money(spending)}, income {money(income)}, "
          f"net {money(income - spending)}")


def cmd_week(args):
    if args.offset < 0:
        sys.exit("error: --offset cannot be negative")
    data = load()
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    start = monday - timedelta(weeks=args.offset)
    end = start + timedelta(days=6)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]

    per_day = {d: 0.0 for d in days}
    income = 0.0
    for e in data["expenses"]:
        d = e["date"]
        if days[0] <= d <= days[-1]:
            if kind_of(e) == "income":
                income += e["amount"]
            else:
                per_day[d] = round(per_day[d] + e["amount"], 2)
    spending = round(sum(per_day.values()), 2)
    income = round(income, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "start": days[0], "end": days[-1],
            "days": [{"date": d, "spending": per_day[d]} for d in days],
            "spending": spending, "income": income,
            "net": round(income - spending, 2),
        }, indent=2))
        return

    print(f"Week of {days[0]} to {days[-1]}")
    print("=" * 48)
    peak = max(per_day.values()) if any(per_day.values()) else 0
    for d in days:
        label = datetime.strptime(d, "%Y-%m-%d").strftime("%a %m-%d")
        chart = bar(per_day[d] / peak, width=18) if peak else bar(0, width=18)
        print(f"{label}  {money(per_day[d]):>10}  {chart}")
    print("-" * 48)
    print(f"spending {money(spending)}, income {money(income)}, "
          f"net {money(income - spending)}")


def cmd_weekly(args):
    weeks = args.weeks
    if weeks < 1:
        sys.exit("error: --weeks must be at least 1")
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    data = load()
    rows = expenses_only(data["expenses"])

    series = []
    for i in range(weeks - 1, -1, -1):
        start = monday - timedelta(weeks=i)
        end = start + timedelta(days=6)
        s, e = start.isoformat(), end.isoformat()
        total = round(sum(x["amount"] for x in rows if s <= x["date"] <= e), 2)
        series.append({"week_start": s, "total": total})
    window_total = round(sum(w["total"] for w in series), 2)
    average = round(window_total / weeks, 2)

    if getattr(args, "json", False):
        print(json.dumps({"weeks": series, "total": window_total,
                          "average": average}, indent=2))
        return
    if window_total == 0:
        print(f"no spending in the last {weeks} week(s)")
        return

    peak = max(w["total"] for w in series) or 0
    print(f"Weekly spending (last {weeks} week(s), week starts Mon)")
    print("=" * 52)
    for w in series:
        chart = bar(w["total"] / peak, width=18) if peak else bar(0, width=18)
        print(f"{w['week_start']}  {money(w['total']):>12}  {chart}")
    print("-" * 52)
    print(f"{'average/wk':<12} {money(average):>12}   total {money(window_total)}")


def cmd_month(args):
    check_month(args.month)
    data = load()
    period = args.month or date.today().isoformat()[:7]
    rows = [e for e in data["expenses"] if month_of(e["date"]) == period]
    exp = expenses_only(rows)
    inc = income_only(rows)

    spending = sum(e["amount"] for e in exp)
    income = sum(e["amount"] for e in inc)
    net = income - spending

    cat_tot = {}
    for e in exp:
        cat_tot[e["category"]] = round(cat_tot.get(e["category"], 0) + e["amount"], 2)

    budgets = {}
    for cat, limit in data["budgets"].items():
        spent = sum(e["amount"] for e in exp if e["category"] == cat)
        budgets[cat] = {"spent": round(spent, 2), "limit": round(limit, 2)}

    goal = data.get("goal")

    if getattr(args, "json", False):
        print(json.dumps({
            "month": period,
            "income": round(income, 2),
            "spending": round(spending, 2),
            "net": round(net, 2),
            "expense_count": len(exp),
            "income_count": len(inc),
            "by_category": cat_tot,
            "budgets": budgets,
            "goal": goal,
        }, indent=2))
        return

    try:
        label = datetime.strptime(period, "%Y-%m").strftime("%B %Y")
    except ValueError:
        label = period
    if not exp and not inc:
        print(f"nothing recorded for {label}")
        return

    print(f"{label}  ({period})")
    print("=" * 52)
    print(f"{'income':<10} {money(income):>14}")
    print(f"{'spending':<10} {money(spending):>14}")
    print(f"{'net':<10} {money(net):>14}")

    if cat_tot:
        peak = max(cat_tot.values())
        print()
        print("Top categories")
        print("-" * 52)
        top = sorted(cat_tot.items(), key=lambda kv: kv[1], reverse=True)[:5]
        for cat, amt in top:
            print(f"{cat:<14} {money(amt):>12}  {bar(amt / peak)}")

    if budgets:
        print()
        print("Budgets")
        print("-" * 52)
        for cat, b in sorted(budgets.items()):
            frac = b["spent"] / b["limit"] if b["limit"] else 0
            flag = "  <-- OVER" if b["spent"] > b["limit"] else ""
            print(f"{cat:<14} {money(b['spent']):>10} / {money(b['limit']):<10} "
                  f"{frac * 100:4.0f}%{flag}")

    if goal is not None:
        print()
        status = (f"met (+{money(net - goal)})" if net >= goal
                  else f"{money(goal - net)} to go")
        print(f"savings goal   {money(net)} of {money(goal)}   {status}")


def cmd_insights(args):
    check_month(args.month)
    data = load()
    today = date.today()
    period = args.month or today.isoformat()[:7]
    prev = add_months(date.fromisoformat(f"{period}-01"), -1).isoformat()[:7]

    all_exp = expenses_only(data["expenses"])
    exp = [e for e in all_exp if month_of(e["date"]) == period]
    inc = [e for e in income_only(data["expenses"]) if month_of(e["date"]) == period]
    spending = round(sum(e["amount"] for e in exp), 2)
    income = round(sum(e["amount"] for e in inc), 2)
    net = round(income - spending, 2)
    prev_spend = round(sum(e["amount"] for e in all_exp
                           if month_of(e["date"]) == prev), 2)

    cat_tot = {}
    for e in exp:
        cat_tot[e["category"]] = round(cat_tot.get(e["category"], 0) + e["amount"], 2)

    insights = []
    if not exp and not inc:
        insights.append(f"Nothing recorded for {period} yet.")
    else:
        if income > 0:
            rate = round(net / income * 100, 1)
            if net >= 0:
                insights.append(f"You saved {money(net)} this month "
                                f"({rate}% of income).")
            else:
                insights.append(f"You spent {money(-net)} more than you "
                                "earned this month.")
        elif spending > 0:
            insights.append(f"You spent {money(spending)} this month with no "
                            "recorded income.")
        if cat_tot and spending > 0:
            top_cat, top_amt = max(cat_tot.items(), key=lambda kv: kv[1])
            share = round(top_amt / spending * 100)
            insights.append(f"{top_cat} was your biggest category at {share}% "
                            f"of spending ({money(top_amt)}).")
        if prev_spend > 0:
            delta = round(spending - prev_spend, 2)
            pct = round(abs(delta) / prev_spend * 100)
            if delta > 0:
                insights.append(f"Spending is up {pct}% vs {prev} "
                                f"({money(prev_spend)} to {money(spending)}).")
            elif delta < 0:
                insights.append(f"Spending is down {pct}% vs {prev} "
                                f"({money(prev_spend)} to {money(spending)}).")
            else:
                insights.append(f"Spending is flat vs {prev} ({money(spending)}).")
        overs = [(cat, cat_tot.get(cat, 0.0), limit)
                 for cat, limit in data["budgets"].items()
                 if cat_tot.get(cat, 0.0) > limit]
        if overs:
            for cat, sp, limit in sorted(overs):
                insights.append(f"Over budget on {cat}: {money(sp)} of "
                                f"{money(limit)}.")
        elif data["budgets"]:
            insights.append("All budgets are on track.")
        if exp:
            big = max(exp, key=lambda e: e["amount"])
            note = f" - {big['note']}" if big.get("note") else ""
            insights.append(f"Largest expense: {money(big['amount'])} on "
                            f"{big['category']}{note} ({big['date']}).")
        year, mon = (int(x) for x in period.split("-"))
        dim = calendar.monthrange(year, mon)[1]
        last_day = today.day if period == today.isoformat()[:7] else dim
        spend_days = {int(e["date"][8:10]) for e in exp
                      if int(e["date"][8:10]) <= last_day}
        no_spend = last_day - len(spend_days)
        if no_spend > 0:
            tail = ("so far this month" if period == today.isoformat()[:7]
                    else "this month")
            insights.append(f"{no_spend} no-spend day"
                            f"{'' if no_spend == 1 else 's'} {tail}.")

    metrics = {"income": income, "spending": spending, "net": net,
               "prev_spending": prev_spend}
    if getattr(args, "json", False):
        print(json.dumps({"month": period, "insights": insights,
                          "metrics": metrics}, indent=2))
        return

    print(f"Insights - {period}")
    print("=" * 56)
    for s in insights:
        print(f"- {s}")


def cmd_range(args):
    start = parse_date(args.start)
    end = parse_date(args.end) if args.end else date.today().isoformat()
    if end < start:
        start, end = end, start
    data = load()
    rows = [e for e in data["expenses"] if start <= e["date"] <= end]
    exp = expenses_only(rows)
    inc = income_only(rows)
    spending = round(sum(e["amount"] for e in exp), 2)
    income = round(sum(e["amount"] for e in inc), 2)
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1

    cat_tot = {}
    for e in exp:
        cat_tot[e["category"]] = round(cat_tot.get(e["category"], 0) + e["amount"], 2)
    by_category = dict(sorted(cat_tot.items(), key=lambda kv: kv[1], reverse=True))

    if getattr(args, "json", False):
        print(json.dumps({
            "start": start, "end": end, "days": days,
            "income": income, "spending": spending,
            "net": round(income - spending, 2),
            "expense_count": len(exp), "income_count": len(inc),
            "by_category": by_category,
            "per_day": round(spending / days, 2) if days else 0.0,
        }, indent=2))
        return

    print(f"{start} to {end}  ({days} day{'' if days == 1 else 's'})")
    print("=" * 52)
    print(f"{'income':<10} {money(income):>14}")
    print(f"{'spending':<10} {money(spending):>14}")
    print(f"{'net':<10} {money(income - spending):>14}")
    print(f"{'per day':<10} {money(spending / days if days else 0):>14}")
    if by_category:
        peak = max(by_category.values())
        print()
        print("By category")
        print("-" * 52)
        for cat, amt in by_category.items():
            print(f"{cat:<14} {money(amt):>12}  {bar(amt / peak)}")


def cmd_retag(args):
    old = args.old.strip().lstrip("#").lower()
    new = args.new.strip().lstrip("#").lower()
    if not old or not new:
        sys.exit("error: tag cannot be empty")
    if not re.fullmatch(r"\w+", new):
        sys.exit("error: new tag must be a single word (letters, digits, _)")
    if old == new:
        sys.exit("error: old and new tags are the same")
    data = load()
    pat = re.compile(r"#" + re.escape(old) + r"\b", re.IGNORECASE)

    entries = 0
    for e in data["expenses"]:
        if old in e.get("tags", []):
            e["note"] = pat.sub("#" + new, e["note"])
            e["tags"] = parse_tags(e["note"])
            entries += 1
    rules = 0
    for r in data["recurring"]:
        if old in parse_tags(r.get("note", "")):
            r["note"] = pat.sub("#" + new, r["note"])
            rules += 1

    if not entries and not rules:
        print(f"nothing to retag - no #{old} found")
        return
    save(data)
    print(f"retagged #{old} -> #{new}: {entries} entr"
          f"{'y' if entries == 1 else 'ies'}, {rules} recurring rule(s)")


def _clean_tag_names(names):
    out = []
    for n in names:
        n = n.strip().lstrip("#").lower()
        if not n:
            continue
        if not re.fullmatch(r"\w+", n):
            sys.exit(f"error: '{n}' is not a valid tag (letters, digits, _)")
        if n not in out:
            out.append(n)
    return out


def cmd_tag(args):
    tags = _clean_tag_names(args.tags)
    if not tags:
        sys.exit("error: give at least one tag to add")
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no expense with id #{args.id}")
    existing = set(e.get("tags", []))
    added = [t for t in tags if t not in existing]
    if not added:
        print(f"#{e['id']} already has: " + ", ".join("#" + t for t in tags))
        return
    note = e.get("note", "").rstrip()
    e["note"] = (note + " " + " ".join("#" + t for t in added)).strip()
    e["tags"] = parse_tags(e["note"])
    save(data)
    print(f"#{e['id']} tagged " + ", ".join("#" + t for t in added) +
          f"  (now: {', '.join('#' + t for t in e['tags']) or 'none'})")


def cmd_untag(args):
    tags = _clean_tag_names(args.tags)
    if not tags:
        sys.exit("error: give at least one tag to remove")
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no expense with id #{args.id}")
    present = [t for t in tags if t in e.get("tags", [])]
    if not present:
        print(f"#{e['id']} has none of: " + ", ".join("#" + t for t in tags))
        return
    note = e.get("note", "")
    for t in present:
        note = re.sub(r"#" + re.escape(t) + r"\b", "", note, flags=re.IGNORECASE)
    e["note"] = re.sub(r"\s{2,}", " ", note).strip()
    e["tags"] = parse_tags(e["note"])
    save(data)
    print(f"#{e['id']} untagged " + ", ".join("#" + t for t in present) +
          f"  (now: {', '.join('#' + t for t in e['tags']) or 'none'})")


def cmd_note(args):
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no expense with id #{args.id}")
    if args.clear and args.text is not None:
        sys.exit("error: pass either new text or --clear, not both")
    if args.append and not args.text:
        sys.exit("error: --append needs text to append")

    if args.clear:
        e["note"] = ""
    elif args.text is not None:
        text = args.text.strip()
        if args.append and e.get("note"):
            e["note"] = (e["note"].rstrip() + " " + text).strip()
        else:
            e["note"] = text
    else:  # no change requested - just show the current note
        print(f"#{e['id']}: {e.get('note') or '(no note)'}")
        return
    e["tags"] = parse_tags(e["note"])
    save(data)
    print(f"#{e['id']} note: {e['note'] or '(cleared)'}")


def cmd_recategorize(args):
    old = clean_category(args.old)
    new = clean_category(args.new)
    if old == new:
        sys.exit("error: old and new categories are the same")
    data = load()

    moved = sum(1 for e in data["expenses"] if e["category"] == old)
    for e in data["expenses"]:
        if e["category"] == old:
            e["category"] = new
    rules = 0
    for r in data["recurring"]:
        if r["category"] == old:
            r["category"] = new
            rules += 1

    budget_note = ""
    if old in data["budgets"]:
        if new in data["budgets"]:
            del data["budgets"][old]
            budget_note = f"; kept existing [{new}] budget, dropped [{old}]'s"
        else:
            data["budgets"][new] = data["budgets"].pop(old)
            budget_note = f"; moved the budget to [{new}]"

    if not moved and not rules and not budget_note:
        print(f"nothing to recategorize - no [{old}] found")
        return

    save(data)
    print(f"recategorized [{old}] -> [{new}]: {moved} expense(s), "
          f"{rules} recurring rule(s){budget_note}")


def month_net(rows, period):
    """Income minus expenses for a given YYYY-MM."""
    inc = sum(e["amount"] for e in rows
              if kind_of(e) == "income" and month_of(e["date"]) == period)
    exp = sum(e["amount"] for e in rows
              if kind_of(e) == "expense" and month_of(e["date"]) == period)
    return inc - exp


def cmd_goal(args):
    data = load()

    if args.clear:
        data["goal"] = None
        save(data)
        print("savings goal cleared")
        return

    if args.amount is not None:
        if args.amount < 0:
            sys.exit("error: goal cannot be negative")
        data["goal"] = round(args.amount, 2)
        save(data)
        print(f"set monthly savings goal to {money(data['goal'])}")

    goal = data["goal"]
    if goal is None:
        print("no savings goal set. Try: goal --amount 500")
        return

    period = date.today().isoformat()[:7]
    net = month_net(data["expenses"], period)
    frac = net / goal if goal else 0
    print(f"Savings goal for {period}")
    print("=" * 48)
    print(f"{'goal':<10} {money(goal)}")
    print(f"{'net so far':<10} {money(net)}  {bar(frac)} {frac * 100:4.0f}%")
    if net >= goal:
        print(f"met - {money(net - goal)} over your goal")
    else:
        print(f"{money(goal - net)} to go")


def _completion_spec():
    """Introspect the parser: top-level subcommands and each one's long options
    (plus any nested subcommand names, e.g. for `recur`)."""
    parser = build_parser()
    top, subs = [], {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                top.append(name)
                opts, nested = [], []
                for a in sub._actions:
                    opts += [o for o in a.option_strings if o.startswith("--")]
                    if isinstance(a, argparse._SubParsersAction):
                        nested += list(a.choices.keys())
                subs[name] = sorted(set(opts)) + sorted(set(nested))
    return top, subs


def _bash_completion(top, subs):
    arms = "\n".join(
        f'        {name}) COMPREPLY=( $(compgen -W "{" ".join(subs[name]) or "--help"}"'
        f' -- "$cur") ) ;;'
        for name in top)
    names = " ".join(top)
    return (
        "_ledgerling() {\n"
        '    local cur="${COMP_WORDS[COMP_CWORD]}"\n'
        f'    local subcommands="{names} --help --version"\n'
        '    if [ "$COMP_CWORD" -eq 1 ]; then\n'
        '        COMPREPLY=( $(compgen -W "$subcommands" -- "$cur") )\n'
        "        return\n"
        "    fi\n"
        '    case "${COMP_WORDS[1]}" in\n'
        f"{arms}\n"
        "    esac\n"
        "}\n"
        "complete -F _ledgerling ledgerling\n"
    )


def _zsh_completion(top, subs):
    arms = "\n".join(
        f'        {name}) compadd -- {" ".join(subs[name]) or "--help"} ;;'
        for name in top)
    names = " ".join(top)
    return (
        "#compdef ledgerling\n"
        "_ledgerling() {\n"
        "    if (( CURRENT == 2 )); then\n"
        f"        compadd -- {names} --help --version\n"
        "        return\n"
        "    fi\n"
        "    case ${words[2]} in\n"
        f"{arms}\n"
        "    esac\n"
        "}\n"
        "compdef _ledgerling ledgerling\n"
    )


def cmd_completion(args):
    top, subs = _completion_spec()
    if args.shell == "zsh":
        print(_zsh_completion(top, subs))
    else:
        print(_bash_completion(top, subs))


def cmd_web(args):
    from . import web  # lazy import (web imports cli)
    web.serve(port=args.port, open_browser=not args.no_browser)


def cmd_version(args):
    print(f"ledgerling {__version__}")


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _dir_summary(path):
    try:
        files = [f for f in os.listdir(path)
                 if os.path.isfile(os.path.join(path, f))]
        size = sum(_file_size(os.path.join(path, f)) or 0 for f in files)
        return len(files), size
    except OSError:
        return None, None


def cmd_where(args):
    entries = [
        ("data file", DATA_FILE, "file"),
        ("config file", CONFIG_FILE, "file"),
        ("exports", EXPORT_DIR, "dir"),
        ("backups", BACKUP_DIR, "dir"),
    ]
    if getattr(args, "json", False):
        out = {"home": HOME_DIR, "items": {}}
        for label, path, kind in entries:
            if kind == "file":
                size = _file_size(path)
                out["items"][label] = {"path": path, "exists": size is not None,
                                       "bytes": size}
            else:
                n, size = _dir_summary(path)
                out["items"][label] = {"path": path, "exists": n is not None,
                                       "files": n, "bytes": size}
        print(json.dumps(out, indent=2))
        return

    print("Ledgerling data folder")
    print("=" * 56)
    print(HOME_DIR)
    print("-" * 56)
    for label, path, kind in entries:
        if kind == "file":
            size = _file_size(path)
            info = f"{size:,} bytes" if size is not None else "not created yet"
        else:
            n, size = _dir_summary(path)
            info = (f"{n} file(s), {size:,} bytes" if n is not None
                    else "not created yet")
        print(f"{label:<12} {os.path.basename(path) or path:<26} {info}")
    print("-" * 56)
    print("Everything stays in this folder - nothing is posted or pushed.")


def cmd_config(args):
    cfg = load_config()

    if args.reset:
        save_config(dict(DEFAULT_CONFIG))
        print("config reset to defaults")
        return

    changed = False
    if args.currency is not None:
        cur = args.currency.strip()
        if not cur or len(cur) > 8:
            sys.exit("error: currency must be 1-8 characters")
        cfg["currency"] = cur
        changed = True
    if args.list_limit is not None:
        if args.list_limit < 1:
            sys.exit("error: --list-limit must be at least 1")
        cfg["list_limit"] = args.list_limit
        changed = True
    if getattr(args, "symbol_position", None) is not None:
        cfg["symbol_position"] = args.symbol_position
        changed = True

    if changed:
        save_config(cfg)
        _CONFIG.update(cfg)   # so the sample below prints with the new settings
        print("config updated")

    # Always show the resulting settings.
    print(f"{'currency':<16} {cfg['currency']}")
    print(f"{'list_limit':<16} {cfg['list_limit']}")
    print(f"{'symbol_position':<16} {cfg.get('symbol_position', 'before')}")
    print(f"{'sample':<16} {money(1234.5)}")


def cmd_recur_add(args):
    data = load()
    if args.amount <= 0:
        sys.exit("error: amount must be greater than zero")
    rule = {
        "id": next_id(data["recurring"]),
        "amount": round(args.amount, 2),
        "category": clean_category(args.category),
        "note": args.note.strip(),
        "every": args.every,
        "start": parse_date(args.start),
        "last": None,
        "kind": "income" if args.income else "expense",
    }
    data["recurring"].append(rule)
    created = apply_recurring(data)  # catch up immediately
    save(data)
    what = "income" if rule["kind"] == "income" else "expense"
    print(f"added recurring {what} rule #{rule['id']}: {money(rule['amount'])} "
          f"[{rule['category']}] every {rule['every']} from {rule['start']}")
    if created:
        print(f"  generated {created} expense(s) up to today")


def cmd_recur_from(args):
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no entry with id #{args.id}")
    note = e["note"]
    rule = {
        "id": next_id(data["recurring"]),
        "amount": round(e["amount"], 2),
        "category": e["category"],
        "note": note,
        "every": args.every,
        "start": parse_date(args.start) if args.start else e["date"],
        "last": None,
        "kind": kind_of(e),
    }
    data["recurring"].append(rule)
    created = apply_recurring(data)  # catch up immediately
    save(data)
    what = "income" if rule["kind"] == "income" else "expense"
    print(f"created recurring {what} rule #{rule['id']} from entry #{e['id']}: "
          f"{money(rule['amount'])} [{rule['category']}] every {rule['every']} "
          f"from {rule['start']}")
    if created:
        print(f"  generated {created} entr{'y' if created == 1 else 'ies'} up to today")
    print("undo with `undo`.")


def cmd_recur_edit(args):
    data = load()
    r = find(data["recurring"], args.id)
    if not r:
        sys.exit(f"error: no recurring rule with id #{args.id}")
    if args.income and args.expense:
        sys.exit("error: choose either --income or --expense, not both")
    if all(v is None for v in (args.amount, args.category, args.note, args.every)) \
            and not args.income and not args.expense:
        sys.exit("error: nothing to change - pass --amount/--category/--note/"
                 "--every/--income/--expense")

    if args.amount is not None:
        if args.amount <= 0:
            sys.exit("error: amount must be greater than zero")
        r["amount"] = round(args.amount, 2)
    if args.category is not None:
        r["category"] = clean_category(args.category)
    if args.note is not None:
        r["note"] = args.note.strip()
    if args.every is not None:
        r["every"] = args.every
    if args.income:
        r["kind"] = "income"
    elif args.expense:
        r["kind"] = "expense"

    save(data)
    what = "income" if r.get("kind") == "income" else "expense"
    print(f"updated recurring {what} rule #{r['id']}: {money(r['amount'])} "
          f"[{r['category']}] every {r['every']}")
    print("  (already-generated expenses are unchanged)")


def cmd_recur_list(args):
    data = load()
    if not data["recurring"]:
        print("no recurring rules. Try: recur add 1200 rent --every month")
        return
    today = date.today()
    print("Recurring rules")
    print("=" * 60)
    for r in sorted(data["recurring"], key=lambda x: x["id"]):
        skips = set(r.get("skips", []))
        occ = _occurrences(r, add_months(today, 2))
        # the next charge is the first upcoming date that isn't skipped
        upcoming = [d for d in occ if d > today and d.isoformat() not in skips]
        nxt = upcoming[0].isoformat() if upcoming else "-"
        note = f" - {r['note']}" if r["note"] else ""
        mark = " +income" if r.get("kind") == "income" else ""
        future_skips = sorted(s for s in skips if s >= today.isoformat())
        skip_note = f"  skips: {', '.join(future_skips)}" if future_skips else ""
        state = "  (PAUSED)" if r.get("paused") else ""
        nxt_disp = "paused" if r.get("paused") else nxt
        print(f"#{r['id']:<3} {money(r['amount']):>10}  [{r['category']}]{mark}"
              f"  every {r['every']:<5}  next: {nxt_disp}{note}{skip_note}{state}")


def cmd_recur_remove(args):
    data = load()
    r = find(data["recurring"], args.id)
    if not r:
        sys.exit(f"error: no recurring rule with id #{args.id}")
    data["recurring"] = [x for x in data["recurring"] if x["id"] != args.id]
    save(data)
    print(f"removed recurring rule #{r['id']} [{r['category']}] "
          f"(past expenses it created are kept)")


def cmd_recur_skip(args):
    data = load()
    rule = find(data["recurring"], args.id)
    if not rule:
        sys.exit(f"error: no recurring rule with id #{args.id}")
    if args.date:
        target = parse_date(args.date)
    else:
        today = date.today()
        after = today
        if rule.get("last"):
            after = max(after, date.fromisoformat(rule["last"]))
        horizon = today + timedelta(days=400)
        nxt = next((d for d in _occurrences(rule, horizon) if d > after), None)
        if nxt is None:
            sys.exit("error: no upcoming occurrence to skip in the next ~year")
        target = nxt.isoformat()
    skips = rule.setdefault("skips", [])
    if target in skips:
        print(f"rule #{rule['id']} already skips {target}")
        return
    skips.append(target)
    skips.sort()
    save(data)
    print(f"rule #{rule['id']} [{rule['category']}] will skip its "
          f"{target} occurrence.  undo with `undo`.")


def cmd_recur_unskip(args):
    data = load()
    rule = find(data["recurring"], args.id)
    if not rule:
        sys.exit(f"error: no recurring rule with id #{args.id}")
    skips = rule.get("skips", [])
    if not skips:
        print(f"rule #{rule['id']} has no skips")
        return
    if getattr(args, "all", False):
        n = len(skips)
        rule["skips"] = []
        save(data)
        print(f"cleared {n} skip(s) on rule #{rule['id']}.  undo with `undo`.")
        return
    if not args.date:
        sys.exit("error: give a date to unskip, or --all")
    target = parse_date(args.date)
    if target not in skips:
        sys.exit(f"error: rule #{rule['id']} does not skip {target}")
    skips.remove(target)
    save(data)
    print(f"rule #{rule['id']} will no longer skip {target}.  undo with `undo`.")


def cmd_recur_pause(args):
    data = load()
    rule = find(data["recurring"], args.id)
    if not rule:
        sys.exit(f"error: no recurring rule with id #{args.id}")
    if rule.get("paused"):
        print(f"rule #{rule['id']} is already paused")
        return
    rule["paused"] = True
    save(data)
    print(f"paused rule #{rule['id']} [{rule['category']}] - it won't generate "
          "until resumed.  undo with `undo`.")


def cmd_recur_resume(args):
    data = load()
    rule = find(data["recurring"], args.id)
    if not rule:
        sys.exit(f"error: no recurring rule with id #{args.id}")
    if not rule.get("paused"):
        print(f"rule #{rule['id']} is not paused")
        return
    rule["paused"] = False
    # Don't backfill the paused gap: resume from today going forward.
    rule["last"] = date.today().isoformat()
    save(data)
    print(f"resumed rule #{rule['id']} [{rule['category']}] - future occurrences "
          "will generate (the paused gap is not backfilled).  undo with `undo`.")


def cmd_recur_run(args):
    data = load()
    created = apply_recurring(data)
    save(data)
    print(f"generated {created} recurring expense(s)" if created
          else "nothing due - all recurring rules are up to date")


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #

_EXAMPLES = """\
examples:
  ledgerling add 12.50 food "lunch #work"      record an expense (with a tag)
  ledgerling list --month 2026-09              list this month's expenses
  ledgerling search --tag work --json          machine-readable, filtered
  ledgerling budget --category food --amount 400   set a monthly budget
  ledgerling recur add 1200 rent --every month     add a recurring charge
  ledgerling report --months 12                a year of trend + adherence
  ledgerling backup                            snapshot your data

All data stays inside this app's folder; nothing is posted or pushed.
"""


def build_parser():
    p = argparse.ArgumentParser(
        prog="ledgerling",
        description="Ledgerling - a tiny, sandboxed personal expense tracker.",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version",
                   version=f"ledgerling {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    a = sub.add_parser("add", help="record an expense")
    a.add_argument("amount", type=float, help="amount spent, e.g. 12.50")
    a.add_argument("category", help="category, e.g. food, rent, transit")
    a.add_argument("note", nargs="?", default="", help="optional note")
    a.add_argument("--date", default="today",
                   help="YYYY-MM-DD, 'today', or 'yesterday'")
    a.set_defaults(func=cmd_add)

    inc = sub.add_parser("income", help="record an income entry")
    inc.add_argument("amount", type=float, help="amount received, e.g. 2500")
    inc.add_argument("category", help="source, e.g. salary, freelance, gift")
    inc.add_argument("note", nargs="?", default="", help="optional note")
    inc.add_argument("--date", default="today",
                     help="YYYY-MM-DD, 'today', or 'yesterday'")
    inc.set_defaults(func=cmd_income)

    l = sub.add_parser("list", help="show recent expenses")
    l.add_argument("--category", help="filter by category")
    l.add_argument("--month", help="filter by month, YYYY-MM")
    l.add_argument("--limit", type=int, default=None,
                   help="show at most N most-recent items (default from config)")
    l.add_argument("--income", action="store_true", help="show income instead")
    l.add_argument("--all", action="store_true", help="show expenses and income")
    l.add_argument("--json", action="store_true", help="output JSON instead of text")
    l.set_defaults(func=cmd_list)

    e = sub.add_parser("edit", help="change fields on an expense")
    e.add_argument("id", type=int, help="expense id (see `list`)")
    e.add_argument("--amount", type=float, help="new amount")
    e.add_argument("--category", help="new category")
    e.add_argument("--note", help="new note (re-parses #tags)")
    e.add_argument("--date", help="new date: YYYY-MM-DD, 'today', or 'yesterday'")
    e.set_defaults(func=cmd_edit)

    d = sub.add_parser("delete", help="remove an expense by id")
    d.add_argument("id", type=int, help="expense id (see `list`)")
    d.set_defaults(func=cmd_delete)

    cl = sub.add_parser("clone", help="duplicate an entry (defaults to today)")
    cl.add_argument("id", type=int, help="entry id to copy (see `list`)")
    cl.add_argument("--date", default="today",
                    help="date for the copy: YYYY-MM-DD, 'today', or 'yesterday'")
    cl.set_defaults(func=cmd_clone)

    rf = sub.add_parser("refund",
                        help="record a refund for an expense (as income)")
    rf.add_argument("id", type=int, help="expense id being refunded")
    rf.add_argument("--amount", type=float,
                    help="refund amount (default: the full expense amount)")
    rf.add_argument("--date", default="today",
                    help="refund date: YYYY-MM-DD, 'today', or 'yesterday'")
    rf.set_defaults(func=cmd_refund)

    s = sub.add_parser("summary", help="totals by category with a chart")
    s.add_argument("--month", help="month to summarize, YYYY-MM (default: current)")
    s.set_defaults(func=cmd_summary)

    b = sub.add_parser("budget", help="set or view monthly budgets")
    b.add_argument("--category", help="category to set a budget for")
    b.add_argument("--amount", type=float, help="monthly budget amount")
    b.add_argument("--month", help="month to check against, YYYY-MM")
    b.set_defaults(func=cmd_budget)

    ub = sub.add_parser("unbudget", help="remove a category's budget")
    ub.add_argument("category", nargs="?", help="category whose budget to remove")
    ub.add_argument("--all", action="store_true", help="clear every budget")
    ub.set_defaults(func=cmd_unbudget)

    x = sub.add_parser("export", help="write expenses to CSV (in data folder)")
    x.add_argument("--file", help="file name (basename only; saved in exports/)")
    x.add_argument("--month", help="only export this month, YYYY-MM")
    x.add_argument("--start", help="range start date (with --end); YYYY-MM-DD/today")
    x.add_argument("--end", help="range end date (with --start); YYYY-MM-DD/today")
    x.add_argument("--format", choices=["csv", "json"], default="csv",
                   help="output format (default csv)")
    x.set_defaults(func=cmd_export)

    im = sub.add_parser("import", help="import expenses from a CSV in the data folder")
    im.add_argument("--file", required=True,
                    help="file name (looked up in exports/ then the data folder)")
    im.add_argument("--dry-run", action="store_true",
                    help="preview counts without importing anything")
    im.set_defaults(func=cmd_import)

    rp = sub.add_parser("report", help="month-over-month trend and budget adherence")
    rp.add_argument("--months", type=int, default=6,
                    help="how many months to show (default 6)")
    rp.set_defaults(func=cmd_report)

    sr = sub.add_parser("search", help="find expenses by keyword and filters")
    sr.add_argument("keyword", nargs="?", default="",
                    help="substring to match in note or category")
    sr.add_argument("--category", help="restrict to this exact category")
    sr.add_argument("--tag", help="restrict to a #tag (with or without the #)")
    sr.add_argument("--month", help="restrict to a month, YYYY-MM")
    sr.add_argument("--min", type=float, help="minimum amount")
    sr.add_argument("--max", type=float, help="maximum amount")
    sr.add_argument("--income", action="store_true", help="search income instead")
    sr.add_argument("--all", action="store_true", help="search expenses and income")
    sr.add_argument("--sort", choices=["date", "amount", "category"],
                    default="date", help="sort order (default date)")
    sr.add_argument("--desc", action="store_true", help="sort descending")
    sr.add_argument("--json", action="store_true", help="output JSON instead of text")
    sr.set_defaults(func=cmd_search)

    st = sub.add_parser("stats", help="analytics: extremes, averages, projection")
    st.add_argument("--json", action="store_true", help="output JSON instead of text")
    st.set_defaults(func=cmd_stats)

    wd = sub.add_parser("weekday", help="spending by day of week")
    wd.add_argument("--month", help="restrict to a month, YYYY-MM")
    wd.add_argument("--json", action="store_true", help="output JSON instead of text")
    wd.set_defaults(func=cmd_weekday)

    hm = sub.add_parser("heatmap", help="daily-spending calendar for a month")
    hm.add_argument("--month", help="which month, YYYY-MM (default: current)")
    hm.add_argument("--json", action="store_true", help="output JSON instead of text")
    hm.set_defaults(func=cmd_heatmap)

    cu = sub.add_parser("cumulative",
                        help="cumulative spending by day within a month")
    cu.add_argument("--month", help="which month, YYYY-MM (default: current)")
    cu.add_argument("--json", action="store_true", help="output JSON instead of text")
    cu.set_defaults(func=cmd_cumulative)

    sk = sub.add_parser("streak", help="no-spend-day streaks for a month")
    sk.add_argument("--month", help="which month, YYYY-MM (default: current)")
    sk.add_argument("--json", action="store_true", help="output JSON instead of text")
    sk.set_defaults(func=cmd_streak)

    ba = sub.add_parser("balance", help="running cumulative net over time")
    ba.add_argument("--json", action="store_true", help="output JSON instead of text")
    ba.set_defaults(func=cmd_balance)

    cm = sub.add_parser("commitments",
                        help="recurring rules normalized to monthly/annual cost")
    cm.add_argument("--json", action="store_true", help="output JSON instead of text")
    cm.set_defaults(func=cmd_commitments)

    sv = sub.add_parser("savings", help="monthly savings rate (net / income) trend")
    sv.add_argument("--json", action="store_true", help="output JSON instead of text")
    sv.set_defaults(func=cmd_savings)

    fc = sub.add_parser("forecast", help="project this year to year-end")
    fc.add_argument("--json", action="store_true", help="output JSON instead of text")
    fc.set_defaults(func=cmd_forecast)

    qt = sub.add_parser("quarter", help="quarterly rollup (Q1-Q4) for a year")
    qt.add_argument("year", nargs="?", type=int, default=None,
                    help="which year, YYYY (default: current)")
    qt.add_argument("--json", action="store_true", help="output JSON instead of text")
    qt.set_defaults(func=cmd_quarter)

    yr = sub.add_parser("year", help="calendar-year rollup by month")
    yr.add_argument("year", nargs="?", type=int, default=None,
                    help="which year, YYYY (default: current)")
    yr.add_argument("--json", action="store_true", help="output JSON instead of text")
    yr.set_defaults(func=cmd_year)

    ys = sub.add_parser("years", help="multi-year rollup (spending/income/net)")
    ys.add_argument("--json", action="store_true", help="output JSON instead of text")
    ys.set_defaults(func=cmd_years)

    dy = sub.add_parser("day", help="entries for a single day")
    dy.add_argument("--date", default="today",
                    help="YYYY-MM-DD, 'today', or 'yesterday'")
    dy.add_argument("--json", action="store_true", help="output JSON instead of text")
    dy.set_defaults(func=cmd_day)

    wk = sub.add_parser("week", help="this week's spending by day (Mon-Sun)")
    wk.add_argument("--offset", type=int, default=0,
                    help="how many weeks back (0 = this week)")
    wk.add_argument("--json", action="store_true", help="output JSON instead of text")
    wk.set_defaults(func=cmd_week)

    wy = sub.add_parser("weekly", help="weekly spending trend over the last N weeks")
    wy.add_argument("--weeks", type=int, default=8,
                    help="how many weeks to show (default 8)")
    wy.add_argument("--json", action="store_true", help="output JSON instead of text")
    wy.set_defaults(func=cmd_weekly)

    mo = sub.add_parser("month", help="one-screen dashboard for a month")
    mo.add_argument("--month", help="which month, YYYY-MM (default: current)")
    mo.add_argument("--json", action="store_true", help="output JSON instead of text")
    mo.set_defaults(func=cmd_month)

    ins = sub.add_parser("insights",
                         help="plain-language observations about a month")
    ins.add_argument("--month", help="which month, YYYY-MM (default: current)")
    ins.add_argument("--json", action="store_true", help="output JSON instead of text")
    ins.set_defaults(func=cmd_insights)

    rg = sub.add_parser("range", help="totals over an arbitrary date range")
    rg.add_argument("start", help="start date: YYYY-MM-DD, 'today', or 'yesterday'")
    rg.add_argument("end", nargs="?",
                    help="end date (default: today); YYYY-MM-DD/today/yesterday")
    rg.add_argument("--json", action="store_true", help="output JSON instead of text")
    rg.set_defaults(func=cmd_range)

    up = sub.add_parser("upcoming",
                        help="forecast recurring charges/income due soon")
    up.add_argument("--days", type=int, default=30,
                    help="how many days ahead to look (default 30)")
    up.add_argument("--json", action="store_true", help="output JSON instead of text")
    up.set_defaults(func=cmd_upcoming)

    sg = sub.add_parser("suggest",
                        help="suggest per-category budgets from recent spending")
    sg.add_argument("--months", type=int, default=3,
                    help="how many recent months to average (default 3)")
    sg.add_argument("--json", action="store_true", help="output JSON instead of text")
    sg.set_defaults(func=cmd_suggest)

    ab = sub.add_parser("autobudget",
                        help="set budgets from recent spending (applies `suggest`)")
    ab.add_argument("--months", type=int, default=3,
                    help="how many recent months to average (default 3)")
    ab.add_argument("--replace", action="store_true",
                    help="also overwrite categories that already have a budget")
    ab.add_argument("--dry-run", action="store_true",
                    help="preview the budgets without changing anything")
    ab.add_argument("--json", action="store_true", help="output JSON instead of text")
    ab.set_defaults(func=cmd_autobudget)

    ct = sub.add_parser("categories", help="list categories with counts and totals")
    ct.add_argument("--month", help="restrict to a month, YYYY-MM")
    ct.add_argument("--json", action="store_true", help="output JSON instead of text")
    ct.set_defaults(func=cmd_categories)

    tg = sub.add_parser("tags", help="list #tags with counts and totals")
    tg.add_argument("--month", help="restrict to a month, YYYY-MM")
    tg.add_argument("--json", action="store_true", help="output JSON instead of text")
    tg.set_defaults(func=cmd_tags)

    so = sub.add_parser("sources", help="income broken down by source")
    so.add_argument("--month", help="restrict to a month, YYYY-MM")
    so.add_argument("--json", action="store_true", help="output JSON instead of text")
    so.set_defaults(func=cmd_sources)

    ut = sub.add_parser("untagged", help="list expenses that have no #tags")
    ut.add_argument("--month", help="restrict to a month, YYYY-MM")
    ut.add_argument("--json", action="store_true", help="output JSON instead of text")
    ut.set_defaults(func=cmd_untagged)

    dp = sub.add_parser("duplicates",
                        help="find likely double-entered records")
    dp.add_argument("--json", action="store_true", help="output JSON instead of text")
    dp.set_defaults(func=cmd_duplicates)

    dd = sub.add_parser("dedupe",
                        help="remove duplicate entries (keeps one per group)")
    dd.add_argument("--dry-run", action="store_true",
                    help="preview what would be removed without changing anything")
    dd.add_argument("--json", action="store_true", help="output JSON instead of text")
    dd.set_defaults(func=cmd_dedupe)

    pc = sub.add_parser("pace", help="budget pace: are you ahead or behind?")
    pc.add_argument("--month", help="which month, YYYY-MM (default: current)")
    pc.add_argument("--json", action="store_true", help="output JSON instead of text")
    pc.set_defaults(func=cmd_pace)

    al = sub.add_parser("allowance",
                        help="how much you can still spend per day this month")
    al.add_argument("--month", help="which month, YYYY-MM (default: current)")
    al.add_argument("--json", action="store_true", help="output JSON instead of text")
    al.set_defaults(func=cmd_allowance)

    di = sub.add_parser("distribution", help="histogram of expense sizes")
    di.add_argument("--month", help="restrict to a month, YYYY-MM")
    di.add_argument("--json", action="store_true", help="output JSON instead of text")
    di.set_defaults(func=cmd_distribution)

    an = sub.add_parser("anomalies",
                        help="flag unusually large expenses within each category")
    an.add_argument("--month", help="restrict to a month, YYYY-MM")
    an.add_argument("--category", help="restrict to a single category")
    an.add_argument("--z", type=float, default=2.0,
                    help="threshold in standard deviations above the mean "
                         "(default 2.0)")
    an.add_argument("--min-count", type=int, default=4, dest="min_count",
                    help="minimum expenses a category needs before it is "
                         "analyzed (default 4)")
    an.add_argument("--json", action="store_true", help="output JSON instead of text")
    an.set_defaults(func=cmd_anomalies)

    ru2 = sub.add_parser("roundup",
                         help="simulate round-up savings (round each expense up)")
    ru2.add_argument("--to", type=float, default=1.0,
                     help="round each expense up to the nearest this many "
                          "dollars (default 1.0)")
    ru2.add_argument("--month", help="restrict to a month, YYYY-MM")
    ru2.add_argument("--json", action="store_true", help="output JSON instead of text")
    ru2.set_defaults(func=cmd_roundup)

    av = sub.add_parser("average", help="average spending per day/week/month")
    av.add_argument("--json", action="store_true", help="output JSON instead of text")
    av.set_defaults(func=cmd_average)

    tp = sub.add_parser("top", help="list your largest expenses")
    tp.add_argument("--limit", type=int, default=10,
                    help="how many to show (default 10)")
    tp.add_argument("--month", help="restrict to a month, YYYY-MM")
    tp.add_argument("--category", help="restrict to a category")
    tp.add_argument("--income", action="store_true", help="rank income instead")
    tp.add_argument("--all", action="store_true", help="rank expenses and income")
    tp.add_argument("--json", action="store_true", help="output JSON instead of text")
    tp.set_defaults(func=cmd_top)

    tr = sub.add_parser("trend", help="monthly spending trend for one category")
    tr.add_argument("category", help="category to chart")
    tr.add_argument("--months", type=int, default=6,
                    help="how many months to show (default 6)")
    tr.add_argument("--json", action="store_true", help="output JSON instead of text")
    tr.set_defaults(func=cmd_trend)

    tt = sub.add_parser("tagtrend", help="monthly spending trend for one #tag")
    tt.add_argument("tag", help="tag to chart (with or without a leading #)")
    tt.add_argument("--months", type=int, default=6,
                    help="how many months to show (default 6)")
    tt.add_argument("--json", action="store_true", help="output JSON instead of text")
    tt.set_defaults(func=cmd_tagtrend)

    mx = sub.add_parser("matrix", help="category x month spending grid")
    mx.add_argument("--months", type=int, default=6,
                    help="how many months to show (default 6)")
    mx.add_argument("--json", action="store_true", help="output JSON instead of text")
    mx.set_defaults(func=cmd_matrix)

    tmx = sub.add_parser("tagmatrix", help="#tag x month spending grid")
    tmx.add_argument("--months", type=int, default=6,
                     help="how many months to show (default 6)")
    tmx.add_argument("--json", action="store_true", help="output JSON instead of text")
    tmx.set_defaults(func=cmd_tagmatrix)

    cm = sub.add_parser("compare", help="compare two months side by side")
    cm.add_argument("month_a", nargs="?", help="first month, YYYY-MM "
                    "(default: last month)")
    cm.add_argument("month_b", nargs="?", help="second month, YYYY-MM "
                    "(default: this month)")
    cm.add_argument("--json", action="store_true", help="output JSON instead of text")
    cm.set_defaults(func=cmd_compare)

    rc = sub.add_parser("recategorize",
                        help="rename a category across all records")
    rc.add_argument("old", help="existing category name")
    rc.add_argument("new", help="new category name")
    rc.set_defaults(func=cmd_recategorize)

    rt = sub.add_parser("retag", help="rename a #tag across all records")
    rt.add_argument("old", help="existing tag (with or without #)")
    rt.add_argument("new", help="new tag (single word)")
    rt.set_defaults(func=cmd_retag)

    tg = sub.add_parser("tag", help="add #tag(s) to an existing entry")
    tg.add_argument("id", type=int, help="entry id")
    tg.add_argument("tags", nargs="+", help="tag name(s), with or without #")
    tg.set_defaults(func=cmd_tag)

    utg = sub.add_parser("untag", help="remove #tag(s) from an existing entry")
    utg.add_argument("id", type=int, help="entry id")
    utg.add_argument("tags", nargs="+", help="tag name(s), with or without #")
    utg.set_defaults(func=cmd_untag)

    nt = sub.add_parser("note", help="set, append to, or clear an entry's note")
    nt.add_argument("id", type=int, help="entry id")
    nt.add_argument("text", nargs="?", help="new note text (omit to just view)")
    nt.add_argument("--append", action="store_true",
                    help="append to the existing note instead of replacing it")
    nt.add_argument("--clear", action="store_true", help="clear the note")
    nt.set_defaults(func=cmd_note)

    un = sub.add_parser("undo", help="revert the last data change (toggles redo)")
    un.set_defaults(func=cmd_undo)

    gl = sub.add_parser("goal", help="set or view a monthly savings goal")
    gl.add_argument("--amount", type=float, help="monthly savings target")
    gl.add_argument("--clear", action="store_true", help="remove the goal")
    gl.set_defaults(func=cmd_goal)

    bk = sub.add_parser("backup", help="save a timestamped copy of your data")
    bk.add_argument("--list", action="store_true", help="list existing backups")
    bk.set_defaults(func=cmd_backup)

    rs = sub.add_parser("restore", help="restore data from a backup file")
    rs.add_argument("--file", help="backup file name (looked up in backups/)")
    rs.add_argument("--list", action="store_true", help="list existing backups")
    rs.set_defaults(func=cmd_restore)

    cf = sub.add_parser("config", help="view or change settings")
    cf.add_argument("--currency", help="currency symbol, e.g. $ EUR kr")
    cf.add_argument("--list-limit", type=int, dest="list_limit",
                    help="default number of items `list` shows")
    cf.add_argument("--symbol-position", dest="symbol_position",
                    choices=["before", "after"],
                    help="show the currency symbol before or after amounts")
    cf.add_argument("--reset", action="store_true", help="restore default settings")
    cf.set_defaults(func=cmd_config)

    vs = sub.add_parser("version", help="show the version")
    vs.set_defaults(func=cmd_version)

    wh = sub.add_parser("where", help="show the data folder and its files")
    wh.add_argument("--json", action="store_true", help="output JSON instead of text")
    wh.set_defaults(func=cmd_where)

    wb = sub.add_parser("web", help="launch a local web UI (auto-covers every command)")
    wb.add_argument("--port", type=int, default=8730, help="port (default 8730)")
    wb.add_argument("--no-browser", action="store_true",
                    help="don't open a browser automatically")
    wb.set_defaults(func=cmd_web)

    cp = sub.add_parser("completion",
                        help="print a shell completion script (bash or zsh)")
    cp.add_argument("shell", nargs="?", choices=["bash", "zsh"], default="bash",
                    help="which shell (default bash)")
    cp.set_defaults(func=cmd_completion)

    r = sub.add_parser("recur", help="manage recurring expenses")
    rsub = r.add_subparsers(dest="recur_command")

    ra = rsub.add_parser("add", help="define a recurring expense")
    ra.add_argument("amount", type=float, help="amount charged each time")
    ra.add_argument("category", help="category, e.g. rent, subscriptions")
    ra.add_argument("note", nargs="?", default="",
                    help="optional note (may include #tags)")
    ra.add_argument("--every", choices=["day", "week", "month"], required=True,
                    help="how often it recurs")
    ra.add_argument("--start", default="today",
                    help="first date, YYYY-MM-DD (default today)")
    ra.add_argument("--income", action="store_true",
                    help="mark this as recurring income (e.g. salary)")
    ra.set_defaults(func=cmd_recur_add)

    rfr = rsub.add_parser("from", help="create a recurring rule from an existing entry")
    rfr.add_argument("id", type=int, help="entry id to base the rule on (see `list`)")
    rfr.add_argument("--every", choices=["day", "week", "month"], required=True,
                     help="how often it recurs")
    rfr.add_argument("--start", help="first date, YYYY-MM-DD (default: the entry's date)")
    rfr.set_defaults(func=cmd_recur_from)

    re_ = rsub.add_parser("edit", help="change fields on a recurring rule")
    re_.add_argument("id", type=int, help="rule id (see `recur list`)")
    re_.add_argument("--amount", type=float, help="new amount")
    re_.add_argument("--category", help="new category")
    re_.add_argument("--note", help="new note")
    re_.add_argument("--every", choices=["day", "week", "month"],
                     help="new frequency")
    re_.add_argument("--income", action="store_true", help="mark as income")
    re_.add_argument("--expense", action="store_true", help="mark as expense")
    re_.set_defaults(func=cmd_recur_edit)

    rl = rsub.add_parser("list", help="show recurring rules")
    rl.set_defaults(func=cmd_recur_list)

    rr = rsub.add_parser("remove", help="delete a recurring rule")
    rr.add_argument("id", type=int, help="recurring rule id (see `recur list`)")
    rr.set_defaults(func=cmd_recur_remove)

    rn = rsub.add_parser("run", help="generate any due recurring expenses now")
    rn.set_defaults(func=cmd_recur_run)

    rk = rsub.add_parser("skip", help="skip a rule's next (or a given) occurrence")
    rk.add_argument("id", type=int, help="recurring rule id (see `recur list`)")
    rk.add_argument("--date",
                    help="occurrence to skip, YYYY-MM-DD (default: the next one)")
    rk.set_defaults(func=cmd_recur_skip)

    ru = rsub.add_parser("unskip", help="cancel a skip on a rule")
    ru.add_argument("id", type=int, help="recurring rule id (see `recur list`)")
    ru.add_argument("--date", help="the skipped date to restore, YYYY-MM-DD")
    ru.add_argument("--all", action="store_true", help="clear all skips on the rule")
    ru.set_defaults(func=cmd_recur_unskip)

    rp = rsub.add_parser("pause", help="pause a rule (stops generating until resumed)")
    rp.add_argument("id", type=int, help="recurring rule id (see `recur list`)")
    rp.set_defaults(func=cmd_recur_pause)

    rs = rsub.add_parser("resume", help="resume a paused rule (no backfill)")
    rs.add_argument("id", type=int, help="recurring rule id (see `recur list`)")
    rs.set_defaults(func=cmd_recur_resume)

    r.set_defaults(func=lambda args: r.print_help())

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return

    _CONFIG.update(load_config())  # apply saved settings (currency, defaults)

    # Auto catch-up on recurring rules for read/report commands, so lists,
    # summaries, budgets and exports always reflect what's due. (Purely local
    # file writes, inside the data folder.)
    if args.command in ("list", "summary", "budget", "export", "report",
                        "stats", "search", "categories", "tags", "month",
                        "upcoming", "compare", "trend", "top", "pace",
                        "duplicates", "week", "streak", "weekday", "day",
                        "year", "untagged", "average", "distribution",
                        "sources", "quarter", "forecast", "balance",
                        "commitments", "savings", "heatmap", "suggest",
                        "insights", "tagtrend", "range", "matrix",
                        "cumulative", "allowance", "tagmatrix", "weekly",
                        "years", "anomalies", "roundup"):
        data = load()
        if apply_recurring(data):
            save(data)

    args.func(args)


if __name__ == "__main__":
    main()
