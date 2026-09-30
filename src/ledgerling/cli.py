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
    list      Show recent expenses (with optional filters)
    edit      Change fields on an existing expense
    delete    Remove an expense by id
    search    Find expenses by keyword, #tag, category, month, or amount range
    summary   Totals by category with an ASCII bar chart
    report    Month-over-month trend and budget adherence
    stats     Analytics: extremes, averages, per-tag totals, projection
    month     One-screen dashboard for a month (income, spend, net, budgets)
    compare   Compare two months side by side (with per-category deltas)
    trend     Monthly spending trend for one category
    top       List your largest expenses (optionally by month/category)
    upcoming  Forecast recurring charges/income due in the next N days
    categories  List categories with counts and totals
    tags      List #tags with counts and totals
    recategorize  Rename a category across all records
    duplicates  Find likely double-entered records
    undo      Revert the last data change (toggles redo)
    budget    Set / view monthly budgets
    pace      Budget pace: spent vs day-adjusted expected, projected EOM
    goal      Set / view a monthly savings goal
    recur     Manage recurring expenses (add / edit / list / remove / run)
    export    Write expenses to a CSV file (inside the data folder)
    import    Read expenses back from a CSV (deduped)
    backup    Save a timestamped copy of your data
    restore   Restore data from a backup (with a pre-restore safety copy)
    config    View or change settings (currency symbol, default list limit)
    version   Show the version (also `--version`)
    completion  Print a bash/zsh tab-completion script

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

__version__ = "1.14.0"

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
DEFAULT_CONFIG = {"currency": "$", "list_limit": 20}

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
    return f"{_CONFIG['currency']}{amount:,.2f}"


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
        last = date.fromisoformat(rule["last"]) if rule.get("last") else None
        latest = last
        for d in _occurrences(rule, today):
            if last is not None and d <= last:
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


def cmd_export(args):
    check_month(args.month)
    data = load()
    rows = sorted(data["expenses"], key=lambda e: (e["date"], e["id"]))
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]

    if args.file:
        # Force the export to stay inside the data folder, ignoring any path
        # components the user supplied.
        target = os.path.join(EXPORT_DIR, os.path.basename(args.file))
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = os.path.join(EXPORT_DIR, f"expenses_{stamp}.csv")

    os.makedirs(EXPORT_DIR, exist_ok=True)
    _within_home(target)
    try:
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
                data["expenses"].append({
                    "id": next_id(data["expenses"]),
                    "amount": amount, "category": category,
                    "note": note, "date": d, "tags": parse_tags(note),
                    "kind": kind,
                })
                added += 1
    except OSError as exc:
        sys.exit(f"error: could not read {path}: {exc}")

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

    rows = sorted(rows, key=lambda e: (e["date"], e["id"]))

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
          "  Remove with `delete <id>`.")


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


def cmd_top(args):
    check_month(args.month)
    data = load()
    rows = expenses_only(data["expenses"])
    if args.category:
        rows = [e for e in rows if e["category"] == args.category.strip().lower()]
    if args.month:
        rows = [e for e in rows if month_of(e["date"]) == args.month]
    rows = sorted(rows, key=lambda e: e["amount"], reverse=True)
    limit = args.limit if args.limit and args.limit > 0 else 10
    rows = rows[:limit]

    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("no expenses found")
        return

    print(f"Top {len(rows)} expense(s)")
    print("=" * 56)
    for rank, e in enumerate(rows, 1):
        note = f" - {e['note']}" if e["note"] else ""
        print(f"{rank:>2}. {money(e['amount']):>12}  {e['date']}  "
              f"[{e['category']}]{note}")
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


def cmd_tags(args):
    data = load()
    rows = expenses_only(data["expenses"])
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
        print("no tags yet - add #tags in a note, e.g. add 40 food \"dinner #work\"")
        return

    print("Tags")
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


def cmd_categories(args):
    data = load()
    rows = expenses_only(data["expenses"])
    agg = {}
    for e in rows:
        a = agg.setdefault(e["category"], {"count": 0, "total": 0.0})
        a["count"] += 1
        a["total"] = round(a["total"] + e["amount"], 2)
    # include categories that only have a budget set
    for cat in data["budgets"]:
        agg.setdefault(cat, {"count": 0, "total": 0.0})

    result = {c: {**v, "budget": data["budgets"].get(c)}
              for c, v in agg.items()}

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return
    if not result:
        print("no categories yet")
        return

    print("Categories")
    print("=" * 56)
    for cat, v in sorted(result.items(), key=lambda kv: kv[1]["total"],
                         reverse=True):
        budget = f"  budget {money(v['budget'])}" if v["budget"] else ""
        print(f"{cat:<14} {v['count']:>3} item(s)  {money(v['total']):>12}{budget}")


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


def cmd_version(args):
    print(f"ledgerling {__version__}")


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

    if changed:
        save_config(cfg)
        print("config updated")

    # Always show the resulting settings.
    print(f"{'currency':<12} {cfg['currency']}")
    print(f"{'list_limit':<12} {cfg['list_limit']}")


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
        occ = _occurrences(r, add_months(today, 2))
        upcoming = [d for d in occ if d > today]
        nxt = upcoming[0].isoformat() if upcoming else "-"
        note = f" - {r['note']}" if r["note"] else ""
        mark = " +income" if r.get("kind") == "income" else ""
        print(f"#{r['id']:<3} {money(r['amount']):>10}  [{r['category']}]{mark}"
              f"  every {r['every']:<5}  next: {nxt}{note}")


def cmd_recur_remove(args):
    data = load()
    r = find(data["recurring"], args.id)
    if not r:
        sys.exit(f"error: no recurring rule with id #{args.id}")
    data["recurring"] = [x for x in data["recurring"] if x["id"] != args.id]
    save(data)
    print(f"removed recurring rule #{r['id']} [{r['category']}] "
          f"(past expenses it created are kept)")


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

    s = sub.add_parser("summary", help="totals by category with a chart")
    s.add_argument("--month", help="month to summarize, YYYY-MM (default: current)")
    s.set_defaults(func=cmd_summary)

    b = sub.add_parser("budget", help="set or view monthly budgets")
    b.add_argument("--category", help="category to set a budget for")
    b.add_argument("--amount", type=float, help="monthly budget amount")
    b.add_argument("--month", help="month to check against, YYYY-MM")
    b.set_defaults(func=cmd_budget)

    x = sub.add_parser("export", help="write expenses to CSV (in data folder)")
    x.add_argument("--file", help="file name (basename only; saved in exports/)")
    x.add_argument("--month", help="only export this month, YYYY-MM")
    x.set_defaults(func=cmd_export)

    im = sub.add_parser("import", help="import expenses from a CSV in the data folder")
    im.add_argument("--file", required=True,
                    help="file name (looked up in exports/ then the data folder)")
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
    sr.add_argument("--json", action="store_true", help="output JSON instead of text")
    sr.set_defaults(func=cmd_search)

    st = sub.add_parser("stats", help="analytics: extremes, averages, projection")
    st.add_argument("--json", action="store_true", help="output JSON instead of text")
    st.set_defaults(func=cmd_stats)

    mo = sub.add_parser("month", help="one-screen dashboard for a month")
    mo.add_argument("--month", help="which month, YYYY-MM (default: current)")
    mo.add_argument("--json", action="store_true", help="output JSON instead of text")
    mo.set_defaults(func=cmd_month)

    up = sub.add_parser("upcoming",
                        help="forecast recurring charges/income due soon")
    up.add_argument("--days", type=int, default=30,
                    help="how many days ahead to look (default 30)")
    up.add_argument("--json", action="store_true", help="output JSON instead of text")
    up.set_defaults(func=cmd_upcoming)

    ct = sub.add_parser("categories", help="list categories with counts and totals")
    ct.add_argument("--json", action="store_true", help="output JSON instead of text")
    ct.set_defaults(func=cmd_categories)

    tg = sub.add_parser("tags", help="list #tags with counts and totals")
    tg.add_argument("--json", action="store_true", help="output JSON instead of text")
    tg.set_defaults(func=cmd_tags)

    dp = sub.add_parser("duplicates",
                        help="find likely double-entered records")
    dp.add_argument("--json", action="store_true", help="output JSON instead of text")
    dp.set_defaults(func=cmd_duplicates)

    pc = sub.add_parser("pace", help="budget pace: are you ahead or behind?")
    pc.add_argument("--month", help="which month, YYYY-MM (default: current)")
    pc.add_argument("--json", action="store_true", help="output JSON instead of text")
    pc.set_defaults(func=cmd_pace)

    tp = sub.add_parser("top", help="list your largest expenses")
    tp.add_argument("--limit", type=int, default=10,
                    help="how many to show (default 10)")
    tp.add_argument("--month", help="restrict to a month, YYYY-MM")
    tp.add_argument("--category", help="restrict to a category")
    tp.add_argument("--json", action="store_true", help="output JSON instead of text")
    tp.set_defaults(func=cmd_top)

    tr = sub.add_parser("trend", help="monthly spending trend for one category")
    tr.add_argument("category", help="category to chart")
    tr.add_argument("--months", type=int, default=6,
                    help="how many months to show (default 6)")
    tr.add_argument("--json", action="store_true", help="output JSON instead of text")
    tr.set_defaults(func=cmd_trend)

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
    cf.add_argument("--reset", action="store_true", help="restore default settings")
    cf.set_defaults(func=cmd_config)

    vs = sub.add_parser("version", help="show the version")
    vs.set_defaults(func=cmd_version)

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
                        "duplicates"):
        data = load()
        if apply_recurring(data):
            save(data)

    args.func(args)


if __name__ == "__main__":
    main()
