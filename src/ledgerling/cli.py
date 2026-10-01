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
    split     Split an entry into category/amount parts that sum to it
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
    net       Income, expenses, net and savings rate (all-time or a month)
    average   Average spending per day / week / month
    distribution  Histogram of expense sizes
    anomalies  Flag unusually large expenses within each category
    roundup   Simulate round-up savings (round each expense up to $N)
    tip       Tip calculator and even bill splitter
    interest  Compound-growth / future-value calculator
    loan      Loan payment / amortization calculator
    fx        Offline currency converter (set/list/rm/convert, user-set rates)
    upcoming  Forecast recurring charges/income due in the next N days
    cashflow  Project a running balance forward (flags if it goes negative)
    target    Estimate how long to reach a lump-sum savings target
    runway    How long a balance lasts at your average monthly net
    commitments  Recurring rules normalized to monthly/annual cost
    subscriptions  Detect subscription-like charges from spending history
    suggest   Suggest per-category budgets from recent average spending
    autobudget  Apply suggested budgets from recent spending (undoable)
    categories  List categories with counts and totals
    payees    Rank spending by payee (merchant), from the note
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
    overbudget  Budget breaches across every month of history
    goal      Set / view a monthly savings goal
    recur     Recurring rules (add/from/edit/list/remove/run/skip/unskip/pause/resume)
    export    Write entries to CSV/JSON (filter by month/range/category/kind)
    import    Read entries back from a CSV or JSON file (deduped)
    backup    Save a timestamped copy of your data
    restore   Restore data from a backup (with a pre-restore safety copy)
    config    View or change settings (currency symbol, default list limit)
    version   Show the version (also `--version`)
    where     Show the data folder and its files
    check     Scan your data for integrity problems
    completion  Print a bash/zsh tab-completion script
    web       Launch a local web UI covering every command

Run `python ledgerling.py --help` or `<command> --help` for details.
"""

import argparse
import calendar
import copy
import csv
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, date, timedelta

__version__ = "1.127.1"

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
DEFAULT_CONFIG = {"currency": "$", "list_limit": 20, "symbol_position": "before",
                  "fx": {}, "home_code": ""}

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
        # os.replace can transiently fail on Windows when antivirus or a sync
        # client (e.g. OneDrive) briefly locks the target; retry a few times.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)
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
    """Return settings merged over defaults.

    Unknown keys are ignored, and a stored value is only accepted when its type
    matches the default's (so a corrupt or hand-edited config -- e.g. fx set to
    a string -- falls back to the default for that key instead of crashing a
    command later). deepcopy keeps mutable defaults (the fx dict) from being
    aliased and accidentally mutated process-wide.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if not os.path.exists(CONFIG_FILE):
        return cfg
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return cfg  # a broken config falls back to defaults rather than crashing
    if isinstance(stored, dict):
        for key, default in DEFAULT_CONFIG.items():
            if key not in stored:
                continue
            value = stored[key]
            # bool is a subclass of int; don't let one masquerade as the other.
            if isinstance(value, bool) != isinstance(default, bool):
                continue
            if isinstance(value, type(default)):
                cfg[key] = copy.deepcopy(value)
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


def _date_bounds(since, until):
    """Return (start_iso, end_iso) for an optional date range. Missing ends are
    open; a reversed range is swapped. Used by export and search."""
    start = parse_date(since) if since else "0000-01-01"
    end = parse_date(until) if until else "9999-12-31"
    return (end, start) if end < start else (start, end)


def month_of(iso_date):
    return iso_date[:7]


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def check_month(m):
    """Exit with a clear message if a --month filter is malformed."""
    if m is not None and not _MONTH_RE.match(m):
        sys.exit(f"error: '{m}' is not a valid month (use YYYY-MM)")


def filter_month(rows, month):
    """Return only the rows dated within a YYYY-MM month, or all rows unchanged
    when month is falsy. Centralizes the optional --month filter that most read
    commands share (validate the value with check_month first)."""
    if not month:
        return rows
    return [e for e in rows if month_of(e["date"]) == month]


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


def all_time_net(data):
    """All-time net position: total income minus total expenses (rounded)."""
    inc = sum(e["amount"] for e in income_only(data["expenses"]))
    exp = sum(e["amount"] for e in expenses_only(data["expenses"]))
    return round(inc - exp, 2)


def group_totals(rows, key_fn):
    """Group entries by key_fn(entry) into {key: {"income", "spending"}},
    accumulating each bucket with running 2dp rounding (the shape the per-period
    reports -- savings, years -- share). A None key skips the entry."""
    agg = {}
    for e in rows:
        k = key_fn(e)
        if k is None:
            continue
        a = agg.setdefault(k, {"income": 0.0, "spending": 0.0})
        if kind_of(e) == "income":
            a["income"] = round(a["income"] + e["amount"], 2)
        else:
            a["spending"] = round(a["spending"] + e["amount"], 2)
    return agg


_TAG_RE = re.compile(r"#(\w+)")


def parse_tags(note):
    """Extract unique, lowercased #tags from a note (without the '#')."""
    return sorted({m.lower() for m in _TAG_RE.findall(note or "")})


def money(amount):
    cur = _CONFIG.get("currency", "$")
    # Put the sign in front of the whole thing ("-$5.00", not "$-5.00") to match
    # the web UI and the usual convention. -0.0 formats as a plain zero.
    neg = amount < 0
    mag = abs(amount)
    body = (f"{mag:,.2f} {cur}" if _CONFIG.get("symbol_position") == "after"
            else f"{cur}{mag:,.2f}")
    return f"-{body}" if neg else body


def bar(fraction, width=24):
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "#" * filled + "-" * (width - filled)


def next_id(items):
    # Only consider genuine integer ids, so a corrupt/hand-edited entry with a
    # null or string id can't crash id assignment (bool is excluded: it is an
    # int subclass but never a valid id).
    ids = [i.get("id") for i in items]
    ids = [x for x in ids if isinstance(x, int) and not isinstance(x, bool)]
    return (max(ids) + 1) if ids else 1


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


def _ics_escape(text):
    """Escape a value for an iCalendar text field (RFC 5545 §3.3.11)."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def build_ics(items, now=None):
    """Render upcoming recurring occurrences as an iCalendar (.ics) document.

    Each item (a dict with date/amount/category/kind/note/recur_id, as produced
    by `upcoming`) becomes an all-day VEVENT with a stable UID so re-importing
    updates rather than duplicates. Pure function -> easy to test. `now` (a
    datetime) is only overridable for deterministic tests."""
    stamp = (now or datetime.now()).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Ledgerling//Upcoming//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for it in items:
        day = it["date"].replace("-", "")
        sign = "+" if it.get("kind") == "income" else "-"
        amt = f"{sign}{abs(it['amount']):.2f}"
        label = it.get("note") or it["category"]
        summary = f"{label} ({amt})"
        uid = f"{it.get('recur_id', 'x')}-{it['date']}@ledgerling"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_ics_escape(uid)}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"CATEGORIES:{_ics_escape(it['category'])}",
            f"DESCRIPTION:{_ics_escape(it.get('kind', 'expense'))} "
            f"{_ics_escape(amt)} [{_ics_escape(it['category'])}]",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    # iCalendar lines are CRLF-terminated, including a trailing CRLF.
    return "\r\n".join(lines) + "\r\n"


def category_spent(data, category, period):
    """Raw total expense spend for a category in a YYYY-MM period.

    Returns an unrounded sum; callers round for display as they see fit.
    """
    return sum(e["amount"] for e in data["expenses"]
               if kind_of(e) == "expense" and e["category"] == category
               and month_of(e["date"]) == period)


def budget_status_line(data, category, ref_iso):
    """Return a one-line budget status for a category/month, or None."""
    limit = data["budgets"].get(category)
    if not limit:
        return None
    spent = category_spent(data, category, month_of(ref_iso))
    left = limit - spent
    status = "OVER" if left < 0 else "left"
    return (f"  budget: {money(spent)} of {money(limit)} this month "
            f"({money(abs(left))} {status})")


# --------------------------------------------------------------------------- #
# Recurring engine
# --------------------------------------------------------------------------- #

def _first_index(start, every, since):
    """Smallest k >= 0 such that the k-th occurrence of the rule is >= since."""
    if since <= start:
        return 0
    diff = (since - start).days
    if every == "day":
        return diff
    if every == "week":
        return -(-diff // 7)  # ceil division
    # month: add_months is nonlinear (day clamping) but monotonic in k, so
    # estimate a safe lower bound then step forward to the exact first index.
    k = max(0, (since.year - start.year) * 12 + (since.month - start.month) - 1)
    while add_months(start, k) < since:
        k += 1
    return k


def _occurrences(rule, through, since=None):
    """All dates this rule should have fired on, from its start through
    `through` (inclusive).

    `since` is an optional lower bound: when given, occurrences strictly before
    it are skipped without iterating from the rule's start. Every caller already
    discards those earlier dates, so the result is identical -- this just keeps
    long-running daily/weekly rules from looping over years of history.

    A rule may carry an optional `until` end date and/or an optional `count`
    (a fixed number of occurrences from the start); occurrences past either
    limit are not generated. `count` is an absolute index from the start, so it
    holds regardless of the `since` fast-forward.
    """
    start = date.fromisoformat(rule["start"])
    every = rule["every"]
    until = rule.get("until")
    count = rule.get("count")
    # The rule never fires past its end date, so cap the horizon there.
    if until:
        until_d = date.fromisoformat(until)
        if until_d < through:
            through = until_d
    out = []
    k = _first_index(start, every, since) if since is not None else 0
    while True:
        if count is not None and k >= count:
            break  # a fixed-count rule stops after `count` occurrences
        if every == "day":
            d = start + timedelta(days=k)
        elif every == "week":
            d = start + timedelta(weeks=k)
        else:  # month
            d = add_months(start, k)
        if d > through:
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
    # Assign ids from a running counter instead of re-scanning the list on each
    # append -- a long-overdue daily rule can generate thousands at once.
    next_free = next_id(data["expenses"])
    for rule in data["recurring"]:
        if rule.get("paused"):
            continue  # a paused rule generates nothing until resumed
        last = date.fromisoformat(rule["last"]) if rule.get("last") else None
        latest = last
        skips = set(rule.get("skips", []))
        note_tags = parse_tags(rule["note"])  # constant per rule
        # Only occurrences after `last` matter; fast-forward past the history.
        since = (last + timedelta(days=1)) if last is not None else None
        for d in _occurrences(rule, today, since):
            if last is not None and d <= last:
                continue
            if d.isoformat() in skips:
                # honour a `recur skip`: don't generate, but move past it
                if latest is None or d > latest:
                    latest = d
                continue
            data["expenses"].append({
                "id": next_free,
                "amount": rule["amount"],
                "category": rule["category"],
                "note": rule["note"],
                "date": d.isoformat(),
                "tags": note_tags,
                "kind": rule.get("kind", "expense"),
                "recur_id": rule["id"],
            })
            next_free += 1
            created += 1
            if latest is None or d > latest:
                latest = d
        if latest is not None:
            rule["last"] = latest.isoformat()
    return created


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _entry_amount(args):
    """Resolve an add/income entry's stored amount, applying `--in CODE` fx
    conversion to the home currency. Returns (amount, note_suffix)."""
    src = getattr(args, "in_", None)
    if src:
        return (_amount_in_home(args.amount, src),
                f" (from {args.amount:g} {_fx_code(src)})")
    return round(args.amount, 2), ""


def cmd_add(args):
    data = load()
    if args.amount <= 0:
        sys.exit("error: amount must be greater than zero")
    note = args.note.strip()
    amount, conv = _entry_amount(args)
    entry = {
        "id": next_id(data["expenses"]),
        "amount": amount,
        "category": clean_category(args.category),
        "note": note,
        "date": parse_date(args.date),
        "tags": parse_tags(note),
        "kind": "expense",
    }
    data["expenses"].append(entry)
    save(data)
    print(f"added #{entry['id']}: {money(entry['amount'])}{conv} "
          f"[{entry['category']}] {entry['note']} on {entry['date']}")
    line = budget_status_line(data, entry["category"], entry["date"])
    if line:
        print(line)


def cmd_income(args):
    data = load()
    if args.amount <= 0:
        sys.exit("error: amount must be greater than zero")
    note = args.note.strip()
    amount, conv = _entry_amount(args)
    entry = {
        "id": next_id(data["expenses"]),
        "amount": amount,
        "category": clean_category(args.category),
        "note": note,
        "date": parse_date(args.date),
        "tags": parse_tags(note),
        "kind": "income",
    }
    data["expenses"].append(entry)
    save(data)
    print(f"recorded income #{entry['id']}: {money(entry['amount'])}{conv} "
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
    if getattr(args, "tag", None):
        want = args.tag.strip().lstrip("#").lower()
        rows = [e for e in rows if want in e.get("tags", [])]
    rows = filter_month(rows, args.month)
    key = getattr(args, "sort", "date") or "date"
    reverse = bool(getattr(args, "desc", False))
    if key == "amount":
        rows.sort(key=lambda e: (e["amount"], e["date"], e["id"]), reverse=reverse)
    elif key == "category":
        rows.sort(key=lambda e: (e["category"], e["date"], e["id"]), reverse=reverse)
    else:  # date
        rows.sort(key=lambda e: (e["date"], e["id"]), reverse=reverse)
    limit = args.limit if args.limit is not None else _CONFIG["list_limit"]
    if limit and limit > 0:
        # Default (date, ascending) keeps the most recent N; any other sort or
        # an explicit --desc takes the first N of the chosen order.
        rows = rows[-limit:] if (key == "date" and not reverse) else rows[:limit]

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


def cmd_split(args):
    """Replace one entry with several category/amount parts that sum to it.

    Useful for a single receipt covering multiple categories (a Costco run that
    was groceries + household). The parts inherit the original's date, note,
    tags and kind. By default the parts are exact amounts that must total the
    original to the cent; with --pct they are percentages that must sum to 100,
    and the amounts are derived (the last part absorbs any rounding remainder).
    """
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no entry with id #{args.id}")
    parts = args.parts
    by_pct = getattr(args, "pct", False)
    label = "percent" if by_pct else "amount"
    if len(parts) % 2 != 0:
        example = ("split 5 --pct groceries 60 household 40" if by_pct
                   else "split 5 groceries 70 household 30")
        sys.exit(f"error: split needs category/{label} pairs, e.g. {example}")
    raw = []
    i = 0
    while i < len(parts):
        cat = clean_category(parts[i])
        try:
            val = float(parts[i + 1])
        except ValueError:
            sys.exit(f"error: '{parts[i + 1]}' is not a valid {label}")
        if val <= 0:
            sys.exit(f"error: split {label}s must be greater than zero")
        raw.append((cat, val))
        i += 2
    if len(raw) < 2:
        sys.exit("error: split into at least two parts")

    orig_cents = round(e["amount"] * 100)
    if by_pct:
        if round(sum(v for _, v in raw), 4) != 100:
            sys.exit(f"error: percentages must sum to 100 "
                     f"(got {sum(v for _, v in raw):g})")
        pairs, used = [], 0
        for idx, (cat, pct) in enumerate(raw):
            cents = (orig_cents - used if idx == len(raw) - 1
                     else round(orig_cents * pct / 100))
            if cents <= 0:
                sys.exit("error: a part rounds to zero; use larger "
                         "percentages or exact amounts")
            used += cents if idx < len(raw) - 1 else 0
            pairs.append((cat, round(cents / 100, 2)))
    else:
        pairs = [(cat, round(v, 2)) for cat, v in raw]
        sum_cents = sum(round(a * 100) for _, a in pairs)
        if sum_cents != orig_cents:
            sys.exit(f"error: parts total {money(round(sum_cents / 100, 2))} but "
                     f"#{e['id']} is {money(e['amount'])}")

    note, kind, tags = e["note"], kind_of(e), parse_tags(e["note"])
    date_iso = e["date"]
    data["expenses"] = [x for x in data["expenses"] if x["id"] != args.id]
    made = []
    for cat, amt in pairs:
        nid = next_id(data["expenses"])
        data["expenses"].append({
            "id": nid, "amount": amt, "category": cat, "note": note,
            "date": date_iso, "tags": tags, "kind": kind,
        })
        made.append((nid, cat, amt))
    save(data)

    what = "income" if kind == "income" else "expense"
    print(f"split {what} #{e['id']} ({money(e['amount'])}) into "
          f"{len(made)} parts:")
    for nid, cat, amt in made:
        print(f"  #{nid:<4} {money(amt):>12}  [{cat}]")


def cmd_summary(args):
    check_month(args.month)
    data = load()
    period = args.month or date.today().isoformat()[:7]
    rows = [e for e in expenses_only(data["expenses"])
            if month_of(e["date"]) == period]

    totals = {}
    for e in rows:
        totals[e["category"]] = round(totals.get(e["category"], 0)
                                      + e["amount"], 2)
    grand = round(sum(totals.values()), 2)
    cats = [{"category": c, "total": t,
             "percent": round(t / grand * 100, 1) if grand else 0.0}
            for c, t in sorted(totals.items(), key=lambda kv: kv[1],
                               reverse=True)]

    if getattr(args, "json", False):
        print(json.dumps({"month": period, "total": grand,
                          "categories": cats}, indent=2))
        return

    if not rows:
        print(f"no expenses for {period}")
        return

    biggest = max(totals.values())
    print(f"Summary for {period}")
    print("=" * 50)
    for r in cats:
        print(f"{r['category']:<14} {money(r['total']):>12}  "
              f"{bar(r['total'] / biggest)} {r['percent']:4.0f}%")
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

    period = args.month or date.today().isoformat()[:7]
    rows = []
    for cat, limit in sorted(data["budgets"].items()):
        spent = round(category_spent(data, cat, period), 2)
        rows.append({
            "category": cat, "limit": limit, "spent": spent,
            "remaining": round(limit - spent, 2),
            "percent": round(spent / limit * 100, 1) if limit else 0.0,
            "over": spent > limit,
        })

    if getattr(args, "json", False):
        print(json.dumps({"month": period, "budgets": rows}, indent=2))
        return

    if not rows:
        print("no budgets set. Try: budget --category food --amount 400")
        return

    print(f"Budgets for {period}")
    print("=" * 60)
    for r in rows:
        frac = r["spent"] / r["limit"] if r["limit"] else 0
        if r["over"]:
            tail = f"  OVER by {money(-r['remaining'])}"
        else:
            tail = f"  {money(r['remaining'])} left"
        print(f"{r['category']:<14} {money(r['spent']):>10} / "
              f"{money(r['limit']):<10} {bar(frac)} {r['percent']:4.0f}%{tail}")


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
        rows = filter_month(rows, args.month)
    elif arg_start or arg_end:
        start, end = _date_bounds(arg_start, arg_end)
        rows = [e for e in rows if start <= e["date"] <= end]

    if getattr(args, "income", False) and getattr(args, "expenses", False):
        sys.exit("error: choose either --income or --expenses, not both")
    if getattr(args, "category", None):
        cat = clean_category(args.category)
        rows = [e for e in rows if e["category"] == cat]
    if getattr(args, "income", False):
        rows = [e for e in rows if kind_of(e) == "income"]
    elif getattr(args, "expenses", False):
        rows = [e for e in rows if kind_of(e) == "expense"]

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
    print(f"exported {len(rows)} entr{'y' if len(rows) == 1 else 'ies'} "
          f"to {target}")


def _normalize_import_row(row):
    """Validate one raw record (a CSV or JSON dict) and return a normalized
    entry (amount/category/note/date/kind), or None if it's malformed."""
    if not isinstance(row, dict):
        return None
    try:
        d = datetime.strptime(str(row["date"]).strip(),
                              "%Y-%m-%d").date().isoformat()
        amount = round(float(row["amount"]), 2)
        category = str(row["category"]).strip().lower()
        note = str(row.get("note") or "").strip()
        kind = str(row.get("kind") or "expense").strip().lower()
        if kind not in ("expense", "income"):
            kind = "expense"
        if amount <= 0 or not category:
            return None
    except (KeyError, ValueError, AttributeError, TypeError):
        return None
    return {"amount": amount, "category": category, "note": note,
            "date": d, "kind": kind}


def cmd_import(args):
    name = os.path.basename(args.file)  # keep the read inside the data folder
    candidates = [os.path.join(EXPORT_DIR, name), os.path.join(HOME_DIR, name)]
    path = next((c for c in candidates if os.path.exists(c)), None)
    if not path:
        sys.exit(f"error: '{name}' not found in exports/ or the data folder")
    _within_home(path)

    # Read raw records from JSON (an exported array) or CSV, by extension.
    try:
        if path.lower().endswith(".json"):
            with open(path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
            if not isinstance(payload, list):
                sys.exit(f"error: {path} is not a JSON array of entries")
            raw_rows = payload
        else:
            # utf-8-sig tolerates a BOM (Excel / PowerShell often add one).
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                raw_rows = list(csv.DictReader(fh))
    except OSError as exc:
        sys.exit(f"error: could not read {path}: {exc}")
    except json.JSONDecodeError as exc:
        sys.exit(f"error: {path} is not valid JSON: {exc}")

    data = load()
    seen = {(e["date"], round(e["amount"], 2), e["category"], e["note"],
             kind_of(e)) for e in data["expenses"]}
    next_free = next_id(data["expenses"])  # running id, not an O(n) rescan per row

    added = skipped = bad = 0
    for row in raw_rows:
        norm = _normalize_import_row(row)
        if norm is None:
            bad += 1
            continue
        key = (norm["date"], norm["amount"], norm["category"], norm["note"],
               norm["kind"])
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        if not getattr(args, "dry_run", False):
            data["expenses"].append({
                "id": next_free, "amount": norm["amount"],
                "category": norm["category"], "note": norm["note"],
                "date": norm["date"], "tags": parse_tags(norm["note"]),
                "kind": norm["kind"],
            })
            next_free += 1
        added += 1

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

    if getattr(args, "json", False):
        rows = []
        prev = None
        for k in keys:
            amt = round(totals[k], 2)
            inc = round(income[k], 2)
            change = (None if (prev is None or prev == 0)
                      else round((amt - prev) / prev * 100, 1))
            rows.append({"month": k, "spending": amt, "income": inc,
                         "net": round(inc - amt, 2), "change_pct": change})
            prev = amt
        active = [k for k in keys if totals[k] > 0]
        avg = round(sum(totals[k] for k in active) / len(active), 2) \
            if active else 0.0
        ti = round(sum(income.values()), 2)
        ts = round(sum(totals.values()), 2)
        out = {"months": rows, "average": avg, "total_income": ti,
               "total_spending": ts, "net": round(ti - ts, 2)}
        goal = data.get("goal")
        if goal is not None:
            out["goal_per_month"] = goal
            out["goal_target"] = round(goal * months, 2)
        print(json.dumps(out, indent=2))
        return

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
            spent = category_spent(data, cat, latest)
            frac = spent / limit if limit else 0
            flag = "  <-- OVER" if spent > limit else ""
            if spent > limit:
                over += 1
            print(f"{cat:<14} {money(spent):>10} / {money(limit):<10} "
                  f"{bar(frac)} {frac * 100:4.0f}%{flag}")
        print("-" * 56)
        verdict = "all within budget" if over == 0 else f"{over} category(ies) over"
        print(f"result: {verdict}")


def cmd_overbudget(args):
    """List budget breaches across history: for every month with spending, which
    budgeted categories exceeded their budget and by how much. Your current
    budgets are applied retroactively to each month -- the longitudinal view
    that `report` (latest month only) and `pace` (current month) don't give."""
    check_month(getattr(args, "month", None))
    data = load()
    if not data["budgets"]:
        print("no budgets set. Try: budget --category food --amount 400")
        return

    exp_months = sorted({month_of(e["date"])
                         for e in expenses_only(data["expenses"])})
    months = ([args.month] if args.month in exp_months else []) \
        if args.month else exp_months

    only = clean_category(args.category) if getattr(args, "category", None) else None
    budgets = {c: lim for c, lim in data["budgets"].items()
               if only is None or c == only}

    breaches = []
    for m in months:
        for cat, limit in sorted(budgets.items()):
            spent = round(category_spent(data, cat, m), 2)
            if limit > 0 and spent > limit:
                breaches.append({
                    "month": m, "category": cat, "budget": round(limit, 2),
                    "spent": spent, "over": round(spent - limit, 2),
                    "pct": round(spent / limit * 100, 1),
                })
    breaches.sort(key=lambda b: (b["month"], -b["over"]))
    total_over = round(sum(b["over"] for b in breaches), 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "breaches": breaches,
            "count": len(breaches),
            "months_checked": len(months),
            "total_over": total_over,
        }, indent=2))
        return

    if not breaches:
        scope = f" in {args.month}" if args.month else ""
        print(f"no budget breaches{scope} - all within budget")
        return

    print("Budget breaches (current budgets applied to each month)")
    print("=" * 64)
    for b in breaches:
        print(f"{b['month']}  {b['category']:<14} "
              f"{money(b['spent']):>10} / {money(b['budget']):<10} "
              f"over {money(b['over']):>9}  {b['pct']:4.0f}%")
    print("-" * 64)
    print(f"{len(breaches)} breach(es) across {len(months)} month(s); "
          f"total overspend {money(total_over)}")


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
    rows = filter_month(rows, args.month)
    if getattr(args, "since", None) or getattr(args, "until", None):
        start, end = _date_bounds(args.since, args.until)
        rows = [e for e in rows if start <= e["date"] <= end]
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
        spent = category_spent(data, cat, period)
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
        spent = category_spent(data, cat, period)
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
    rows = filter_month(rows, args.month)

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
    rows = filter_month(rows, args.month)
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
    rows = filter_month(rows, args.month)

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


def cmd_tip(args):
    """Tip calculator and even bill splitter. Pure arithmetic -- touches no
    stored data. Works in integer cents so a split always sums back to the
    total: if it doesn't divide evenly, the leftover cents are spread one each
    across the first few people.
    """
    if args.amount < 0:
        sys.exit("error: amount cannot be negative")
    pct = args.pct
    if pct < 0:
        sys.exit("error: --pct cannot be negative")
    split = args.split
    if split < 1:
        sys.exit("error: --split must be at least 1")

    bill_cents = round(args.amount * 100)
    tip_cents = round(bill_cents * pct / 100)
    total_cents = bill_cents + tip_cents

    base = total_cents // split
    extra = total_cents % split           # this many people pay one cent more
    low_share = round(base / 100, 2)
    high_share = round((base + (1 if extra else 0)) / 100, 2)
    uneven = extra > 0

    bill = round(bill_cents / 100, 2)
    tip = round(tip_cents / 100, 2)
    total = round(total_cents / 100, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "bill": bill, "pct": pct, "tip": tip, "total": total,
            "split": split, "per_person": high_share,
            "uneven": uneven, "low_share": low_share,
            "high_share": high_share, "high_count": extra,
        }, indent=2))
        return

    pct_str = f"{pct:g}"
    print("Tip calculator")
    print("=" * 40)
    print(f"{'bill':<14} {money(bill):>14}")
    print(f"{'tip (' + pct_str + '%)':<14} {money(tip):>14}")
    print(f"{'total':<14} {money(total):>14}")
    if split > 1:
        if uneven:
            others = split - extra
            who = "person pays" if extra == 1 else "people pay"
            print(f"split {split} ways  {money(low_share)} each "
                  f"({extra} {who} {money(high_share)} to cover the cents)")
            print(f"{'':<14} {others} x {money(low_share)} + "
                  f"{extra} x {money(high_share)}")
        else:
            print(f"split {split} ways  {money(high_share)} each")


def cmd_loan(args):
    """Loan / amortization calculator: the level monthly payment for a fixed
    principal, annual rate, and term, plus total paid and total interest.

    Pure arithmetic -- touches no stored data. A calculator, not a loan offer.
    """
    if args.principal <= 0:
        sys.exit("error: principal must be greater than zero")
    if args.rate < 0:
        sys.exit("error: --rate cannot be negative")
    if args.years <= 0:
        sys.exit("error: --years must be greater than 0")

    n = round(args.years * 12)
    if n < 1:
        sys.exit("error: --years is too short (rounds to zero monthly periods)")
    i = args.rate / 100 / 12
    if i:
        payment = args.principal * i / (1 - (1 + i) ** -n)
    else:
        payment = args.principal / n
    payment = round(payment, 2)
    total = round(payment * n, 2)
    interest = round(total - args.principal, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "principal": round(args.principal, 2), "rate": args.rate,
            "years": args.years, "periods": n,
            "monthly_payment": payment, "total_paid": total,
            "total_interest": interest,
        }, indent=2))
        return

    print("Loan estimate")
    print("=" * 44)
    print(f"{'principal':<16} {money(round(args.principal, 2)):>14}")
    print(f"{'rate':<16} {args.rate:g}% / year")
    print(f"{'term':<16} {args.years:g} year(s) ({n} payments)")
    print("-" * 44)
    print(f"{'monthly payment':<16} {money(payment):>14}")
    print(f"{'total paid':<16} {money(total):>14}")
    print(f"{'total interest':<16} {money(interest):>14}")


def cmd_interest(args):
    """Compound-growth / future-value calculator (monthly compounding).

    Pure arithmetic -- touches no stored data. Projects what a starting
    `principal` grows to over `--years` at an annual `--rate`, optionally with a
    fixed `--monthly` contribution. Not investment advice, just the math.
    """
    if args.principal < 0:
        sys.exit("error: principal cannot be negative")
    if args.rate < 0:
        sys.exit("error: --rate cannot be negative")
    if args.years <= 0:
        sys.exit("error: --years must be greater than 0")
    monthly = args.monthly
    if monthly < 0:
        sys.exit("error: --monthly cannot be negative")

    n = round(args.years * 12)
    if n < 1:
        sys.exit("error: --years is too short (rounds to zero monthly periods)")
    i = args.rate / 100 / 12
    if i:
        growth = (1 + i) ** n
        future = args.principal * growth + monthly * ((growth - 1) / i)
    else:
        future = args.principal + monthly * n
    future = round(future, 2)
    contributed = round(args.principal + monthly * n, 2)
    interest = round(future - contributed, 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "principal": round(args.principal, 2), "rate": args.rate,
            "years": args.years, "monthly": round(monthly, 2), "periods": n,
            "contributed": contributed, "interest": interest,
            "future_value": future,
        }, indent=2))
        return

    print("Compound growth")
    print("=" * 44)
    print(f"{'principal':<14} {money(round(args.principal, 2)):>16}")
    if monthly:
        print(f"{'monthly':<14} {money(round(monthly, 2)):>16}")
    print(f"{'rate':<14} {args.rate:g}% / year (monthly compounding)")
    print(f"{'years':<14} {args.years:g}")
    print("-" * 44)
    print(f"{'contributed':<14} {money(contributed):>16}")
    print(f"{'interest':<14} {money(interest):>16}")
    print(f"{'future value':<14} {money(future):>16}")


def _avg_monthly_net(data, months):
    """Average net (income - expenses) per month over the trailing `months`."""
    today = date.today()
    keys = {month_of(add_months(today, -i).isoformat()) for i in range(months)}
    net = 0.0
    for e in data["expenses"]:
        if month_of(e["date"]) in keys:
            net += e["amount"] if kind_of(e) == "income" else -e["amount"]
    return round(net / months, 2)


def cmd_target(args):
    """Estimate how long to reach a lump-sum savings target at a monthly rate.

    The monthly contribution defaults to your average net over the last
    --months months; the starting balance defaults to your all-time net.
    """
    if args.amount <= 0:
        sys.exit("error: target must be greater than zero")
    months_window = args.months if args.months and args.months > 0 else 6
    data = load()

    if args.start is not None:
        start = round(args.start, 2)
    else:
        start = all_time_net(data)

    if args.monthly is not None:
        monthly = round(args.monthly, 2)
        basis = "given"
    else:
        monthly = _avg_monthly_net(data, months_window)
        basis = f"avg of last {months_window} mo"

    remaining = round(args.amount - start, 2)
    today = date.today()
    if remaining <= 0:
        status, months_needed, reach_date = "reached", 0, today.isoformat()
    elif monthly <= 0:
        status, months_needed, reach_date = "unreachable", None, None
    else:
        months_needed = math.ceil(round(remaining / monthly, 6))
        status = "on_track"
        reach_date = add_months(today, months_needed).isoformat()

    if getattr(args, "json", False):
        print(json.dumps({
            "target": round(args.amount, 2), "start": start, "monthly": monthly,
            "remaining": max(0.0, remaining), "status": status,
            "months": months_needed, "reach_date": reach_date,
        }, indent=2))
        return

    print("Savings target")
    print("=" * 44)
    print(f"{'target':<14} {money(round(args.amount, 2)):>16}")
    print(f"{'current':<14} {money(start):>16}")
    print(f"{'monthly':<14} {money(monthly):>16}  ({basis})")
    if status == "reached":
        print(f"already reached (+{money(start - args.amount)})")
    elif status == "unreachable":
        print(f"{'remaining':<14} {money(remaining):>16}")
        print("at this rate you won't reach it - spending meets or exceeds income")
    else:
        print(f"{'remaining':<14} {money(remaining):>16}")
        label = "month" if months_needed == 1 else "months"
        print(f"about {months_needed} {label} -> around {reach_date}")


def cmd_runway(args):
    """How long a balance lasts at your average monthly net (burn rate).

    The depletion counterpart to `target`. Balance defaults to your all-time
    net; the monthly net defaults to your average over the last --months.
    """
    months_window = args.months if args.months and args.months > 0 else 6
    data = load()
    if args.balance is not None:
        balance = round(args.balance, 2)
    else:
        balance = all_time_net(data)
    if args.monthly_net is not None:
        net = round(args.monthly_net, 2)
        basis = "given"
    else:
        net = _avg_monthly_net(data, months_window)
        basis = f"avg of last {months_window} mo"

    today = date.today()
    if net >= 0:
        status, months, depletion = "positive", None, None
    elif balance <= 0:
        status, months, depletion = "depleted", 0.0, today.isoformat()
    else:
        months = round(balance / abs(net), 1)
        depletion = add_months(today, int(balance / abs(net))).isoformat()
        status = "limited"

    if getattr(args, "json", False):
        print(json.dumps({
            "balance": balance, "monthly_net": net, "status": status,
            "months": months, "depletion_date": depletion,
        }, indent=2))
        return

    print("Runway")
    print("=" * 44)
    print(f"{'balance':<16} {money(balance):>14}")
    flow = f"{'+' if net >= 0 else ''}{money(net)}"
    print(f"{'monthly net':<16} {flow:>14}  ({basis})")
    if status == "positive":
        print("net is >= 0 - your balance isn't shrinking")
    elif status == "depleted":
        print("balance is already at or below zero")
    else:
        label = "month" if months == 1 else "months"
        print(f"about {months:g} {label} of runway -> ~{depletion}")


def cmd_net(args):
    """Income, expenses, net, and savings rate for all time or one month."""
    check_month(args.month)
    data = load()
    if args.month:
        rows = [e for e in data["expenses"] if month_of(e["date"]) == args.month]
        scope = args.month
    else:
        rows = data["expenses"]
        scope = "all time"
    income = round(sum(e["amount"] for e in rows if kind_of(e) == "income"), 2)
    expenses = round(sum(e["amount"] for e in rows
                         if kind_of(e) == "expense"), 2)
    net = round(income - expenses, 2)
    rate = round(net / income * 100, 1) if income > 0 else None

    if getattr(args, "json", False):
        # "spending" (not "expenses") so the web table formats it as money and
        # to match the key month --json uses; "expenses" is a count elsewhere.
        print(json.dumps({"scope": scope, "income": income,
                          "spending": expenses, "net": net,
                          "savings_rate": rate}, indent=2))
        return

    print(f"Net ({scope})")
    print("=" * 36)
    print(f"{'income':<12} {money(income):>16}")
    print(f"{'expenses':<12} {money(expenses):>16}")
    print(f"{'net':<12} {money(net):>16}")
    if rate is not None:
        verb = "saved" if net >= 0 else "overspent by"
        print(f"{verb} {abs(rate):g}% of income")


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
    if getattr(args, "tag", None):
        want = args.tag.strip().lstrip("#").lower()
        rows = [e for e in rows if want in e.get("tags", [])]
    rows = filter_month(rows, args.month)
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


def cmd_payees(args):
    """Rank spending by payee -- the note with #tags stripped (falling back to
    the category when a note is blank), the same merchant key `subscriptions`
    uses. Complements `categories`/`top`, which group by category."""
    check_month(args.month)
    data = load()
    rows = expenses_only(data["expenses"])
    rows = filter_month(rows, args.month)

    agg = {}
    for e in rows:
        p = _normalize_payee(e)
        a = agg.setdefault(p, {"count": 0, "total": 0.0,
                               "first": e["date"], "last": e["date"]})
        a["count"] += 1
        a["total"] = round(a["total"] + e["amount"], 2)
        if e["date"] < a["first"]:
            a["first"] = e["date"]
        if e["date"] > a["last"]:
            a["last"] = e["date"]

    ranked = sorted(agg.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
    limit = args.limit if args.limit and args.limit > 0 else 20
    payees = [{"payee": p, "count": v["count"], "total": v["total"],
               "average": round(v["total"] / v["count"], 2) if v["count"] else 0.0,
               "first": v["first"], "last": v["last"]}
              for p, v in ranked[:limit]]

    if getattr(args, "json", False):
        print(json.dumps({
            "payees": payees,
            "shown": len(payees),
            "count": len(ranked),
            "total": round(sum(v["total"] for _, v in ranked), 2),
        }, indent=2))
        return

    if not payees:
        print(f"no spending{f' in {args.month}' if args.month else ''} yet")
        return

    peak = max(p["total"] for p in payees) or 1.0
    print("Spending by payee" + (f" ({args.month})" if args.month else ""))
    print("=" * 62)
    for p in payees:
        print(f"{money(p['total']):>12}  {p['payee'][:22]:<22} "
              f"x{p['count']:<4} avg {money(p['average']):>10}  "
              f"{bar(p['total'] / peak, 12)}")
    print("-" * 62)
    if len(ranked) > len(payees):
        print(f"showing top {len(payees)} of {len(ranked)} payees")
    else:
        print(f"{len(ranked)} payee(s)")


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
    rows = filter_month(rows, args.month)
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
    rows = filter_month(rows, args.month)
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
    rows = filter_month(rows, args.month)
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
    tomorrow = today + timedelta(days=1)
    for rule in data["recurring"]:
        if rule.get("paused"):
            continue  # a paused rule won't actually charge
        skips = set(rule.get("skips", []))
        for d in _occurrences(rule, horizon, tomorrow):
            if d > today and d.isoformat() not in skips:
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

    ics_target = getattr(args, "ics", None)
    if ics_target:
        # Keep the file inside the data folder, ignoring any path the user gave.
        target = os.path.join(EXPORT_DIR, os.path.basename(ics_target))
        os.makedirs(EXPORT_DIR, exist_ok=True)
        _within_home(target)
        try:
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(build_ics(items))
        except OSError as exc:
            sys.exit(f"error: could not write {target}: {exc}")
        if not getattr(args, "json", False):
            print(f"wrote {len(items)} event(s) to {target}")
            return

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


def cmd_cashflow(args):
    """Project a running balance forward over the next N days using scheduled
    recurring income and expenses, and flag if/when it dips below zero.

    The starting balance defaults to your all-time net (income minus expenses);
    override it with --start-balance. Paused rules and skipped occurrences are
    excluded, since they won't actually happen.
    """
    days = args.days
    if days < 1:
        sys.exit("error: --days must be at least 1")
    data = load()
    today = date.today()
    horizon = today + timedelta(days=days)

    if args.start_balance is not None:
        balance = round(args.start_balance, 2)
    else:
        balance = all_time_net(data)
    start_balance = balance

    events = []
    tomorrow = today + timedelta(days=1)
    for rule in data["recurring"]:
        if rule.get("paused"):
            continue
        skips = set(rule.get("skips", []))
        for d in _occurrences(rule, horizon, tomorrow):
            if d > today and d.isoformat() not in skips:
                events.append({
                    "date": d.isoformat(),
                    "amount": rule["amount"],
                    "category": rule["category"],
                    "kind": rule.get("kind", "expense"),
                    "note": rule["note"],
                })
    # Income before expense on the same day, so a payday that covers a bill
    # doesn't show a spurious dip.
    events.sort(key=lambda e: (e["date"], e["kind"] != "income", e["category"]))

    low_balance, low_date = start_balance, today.isoformat()
    negative_on = None
    for e in events:
        delta = e["amount"] if e["kind"] == "income" else -e["amount"]
        balance = round(balance + delta, 2)
        e["balance"] = balance
        if balance < low_balance:
            low_balance, low_date = balance, e["date"]
        if negative_on is None and balance < 0:
            negative_on = e["date"]

    if getattr(args, "json", False):
        print(json.dumps({
            "days": days, "until": horizon.isoformat(),
            "start_balance": start_balance, "end_balance": balance,
            "net_change": round(balance - start_balance, 2),
            "low_balance": low_balance, "low_date": low_date,
            "negative_on": negative_on, "events": events,
        }, indent=2))
        return

    print(f"Cash-flow projection (next {days} day(s), through "
          f"{horizon.isoformat()})")
    print("=" * 60)
    print(f"{'start balance':<22} {money(start_balance):>14}")
    if not events:
        print("nothing scheduled in range")
        return
    for e in events:
        sign = "+" if e["kind"] == "income" else "-"
        amt = f"{sign}{money(e['amount'])}"
        cat = f"[{e['category']}]"
        print(f"{e['date']}  {amt:>13}  {cat:<16}{money(e['balance']):>14}")
    print("-" * 60)
    change = round(balance - start_balance, 2)
    change_str = ("+" if change >= 0 else "-") + money(abs(change))
    print(f"{'end balance':<22} {money(balance):>14}")
    print(f"{'net change':<22} {change_str:>14}")
    print(f"{'lowest balance':<22} {money(low_balance):>14}  on {low_date}")
    if negative_on:
        print(f"** balance goes negative on {negative_on} **")


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
    rows = filter_month(rows, args.month)
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
    rows = filter_month(rows, args.month)

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


# (name, min_gap_days, max_gap_days, charges_per_month) for cadence detection.
# Ranges overlap nothing and are tuned to real-world billing (month-end dates
# drift 28-31 days, so "monthly" is wide).
_CADENCES = [
    ("weekly", 5, 10, 52 / 12),
    ("biweekly", 11, 18, 26 / 12),
    ("monthly", 25, 35, 1.0),
    ("quarterly", 80, 100, 1 / 3),
    ("yearly", 330, 400, 1 / 12),
]


def _normalize_payee(entry):
    """A stable grouping key for an entry: its note minus #tags, lowercased and
    whitespace-collapsed; falls back to the category when the note is empty."""
    note = _TAG_RE.sub("", entry.get("note") or "")
    key = " ".join(note.split()).strip().lower()
    return key if key else "(" + entry["category"] + ")"


def detect_subscriptions(data, min_count=3, tolerance=0.25):
    """Find subscription-like spending in the expense history: a payee charged
    on a regular cadence (weekly..yearly) with a stable amount. Returns a list
    of dicts sorted by estimated annual cost, each with the detected cadence,
    representative (latest) amount and monthly/annual projections. Read-only."""
    groups = {}
    for e in expenses_only(data["expenses"]):
        groups.setdefault(_normalize_payee(e), []).append(e)

    found = []
    for payee, entries in groups.items():
        entries.sort(key=lambda e: (e["date"], e["id"]))
        # Use unique charge dates to judge cadence (same-day double charges
        # shouldn't read as a zero-day interval).
        by_date = {}
        for e in entries:
            by_date.setdefault(e["date"], e)
        dates = sorted(by_date)
        if len(dates) < max(min_count, 2):
            continue

        days = [date.fromisoformat(d) for d in dates]
        gaps = [(days[i + 1] - days[i]).days for i in range(len(days) - 1)]
        gap = _median(gaps)
        cadence = next((c for c in _CADENCES if c[1] <= gap <= c[2]), None)
        if cadence is None:
            continue

        amounts = [round(e["amount"], 2) for e in by_date.values()]
        mean = sum(amounts) / len(amounts)
        if mean <= 0:
            continue
        if (max(amounts) - min(amounts)) / mean > tolerance:
            continue  # amount too variable to be a fixed subscription

        name, _lo, _hi, per_month = cadence
        latest = round(entries[-1]["amount"], 2)
        monthly = round(latest * per_month, 2)
        found.append({
            "payee": payee,
            "category": entries[-1]["category"],
            "cadence": name,
            "amount": latest,
            "count": len(entries),
            "first": dates[0],
            "last": dates[-1],
            "monthly": monthly,
            "annual": round(monthly * 12, 2),
        })

    found.sort(key=lambda s: (-s["annual"], s["payee"]))
    return found


def cmd_subscriptions(args):
    """Detect recurring subscription-like charges from the expense history."""
    data = load()
    min_count = max(2, getattr(args, "min_count", 3))
    subs = detect_subscriptions(data, min_count=min_count)
    monthly = round(sum(s["monthly"] for s in subs), 2)

    if getattr(args, "json", False):
        print(json.dumps({
            "subscriptions": subs,
            "count": len(subs),
            "monthly": monthly,
            "annual": round(monthly * 12, 2),
        }, indent=2))
        return

    if not subs:
        print("no subscription-like charges detected"
              f" (need {min_count}+ regular charges with a stable amount)")
        return

    print("Detected subscriptions (estimated from spending history)")
    print("=" * 66)
    for s in subs:
        print(f"{money(s['amount']):>11}/{s['cadence']:<9} "
              f"{money(s['monthly']):>11}/mo  "
              f"{s['payee'][:24]:<24} [{s['category']}] x{s['count']}")
    print("-" * 66)
    print(f"{len(subs)} subscription(s): {money(monthly)}/mo, "
          f"{money(monthly * 12)}/yr estimated.")
    print("Tip: formalize one with `recur from <id>` or `recur add`.")


def cmd_savings(args):
    data = load()
    agg = group_totals(data["expenses"], lambda e: month_of(e["date"]))

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
    agg = group_totals(data["expenses"], lambda e: e["date"][:4])

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


def _valid_iso(s):
    try:
        date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


# Issue kinds that _autofix can safely repair on its own.
_FIXABLE_KINDS = frozenset({
    "duplicate_id", "bad_id", "orphan_recur_id", "empty_category", "bad_budget",
})


def _is_valid_id(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _scan_issues(data):
    """Return a list of integrity problems found in `data` (read-only)."""
    issues = []

    counts = {}
    for e in data["expenses"]:
        counts[e.get("id")] = counts.get(e.get("id"), 0) + 1
    # str() the key so a mix of int and non-int ids can't raise in the sort.
    for eid, n in sorted(counts.items(), key=lambda kv: (kv[0] is None, str(kv[0]))):
        if n > 1:
            issues.append({"kind": "duplicate_id", "id": eid,
                           "detail": f"id #{eid} is used by {n} entries"})

    rule_ids = {r.get("id") for r in data["recurring"]}
    for e in data["expenses"]:
        eid = e.get("id")
        if not _is_valid_id(eid):
            issues.append({"kind": "bad_id", "id": eid,
                           "detail": f"entry has a non-integer id {eid!r}"})
        amt = e.get("amount")
        if isinstance(amt, bool) or not isinstance(amt, (int, float)) or amt <= 0:
            issues.append({"kind": "bad_amount", "id": eid,
                           "detail": f"#{eid} has amount {amt!r}"})
        if not _valid_iso(e.get("date")):
            issues.append({"kind": "bad_date", "id": eid,
                           "detail": f"#{eid} has date {e.get('date')!r}"})
        if not str(e.get("category") or "").strip():
            issues.append({"kind": "empty_category", "id": eid,
                           "detail": f"#{eid} has no category"})
        rid = e.get("recur_id")
        if rid is not None and rid not in rule_ids:
            issues.append({"kind": "orphan_recur_id", "id": eid,
                           "detail": f"#{eid} points at missing rule #{rid}"})

    for r in data["recurring"]:
        rid = r.get("id")
        if r.get("every") not in ("day", "week", "month"):
            issues.append({"kind": "bad_frequency", "id": rid,
                           "detail": f"rule #{rid} has every={r.get('every')!r}"})
        if not _valid_iso(r.get("start")):
            issues.append({"kind": "bad_rule_start", "id": rid,
                           "detail": f"rule #{rid} start {r.get('start')!r}"})
        if r.get("until") is not None and not _valid_iso(r.get("until")):
            issues.append({"kind": "bad_rule_until", "id": rid,
                           "detail": f"rule #{rid} until {r.get('until')!r}"})
        cnt = r.get("count")
        if cnt is not None and (isinstance(cnt, bool)
                                or not isinstance(cnt, int) or cnt < 1):
            issues.append({"kind": "bad_rule_count", "id": rid,
                           "detail": f"rule #{rid} count {cnt!r}"})

    for cat, lim in data["budgets"].items():
        if isinstance(lim, bool) or not isinstance(lim, (int, float)) or lim <= 0:
            issues.append({"kind": "bad_budget", "id": None,
                           "detail": f"budget [{cat}] is {lim!r}"})

    return issues


def _autofix(data):
    """Repair the safe, unambiguous problems in-place. Returns a list of the
    repairs made (each a short human-readable string)."""
    fixed = []
    rule_ids = {r.get("id") for r in data["recurring"]}
    for e in data["expenses"]:
        rid = e.get("recur_id")
        if rid is not None and rid not in rule_ids:
            del e["recur_id"]
            fixed.append(f"unlinked #{e.get('id')} from missing rule #{rid}")
        if not str(e.get("category") or "").strip():
            e["category"] = "uncategorized"
            fixed.append(f"set #{e.get('id')} category to 'uncategorized'")

    # Reassign duplicate or non-integer ids (keep the first use of each).
    seen = set()
    next_free = next_id(data["expenses"])
    for e in data["expenses"]:
        eid = e.get("id")
        if not _is_valid_id(eid) or eid in seen:
            e["id"] = next_free
            fixed.append(f"reassigned a duplicate/invalid id to #{next_free}")
            next_free += 1
        seen.add(e["id"])

    for cat in [c for c, lim in list(data["budgets"].items())
                if isinstance(lim, bool) or not isinstance(lim, (int, float))
                or lim <= 0]:
        del data["budgets"][cat]
        fixed.append(f"removed invalid budget [{cat}]")

    return fixed


def cmd_check(args):
    """Scan the stored data for integrity problems and report them.

    Read-only by default (no recurring catch-up runs first, so it inspects the
    data as stored). With --fix, repairs the safe, unambiguous problems
    (orphan recurring links, empty categories, duplicate ids, invalid budgets)
    and reports what remains for you to handle manually.
    """
    data = load()
    repaired = []
    if getattr(args, "fix", False):
        repaired = _autofix(data)
        if repaired:
            save(data)
    issues = _scan_issues(data)

    if getattr(args, "json", False):
        print(json.dumps({"ok": not issues, "count": len(issues),
                          "issues": issues, "fixed": repaired}, indent=2))
        return

    if repaired:
        print(f"Fixed {len(repaired)} problem(s)")
        for f in repaired:
            print(f"  - {f}")
        print("-" * 52)

    n_exp = len(data["expenses"])
    n_rules = len(data["recurring"])
    if not issues:
        tail = "now look healthy" if repaired else "look healthy"
        print(f"No problems found - {n_exp} entr{'y' if n_exp == 1 else 'ies'}, "
              f"{n_rules} recurring rule(s) {tail}.")
        return
    print(f"Found {len(issues)} problem(s)"
          + (" still needing a manual fix" if repaired else ""))
    print("=" * 52)
    for i in issues:
        print(f"  [{i['kind']}] {i['detail']}")
    print("-" * 52)
    if any(i["kind"] in _FIXABLE_KINDS for i in issues):
        print("Tip: run `check --fix` to repair the auto-fixable ones.")
    else:
        print("Tip: fix with edit/delete/recategorize, or restore a backup.")


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
        save_config(copy.deepcopy(DEFAULT_CONFIG))
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
    if getattr(args, "home_code", None) is not None:
        hc = args.home_code.strip()
        if hc and not _FX_CODE_RE.match(hc):
            sys.exit("error: --home-code must be a currency code (letters only)")
        cfg["home_code"] = hc.upper()
        changed = True

    if changed:
        save_config(cfg)
        _CONFIG.update(cfg)   # so the sample below prints with the new settings
        print("config updated")

    # Always show the resulting settings.
    print(f"{'currency':<16} {cfg['currency']}")
    print(f"{'list_limit':<16} {cfg['list_limit']}")
    print(f"{'symbol_position':<16} {cfg.get('symbol_position', 'before')}")
    print(f"{'home_code':<16} {cfg.get('home_code') or '(unset)'}")
    print(f"{'sample':<16} {money(1234.5)}")


# --------------------------------------------------------------------------- #
# Offline currency converter (fx)
# --------------------------------------------------------------------------- #

_FX_CODE_RE = re.compile(r"^[A-Za-z]{1,6}$")


def _fx_code(raw):
    """Normalize and validate a currency code (letters only, upper-cased)."""
    c = (raw or "").strip().upper()
    if not _FX_CODE_RE.match(c):
        sys.exit(f"error: '{raw}' is not a valid currency code (letters only)")
    return c


def _fx_rates():
    return dict(load_config().get("fx", {}))


def _amount_in_home(amount, code):
    """Convert `amount` (given in currency `code`) to the home currency using
    the stored fx rates. Exits with a clear message if the setup is missing."""
    cfg = load_config()
    home = (cfg.get("home_code") or "").strip().upper()
    if not home:
        sys.exit("error: set your home currency code first, e.g. "
                 "`config --home-code USD`")
    rates = cfg.get("fx", {})
    code = _fx_code(code)
    for c in (code, home):
        if c not in rates:
            sys.exit(f"error: no fx rate set for {c}. Try: `fx set {c} <rate>`")
    return round(amount * rates[code] / rates[home], 2)


def cmd_fx_set(args):
    code = _fx_code(args.code)
    if args.rate <= 0:
        sys.exit("error: rate must be greater than zero")
    cfg = load_config()
    rates = dict(cfg.get("fx", {}))
    rates[code] = round(args.rate, 6)
    cfg["fx"] = rates
    save_config(cfg)
    print(f"set {code} = {rates[code]:g} (per reference unit)")


def cmd_fx_rm(args):
    code = _fx_code(args.code)
    cfg = load_config()
    rates = dict(cfg.get("fx", {}))
    if code not in rates:
        sys.exit(f"error: no rate set for {code}")
    del rates[code]
    cfg["fx"] = rates
    save_config(cfg)
    print(f"removed {code}")


def cmd_fx_list(args):
    rates = _fx_rates()
    if getattr(args, "json", False):
        print(json.dumps({"rates": rates}, indent=2))
        return
    if not rates:
        print("no exchange rates set. Try: fx set EUR 1.09")
        return
    print("Exchange rates (per reference unit)")
    print("=" * 40)
    for code in sorted(rates):
        print(f"{code:<8} {rates[code]:>12g}")


def cmd_fx_convert(args):
    rates = _fx_rates()
    src = _fx_code(args.src)
    if args.amount < 0:
        sys.exit("error: amount cannot be negative")
    if src not in rates:
        sys.exit(f"error: no rate set for {src}. Try: fx set {src} <rate>")

    # No target given: convert into every other stored currency.
    if args.dst is None:
        others = [c for c in sorted(rates) if c != src]
        if not others:
            sys.exit(f"error: no other currencies set. Try: fx set <code> <rate>")
        conversions = [{"to": c, "rate": round(rates[src] / rates[c], 6),
                        "result": round(args.amount * rates[src] / rates[c], 2)}
                       for c in others]
        if getattr(args, "json", False):
            print(json.dumps({"amount": round(args.amount, 2), "from": src,
                              "conversions": conversions}, indent=2))
            return
        print(f"{args.amount:g} {src} =")
        for c in conversions:
            print(f"  {c['result']:>14,.2f} {c['to']}")
        return

    dst = _fx_code(args.dst)
    if dst not in rates:
        sys.exit(f"error: no rate set for {dst}. Try: fx set {dst} <rate>")
    pair = rates[src] / rates[dst]
    result = round(args.amount * pair, 2)
    if getattr(args, "json", False):
        print(json.dumps({
            "amount": round(args.amount, 2), "from": src, "to": dst,
            "rate": round(pair, 6), "result": result,
        }, indent=2))
        return
    print(f"{args.amount:g} {src} = {result:,.2f} {dst}")
    print(f"rate  1 {src} = {pair:,.6g} {dst}")


def _parse_until(raw, start_iso):
    """Validate an optional --until date against a rule's start; return ISO or None."""
    if raw is None:
        return None
    u = parse_date(raw)
    if u < start_iso:
        sys.exit(f"error: --until {u} is before the start {start_iso}")
    return u


def cmd_recur_add(args):
    data = load()
    if args.amount <= 0:
        sys.exit("error: amount must be greater than zero")
    start = parse_date(args.start)
    rule = {
        "id": next_id(data["recurring"]),
        "amount": round(args.amount, 2),
        "category": clean_category(args.category),
        "note": args.note.strip(),
        "every": args.every,
        "start": start,
        "last": None,
        "kind": "income" if args.income else "expense",
    }
    until = _parse_until(getattr(args, "until", None), start)
    if until:
        rule["until"] = until
    count = getattr(args, "count", None)
    if count is not None:
        if count < 1:
            sys.exit("error: --count must be at least 1")
        rule["count"] = count
    data["recurring"].append(rule)
    created = apply_recurring(data)  # catch up immediately
    save(data)
    what = "income" if rule["kind"] == "income" else "expense"
    ends = (f" until {rule['until']}" if rule.get("until")
            else f" x{rule['count']}" if rule.get("count") else "")
    print(f"added recurring {what} rule #{rule['id']}: {money(rule['amount'])} "
          f"[{rule['category']}] every {rule['every']} from {rule['start']}{ends}")
    if created:
        print(f"  generated {created} expense(s) up to today")


def cmd_recur_from(args):
    data = load()
    e = find(data["expenses"], args.id)
    if not e:
        sys.exit(f"error: no entry with id #{args.id}")
    note = e["note"]
    start = parse_date(args.start) if args.start else e["date"]
    rule = {
        "id": next_id(data["recurring"]),
        "amount": round(e["amount"], 2),
        "category": e["category"],
        "note": note,
        "every": args.every,
        "start": start,
        "last": None,
        "kind": kind_of(e),
    }
    until = _parse_until(getattr(args, "until", None), start)
    if until:
        rule["until"] = until
    count = getattr(args, "count", None)
    if count is not None:
        if count < 1:
            sys.exit("error: --count must be at least 1")
        rule["count"] = count
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
    if args.until is not None and args.no_until:
        sys.exit("error: choose either --until or --no-until, not both")
    if args.count is not None and args.no_count:
        sys.exit("error: choose either --count or --no-count, not both")
    if all(v is None for v in (args.amount, args.category, args.note, args.every,
                               args.until, args.count)) \
            and not args.income and not args.expense \
            and not args.no_until and not args.no_count:
        sys.exit("error: nothing to change - pass --amount/--category/--note/"
                 "--every/--income/--expense/--until/--no-until/--count/--no-count")

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
    if args.no_until:
        r.pop("until", None)
    elif args.until is not None:
        r["until"] = _parse_until(args.until, r["start"])
    if args.no_count:
        r.pop("count", None)
    elif args.count is not None:
        if args.count < 1:
            sys.exit("error: --count must be at least 1")
        r["count"] = args.count

    save(data)
    what = "income" if r.get("kind") == "income" else "expense"
    ends = (f" until {r['until']}" if r.get("until")
            else f" x{r['count']}" if r.get("count") else "")
    print(f"updated recurring {what} rule #{r['id']}: {money(r['amount'])} "
          f"[{r['category']}] every {r['every']}{ends}")
    print("  (already-generated expenses are unchanged)")


def _recur_row(rule, today):
    """Build a structured status row for a recurring rule (shared by text/JSON)."""
    today_iso = today.isoformat()
    skips = set(rule.get("skips", []))
    occ = _occurrences(rule, add_months(today, 2), today + timedelta(days=1))
    upcoming = [d for d in occ if d > today and d.isoformat() not in skips]
    nxt = upcoming[0].isoformat() if upcoming else None
    if rule.get("paused"):
        status = "paused"
    elif (rule.get("until") and rule["until"] < today_iso) or nxt is None:
        status = "ended"
    else:
        status = "active"
    return {
        "id": rule["id"], "amount": rule["amount"], "category": rule["category"],
        "every": rule["every"], "kind": rule.get("kind", "expense"),
        "note": rule.get("note", ""), "start": rule["start"],
        "until": rule.get("until"), "count": rule.get("count"),
        "next": nxt if status == "active" else None,
        "status": status,
        "skips": sorted(s for s in skips if s >= today_iso),
    }


def cmd_recur_list(args):
    data = load()
    today = date.today()
    rows = [_recur_row(r, today)
            for r in sorted(data["recurring"], key=lambda x: x["id"])]

    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print("no recurring rules. Try: recur add 1200 rent --every month")
        return

    print("Recurring rules")
    print("=" * 60)
    for row in rows:
        note = f" - {row['note']}" if row["note"] else ""
        mark = " +income" if row["kind"] == "income" else ""
        skip_note = f"  skips: {', '.join(row['skips'])}" if row["skips"] else ""
        until_note = (f"  until {row['until']}" if row["until"]
                      else f"  x{row['count']}" if row["count"] else "")
        state = ("  (PAUSED)" if row["status"] == "paused"
                 else "  (ENDED)" if row["status"] == "ended" else "")
        nxt_disp = {"paused": "paused", "ended": "ended"}.get(
            row["status"], row["next"])
        print(f"#{row['id']:<3} {money(row['amount']):>10}  [{row['category']}]{mark}"
              f"  every {row['every']:<5}  next: {nxt_disp}{until_note}{note}"
              f"{skip_note}{state}")


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
        nxt = next((d for d in _occurrences(rule, horizon, after + timedelta(days=1))
                    if d > after), None)
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
    a.add_argument("--in", dest="in_", metavar="CODE",
                   help="amount is in this currency; convert to home via fx rates")
    a.set_defaults(func=cmd_add)

    inc = sub.add_parser("income", help="record an income entry")
    inc.add_argument("amount", type=float, help="amount received, e.g. 2500")
    inc.add_argument("category", help="source, e.g. salary, freelance, gift")
    inc.add_argument("note", nargs="?", default="", help="optional note")
    inc.add_argument("--date", default="today",
                     help="YYYY-MM-DD, 'today', or 'yesterday'")
    inc.add_argument("--in", dest="in_", metavar="CODE",
                     help="amount is in this currency; convert to home via fx rates")
    inc.set_defaults(func=cmd_income)

    l = sub.add_parser("list", help="show recent expenses")
    l.add_argument("--category", help="filter by category")
    l.add_argument("--tag", help="filter by #tag (with or without the #)")
    l.add_argument("--month", help="filter by month, YYYY-MM")
    l.add_argument("--limit", type=int, default=None,
                   help="show at most N most-recent items (default from config)")
    l.add_argument("--income", action="store_true", help="show income instead")
    l.add_argument("--all", action="store_true", help="show expenses and income")
    l.add_argument("--sort", choices=["date", "amount", "category"],
                   default="date", help="sort order (default date)")
    l.add_argument("--desc", action="store_true",
                   help="reverse the sort (e.g. largest first with --sort amount)")
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

    sp = sub.add_parser("split",
                        help="split an entry into category/amount parts")
    sp.add_argument("id", type=int, help="entry id to split (see `list`)")
    sp.add_argument("parts", nargs="+",
                    help="category/amount (or category/percent with --pct) pairs, "
                         "e.g. groceries 70 household 30")
    sp.add_argument("--pct", action="store_true",
                    help="treat values as percentages that sum to 100")
    sp.set_defaults(func=cmd_split)

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
    s.add_argument("--json", action="store_true", help="output JSON instead of text")
    s.set_defaults(func=cmd_summary)

    b = sub.add_parser("budget", help="set or view monthly budgets")
    b.add_argument("--category", help="category to set a budget for")
    b.add_argument("--amount", type=float, help="monthly budget amount")
    b.add_argument("--month", help="month to check against, YYYY-MM")
    b.add_argument("--json", action="store_true",
                   help="output JSON instead of text (view mode)")
    b.set_defaults(func=cmd_budget)

    ub = sub.add_parser("unbudget", help="remove a category's budget")
    ub.add_argument("category", nargs="?", help="category whose budget to remove")
    ub.add_argument("--all", action="store_true", help="clear every budget")
    ub.set_defaults(func=cmd_unbudget)

    x = sub.add_parser("export",
                       help="write entries to CSV/JSON (in data folder)")
    x.add_argument("--file", help="file name (basename only; saved in exports/)")
    x.add_argument("--month", help="only export this month, YYYY-MM")
    x.add_argument("--start", help="range start date (with --end); YYYY-MM-DD/today")
    x.add_argument("--end", help="range end date (with --start); YYYY-MM-DD/today")
    x.add_argument("--category", help="only export this category")
    x.add_argument("--income", action="store_true", help="only income entries")
    x.add_argument("--expenses", action="store_true", help="only expense entries")
    x.add_argument("--format", choices=["csv", "json"], default="csv",
                   help="output format (default csv)")
    x.set_defaults(func=cmd_export)

    im = sub.add_parser("import",
                        help="import entries from a CSV or JSON file in the data folder")
    im.add_argument("--file", required=True,
                    help="file name (.csv or .json; looked up in exports/ then "
                         "the data folder)")
    im.add_argument("--dry-run", action="store_true",
                    help="preview counts without importing anything")
    im.set_defaults(func=cmd_import)

    rp = sub.add_parser("report", help="month-over-month trend and budget adherence")
    rp.add_argument("--months", type=int, default=6,
                    help="how many months to show (default 6)")
    rp.add_argument("--json", action="store_true", help="output JSON instead of text")
    rp.set_defaults(func=cmd_report)

    sr = sub.add_parser("search", help="find expenses by keyword and filters")
    sr.add_argument("keyword", nargs="?", default="",
                    help="substring to match in note or category")
    sr.add_argument("--category", help="restrict to this exact category")
    sr.add_argument("--tag", help="restrict to a #tag (with or without the #)")
    sr.add_argument("--month", help="restrict to a month, YYYY-MM")
    sr.add_argument("--since", help="only entries on/after this date (YYYY-MM-DD/today)")
    sr.add_argument("--until", help="only entries on/before this date (YYYY-MM-DD/today)")
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

    subn = sub.add_parser("subscriptions",
                          help="detect recurring subscription-like charges from history")
    subn.add_argument("--min-count", type=int, default=3, dest="min_count",
                      help="minimum regular charges to flag (default: 3)")
    subn.add_argument("--json", action="store_true", help="output JSON instead of text")
    subn.set_defaults(func=cmd_subscriptions)

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
    up.add_argument("--ics", nargs="?", const="upcoming.ics", default=None,
                    metavar="FILE",
                    help="also write the schedule as an iCalendar (.ics) file "
                         "in the data folder (default name: upcoming.ics)")
    up.add_argument("--json", action="store_true", help="output JSON instead of text")
    up.set_defaults(func=cmd_upcoming)

    cfw = sub.add_parser("cashflow",
                         help="project a running balance forward from recurring rules")
    cfw.add_argument("--days", type=int, default=30,
                     help="how many days ahead to project (default 30)")
    cfw.add_argument("--start-balance", type=float, default=None,
                     dest="start_balance",
                     help="starting balance (default: your all-time net)")
    cfw.add_argument("--json", action="store_true", help="output JSON instead of text")
    cfw.set_defaults(func=cmd_cashflow)

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

    ob = sub.add_parser("overbudget",
                        help="list budget breaches across every month of history")
    ob.add_argument("--month", help="only check this month, YYYY-MM")
    ob.add_argument("--category", help="only check this category")
    ob.add_argument("--json", action="store_true", help="output JSON instead of text")
    ob.set_defaults(func=cmd_overbudget)

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

    tip = sub.add_parser("tip",
                         help="tip calculator and even bill splitter")
    tip.add_argument("amount", type=float, help="the bill amount (before tip)")
    tip.add_argument("--pct", type=float, default=18.0,
                     help="tip percentage (default 18)")
    tip.add_argument("--split", type=int, default=1,
                     help="split the total between this many people (default 1)")
    tip.add_argument("--json", action="store_true", help="output JSON instead of text")
    tip.set_defaults(func=cmd_tip)

    it = sub.add_parser("interest",
                        help="compound-growth / future-value calculator")
    it.add_argument("principal", type=float, help="starting amount")
    it.add_argument("--rate", type=float, default=5.0,
                    help="annual interest rate in %% (default 5)")
    it.add_argument("--years", type=float, default=10.0,
                    help="number of years (default 10)")
    it.add_argument("--monthly", type=float, default=0.0,
                    help="fixed monthly contribution (default 0)")
    it.add_argument("--json", action="store_true", help="output JSON instead of text")
    it.set_defaults(func=cmd_interest)

    ln = sub.add_parser("loan",
                        help="loan payment / amortization calculator")
    ln.add_argument("principal", type=float, help="amount borrowed")
    ln.add_argument("--rate", type=float, default=6.0,
                    help="annual interest rate in %% (default 6)")
    ln.add_argument("--years", type=float, default=5.0,
                    help="term in years (default 5)")
    ln.add_argument("--json", action="store_true", help="output JSON instead of text")
    ln.set_defaults(func=cmd_loan)

    tgt = sub.add_parser("target",
                         help="estimate how long to reach a savings target")
    tgt.add_argument("amount", type=float, help="the savings amount to reach")
    tgt.add_argument("--monthly", type=float,
                     help="monthly contribution (default: your recent average net)")
    tgt.add_argument("--start", type=float,
                     help="starting balance (default: your all-time net)")
    tgt.add_argument("--months", type=int, default=6,
                     help="months of history to average for the default rate "
                          "(default 6)")
    tgt.add_argument("--json", action="store_true", help="output JSON instead of text")
    tgt.set_defaults(func=cmd_target)

    rw = sub.add_parser("runway",
                        help="how long a balance lasts at your average monthly net")
    rw.add_argument("--balance", type=float,
                    help="current balance (default: your all-time net)")
    rw.add_argument("--monthly-net", type=float, dest="monthly_net",
                    help="monthly net (default: your recent average net)")
    rw.add_argument("--months", type=int, default=6,
                    help="months of history to average for the default rate "
                         "(default 6)")
    rw.add_argument("--json", action="store_true", help="output JSON instead of text")
    rw.set_defaults(func=cmd_runway)

    nt = sub.add_parser("net",
                        help="income, expenses, net and savings rate (all-time or a month)")
    nt.add_argument("--month", help="restrict to a month, YYYY-MM")
    nt.add_argument("--json", action="store_true", help="output JSON instead of text")
    nt.set_defaults(func=cmd_net)

    av = sub.add_parser("average", help="average spending per day/week/month")
    av.add_argument("--json", action="store_true", help="output JSON instead of text")
    av.set_defaults(func=cmd_average)

    tp = sub.add_parser("top", help="list your largest expenses")
    tp.add_argument("--limit", type=int, default=10,
                    help="how many to show (default 10)")
    tp.add_argument("--month", help="restrict to a month, YYYY-MM")
    tp.add_argument("--category", help="restrict to a category")
    tp.add_argument("--tag", help="restrict to a #tag (with or without the #)")
    tp.add_argument("--income", action="store_true", help="rank income instead")
    tp.add_argument("--all", action="store_true", help="rank expenses and income")
    tp.add_argument("--json", action="store_true", help="output JSON instead of text")
    tp.set_defaults(func=cmd_top)

    pay = sub.add_parser("payees",
                         help="rank spending by payee (merchant), from the note")
    pay.add_argument("--month", help="restrict to a month, YYYY-MM")
    pay.add_argument("--limit", type=int, default=20,
                     help="show the top N payees (default 20)")
    pay.add_argument("--json", action="store_true", help="output JSON instead of text")
    pay.set_defaults(func=cmd_payees)

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
    cf.add_argument("--home-code", dest="home_code",
                    help="your home currency's fx code (e.g. USD) for `add --in`")
    cf.add_argument("--reset", action="store_true", help="restore default settings")
    cf.set_defaults(func=cmd_config)

    vs = sub.add_parser("version", help="show the version")
    vs.set_defaults(func=cmd_version)

    wh = sub.add_parser("where", help="show the data folder and its files")
    wh.add_argument("--json", action="store_true", help="output JSON instead of text")
    wh.set_defaults(func=cmd_where)

    ck = sub.add_parser("check",
                        help="scan your data for integrity problems")
    ck.add_argument("--fix", action="store_true",
                    help="repair the safe, unambiguous problems (undoable)")
    ck.add_argument("--json", action="store_true", help="output JSON instead of text")
    ck.set_defaults(func=cmd_check)

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

    fx = sub.add_parser("fx",
                        help="offline currency converter (user-set rates)")
    fxsub = fx.add_subparsers(dest="fx_command")
    fxs = fxsub.add_parser("set", help="set or update a currency's rate")
    fxs.add_argument("code", help="currency code, e.g. EUR")
    fxs.add_argument("rate", type=float,
                     help="value of 1 unit in your reference currency")
    fxs.set_defaults(func=cmd_fx_set)
    fxl = fxsub.add_parser("list", help="list stored rates")
    fxl.add_argument("--json", action="store_true", help="output JSON instead of text")
    fxl.set_defaults(func=cmd_fx_list)
    fxr = fxsub.add_parser("rm", help="remove a currency's rate")
    fxr.add_argument("code", help="currency code to remove")
    fxr.set_defaults(func=cmd_fx_rm)
    fxc = fxsub.add_parser("convert",
                           help="convert an amount between two set currencies")
    fxc.add_argument("amount", type=float, help="the amount to convert")
    fxc.add_argument("src", metavar="from", help="source currency code")
    fxc.add_argument("dst", metavar="to", nargs="?", default=None,
                     help="target currency code (omit to show every currency)")
    fxc.add_argument("--json", action="store_true", help="output JSON instead of text")
    fxc.set_defaults(func=cmd_fx_convert)
    fx.set_defaults(func=lambda args: fx.print_help())

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
    ra.add_argument("--until",
                    help="stop generating after this date, YYYY-MM-DD "
                         "(e.g. a lease or loan end)")
    ra.add_argument("--count", type=int,
                    help="stop after this many occurrences (e.g. 12 payments)")
    ra.set_defaults(func=cmd_recur_add)

    rfr = rsub.add_parser("from", help="create a recurring rule from an existing entry")
    rfr.add_argument("id", type=int, help="entry id to base the rule on (see `list`)")
    rfr.add_argument("--every", choices=["day", "week", "month"], required=True,
                     help="how often it recurs")
    rfr.add_argument("--start", help="first date, YYYY-MM-DD (default: the entry's date)")
    rfr.add_argument("--until", help="stop generating after this date, YYYY-MM-DD")
    rfr.add_argument("--count", type=int,
                     help="stop after this many occurrences")
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
    re_.add_argument("--until", help="set an end date, YYYY-MM-DD")
    re_.add_argument("--no-until", action="store_true", dest="no_until",
                     help="remove the end date (recur forever again)")
    re_.add_argument("--count", type=int, help="set a fixed occurrence count")
    re_.add_argument("--no-count", action="store_true", dest="no_count",
                     help="remove the occurrence count")
    re_.set_defaults(func=cmd_recur_edit)

    rl = rsub.add_parser("list", help="show recurring rules")
    rl.add_argument("--json", action="store_true", help="output JSON instead of text")
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


# Every top-level command is classified as either a read command (safe to run a
# recurring catch-up before it, so views reflect what's due) or a mutating/meta
# command (no implicit catch-up). Keeping these as explicit, exhaustive sets --
# rather than an inline list -- lets test_commands_are_all_classified() fail
# loudly if a newly added command is left out of both, so the catch-up behaviour
# can never silently drift out of sync with the parser.
CATCHUP_COMMANDS = frozenset({
    "list", "summary", "budget", "export", "report", "stats", "search",
    "categories", "tags", "month", "upcoming", "compare", "trend", "top",
    "pace", "duplicates", "week", "streak", "weekday", "day", "year",
    "untagged", "average", "distribution", "sources", "quarter", "forecast",
    "balance", "commitments", "savings", "heatmap", "suggest", "insights",
    "tagtrend", "range", "matrix", "cumulative", "allowance", "tagmatrix",
    "weekly", "years", "anomalies", "roundup", "cashflow", "target", "runway",
    "net", "subscriptions", "payees", "overbudget",
})
MUTATING_COMMANDS = frozenset({
    "add", "income", "edit", "delete", "clone", "refund", "note", "tag",
    "untag", "retag", "recategorize", "unbudget", "goal", "autobudget",
    "import", "restore", "backup", "dedupe", "undo", "config", "recur",
    "completion", "version", "web", "where", "tip", "split", "fx", "check",
    "interest", "loan",
})


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
    if args.command in CATCHUP_COMMANDS:
        data = load()
        if apply_recurring(data):
            save(data)

    args.func(args)


if __name__ == "__main__":
    main()
