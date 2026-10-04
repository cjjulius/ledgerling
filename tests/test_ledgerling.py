"""Tests for Ledgerling. Pure-logic tests run in memory; the few that touch
files redirect the app's paths to a temp dir, so the real data is never used
and the sandbox guard still holds.

Run from the app folder:  python -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import date, datetime, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
from ledgerling import cli as L  # noqa: E402


class PureLogic(unittest.TestCase):
    def test_read_commands_support_json(self):
        # Every catch-up (read) command should offer --json for scripting and
        # the self-generating web UI. export is exempt: it writes files and
        # uses --format json for that instead.
        import argparse
        parser = L.build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))
        exempt = {"export"}
        missing = []
        for name in L.CATCHUP_COMMANDS:
            if name in exempt:
                continue
            opts = set()
            for a in sub.choices[name]._actions:
                opts.update(a.option_strings)
            if "--json" not in opts:
                missing.append(name)
        self.assertEqual(sorted(missing), [],
                         "catch-up commands missing --json: %s" % sorted(missing))

    def test_paired_filter_options_consistent(self):
        # A command that offers one side of a paired filter must offer the other,
        # so --min/--max and --since/--until can never drift apart as commands
        # gain filters over time.
        import argparse
        parser = L.build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))
        pairs = [("--min", "--max"), ("--since", "--until")]
        problems = []
        for name, p in sub.choices.items():
            opts = set()
            for a in p._actions:
                opts.update(a.option_strings)
            for lo, hi in pairs:
                if (lo in opts) != (hi in opts):
                    problems.append(f"{name}: has only one of {lo}/{hi}")
        self.assertEqual(problems, [], "; ".join(problems))

    def test_docstring_lists_every_command(self):
        # Guard against help-text drift: every top-level command must appear in
        # the module docstring's command list (indented "name   description").
        import argparse
        import re
        parser = L.build_parser()
        cmds = set()
        for a in parser._actions:
            if isinstance(a, argparse._SubParsersAction):
                cmds |= set(a.choices.keys())
        documented = set(re.findall(r'^\s{4}([a-z][a-z0-9-]+)\s{2,}',
                                    L.__doc__ or "", re.M))
        missing = cmds - documented
        self.assertEqual(missing, set(),
                         "commands missing from the module docstring: %s"
                         % sorted(missing))

    def test_commands_are_all_classified(self):
        # Guard: every top-level command must be classified as either a
        # catch-up (read) command or a mutating/meta command, so the implicit
        # recurring catch-up in main() can never drift out of sync when a new
        # command is added. Fails loudly naming any unclassified/stale entry.
        import argparse
        parser = L.build_parser()
        names = set()
        for a in parser._actions:
            if isinstance(a, argparse._SubParsersAction):
                names |= set(a.choices.keys())
                break
        classified = L.CATCHUP_COMMANDS | L.MUTATING_COMMANDS
        self.assertEqual(
            names, classified,
            "unclassified: %s | stale: %s" % (
                sorted(names - classified), sorted(classified - names)))
        self.assertEqual(
            L.CATCHUP_COMMANDS & L.MUTATING_COMMANDS, frozenset(),
            "a command is in both buckets")

    def test_first_index_bounds(self):
        start = date(2020, 1, 1)
        self.assertEqual(L._first_index(start, "day", date(2019, 6, 1)), 0)
        self.assertEqual(L._first_index(start, "day", start), 0)
        self.assertEqual(L._first_index(start, "day", date(2020, 1, 11)), 10)
        self.assertEqual(L._first_index(start, "week", date(2020, 1, 15)), 2)
        self.assertEqual(L._first_index(start, "week", date(2020, 1, 14)), 2)
        self.assertEqual(L._first_index(start, "month", date(2020, 3, 1)), 2)

    def test_occurrences_since_matches_bruteforce(self):
        # The `since` fast-forward must return exactly the occurrences a full
        # scan would, filtered to >= since -- including month-end day clamping.
        through = date(2027, 6, 15)
        rules = [
            {"start": "2020-01-01", "every": "day"},
            {"start": "2020-01-01", "every": "week"},
            {"start": "2020-01-31", "every": "month"},   # clamps to 28/29/30
            {"start": "2026-09-30", "every": "month"},
        ]
        sinces = [date(2019, 1, 1), date(2020, 1, 1), date(2026, 1, 1),
                  date(2026, 9, 30), date(2027, 1, 1), date(2027, 6, 15)]
        for rule in rules:
            full = L._occurrences(rule, through)
            for since in sinces:
                got = L._occurrences(rule, through, since)
                expected = [d for d in full if d >= since]
                self.assertEqual(got, expected,
                                 "rule %s since %s" % (rule, since))

    def test_parse_tags(self):
        self.assertEqual(L.parse_tags("dinner #Work #work #reimbursable"),
                         ["reimbursable", "work"])  # deduped + lowercased
        self.assertEqual(L.parse_tags("no tags here"), [])
        self.assertEqual(L.parse_tags(None), [])

    def test_add_months_rollover_and_clamp(self):
        self.assertEqual(L.add_months(date(2026, 1, 15), 1), date(2026, 2, 15))
        self.assertEqual(L.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(L.add_months(date(2024, 1, 31), 1), date(2024, 2, 29))
        self.assertEqual(L.add_months(date(2026, 12, 10), 1), date(2027, 1, 10))
        self.assertEqual(L.add_months(date(2026, 3, 15), -3), date(2025, 12, 15))

    def test_occurrences_count_and_until_combined(self):
        # Whichever limit (count or until) is hit first should cap generation.
        rule = {"start": "2026-01-01", "every": "month", "count": 6,
                "until": "2026-03-15"}
        got = L._occurrences(rule, date(2026, 12, 31))
        self.assertEqual(got, [date(2026, 1, 1), date(2026, 2, 1),
                               date(2026, 3, 1)])          # until wins (3 < 6)
        rule2 = {"start": "2026-01-01", "every": "month", "count": 2,
                 "until": "2026-12-31"}
        got2 = L._occurrences(rule2, date(2026, 12, 31))
        self.assertEqual(got2, [date(2026, 1, 1), date(2026, 2, 1)])  # count wins

    def test_occurrences_count_with_since_is_absolute(self):
        rule = {"start": "2026-01-01", "every": "month", "count": 4}
        # since fast-forward must not change the absolute 4-occurrence cap
        got = L._occurrences(rule, date(2026, 12, 31), since=date(2026, 3, 1))
        self.assertEqual(got, [date(2026, 3, 1), date(2026, 4, 1)])  # #3 and #4

    def test_occurrences_weekly_first_index_boundaries(self):
        rule = {"start": "2026-01-01", "every": "week"}
        full = L._occurrences(rule, date(2026, 3, 1))
        for since in (date(2026, 1, 1), date(2026, 1, 8), date(2026, 1, 9),
                      date(2026, 2, 15)):
            got = L._occurrences(rule, date(2026, 3, 1), since=since)
            self.assertEqual(got, [d for d in full if d >= since],
                             "weekly since %s" % since)

    def test_money_large_and_negative(self):
        L._CONFIG.clear(); L._CONFIG.update(L.DEFAULT_CONFIG)
        self.assertEqual(L.money(1234567.5), "$1,234,567.50")
        self.assertEqual(L.money(-1234567.5), "-$1,234,567.50")

    def test_category_spent(self):
        data = {"expenses": [
            {"id": 1, "amount": 10.0, "category": "food", "kind": "expense",
             "date": "2026-01-05", "note": "", "tags": []},
            {"id": 2, "amount": 20.0, "category": "food", "kind": "expense",
             "date": "2026-01-20", "note": "", "tags": []},
            {"id": 3, "amount": 99.0, "category": "food", "kind": "expense",
             "date": "2026-02-01", "note": "", "tags": []},     # other month
            {"id": 4, "amount": 50.0, "category": "food", "kind": "income",
             "date": "2026-01-10", "note": "", "tags": []},     # income, ignored
        ]}
        self.assertEqual(L.category_spent(data, "food", "2026-01"), 30.0)
        self.assertEqual(L.category_spent(data, "rent", "2026-01"), 0)

    def test_date_bounds(self):
        self.assertEqual(L._date_bounds("2026-02-01", "2026-02-28"),
                         ("2026-02-01", "2026-02-28"))
        self.assertEqual(L._date_bounds("2026-02-28", "2026-02-01"),
                         ("2026-02-01", "2026-02-28"))      # reversed -> swapped
        lo, hi = L._date_bounds(None, "2026-02-28")
        self.assertEqual((lo, hi), ("0000-01-01", "2026-02-28"))  # open start
        lo, hi = L._date_bounds("2026-02-01", None)
        self.assertEqual((lo, hi), ("2026-02-01", "9999-12-31"))  # open end

    def test_all_time_net(self):
        data = {"expenses": [
            {"id": 1, "amount": 100.0, "category": "food", "kind": "expense",
             "date": "2026-01-01", "note": "", "tags": []},
            {"id": 2, "amount": 30.0, "category": "x", "kind": "expense",
             "date": "2026-01-02", "note": "", "tags": []},
            {"id": 3, "amount": 500.0, "category": "salary", "kind": "income",
             "date": "2026-01-03", "note": "", "tags": []},
        ]}
        self.assertEqual(L.all_time_net(data), 370.0)   # 500 - (100 + 30)
        self.assertEqual(L.all_time_net({"expenses": []}), 0.0)

    def test_median(self):
        self.assertEqual(L._median([]), 0.0)            # empty -> 0.0
        self.assertEqual(L._median([5]), 5)
        self.assertEqual(L._median([1, 3]), 2)          # even -> mean of middle two
        self.assertEqual(L._median([1, 2, 3, 4]), 2.5)  # even, four values
        self.assertEqual(L._median([3, 1, 2]), 2)       # odd, unsorted input

    def test_money_formatting(self):
        L._CONFIG.clear(); L._CONFIG.update(L.DEFAULT_CONFIG)
        self.assertEqual(L.money(5), "$5.00")
        self.assertEqual(L.money(-5), "-$5.00")      # sign before the symbol
        self.assertEqual(L.money(1234.5), "$1,234.50")
        self.assertEqual(L.money(-0.0), "$0.00")     # negative zero -> plain
        L._CONFIG["symbol_position"] = "after"
        L._CONFIG["currency"] = "kr"
        self.assertEqual(L.money(5), "5.00 kr")
        self.assertEqual(L.money(-5), "-5.00 kr")
        L._CONFIG.clear(); L._CONFIG.update(L.DEFAULT_CONFIG)

    def test_bar_clamps(self):
        self.assertEqual(L.bar(0, width=4), "----")
        self.assertEqual(L.bar(1, width=4), "####")
        self.assertEqual(L.bar(2.0, width=4), "####")   # >1 clamped
        self.assertEqual(L.bar(-1, width=4), "----")    # <0 clamped

    def test_clean_category(self):
        self.assertEqual(L.clean_category("  Food  "), "food")
        with self.assertRaises(SystemExit):
            L.clean_category("   ")

    def test_entry_line(self):
        L._CONFIG.clear(); L._CONFIG.update(L.DEFAULT_CONFIG)
        base = {"id": 7, "date": "2026-01-05", "amount": 12.5,
                "category": "food", "note": "lunch"}
        line = L._entry_line(base)
        self.assertIn("#7", line)
        self.assertIn("$12.50", line)
        self.assertIn("[food] - lunch", line)
        self.assertNotIn("✓", line)
        self.assertNotIn("+income", line)
        # markers appear when the flags are set
        marked = L._entry_line({**base, "recur_id": 3, "cleared": True,
                                "kind": "income", "note": ""})
        self.assertIn("*", marked)
        self.assertIn("✓", marked)
        self.assertIn("+income", marked)
        self.assertNotIn(" - ", marked)   # empty note -> no dash

    def test_filter_month(self):
        rows = [{"date": "2026-01-05"}, {"date": "2026-02-10"},
                {"date": "2026-01-31"}]
        got = L.filter_month(rows, "2026-01")
        self.assertEqual([r["date"] for r in got], ["2026-01-05", "2026-01-31"])
        # a falsy month returns the same list object unchanged (no filtering)
        self.assertIs(L.filter_month(rows, None), rows)
        self.assertIs(L.filter_month(rows, ""), rows)
        # no matches -> empty
        self.assertEqual(L.filter_month(rows, "2026-12"), [])

    def test_check_month(self):
        L.check_month(None)          # no filter -> ok
        L.check_month("2026-09")     # valid -> ok
        for bad in ("2026-9", "2026-13", "2026/09", "bad"):
            with self.assertRaises(SystemExit):
                L.check_month(bad)

    def test_parse_date(self):
        self.assertEqual(L.parse_date("2026-09-05"), "2026-09-05")
        self.assertEqual(L.parse_date("today"), date.today().isoformat())
        with self.assertRaises(SystemExit):
            L.parse_date("2026-99-99")

    def test_normalize_tag_pure(self):
        self.assertEqual(L.normalize_tag("work"), "work")
        self.assertEqual(L.normalize_tag("#Work"), "work")
        self.assertEqual(L.normalize_tag("  #Work  "), "work")   # space then '#'
        self.assertEqual(L.normalize_tag(None), "")
        self.assertEqual(L.normalize_tag(""), "")

    def test_merge_tag_flags_pure(self):
        self.assertEqual(L.merge_tag_flags("lunch", None), "lunch")
        self.assertEqual(L.merge_tag_flags("lunch", ["work"]), "lunch #work")
        # leading '#' tolerated; duplicates (already in note) skipped
        self.assertEqual(L.merge_tag_flags("lunch #work", ["#work", "food"]),
                         "lunch #work #food")
        # sanitized to word chars so it round-trips through parse_tags
        out = L.merge_tag_flags("", ["foo-bar"])
        self.assertEqual(L.parse_tags(out), ["foobar"])

    def test_this_month_pure(self):
        self.assertEqual(L.this_month(), date.today().isoformat()[:7])
        self.assertRegex(L.this_month(), r"^\d{4}-\d{2}$")

    def test_month_expenses_pure(self):
        data = {"expenses": [
            {"id": 1, "amount": 5.0, "category": "a", "date": "2026-01-10",
             "note": ""},
            {"id": 2, "amount": 9.0, "category": "b", "date": "2026-02-10",
             "note": ""},
            {"id": 3, "amount": 2.0, "category": "c", "date": "2026-01-20",
             "note": "", "kind": "income"},
        ]}
        # income excluded; month filter applied
        jan = L.month_expenses(data, "2026-01")
        self.assertEqual(sorted(e["id"] for e in jan), [1])
        # no month -> all expenses (still income-excluded)
        allx = L.month_expenses(data)
        self.assertEqual(sorted(e["id"] for e in allx), [1, 2])

    def test_period_totals(self):
        # shared by compare / today / statement: income, spending, net for a month
        rows = [
            {"amount": 100.0, "date": "2026-01-05", "kind": "income"},
            {"amount": 30.0, "date": "2026-01-10"},            # expense (default)
            {"amount": 12.5, "date": "2026-01-20", "kind": "expense"},
            {"amount": 999.0, "date": "2026-02-01"},           # other month
        ]
        self.assertEqual(L._period_totals(rows, "2026-01"),
                         {"income": 100.0, "spending": 42.5, "net": 57.5})
        self.assertEqual(L._period_totals(rows, "2099-01"),
                         {"income": 0.0, "spending": 0.0, "net": 0.0})

    def test_group_totals(self):
        rows = [
            {"amount": 10.0, "date": "2026-01-05"},                 # expense
            {"amount": 5.0, "date": "2026-01-20"},                  # expense
            {"amount": 100.0, "date": "2026-01-01", "kind": "income"},
            {"amount": 7.0, "date": "2026-02-03"},                  # expense
        ]
        agg = L.group_totals(rows, lambda e: L.month_of(e["date"]))
        self.assertEqual(agg["2026-01"], {"income": 100.0, "spending": 15.0})
        self.assertEqual(agg["2026-02"], {"income": 0.0, "spending": 7.0})
        # a None key drops the entry
        self.assertEqual(L.group_totals(rows, lambda e: None), {})

    def test_filter_entries_shared(self):
        import types
        rows = [
            {"id": 1, "amount": 10.0, "date": "2026-07-03", "category": "food",
             "tags": ["work"], "kind": "expense", "cleared": True},
            {"id": 2, "amount": 20.0, "date": "2026-07-10", "category": "travel",
             "tags": [], "kind": "expense"},
            {"id": 3, "amount": 99.0, "date": "2026-06-01", "category": "food",
             "tags": ["work"], "kind": "expense"},
            {"id": 4, "amount": 500.0, "date": "2026-07-05", "category": "salary",
             "tags": [], "kind": "income"},
        ]

        def ns(**kw):
            base = dict(all=False, income=False, category=None, tag=None,
                        month=None, cleared=False, pending=False)
            base.update(kw)
            return types.SimpleNamespace(**base)
        ids = lambda a: [e["id"] for e in L._filter_entries(rows, a)]
        self.assertEqual(ids(ns()), [1, 2, 3])                     # expenses only
        self.assertEqual(ids(ns(category="Food", month="2026-07")), [1])
        self.assertEqual(ids(ns(tag="#work")), [1, 3])             # '#' tolerated
        self.assertEqual(ids(ns(income=True)), [4])                # income scope
        self.assertEqual(ids(ns(all=True)), [1, 2, 3, 4])          # both kinds
        self.assertEqual(ids(ns(cleared=True)), [1])
        self.assertEqual(ids(ns(pending=True)), [2, 3])
        with self.assertRaises(SystemExit):                        # mutually exclusive
            with contextlib.redirect_stderr(io.StringIO()):
                L._filter_entries(rows, ns(cleared=True, pending=True))

    def test_profile_stats(self):
        rows = [
            {"amount": 10.0, "date": "2026-07-03", "category": "food", "note": "a"},
            {"amount": 30.0, "date": "2026-08-20", "category": "food", "note": "b"},
        ]
        s = L._profile_stats(rows, universe_total=100.0)
        self.assertEqual(s["total"], 40.0)
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["average"], 20.0)
        self.assertEqual(s["median"], 20.0)
        self.assertEqual(s["share_pct"], 40.0)       # 40 of a 100 universe
        self.assertEqual(s["min"]["amount"], 10.0)
        self.assertEqual(s["max"]["amount"], 30.0)
        self.assertEqual((s["first"], s["last"]), ("2026-07-03", "2026-08-20"))
        self.assertEqual(s["active_months"], 2)
        # empty input and a zero universe are safe (no division by zero)
        z = L._profile_stats([], universe_total=0.0)
        self.assertEqual((z["total"], z["count"], z["min"], z["share_pct"]),
                         (0.0, 0, None, 0.0))

    def test_category_totals(self):
        rows = [
            {"category": "food", "amount": 10.0},
            {"category": "food", "amount": 5.25},
            {"category": "transit", "amount": 2.50},
        ]
        self.assertEqual(L.category_totals(rows),
                         {"food": 15.25, "transit": 2.5})
        self.assertEqual(L.category_totals([]), {})
        # running 2dp rounding, matching the per-command loops it replaced
        pennies = [{"category": "x", "amount": 0.1} for _ in range(3)]
        self.assertEqual(L.category_totals(pennies), {"x": 0.3})

    def test_build_ics(self):
        items = [
            {"date": "2026-02-01", "amount": 1200.0, "category": "rent",
             "kind": "expense", "note": "flat; cozy", "recur_id": 3},
            {"date": "2026-02-05", "amount": 3000.0, "category": "salary",
             "kind": "income", "note": "", "recur_id": 7},
        ]
        ics = L.build_ics(items, now=datetime(2026, 1, 1, 12, 0, 0))
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(ics.count("BEGIN:VEVENT"), 2)
        self.assertIn("DTSTART;VALUE=DATE:20260201", ics)
        self.assertIn("UID:3-2026-02-01@ledgerling", ics)
        self.assertIn("DTSTAMP:20260101T120000Z", ics)
        # expense is negative and ';' is iCalendar-escaped
        self.assertIn("SUMMARY:flat\\; cozy (-1200.00)", ics)
        # income is positive and falls back to the category when the note is empty
        self.assertIn("SUMMARY:salary (+3000.00)", ics)
        # every line is CRLF-terminated (no stray bare newline)
        self.assertNotIn("\n", ics.replace("\r\n", ""))

    def test_build_ics_empty(self):
        ics = L.build_ics([], now=datetime(2026, 1, 1))
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 0)

    def test_normalize_payee(self):
        # note wins, #tags stripped, whitespace collapsed and lowercased
        self.assertEqual(
            L._normalize_payee({"note": "  Netflix  #fun ", "category": "ent"}),
            "netflix")
        # empty note falls back to the (category)
        self.assertEqual(
            L._normalize_payee({"note": "", "category": "rent"}), "(rent)")
        self.assertEqual(
            L._normalize_payee({"note": "#only #tags", "category": "misc"}),
            "(misc)")

    def test_detect_subscriptions(self):
        # a clean monthly Netflix charge (stable amount) + irregular groceries
        exp = []
        for i, d in enumerate(("2026-01-05", "2026-02-05", "2026-03-05",
                               "2026-04-05")):
            exp.append({"id": i + 1, "amount": 15.99, "category": "ent",
                        "note": "netflix", "date": d, "tags": []})
        # groceries: irregular dates + variable amounts -> not a subscription
        exp += [
            {"id": 10, "amount": 54.0, "category": "food", "note": "groceries",
             "date": "2026-01-03", "tags": []},
            {"id": 11, "amount": 12.0, "category": "food", "note": "groceries",
             "date": "2026-01-19", "tags": []},
            {"id": 12, "amount": 88.0, "category": "food", "note": "groceries",
             "date": "2026-03-02", "tags": []},
        ]
        subs = L.detect_subscriptions({"expenses": exp, "budgets": {},
                                       "recurring": []})
        self.assertEqual(len(subs), 1)
        s = subs[0]
        self.assertEqual(s["payee"], "netflix")
        self.assertEqual(s["cadence"], "monthly")
        self.assertEqual(s["amount"], 15.99)
        self.assertEqual(s["count"], 4)
        self.assertEqual(s["monthly"], 15.99)
        self.assertEqual(s["annual"], round(15.99 * 12, 2))

    def test_detect_subscriptions_weekly_and_min_count(self):
        exp = [{"id": i + 1, "amount": 4.5, "category": "coffee",
                "note": "latte", "date": d, "tags": []}
               for i, d in enumerate(("2026-01-01", "2026-01-08",
                                      "2026-01-15", "2026-01-22"))]
        subs = L.detect_subscriptions({"expenses": exp, "budgets": {},
                                       "recurring": []})
        self.assertEqual(subs[0]["cadence"], "weekly")
        self.assertEqual(subs[0]["monthly"], round(4.5 * 52 / 12, 2))
        # raising min_count above the sample size suppresses it
        self.assertEqual(
            L.detect_subscriptions({"expenses": exp, "budgets": {},
                                    "recurring": []}, min_count=5), [])

    def test_detect_subscriptions_ignores_income(self):
        # a regular monthly income shouldn't be flagged as a subscription
        exp = [{"id": i + 1, "amount": 3000.0, "category": "salary",
                "note": "pay", "date": d, "kind": "income", "tags": []}
               for i, d in enumerate(("2026-01-01", "2026-02-01",
                                      "2026-03-01"))]
        self.assertEqual(
            L.detect_subscriptions({"expenses": exp, "budgets": {},
                                    "recurring": []}), [])


class RecurringEngine(unittest.TestCase):
    def test_occurrences_monthly_clamp(self):
        rule = {"start": "2026-01-31", "every": "month"}
        # today = Apr 30 so the clamped Apr-30 occurrence is included; each
        # month anchors on the start day (31), clamped to the month's length.
        got = L._occurrences(rule, date(2026, 4, 30))
        self.assertEqual(got, [date(2026, 1, 31), date(2026, 2, 28),
                               date(2026, 3, 31), date(2026, 4, 30)])

    def test_occurrences_excludes_future(self):
        rule = {"start": "2026-01-31", "every": "month"}
        # Apr 30 is after the 15th, so it must not be generated yet.
        got = L._occurrences(rule, date(2026, 4, 15))
        self.assertEqual(got, [date(2026, 1, 31), date(2026, 2, 28),
                               date(2026, 3, 31)])

    def test_occurrences_respects_count(self):
        rule = {"start": "2026-01-01", "every": "month", "count": 3}
        got = L._occurrences(rule, date(2026, 12, 31))
        self.assertEqual(got, [date(2026, 1, 1), date(2026, 2, 1),
                               date(2026, 3, 1)])  # exactly 3, then stops

    def test_occurrences_count_holds_with_since(self):
        # The since fast-forward must not change the absolute count cap.
        rule = {"start": "2026-01-01", "every": "month", "count": 3}
        got = L._occurrences(rule, date(2026, 12, 31), since=date(2026, 2, 1))
        self.assertEqual(got, [date(2026, 2, 1), date(2026, 3, 1)])

    def test_occurrences_respects_until(self):
        rule = {"start": "2026-01-01", "every": "month", "until": "2026-03-15"}
        got = L._occurrences(rule, date(2026, 12, 31))
        self.assertEqual(got, [date(2026, 1, 1), date(2026, 2, 1),
                               date(2026, 3, 1)])  # Apr 1 is after until

    def test_apply_recurring_is_idempotent(self):
        data = {"expenses": [], "budgets": {}, "recurring": [{
            "id": 1, "amount": 1200.0, "category": "rent", "note": "flat #home",
            "every": "month", "start": "2026-01-01", "last": None,
        }]}
        first = L.apply_recurring(data)
        self.assertGreater(first, 0)                     # caught up from Jan
        self.assertEqual(len(data["expenses"]), first)
        second = L.apply_recurring(data)                 # nothing new is due
        self.assertEqual(second, 0)
        self.assertEqual(len(data["expenses"]), first)   # no duplication
        # tags flow through from the rule note
        self.assertIn("home", data["expenses"][0]["tags"])


    def test_apply_recurring_ids_unique_and_sequential(self):
        # Pre-existing entry at id 5; two rules generate several occurrences.
        # The running id counter must keep every id unique and continue past 5.
        data = {"expenses": [{"id": 5, "amount": 9.0, "category": "x",
                              "note": "", "date": "2026-01-01", "tags": [],
                              "kind": "expense"}],
                "budgets": {}, "recurring": [
                    {"id": 1, "amount": 3.0, "category": "coffee", "note": "",
                     "every": "week", "start": "2026-01-01", "last": None},
                    {"id": 2, "amount": 50.0, "category": "gym", "note": "",
                     "every": "month", "start": "2026-01-01", "last": None},
                ]}
        created = L.apply_recurring(data)
        self.assertGreater(created, 0)
        ids = [e["id"] for e in data["expenses"]]
        self.assertEqual(len(ids), len(set(ids)))        # all unique
        self.assertEqual(max(ids), 5 + created)          # continued past id 5

    def test_apply_recurring_skips_paused_rule(self):
        data = {"expenses": [], "budgets": {}, "recurring": [{
            "id": 1, "amount": 15.0, "category": "subscriptions", "note": "music",
            "every": "month", "start": "2026-01-01", "last": None, "paused": True,
        }]}
        created = L.apply_recurring(data)
        self.assertEqual(created, 0)                 # paused -> nothing generated
        self.assertEqual(data["expenses"], [])

    def test_apply_recurring_honours_skips(self):
        data = {"expenses": [], "budgets": {}, "recurring": [{
            "id": 1, "amount": 15.0, "category": "subscriptions", "note": "music",
            "every": "month", "start": "2026-01-01", "last": None,
            "skips": ["2026-03-01"],
        }]}
        L.apply_recurring(data)
        dates = [e["date"] for e in data["expenses"]]
        self.assertNotIn("2026-03-01", dates)   # skipped occurrence not generated
        self.assertIn("2026-02-01", dates)      # neighbours still generated
        self.assertIn("2026-04-01", dates)
        before = len(data["expenses"])
        L.apply_recurring(data)                 # idempotent, skip stays skipped
        self.assertEqual(len(data["expenses"]), before)


class TempAppCase(unittest.TestCase):
    """Base class: redirect the app's paths to a temp dir so all writes stay
    sandboxed there, and reset live settings between tests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = {k: getattr(L, k) for k in
                      ("HOME_DIR", "DATA_FILE", "EXPORT_DIR", "BACKUP_DIR",
                       "CONFIG_FILE", "UNDO_FILE")}
        L.HOME_DIR = self.tmp
        L.DATA_FILE = os.path.join(self.tmp, "ledgerling_data.json")
        L.EXPORT_DIR = os.path.join(self.tmp, "exports")
        L.BACKUP_DIR = os.path.join(self.tmp, "backups")
        L.CONFIG_FILE = os.path.join(self.tmp, "ledgerling_config.json")
        L.UNDO_FILE = os.path.join(self.tmp, ".undo.json")
        L._CONFIG.clear()
        L._CONFIG.update(L.DEFAULT_CONFIG)
        L._SUPPRESS_UNDO = False

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(L, k, v)
        L._CONFIG.clear()
        L._CONFIG.update(L.DEFAULT_CONFIG)

    def _main(self, argv):
        """Run the real CLI entry point and return whatever it printed."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            L.main(argv)
        return buf.getvalue()


class FileRoundTrip(TempAppCase):
    def _run(self, fn, args):
        with contextlib.redirect_stdout(io.StringIO()):
            fn(args)

    def test_export_then_import_dedupes(self):
        L.save({"expenses": [
            {"id": 1, "amount": 10.0, "category": "food", "note": "a #x",
             "date": "2026-09-01", "tags": ["x"]},
            {"id": 2, "amount": 20.0, "category": "rent", "note": "",
             "date": "2026-09-02", "tags": []},
        ], "budgets": {}, "recurring": []})

        self._run(L.cmd_export, Namespace(file="out.csv", month=None, format="csv"))
        # re-importing the export should add nothing (all duplicates)
        self._run(L.cmd_import, Namespace(file="out.csv"))
        self.assertEqual(len(L.load()["expenses"]), 2)

    def test_import_new_and_malformed(self):
        L.save({"expenses": [
            {"id": 1, "amount": 10.0, "category": "food", "note": "dup",
             "date": "2026-09-01", "tags": []},
        ], "budgets": {}, "recurring": []})
        os.makedirs(L.EXPORT_DIR, exist_ok=True)
        with open(os.path.join(L.EXPORT_DIR, "in.csv"), "w",
                  encoding="utf-8", newline="") as fh:
            fh.write("date,amount,category,note\n")
            fh.write("2026-09-05,25.00,coffee,new #treat\n")   # new
            fh.write("2026-09-01,10.00,food,dup\n")            # duplicate
            fh.write("2026-13-99,5,food,bad date\n")           # malformed

        # a dry run reports the same counts but changes nothing
        self._run(L.cmd_import, Namespace(file="in.csv", dry_run=True))
        self.assertEqual(len(L.load()["expenses"]), 1)

        self._run(L.cmd_import, Namespace(file="in.csv", dry_run=False))
        exp = L.load()["expenses"]
        self.assertEqual(len(exp), 2)                          # only the new one
        new = [e for e in exp if e["category"] == "coffee"][0]
        self.assertEqual(new["tags"], ["treat"])               # tags parsed on import


class WebUI(TempAppCase):
    """The local web UI is a thin bridge over the CLI; test schema + server."""

    def test_describe_covers_all_commands(self):
        from ledgerling import web
        d = web.describe()
        self.assertEqual(d["version"], L.__version__)
        names = {c["name"] for c in d["commands"]}
        # a representative spread, including nested recur subcommands
        for expected in ("add", "income", "month", "web", "recur add",
                         "recur edit", "distribution"):
            self.assertIn(expected, names)
        # the `add` command exposes its positional/option args
        add = next(c for c in d["commands"] if c["name"] == "add")
        dests = {a["dest"] for a in add["args"]}
        self.assertTrue({"amount", "category", "note", "date"} <= dests)

    def test_describe_marks_variadic_positional(self):
        # split's `parts` is nargs="+", which the UI must know to tokenize.
        from ledgerling import web
        d = web.describe()
        split = next(c for c in d["commands"] if c["name"] == "split")
        parts = next(a for a in split["args"] if a["dest"] == "parts")
        self.assertTrue(parts.get("variadic"))
        self.assertEqual(parts["kind"], "positional")
        # a plain positional is not variadic
        add = next(c for c in d["commands"] if c["name"] == "add")
        amount = next(a for a in add["args"] if a["dest"] == "amount")
        self.assertFalse(amount.get("variadic"))

    def test_every_command_is_grouped_in_sidebar(self):
        # Every command must be listed in the web sidebar's GROUP_DEFS, so none
        # silently falls into the catch-all "More" group.
        import re
        from ledgerling import web
        m = re.search(r"const GROUP_DEFS = \[(.*?)\];", web.INDEX_HTML, re.S)
        self.assertIsNotNone(m, "could not locate GROUP_DEFS in the web UI")
        grouped = set(re.findall(r"'([^']+)'", m.group(1)))
        names = {c["name"] for c in web.describe()["commands"]}
        missing = names - grouped
        self.assertEqual(missing, set(),
                         "commands not in any sidebar group: %s" % sorted(missing))

    def test_accessibility_markup_present(self):
        # Core a11y affordances must stay in the shipped page: skip link,
        # screen-reader-only helper class, a reduced-motion stylesheet, the
        # assertive error-status region, and the aria-busy wiring around runs.
        from ledgerling import web
        html = web.INDEX_HTML
        self.assertIn('class="skiplink"', html)
        self.assertIn('.sr-only', html)
        self.assertIn('prefers-reduced-motion', html)
        self.assertIn("id = 'runstatus'", html)
        self.assertIn("'aria-live', 'assertive'", html)
        self.assertIn("'aria-busy'", html)
        self.assertIn('Command failed: ', html)
        # success is announced too, via a separate polite status region
        self.assertIn("id = 'runok'", html)
        self.assertIn("' completed'", html)
        # the calendar heatmap exposes its data to assistive tech: a labeled
        # grid and per-day cells with their own role + aria-label.
        self.assertIn("'Daily spending for '", html)
        self.assertIn("c.setAttribute('role', 'img')", html)
        self.assertIn("c.setAttribute('aria-label', label)", html)
        # data tables are sortable: focusable headers with an aria-sort state
        self.assertIn("th.setAttribute('aria-sort', 'none')", html)
        self.assertIn("'ascending'", html)
        self.assertIn("function sortValue", html)

    def test_gui_groups_every_command(self):
        # The desktop app's sidebar tree must list every command (anything
        # ungrouped would only reach the catch-all "More" node), and importing
        # the GUI module must not require tkinter (it's imported lazily inside).
        from ledgerling import gui, web
        names = {c["name"] for c in web.describe()["commands"]}
        grouped = {n for _g, ns in gui.GROUP_DEFS for n in ns}
        # every grouped name is real, and every real name is grouped or "More"
        self.assertEqual(grouped - names, set(),
                         "GUI groups list unknown commands")
        missing = {n for n in names if gui._group_of(n) == "More"}
        self.assertEqual(missing, set(),
                         "commands not in any GUI group: %s" % sorted(missing))

    def test_gui_table_helpers(self):
        from ledgerling import gui
        # money vs percent vs plain-count column classification
        self.assertTrue(gui._is_money_key("total"))
        self.assertTrue(gui._is_money_key("over"))
        self.assertTrue(gui._is_money_key("2026-01"))   # month columns
        self.assertFalse(gui._is_money_key("count"))
        self.assertFalse(gui._is_money_key("rate"))
        self.assertEqual(gui._fmt_cell("total", 31.98), "$31.98")
        self.assertEqual(gui._fmt_cell("pct", 260.0), "260.0%")
        self.assertEqual(gui._fmt_cell("count", 2), "2")
        self.assertEqual(gui._fmt_cell("note", None), "")
        # JSON shaping: nested array-of-objects, flat dict, scalar list
        mode, cols, rows = gui._tabular(
            {"payees": [{"payee": "a", "total": 5.0}], "count": 1})
        self.assertEqual((mode, cols), ("rows", ["payee", "total"]))
        self.assertEqual(len(rows), 1)
        mode, cols, rows = gui._tabular({"income": 10.0, "spending": 4.0})
        self.assertEqual((mode, cols), ("fields", ["field", "value"]))
        self.assertEqual(len(rows), 2)
        mode, cols, _ = gui._tabular(["a", "b"])
        self.assertEqual((mode, cols), ("scalars", ["value"]))
        self.assertIsNone(gui._tabular(None))

    def test_gui_toolbar_commands_exist(self):
        from ledgerling import gui, web
        names = {c["name"] for c in web.describe()["commands"]}
        bad = [cmd for cmd, _tip in gui.TOOLBAR if cmd not in names]
        self.assertEqual(bad, [], "toolbar lists unknown command(s): %s" % bad)
        self.assertIn("month", [cmd for cmd, _ in gui.TOOLBAR])

    def test_gui_shortcuts_defined(self):
        from ledgerling import gui
        accels = [s[0] for s in gui.SHORTCUTS]
        for a in ("Ctrl+Enter", "Ctrl+K", "Ctrl+T", "Ctrl+N", "Ctrl+Q"):
            self.assertIn(a, accels)
        # each row is (accelerator, event, description, action), all non-empty
        self.assertTrue(all(len(s) == 4 and all(s) for s in gui.SHORTCUTS))
        # action names are unique (the binding maps them 1:1 to methods)
        actions = [s[3] for s in gui.SHORTCUTS]
        self.assertEqual(len(actions), len(set(actions)))
        # events look like Tk bindings
        self.assertTrue(all(s[1].startswith("<") and s[1].endswith(">")
                            for s in gui.SHORTCUTS))

    def test_gui_themes_consistent_and_cycle(self):
        from ledgerling import gui
        # every theme must define exactly the same colour keys, so the
        # crossfade (which lerps key-by-key) can never hit a missing key
        keysets = [frozenset(v) for v in gui.THEMES.values()]
        self.assertTrue(all(ks == keysets[0] for ks in keysets))
        # the high-contrast accessibility theme exists and is in the cycle
        self.assertIn("high-contrast", gui.THEMES)
        self.assertEqual(set(gui.THEME_ORDER), set(gui.THEMES))
        # Ctrl+T cycles through all themes and wraps around
        seen = [gui.THEME_ORDER[0]]
        for _ in range(len(gui.THEME_ORDER)):
            seen.append(gui._next_theme(seen[-1]))
        self.assertEqual(seen[-1], gui.THEME_ORDER[0])            # wrapped
        self.assertEqual(set(seen), set(gui.THEMES))              # visited all
        self.assertEqual(gui._next_theme("nonsense"), gui.THEME_ORDER[0])

    def test_gui_has_required_args(self):
        from ledgerling import gui, web
        cmds = {c["name"]: c for c in web.describe()["commands"]}
        # today/summary have no required positional -> safe to auto-run on open
        self.assertFalse(gui._has_required_args(cmds["today"]))
        self.assertFalse(gui._has_required_args(cmds["summary"]))
        # add needs amount/category; trend needs a category -> not auto-runnable
        self.assertTrue(gui._has_required_args(cmds["add"]))
        self.assertTrue(gui._has_required_args(cmds["trend"]))

    def test_gui_ordered_commands(self):
        from ledgerling import gui, web
        commands = {c["name"]: c for c in web.describe()["commands"]}
        full = gui.ordered_commands(commands)
        self.assertEqual(set(full), set(commands))        # every command listed
        # ordering follows the sidebar groups: a Record command precedes an
        # Analyze one precedes a Settings one
        self.assertLess(full.index("add"), full.index("summary"))
        self.assertLess(full.index("summary"), full.index("config"))
        # filtering matches name or help text, preserving order
        pay = gui.ordered_commands(commands, "pay")
        self.assertIn("payees", pay)                      # name match
        self.assertTrue(all("pay" in n or "pay" in commands[n]["help"].lower()
                            for n in pay))
        self.assertEqual(gui.ordered_commands(commands, "zzzznope"), [])

    def test_gui_build_argv(self):
        from ledgerling import gui, web
        cmds = {c["name"]: c for c in web.describe()["commands"]}
        # add: amount/category positional (required), note positional (optional),
        # --date option, --in option; bool-less
        add = cmds["add"]
        argv = gui.build_argv(add, {"amount": "12.5", "category": "food",
                                    "note": "", "date": "2026-01-02", "in_": ""})
        self.assertEqual(argv[:3], ["add", "12.5", "food"])   # empty note skipped
        self.assertIn("--date", argv)
        self.assertEqual(argv[argv.index("--date") + 1], "2026-01-02")
        self.assertNotIn("--in", argv)                        # empty option omitted
        # a store-true flag only appears when truthy
        top = cmds["top"]
        self.assertIn("--income", gui.build_argv(top, {"income": True}))
        self.assertNotIn("--income", gui.build_argv(top, {"income": False}))
        # a variadic positional is split on whitespace (synthetic spec)
        fake = {"argv": ["x"], "args": [
            {"dest": "a", "kind": "positional", "type": "str"},
            {"dest": "parts", "kind": "positional", "type": "str",
             "variadic": True}]}
        self.assertEqual(gui.build_argv(fake, {"a": "30", "parts": "a 10 b 20"}),
                         ["x", "30", "a", "10", "b", "20"])
        # nested command keeps its full argv prefix
        self.assertEqual(gui.build_argv(cmds["recur list"], {})[:2],
                         ["recur", "list"])

    def test_gui_cmd_has_json(self):
        from ledgerling import gui, web
        cmds = {c["name"]: c for c in web.describe()["commands"]}
        self.assertTrue(gui._cmd_has_json(cmds["summary"]))   # read command
        self.assertFalse(gui._cmd_has_json(cmds["backup"]))   # no --json

    def test_gui_month_summary_text(self):
        from ledgerling import gui
        data = {"expenses": [
            {"amount": 10.0, "date": "2026-07-03", "category": "food",
             "kind": "expense"},
            {"amount": 5.5, "date": "2026-07-20", "category": "transit",
             "kind": "expense"},
            {"amount": 2000.0, "date": "2026-07-01", "category": "salary",
             "kind": "income"},
            {"amount": 99.0, "date": "2026-06-15", "category": "food",
             "kind": "expense"},            # other month, excluded
        ]}
        txt = gui.month_summary_text(data, "2026-07")
        self.assertIn("2026-07", txt)
        self.assertIn("spent " + L.money(15.5), txt)
        self.assertIn("net " + L.money(1984.5), txt)     # 2000 - 15.5
        self.assertIn("3 entries", txt)
        # singular entry, and empty months produce no summary
        one = gui.month_summary_text(data, "2026-06")
        self.assertIn("1 entry", one)
        self.assertEqual(gui.month_summary_text(data, "2099-01"), "")

    def test_gui_exception_logging_is_nonfatal(self):
        # Regression for the desktop crash (#11): a callback error must not be
        # fatal, and the handler must not touch sys.stderr (None in a windowed
        # build, where Tk's default handler would crash the app).
        from ledgerling import gui
        saved = sys.stderr
        sys.stderr = None
        try:
            try:
                raise ValueError("boom-xyz")
            except ValueError:
                text = gui.log_gui_exception(*sys.exc_info())
        finally:
            sys.stderr = saved
        self.assertIn("boom-xyz", text)
        log = os.path.join(L.HOME_DIR, "gui-errors.log")
        self.assertTrue(os.path.exists(log))
        with open(log, encoding="utf-8") as fh:
            self.assertIn("ValueError: boom-xyz", fh.read())

    def test_gui_state_roundtrip_and_geometry(self):
        from ledgerling import gui
        self.assertTrue(gui._valid_geometry("1040x660+12+34"))
        self.assertTrue(gui._valid_geometry("800x600"))
        self.assertFalse(gui._valid_geometry("nonsense"))
        self.assertFalse(gui._valid_geometry(None))
        # state saves into the (temp) data folder and reads back
        self.assertEqual(gui._load_state(), {})               # none yet
        gui._save_state({"theme": "light", "geometry": "900x700+0+0"})
        self.assertEqual(gui._load_state(),
                         {"theme": "light", "geometry": "900x700+0+0"})
        self.assertTrue(os.path.exists(gui._state_path()))

    def test_run_cli_bridge(self):
        from ledgerling import web
        web.run_cli(["add", "12.50", "food", "lunch #x"])
        res = web.run_cli(["list", "--json"])
        self.assertEqual(res["code"], 0)
        rows = json.loads(res["stdout"])
        self.assertEqual(len(rows), 1)
        # errors are captured, not raised
        bad = web.run_cli(["list", "--month", "2026-13"])
        self.assertNotEqual(bad["code"], 0)

    def test_run_cli_surfaces_validation_message(self):
        # A sys.exit("error: ...") validation message must reach the UI via
        # stderr, not be swallowed into a blank generic error.
        from ledgerling import web
        res = web.run_cli(["budget", "--category", "food"])  # missing --amount
        self.assertEqual(res["code"], 1)
        self.assertIn("provide both", res["stderr"])
        # a successful command still reports code 0 with no error text
        ok = web.run_cli(["version"])
        self.assertEqual(ok["code"], 0)
        self.assertEqual(ok["stderr"], "")

    def test_http_endpoints(self):
        import threading
        import urllib.request
        from ledgerling import web
        httpd = web.make_server(0)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{port}"
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("Ledgerling", page)
            desc = json.loads(urllib.request.urlopen(base + "/api/describe").read())
            self.assertIn("commands", desc)
            req = urllib.request.Request(
                base + "/api/run",
                data=json.dumps({"argv": ["add", "5", "food", "a"]}).encode(),
                headers={"Content-Type": "application/json"})
            res = json.loads(urllib.request.urlopen(req).read())
            self.assertEqual(res["code"], 0)
            self.assertEqual(len(L.load()["expenses"]), 1)
        finally:
            httpd.shutdown()
            httpd.server_close()


class CLI(TempAppCase):
    """End-to-end tests that drive main() with argv arrays."""

    def test_export_json_format(self):
        self._main(["add", "10", "food", "lunch #t"])
        self._main(["export", "--format", "json", "--file", "out.json"])
        with open(os.path.join(L.EXPORT_DIR, "out.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "food")
        self.assertEqual(rows[0]["tags"], ["t"])
        self.assertEqual(rows[0]["kind"], "expense")

    def test_export_date_range(self):
        self._main(["add", "10", "food", "a", "--date", "2026-05-01"])
        self._main(["add", "20", "food", "b", "--date", "2026-05-15"])
        self._main(["add", "30", "food", "c", "--date", "2026-06-01"])
        self._main(["export", "--format", "json", "--file", "r.json",
                    "--start", "2026-05-10", "--end", "2026-05-31"])
        with open(os.path.join(L.EXPORT_DIR, "r.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
        self.assertEqual([r["date"] for r in rows], ["2026-05-15"])

    def test_export_month_and_range_conflict(self):
        self._main(["add", "10", "food", "a", "--date", "2026-05-01"])
        with self.assertRaises(SystemExit):
            self._main(["export", "--month", "2026-05", "--start", "2026-05-01"])

    def test_export_stdout(self):
        self._main(["add", "12.5", "food", "lunch", "--date", "2026-01-01"])
        # JSON to stdout, no file written
        before = set(os.listdir(L.EXPORT_DIR)) if os.path.isdir(L.EXPORT_DIR) else set()
        out = self._main(["export", "--format", "json", "--stdout"])
        rows = json.loads(out)
        self.assertEqual(rows[0]["category"], "food")
        after = set(os.listdir(L.EXPORT_DIR)) if os.path.isdir(L.EXPORT_DIR) else set()
        self.assertEqual(before, after)   # nothing written
        # CSV to stdout parses with a header row
        csv_out = self._main(["export", "--stdout"])   # csv is default
        import csv as _csv
        parsed = list(_csv.reader(io.StringIO(csv_out)))
        self.assertEqual(parsed[0][:3], ["id", "date", "amount"])
        self.assertEqual(parsed[1][2], "12.50")

    def _run_with_stdin(self, argv, text):
        """Run the CLI with sys.stdin fed from `text`; return captured stdout."""
        old = sys.stdin
        sys.stdin = io.StringIO(text)
        try:
            return self._main(argv)
        finally:
            sys.stdin = old

    def test_import_stdin_json_and_csv(self):
        payload = json.dumps([{"date": "2026-01-01", "amount": 9.0,
                               "category": "food", "note": "x",
                               "kind": "expense"}])
        out = self._run_with_stdin(["import", "--stdin", "--format", "json"],
                                   payload)
        self.assertIn("imported from stdin", out)
        self.assertEqual(len(L.load()["expenses"]), 1)
        # CSV via stdin (default format); duplicate of the above is skipped
        csv_text = ("date,amount,category,note,kind\n"
                    "2026-01-01,9.00,food,x,expense\n"
                    "2026-01-02,4.00,coffee,,expense\n")
        out2 = self._run_with_stdin(["import", "--stdin"], csv_text)
        self.assertIn("imported from stdin", out2)
        self.assertEqual(len(L.load()["expenses"]), 2)   # one new, one dup

    def test_import_stdin_and_file_conflict(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["import", "--stdin", "--file", "x.csv"])

    def test_import_requires_source(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["import"])   # neither --file nor --stdin

    def test_export_stdout_import_stdin_roundtrip(self):
        self._main(["add", "7.5", "food", "lunch", "--date", "2026-01-01"])
        dump = self._main(["export", "--format", "json", "--stdout"])
        # fresh store, then re-import the piped dump
        L.save({"expenses": [], "budgets": {}, "recurring": [], "goal": None})
        self._run_with_stdin(["import", "--stdin", "--format", "json"], dump)
        rows = L.load()["expenses"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "food")

    def test_parse_import_text_pure(self):
        self.assertEqual(L._parse_import_text("[]", True, "x"), [])
        rows = L._parse_import_text("a,b\n1,2\n", False, "x")
        self.assertEqual(rows, [{"a": "1", "b": "2"}])
        with self.assertRaises(SystemExit):
            L._parse_import_text("{not json", True, "x")

    def test_serialize_export_pure(self):
        rows = [{"id": 1, "date": "2026-01-01", "amount": 5.0, "category": "x",
                 "note": "", "recur_id": None}]
        self.assertEqual(json.loads(L._serialize_export(rows, "json")), rows)
        csv_text = L._serialize_export(rows, "csv")
        self.assertTrue(csv_text.startswith("id,date,amount"))
        self.assertNotIn("\r\n", csv_text)   # LF line endings, not CRLF

    def test_export_tag_filter(self):
        self._main(["add", "40", "travel", "flight #work"])
        self._main(["add", "10", "food", "lunch #work"])
        self._main(["add", "25", "food", "dinner"])          # no tag
        self._main(["export", "--format", "json", "--file", "work.json",
                    "--tag", "work"])
        with open(os.path.join(L.EXPORT_DIR, "work.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
        self.assertEqual(sorted(r["amount"] for r in rows), [10.0, 40.0])

    def test_export_amount_filter(self):
        self._main(["add", "5", "food", "a"])
        self._main(["add", "80", "shoes", "b"])
        self._main(["add", "900", "rent", "c"])
        self._main(["export", "--format", "json", "--file", "big.json",
                    "--min", "50", "--max", "500"])
        with open(os.path.join(L.EXPORT_DIR, "big.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
        self.assertEqual([r["amount"] for r in rows], [80.0])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["export", "--min", "100", "--max", "1"])

    def test_add_then_list_json(self):
        self._main(["add", "10", "food", "lunch #x"])
        rows = json.loads(self._main(["list", "--json"]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "food")
        self.assertEqual(rows[0]["tags"], ["x"])

    def test_list_auto_catches_up_recurring(self):
        # A rule with past occurrences and no expenses yet; `list` must trigger
        # the auto-catch-up in main() and materialize them.
        start = L.add_months(date.today().replace(day=1), -2).isoformat()
        L.save({"expenses": [], "budgets": {}, "recurring": [{
            "id": 1, "amount": 100.0, "category": "rent", "note": "",
            "every": "month", "start": start, "last": None,
        }]})
        rows = json.loads(self._main(["list", "--json"]))
        self.assertGreaterEqual(len(rows), 3)  # 2 months ago, last month, this
        self.assertTrue(all(r.get("recur_id") == 1 for r in rows))

    def test_config_currency_applies_to_output(self):
        self._main(["config", "--currency", "EUR"])
        self._main(["add", "10", "food", "x"])
        out = self._main(["list"])
        self.assertIn("EUR10.00", out)

    def test_stats_json_shape(self):
        self._main(["add", "10", "food", "a #t"])
        self._main(["add", "20", "food", "b"])
        d = json.loads(self._main(["stats", "--json"]))
        for key in ("expenses", "total", "average", "median", "biggest",
                    "by_category", "by_tag", "projection"):
            self.assertIn(key, d)
        self.assertEqual(d["expenses"], 2)
        self.assertEqual(d["by_tag"], {"t": 10.0})

    def test_search_tag_json(self):
        self._main(["add", "10", "food", "a #work"])
        self._main(["add", "20", "rent", "b"])
        rows = json.loads(self._main(["search", "--tag", "work", "--json"]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "food")

    def test_search_date_range(self):
        for d in ("2026-01-05", "2026-02-10", "2026-03-20"):
            self._main(["add", "10", "food", "x", "--date", d])
        mid = json.loads(self._main(["search", "--since", "2026-02-01",
                                     "--until", "2026-02-28", "--json"]))
        self.assertEqual([e["date"] for e in mid], ["2026-02-10"])
        # open-ended --since
        after = json.loads(self._main(["search", "--since", "2026-02-01",
                                       "--json"]))
        self.assertEqual(sorted(e["date"] for e in after),
                         ["2026-02-10", "2026-03-20"])

    def test_bad_month_exits(self):
        with self.assertRaises(SystemExit):
            self._main(["list", "--month", "2026-13"])

    def test_version_subcommand(self):
        out = self._main(["version"])
        self.assertIn(L.__version__, out)

    def test_version_flag(self):
        # argparse's version action prints and exits 0
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(io.StringIO()):
                L.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_fun_modes_json_and_determinism(self):
        # fortune: same seed -> same result; valid lucky numbers
        a = json.loads(self._main(["fortune", "--seed", "42", "--json"]))
        b = json.loads(self._main(["fortune", "--seed", "42", "--json"]))
        self.assertEqual(a, b)
        self.assertIn(a["fortune"], L._FORTUNES)
        self.assertEqual(len(a["lucky_numbers"]), 6)
        self.assertTrue(all(1 <= n < 50 for n in a["lucky_numbers"]))
        # horoscope: seeded is reproducible; fields present
        h = json.loads(self._main(["horoscope", "leo", "--seed", "1", "--json"]))
        self.assertEqual(h["sign"], "leo")
        self.assertIn(h["money_mood"], L._HORO_MOOD)
        self.assertNotEqual(h["favoured"], h["avoid"])
        self.assertEqual(
            h, json.loads(self._main(["horoscope", "leo", "--seed", "1",
                                      "--json"])))
        # weather: --offline stays hermetic (no network) and reproducible
        w = json.loads(self._main(["weather", "--where", "Dublin", "--offline",
                                   "--seed", "7", "--json"]))
        self.assertTrue(w["offline"])
        self.assertEqual(w["location"], "Dublin")
        self.assertGreaterEqual(w["high_c"], w["low_c"])
        # no location also falls back to a local estimate (no network)
        w2 = json.loads(self._main(["weather", "--offline", "--json"]))
        self.assertTrue(w2["offline"])
        self.assertEqual(w2["location"], "your area")
        # eightball: answer from the canonical set; question echoed
        e = json.loads(self._main(["eightball", "will", "I", "save",
                                   "--seed", "3", "--json"]))
        self.assertIn(e["answer"], L._EIGHTBALL)
        self.assertEqual(e["question"], "will I save")

    def test_today_json(self):
        m = date.today().isoformat()[:7]
        self._main(["income", "1000", "salary", "pay", "--date", f"{m}-02"])
        self._main(["add", "120", "food", "groceries", "--date", f"{m}-03"])
        self._main(["budget", "--category", "food", "--amount", "50"])
        d = json.loads(self._main(["today", "--json"]))
        self.assertEqual(d["month"], m)
        self.assertEqual(d["income"], 1000.0)
        self.assertEqual(d["spending"], 120.0)
        self.assertEqual(d["net"], 880.0)
        self.assertEqual(d["upcoming"]["days"], 7)
        # the over-budget food category shows up in the briefing
        self.assertEqual([b["category"] for b in d["budgets_over"]], ["food"])
        self.assertEqual(d["budgets_over"][0]["over"], 70.0)
        self.assertIn(d["fortune"], L._FORTUNES)

    def test_horoscope_rejects_unknown_sign(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                L.main(["horoscope", "notasign"])

    def test_categories_json(self):
        self._main(["add", "10", "food", "a"])
        self._main(["add", "20", "food", "b"])
        self._main(["add", "5", "transit", "c"])
        self._main(["budget", "--category", "rent", "--amount", "800"])
        d = json.loads(self._main(["categories", "--json"]))
        self.assertEqual(d["food"], {"count": 2, "total": 30.0, "budget": None})
        self.assertEqual(d["transit"]["count"], 1)
        # rent has a budget but no expenses -> still listed
        self.assertEqual(d["rent"], {"count": 0, "total": 0.0, "budget": 800.0})

    def test_category_profile_json(self):
        this = date.today().isoformat()[:7]
        self._main(["add", "10", "food", "cheap", "--date", f"{this}-03"])
        self._main(["add", "30", "food", "big", "--date", f"{this}-20"])
        self._main(["add", "60", "transit", "pass", "--date", f"{this}-05"])
        self._main(["budget", "--category", "food", "--amount", "100"])
        d = json.loads(self._main(["category", "food", "--json"]))
        self.assertEqual(d["category"], "food")
        self.assertEqual(d["total"], 40.0)
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["average"], 20.0)
        self.assertEqual(d["median"], 20.0)        # (10 + 30) / 2
        self.assertEqual(d["min"]["amount"], 10.0)
        self.assertEqual(d["max"]["amount"], 30.0)
        self.assertEqual(d["share_pct"], 40.0)     # 40 of 100 total spending
        self.assertEqual(d["active_months"], 1)
        self.assertEqual(d["budget"]["limit"], 100.0)
        self.assertEqual(d["budget"]["spent"], 40.0)
        # the trend window ends on the current month and includes this month
        self.assertEqual(d["monthly"][-1], {"month": this, "total": 40.0})

    def test_category_empty_and_budget_only(self):
        # unknown category with no spend and no budget -> empty, zeroed profile
        d = json.loads(self._main(["category", "ghost", "--json"]))
        self.assertEqual((d["total"], d["count"], d["min"], d["max"]),
                         (0.0, 0, None, None))
        # a budgeted category with no spend still reports its budget, and the
        # text view shows it rather than the "no spending" line
        self._main(["budget", "--category", "rent", "--amount", "800"])
        txt = self._main(["category", "rent"])
        self.assertIn("Budget", txt)
        self.assertNotIn("no spending recorded", txt)

    def test_retag(self):
        self._main(["add", "40", "food", "dinner #work #client"])
        self._main(["add", "10", "transit", "bus #Work"])   # different case
        self._main(["retag", "work", "business"])
        exp = L.load()["expenses"]
        self.assertTrue(all("work" not in e["tags"] for e in exp))
        self.assertIn("business", exp[0]["tags"])
        self.assertIn("client", exp[0]["tags"])             # other tag preserved
        self.assertIn("#business", exp[0]["note"])
        self.assertIn("business", exp[1]["tags"])           # case-insensitive match

    def test_retag_validation(self):
        self._main(["add", "10", "food", "x #work"])
        with self.assertRaises(SystemExit):                 # not a single word
            self._main(["retag", "work", "two words"])
        with self.assertRaises(SystemExit):                 # same
            self._main(["retag", "work", "#work"])

    def test_recategorize_moves_records_and_budget(self):
        self._main(["add", "10", "food", "a"])
        self._main(["add", "20", "food", "b"])
        self._main(["budget", "--category", "food", "--amount", "300"])
        self._main(["recur", "add", "5", "food", "--every", "month"])
        self._main(["recategorize", "food", "dining"])
        data = L.load()
        self.assertTrue(all(e["category"] == "dining" for e in data["expenses"]))
        self.assertTrue(all(r["category"] == "dining" for r in data["recurring"]))
        self.assertNotIn("food", data["budgets"])
        self.assertEqual(data["budgets"]["dining"], 300.0)

    def test_recategorize_same_name_exits(self):
        with self.assertRaises(SystemExit):
            self._main(["recategorize", "food", "FOOD"])  # same after normalizing

    def test_undo_reverts_and_redoes(self):
        self._main(["add", "10", "food", "first"])
        self._main(["add", "20", "food", "second"])
        self.assertEqual(len(L.load()["expenses"]), 2)
        self._main(["undo"])                       # revert the 2nd add
        self.assertEqual(len(L.load()["expenses"]), 1)
        self._main(["undo"])                       # toggle: redo it
        self.assertEqual(len(L.load()["expenses"]), 2)

    def test_undo_nothing(self):
        out = self._main(["undo"])
        self.assertIn("nothing to undo", out)

    def test_income_excluded_from_spending_views(self):
        self._main(["add", "100", "food", "groceries"])
        self._main(["income", "2500", "salary", "march pay"])
        # list defaults to expenses only
        rows = json.loads(self._main(["list", "--json"]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "food")
        # --income shows only income; --all shows both
        self.assertEqual(len(json.loads(self._main(["list", "--income", "--json"]))), 1)
        self.assertEqual(len(json.loads(self._main(["list", "--all", "--json"]))), 2)
        # categories (a spending taxonomy) must not include the income source
        cats = json.loads(self._main(["categories", "--json"]))
        self.assertIn("food", cats)
        self.assertNotIn("salary", cats)

    def test_stats_net_and_savings(self):
        self._main(["add", "100", "food", "a"])
        self._main(["income", "400", "salary", "b"])
        d = json.loads(self._main(["stats", "--json"]))
        self.assertEqual(d["expenses"], 1)     # income not counted as an expense
        self.assertEqual(d["total"], 100.0)    # spending total
        self.assertEqual(d["income"], 400.0)
        self.assertEqual(d["net"], 300.0)
        self.assertEqual(d["savings_rate"], 75.0)

    def test_recur_edit_fields(self):
        self._main(["recur", "add", "1200", "rent", "flat", "--every", "month"])
        self._main(["recur", "edit", "1", "--amount", "1300", "--note", "raised",
                    "--every", "week"])
        r = L.load()["recurring"][0]
        self.assertEqual(r["amount"], 1300.0)
        self.assertEqual(r["note"], "raised")
        self.assertEqual(r["every"], "week")

    def test_recur_edit_kind_and_guards(self):
        self._main(["recur", "add", "3000", "salary", "--every", "month"])
        self._main(["recur", "edit", "1", "--income"])
        self.assertEqual(L.load()["recurring"][0]["kind"], "income")
        with self.assertRaises(SystemExit):        # no such rule
            self._main(["recur", "edit", "99", "--amount", "5"])
        with self.assertRaises(SystemExit):        # nothing to change
            self._main(["recur", "edit", "1"])
        with self.assertRaises(SystemExit):        # conflicting kind flags
            self._main(["recur", "edit", "1", "--income", "--expense"])

    def test_add_in_foreign_currency_converts(self):
        self._main(["config", "--home-code", "USD"])
        self._main(["fx", "set", "USD", "1"])
        self._main(["fx", "set", "EUR", "1.1"])          # 1 EUR = 1.10 USD
        self._main(["add", "100", "travel", "paris", "--in", "EUR"])
        exp = L.load()["expenses"]
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["amount"], 110.0)        # 100 * 1.1 / 1
        # income works the same way
        self._main(["income", "200", "refund", "x", "--in", "EUR"])
        inc = [e for e in L.load()["expenses"] if e["kind"] == "income"][0]
        self.assertEqual(inc["amount"], 220.0)

    def test_add_in_requires_home_code(self):
        self._main(["fx", "set", "EUR", "1.1"])
        with self.assertRaises(SystemExit):           # no home code set
            self._main(["add", "100", "travel", "x", "--in", "EUR"])

    def test_add_in_requires_rate(self):
        self._main(["config", "--home-code", "USD"])
        self._main(["fx", "set", "USD", "1"])
        with self.assertRaises(SystemExit):           # no EUR rate
            self._main(["add", "100", "travel", "x", "--in", "EUR"])

    def test_clone(self):
        self._main(["income", "500", "salary", "bonus #q3", "--date", "2026-05-01"])
        self._main(["clone", "1", "--date", "2026-06-01"])
        exp = L.load()["expenses"]
        self.assertEqual(len(exp), 2)
        c = exp[1]
        self.assertEqual(c["amount"], 500.0)
        self.assertEqual(c["category"], "salary")
        self.assertEqual(c["kind"], "income")          # kind is preserved
        self.assertEqual(c["tags"], ["q3"])            # tags re-parsed
        self.assertEqual(c["date"], "2026-06-01")
        self.assertNotEqual(c["id"], 1)

    def test_clone_overrides(self):
        self._main(["add", "12", "food", "lunch #work", "--date", "2026-05-01"])
        self._main(["clone", "1", "--date", "2026-06-01", "--amount", "20",
                    "--note", "dinner #home"])
        c = L.load()["expenses"][1]
        self.assertEqual(c["amount"], 20.0)            # amount overridden
        self.assertEqual(c["note"], "dinner #home")    # note overridden
        self.assertEqual(c["tags"], ["home"])          # tags re-parsed from note
        self.assertEqual(c["category"], "food")        # category still copied
        with self.assertRaises(SystemExit):            # zero amount rejected
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["clone", "1", "--amount", "0"])

    def test_clone_missing(self):
        with self.assertRaises(SystemExit):
            self._main(["clone", "99"])

    def test_delete_removes_entry_and_is_undoable(self):
        self._main(["add", "10", "food", "a"])
        self._main(["add", "20", "rent", "b"])
        self._main(["delete", "1"])
        self.assertEqual([e["category"] for e in L.load()["expenses"]], ["rent"])
        self._main(["undo"])                      # delete is undoable
        self.assertEqual(len(L.load()["expenses"]), 2)

    def test_delete_missing_errors(self):
        with self.assertRaises(SystemExit):
            self._main(["delete", "999"])

    def test_delete_multiple_ids(self):
        for amt, cat in [(10, "a"), (20, "b"), (30, "c"), (40, "d")]:
            self._main(["add", str(amt), cat, "x"])
        out = self._main(["delete", "1", "3", "3"])   # dup id tolerated
        self.assertIn("deleted 2 entries", out)
        self.assertEqual(sorted(e["id"] for e in L.load()["expenses"]), [2, 4])
        # if any id is missing, nothing is deleted (all-or-nothing)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["delete", "2", "999"])
        self.assertEqual(sorted(e["id"] for e in L.load()["expenses"]), [2, 4])

    def test_backup_and_restore_roundtrip(self):
        self._main(["add", "10", "food", "a"])
        out = self._main(["backup"])
        self.assertIn("backed up to", out)
        names = os.listdir(L.BACKUP_DIR)
        self.assertEqual(len(names), 1)
        # mutate after the backup, then restore it
        self._main(["add", "999", "splurge", "oops"])
        self.assertEqual(len(L.load()["expenses"]), 2)
        self._main(["restore", "--file", names[0]])
        exp = L.load()["expenses"]
        self.assertEqual(len(exp), 1)
        self.assertEqual(exp[0]["category"], "food")

    def test_backup_prune(self):
        os.makedirs(L.BACKUP_DIR, exist_ok=True)
        names = [f"ledgerling_data_2026010{i}_000000.json" for i in range(1, 6)]
        for n in names:
            with open(os.path.join(L.BACKUP_DIR, n), "w", encoding="utf-8") as fh:
                fh.write("{}")
        out = self._main(["backup", "--prune", "2"])
        self.assertIn("pruned 3", out)
        self.assertEqual(sorted(os.listdir(L.BACKUP_DIR)), names[-2:])  # newest 2
        with self.assertRaises(SystemExit):           # must keep at least one
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["backup", "--prune", "0"])

    def test_restore_backfills_all_sections(self):
        # an older backup that predates newer sections (no pots/accounts/etc.)
        os.makedirs(L.BACKUP_DIR, exist_ok=True)
        old = os.path.join(L.BACKUP_DIR, "old.json")
        with open(old, "w", encoding="utf-8") as fh:
            json.dump({"expenses": [{"id": 1, "amount": 5.0, "category": "x",
                                     "date": "2026-01-01", "note": ""}]}, fh)
        self._main(["restore", "--file", "old.json"])
        # the saved live file itself must carry every section, not rely on load()
        with open(L.DATA_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
        for key in ("expenses", "recurring", "networth_history", "budgets",
                    "accounts", "pots", "templates"):
            self.assertIn(key, raw)

    def test_restore_missing_file_errors(self):
        with self.assertRaises(SystemExit):
            self._main(["restore", "--file", "nope.json"])

    def test_backup_with_no_data_errors(self):
        with self.assertRaises(SystemExit):
            self._main(["backup"])             # nothing recorded yet

    def test_split_replaces_entry_with_parts(self):
        self._main(["add", "100", "costco", "run #bulk", "--date", "2026-07-03"])
        self._main(["split", "1", "groceries", "70", "household", "30"])
        exp = L.load()["expenses"]
        self.assertEqual(len(exp), 2)
        # the original "costco" entry is replaced by the two parts
        self.assertNotIn("costco", [e["category"] for e in exp])
        by_cat = {e["category"]: e for e in exp}
        self.assertEqual(by_cat["groceries"]["amount"], 70.0)
        self.assertEqual(by_cat["household"]["amount"], 30.0)
        # inherits date, note, tags, kind from the original
        self.assertEqual(by_cat["groceries"]["date"], "2026-07-03")
        self.assertEqual(by_cat["groceries"]["tags"], ["bulk"])
        self.assertEqual(by_cat["groceries"]["kind"], "expense")
        self.assertEqual(round(sum(e["amount"] for e in exp), 2), 100.0)

    def test_split_by_percent(self):
        self._main(["add", "100", "costco", "run", "--date", "2026-07-03"])
        self._main(["split", "1", "--pct", "groceries", "70", "household", "30"])
        exp = L.load()["expenses"]
        by_cat = {e["category"]: e["amount"] for e in exp}
        self.assertEqual(by_cat["groceries"], 70.0)
        self.assertEqual(by_cat["household"], 30.0)
        self.assertEqual(round(sum(e["amount"] for e in exp), 2), 100.0)

    def test_split_by_percent_rounding_sums_exact(self):
        # 100.00 split 33.33/33.33/33.34 (3 equal thirds) must sum back exactly
        self._main(["add", "100", "x", "y"])
        self._main(["split", "1", "--pct", "a", "33.33", "b", "33.33",
                    "c", "33.34"])
        exp = L.load()["expenses"]
        self.assertEqual(round(sum(e["amount"] for e in exp), 2), 100.0)

    def test_split_pct_rejects_bad_sum(self):
        self._main(["add", "100", "x", "y"])
        with self.assertRaises(SystemExit):
            self._main(["split", "1", "--pct", "a", "70", "b", "40"])

    def test_split_rejects_total_mismatch(self):
        self._main(["add", "100", "costco", "x"])
        with self.assertRaises(SystemExit):
            self._main(["split", "1", "groceries", "70", "household", "40"])
        self.assertEqual(len(L.load()["expenses"]), 1)   # unchanged

    def test_split_rejects_odd_args(self):
        self._main(["add", "50", "costco", "x"])
        with self.assertRaises(SystemExit):
            self._main(["split", "1", "groceries", "30", "household"])

    def test_duplicates_json(self):
        self._main(["add", "40", "food", "dinner", "--date", "2026-09-10"])
        self._main(["add", "40", "food", "dinner", "--date", "2026-09-10"])  # dup
        self._main(["add", "40", "food", "lunch", "--date", "2026-09-10"])   # not
        d = json.loads(self._main(["duplicates", "--json"]))
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["ids"], [1, 2])
        self.assertEqual(d[0]["note"], "dinner")

    def test_duplicates_none(self):
        self._main(["add", "10", "food", "a"])
        self._main(["add", "10", "food", "b"])       # different note -> not a dup
        out = self._main(["duplicates"])
        self.assertIn("no duplicates found", out)

    def test_pace_json(self):
        self._main(["budget", "--category", "food", "--amount", "300"])
        self._main(["add", "100", "food", "x"])       # current month
        d = json.loads(self._main(["pace", "--json"]))
        c = d["categories"]["food"]
        self.assertEqual(c["spent"], 100.0)
        self.assertEqual(c["limit"], 300.0)
        for key in ("expected", "projected", "on_pace"):
            self.assertIn(key, c)
        self.assertEqual(d["days_in_month"],
                         __import__("calendar").monthrange(
                             *map(int, d["month"].split("-")))[1])

    def test_pace_full_past_month(self):
        # a past month is treated as fully elapsed, so expected == limit
        self._main(["budget", "--category", "food", "--amount", "300"])
        self._main(["add", "250", "food", "x", "--date", "2026-01-15"])
        d = json.loads(self._main(["pace", "--month", "2026-01", "--json"]))
        self.assertEqual(d["categories"]["food"]["expected"], 300.0)
        self.assertEqual(d["categories"]["food"]["projected"], 250.0)

    def test_pace_no_budgets(self):
        out = self._main(["pace"])
        self.assertIn("no budgets set", out)

    def test_overbudget_json(self):
        self._main(["budget", "--category", "food", "--amount", "100"])
        self._main(["budget", "--category", "rent", "--amount", "1000"])
        self._main(["add", "150", "food", "a", "--date", "2026-01-10"])  # over
        self._main(["add", "80", "food", "b", "--date", "2026-02-10"])   # under
        self._main(["add", "120", "food", "c", "--date", "2026-02-20"])  # Feb total 200 over
        self._main(["add", "900", "rent", "d", "--date", "2026-01-05"])  # under
        d = json.loads(self._main(["overbudget", "--json"]))
        self.assertEqual(d["count"], 2)                         # Jan food, Feb food
        self.assertEqual(d["months_checked"], 2)                # Jan, Feb
        self.assertEqual(d["total_over"], 150.0)                # 50 + 100
        first = d["breaches"][0]
        self.assertEqual((first["month"], first["category"]), ("2026-01", "food"))
        self.assertEqual(first["over"], 50.0)
        self.assertEqual(first["pct"], 150.0)
        # rent never breached
        self.assertTrue(all(b["category"] != "rent" for b in d["breaches"]))

    def test_overbudget_filters_and_empty(self):
        self._main(["budget", "--category", "food", "--amount", "100"])
        self._main(["add", "150", "food", "a", "--date", "2026-01-10"])
        self._main(["add", "150", "food", "b", "--date", "2026-02-10"])
        # scope to one month
        d = json.loads(self._main(["overbudget", "--month", "2026-02", "--json"]))
        self.assertEqual([b["month"] for b in d["breaches"]], ["2026-02"])
        self.assertEqual(d["months_checked"], 1)
        # scope to a category with no breaches -> empty
        self._main(["budget", "--category", "rent", "--amount", "1000"])
        d2 = json.loads(self._main(["overbudget", "--category", "rent", "--json"]))
        self.assertEqual(d2["count"], 0)
        # text path for the no-breach case
        self.assertIn("all within budget",
                      self._main(["overbudget", "--category", "rent"]))

    def test_overbudget_no_budgets(self):
        self.assertIn("no budgets set", self._main(["overbudget"]))

    def test_distribution_json(self):
        for amt in (5, 8, 30, 120, 500):
            self._main(["add", str(amt), "food", "x"])
        d = json.loads(self._main(["distribution", "--json"]))
        b = {x["label"]: x for x in d["buckets"]}
        self.assertEqual(b["$0-10"]["count"], 2)      # 5, 8
        self.assertEqual(b["$0-10"]["total"], 13.0)
        self.assertEqual(b["$25-50"]["count"], 1)     # 30
        self.assertEqual(b["$100-250"]["count"], 1)   # 120
        self.assertEqual(b["$250+"]["count"], 1)      # 500
        self.assertEqual(len(d["buckets"]), 6)

    def test_anomalies_flags_outlier(self):
        for amt in (10, 12, 11, 9, 13, 10, 11, 12):
            self._main(["add", str(amt), "groceries", "x"])
        self._main(["add", "60", "groceries", "big"])
        d = json.loads(self._main(["anomalies", "--json"]))
        self.assertEqual(len(d["anomalies"]), 1)
        a = d["anomalies"][0]
        self.assertEqual(a["amount"], 60.0)
        self.assertEqual(a["category"], "groceries")
        self.assertGreater(a["deviations"], 2.0)

    def test_anomalies_respects_min_count(self):
        # Only 3 entries in a category -> below default min-count, never flagged.
        for amt in (5, 5, 500):
            self._main(["add", str(amt), "coffee", "x"])
        d = json.loads(self._main(["anomalies", "--json"]))
        self.assertEqual(d["anomalies"], [])
        # Lowering min-count below the group size lets it be analyzed.
        d2 = json.loads(self._main(
            ["anomalies", "--min-count", "3", "--z", "1", "--json"]))
        self.assertEqual(len(d2["anomalies"]), 1)
        self.assertEqual(d2["anomalies"][0]["amount"], 500.0)

    def test_roundup_default_nearest_dollar(self):
        for amt in ("1.01", "2.50", "3.00", "9.99"):
            self._main(["add", amt, "food", "x"])
        d = json.loads(self._main(["roundup", "--json"]))
        # bumps: 0.99 + 0.50 + 0.00 + 0.01 = 1.50
        self.assertEqual(d["to"], 1.0)
        self.assertEqual(d["expenses"], 4)
        self.assertEqual(d["total_saved"], 1.50)
        self.assertEqual(d["largest"], 0.99)
        self.assertEqual(d["average"], round(1.50 / 4, 2))

    def test_roundup_to_five_and_ignores_income(self):
        self._main(["add", "12", "food", "x"])        # bump to 15 -> 3.00
        self._main(["add", "20", "rent", "y"])        # already on a $5 step -> 0
        self._main(["income", "999", "salary", "z"])  # ignored
        d = json.loads(self._main(["roundup", "--to", "5", "--json"]))
        self.assertEqual(d["to"], 5.0)
        self.assertEqual(d["expenses"], 2)
        self.assertEqual(d["total_saved"], 3.00)

    def test_challenge_nospend_and_roundup(self):
        # a past month so the whole month counts (days_considered = 31)
        self._main(["add", "1.01", "food", "a", "--date", "2026-07-05"])
        self._main(["add", "2.50", "food", "b", "--date", "2026-07-10"])
        d = json.loads(self._main(["challenge", "nospend", "--month", "2026-07",
                                   "--json"]))
        self.assertEqual(d["month"], "2026-07")
        self.assertEqual(d["days_considered"], 31)
        self.assertEqual(d["no_spend_days"], 29)           # 31 - 2 spend days
        self.assertEqual(d["target"], 10)                  # default
        self.assertEqual(d["longest_streak"], 21)          # days 11-31
        r = json.loads(self._main(["challenge", "roundup", "--month", "2026-07",
                                   "--json"]))
        self.assertEqual(r["jar"], 1.49)                   # 0.99 + 0.50
        self.assertEqual(r["expenses"], 2)

    def test_challenge_52week_and_list(self):
        d = json.loads(self._main(["challenge", "52week",
                                   "--start", "2026-01-01", "--json"]))
        self.assertEqual(d["total"], 1378.0)               # 1 + 2 + ... + 52
        self.assertTrue(1 <= d["week"] <= 52)
        self.assertEqual(d["cumulative_target"],
                         float(d["week"] * (d["week"] + 1) // 2))
        # list mode: one entry per challenge
        allc = json.loads(self._main(["challenge", "--json"]))
        self.assertEqual(set(allc), {"52week", "nospend", "roundup"})

    def test_week52_plan_pure(self):
        start = date(2026, 1, 1)
        self.assertEqual(L._week52_plan(start, date(2025, 12, 1))["week"], 0)
        p1 = L._week52_plan(start, date(2026, 1, 1))
        self.assertEqual((p1["week"], p1["week_amount"], p1["cumulative_target"]),
                         (1, 1.0, 1.0))
        self.assertEqual(L._week52_plan(start, date(2026, 1, 8))["week"], 2)
        done = L._week52_plan(start, date(2027, 6, 1))
        self.assertEqual((done["week"], done["cumulative_target"], done["done"]),
                         (52, 1378.0, True))

    def test_nospend_progress_pure(self):
        rows = [{"date": "2026-07-05", "amount": 5.0, "category": "food",
                 "kind": "expense"},
                {"date": "2026-07-10", "amount": 5.0, "category": "food",
                 "kind": "expense"}]
        p = L._nospend_progress(rows, "2026-07", date(2026, 8, 1))
        self.assertEqual((p["days_considered"], p["no_spend_days"],
                          p["longest_streak"], p["current_streak"]),
                         (31, 29, 21, 21))

    def test_achievements_unlocks(self):
        # fresh ledger: only the "nothing yet" baseline, nothing unlocked
        d0 = json.loads(self._main(["achievements", "--json"]))
        self.assertEqual(d0["unlocked"], 0)
        self.assertEqual(d0["total"], len(d0["achievements"]))
        # income + a tagged expense -> first_entry + organized unlock
        self._main(["income", "3000", "salary", "pay", "--date", "2026-05-01"])
        self._main(["add", "100", "food", "lunch #work", "--date", "2026-05-02"])
        d = json.loads(self._main(["achievements", "--json"]))
        by = {a["key"]: a["unlocked"] for a in d["achievements"]}
        self.assertTrue(by["first_entry"])
        self.assertTrue(by["organized"])        # a #tag was used
        self.assertTrue(by["four_figures"])     # net 2900 >= 1000
        self.assertTrue(by["super_saver"])      # saved ~97% of income
        self.assertFalse(by["centurion"])       # not 100 entries
        self.assertFalse(by["goal_achieved"])   # no funded pot
        # funding a pot to target unlocks goal_achieved
        self._main(["pot", "trip", "--target", "50"])
        self._main(["pot", "trip", "--add", "50"])
        d2 = json.loads(self._main(["achievements", "--json"]))
        self.assertTrue(next(a["unlocked"] for a in d2["achievements"]
                             if a["key"] == "goal_achieved"))

    def test_cashflow_projects_and_flags_negative(self):
        # Start from a known balance; one recurring bill pushes it negative.
        self._main(["recur", "add", "100", "rent", "monthly bill",
                    "--every", "month", "--start", date.today().isoformat()])
        d = json.loads(self._main(
            ["cashflow", "--days", "40", "--start-balance", "60", "--json"]))
        self.assertEqual(d["start_balance"], 60.0)
        self.assertTrue(len(d["events"]) >= 1)
        self.assertEqual(d["end_balance"], -40.0)
        self.assertEqual(d["net_change"], -100.0)
        self.assertEqual(d["low_balance"], -40.0)
        self.assertIsNotNone(d["negative_on"])

    def test_cashflow_income_before_expense_same_day(self):
        today = date.today().isoformat()
        self._main(["recur", "add", "200", "salary", "pay", "--every",
                    "month", "--start", today, "--income"])
        self._main(["recur", "add", "150", "rent", "bill", "--every",
                    "month", "--start", today])
        d = json.loads(self._main(
            ["cashflow", "--days", "40", "--start-balance", "0", "--json"]))
        # Same-day: income is applied first, so the running balance never dips
        # below zero even though the expense alone would overdraw a 0 start.
        self.assertIsNone(d["negative_on"])
        self.assertEqual(d["end_balance"], 50.0)

    def test_loan_amortization(self):
        # 10000 at 6%/yr over 5 years -> ~193.33/mo
        d = json.loads(self._main(["loan", "10000", "--rate", "6",
                                   "--years", "5", "--json"]))
        self.assertEqual(d["periods"], 60)
        self.assertEqual(d["monthly_payment"], 193.33)
        self.assertEqual(d["total_paid"], round(193.33 * 60, 2))
        self.assertEqual(d["total_interest"],
                         round(d["total_paid"] - 10000, 2))

    def test_loan_zero_rate_is_principal_over_term(self):
        d = json.loads(self._main(["loan", "1200", "--rate", "0",
                                   "--years", "1", "--json"]))
        self.assertEqual(d["monthly_payment"], 100.0)   # 1200 / 12
        self.assertEqual(d["total_interest"], 0.0)

    def test_loan_rejects_zero_principal(self):
        with self.assertRaises(SystemExit):
            self._main(["loan", "0"])

    def test_loan_rejects_sub_period_term(self):
        # 0.04y -> round(0.48)=0 periods; must error cleanly, not ZeroDivisionError
        with self.assertRaises(SystemExit):
            self._main(["loan", "1000", "--years", "0.04"])

    def test_interest_rejects_sub_period_term(self):
        with self.assertRaises(SystemExit):
            self._main(["interest", "1000", "--years", "0.04"])

    def test_next_id_ignores_non_integer_ids(self):
        self.assertEqual(L.next_id([{"id": None}]), 1)
        self.assertEqual(L.next_id([{"id": "oops"}, {"id": 5}]), 6)
        self.assertEqual(L.next_id([{"id": True}]), 1)   # bool is not a real id
        self.assertEqual(L.next_id([]), 1)

    def test_interest_no_rate_is_linear(self):
        d = json.loads(self._main(["interest", "1000", "--rate", "0",
                                   "--monthly", "100", "--years", "1", "--json"]))
        self.assertEqual(d["periods"], 12)
        self.assertEqual(d["contributed"], 2200.0)
        self.assertEqual(d["future_value"], 2200.0)
        self.assertEqual(d["interest"], 0.0)

    def test_interest_compounds(self):
        # 1000 at 12%/yr (1%/mo) for 1 year = 1000 * 1.01^12 = 1126.83
        d = json.loads(self._main(["interest", "1000", "--rate", "12",
                                   "--years", "1", "--json"]))
        self.assertEqual(d["future_value"], 1126.83)
        self.assertEqual(d["interest"], 126.83)

    def test_interest_rejects_negative_principal(self):
        with self.assertRaises(SystemExit):
            self._main(["interest", "-5"])

    def test_tip_basic_math(self):
        d = json.loads(self._main(["tip", "84.50", "--pct", "20", "--json"]))
        self.assertEqual(d["bill"], 84.50)
        self.assertEqual(d["tip"], 16.90)
        self.assertEqual(d["total"], 101.40)
        self.assertEqual(d["split"], 1)
        self.assertFalse(d["uneven"])

    def test_tip_even_split(self):
        d = json.loads(self._main(["tip", "80", "--pct", "25", "--split", "4",
                                   "--json"]))
        self.assertEqual(d["total"], 100.00)
        self.assertEqual(d["per_person"], 25.00)
        self.assertFalse(d["uneven"])

    def test_tip_uneven_split_sums_to_total(self):
        # $100.00 total across 3 -> 33.34, 33.33, 33.33 (sums back to 100.00)
        d = json.loads(self._main(["tip", "100", "--pct", "0", "--split", "3",
                                   "--json"]))
        self.assertTrue(d["uneven"])
        self.assertEqual(d["high_share"], 33.34)
        self.assertEqual(d["low_share"], 33.33)
        self.assertEqual(d["high_count"], 1)
        total = round(d["high_share"] * d["high_count"]
                      + d["low_share"] * (d["split"] - d["high_count"]), 2)
        self.assertEqual(total, d["total"])

    def test_tip_rejects_bad_split(self):
        with self.assertRaises(SystemExit):
            self._main(["tip", "20", "--split", "0"])

    def test_recur_count_generates_fixed_number(self):
        self._main(["recur", "add", "450", "loan", "car", "--every", "month",
                    "--start", "2026-01-01", "--count", "3"])
        dates = sorted(e["date"] for e in L.load()["expenses"])
        self.assertEqual(dates, ["2026-01-01", "2026-02-01", "2026-03-01"])
        row = json.loads(self._main(["recur", "list", "--json"]))[0]
        self.assertEqual(row["count"], 3)
        self.assertEqual(row["status"], "ended")   # all 3 already generated

    def test_recur_run_dry_run(self):
        start = (date.today() - timedelta(days=2)).isoformat()
        L.save({"expenses": [], "recurring": [
            {"id": 1, "amount": 5.0, "category": "coffee", "note": "",
             "every": "day", "start": start}], "budgets": {}, "goal": None})
        pend = json.loads(self._main(["recur", "run", "--dry-run", "--json"]))
        self.assertEqual(len(pend), 3)                  # day-2, day-1, today
        self.assertTrue(all(p["category"] == "coffee" for p in pend))
        self.assertEqual(len(L.load()["expenses"]), 0)  # nothing created
        out = self._main(["recur", "run", "--dry-run"])
        self.assertIn("would be generated", out)
        self.assertEqual(len(L.load()["expenses"]), 0)
        # a real run creates exactly what the preview promised
        self._main(["recur", "run"])
        self.assertEqual(len(L.load()["expenses"]), 3)

    def test_pending_recurring_matches_apply(self):
        import copy as _copy
        start = (date.today() - timedelta(days=5)).isoformat()
        data = {"expenses": [], "recurring": [
            {"id": 1, "amount": 2.0, "category": "x", "note": "",
             "every": "day", "start": start}], "budgets": {}, "goal": None}
        pend = L.pending_recurring(_copy.deepcopy(data))
        created = L.apply_recurring(_copy.deepcopy(data))
        self.assertEqual(len(pend), created)

    def test_recur_edit_count_and_clear(self):
        self._main(["recur", "add", "10", "gym", "x", "--every", "month",
                    "--start", "2026-01-01"])
        self._main(["recur", "edit", "1", "--count", "6"])
        self.assertEqual(L.load()["recurring"][0]["count"], 6)
        self._main(["recur", "edit", "1", "--no-count"])
        self.assertNotIn("count", L.load()["recurring"][0])

    def test_recur_count_rejects_zero(self):
        with self.assertRaises(SystemExit):
            self._main(["recur", "add", "10", "x", "y", "--every", "month",
                        "--count", "0"])

    def test_recur_list_json_states(self):
        # active (open-ended), ended (past until), and paused rules.
        self._main(["recur", "add", "50", "gym", "x", "--every", "month",
                    "--start", "2026-01-01"])
        self._main(["recur", "add", "1200", "rent", "y", "--every", "month",
                    "--start", "2026-01-01", "--until", "2026-03-31"])
        self._main(["recur", "add", "15", "music", "z", "--every", "month",
                    "--start", "2026-01-01"])
        self._main(["recur", "pause", "3"])
        rows = json.loads(self._main(["recur", "list", "--json"]))
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[1]["status"], "active")
        self.assertIsNotNone(by_id[1]["next"])        # active -> has a next date
        self.assertEqual(by_id[2]["status"], "ended")  # past its until
        self.assertIsNone(by_id[2]["next"])
        self.assertEqual(by_id[2]["until"], "2026-03-31")
        self.assertEqual(by_id[3]["status"], "paused")

    def test_recur_list_json_empty(self):
        self.assertEqual(json.loads(self._main(["recur", "list", "--json"])), [])

    def test_recur_until_stops_generation(self):
        self._main(["recur", "add", "100", "rent", "x", "--every", "month",
                    "--start", "2026-01-01", "--until", "2026-03-10"])
        dates = sorted(e["date"] for e in L.load()["expenses"])
        self.assertEqual(dates, ["2026-01-01", "2026-02-01", "2026-03-01"])

    def test_recur_until_before_start_errors(self):
        with self.assertRaises(SystemExit):
            self._main(["recur", "add", "100", "rent", "x", "--every", "month",
                        "--start", "2026-05-01", "--until", "2026-01-01"])

    def test_recur_edit_until_and_clear(self):
        self._main(["recur", "add", "10", "gym", "x", "--every", "month",
                    "--start", "2026-01-01"])
        self._main(["recur", "edit", "1", "--until", "2026-02-10"])
        self.assertEqual(L.load()["recurring"][0]["until"], "2026-02-10")
        self._main(["recur", "edit", "1", "--no-until"])
        self.assertNotIn("until", L.load()["recurring"][0])

    def test_load_config_rejects_wrong_types(self):
        with open(L.CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump({"fx": "oops", "list_limit": "lots",
                       "currency": "EUR", "symbol_position": "after"}, fh)
        cfg = L.load_config()
        self.assertEqual(cfg["fx"], {})            # wrong type -> default
        self.assertEqual(cfg["list_limit"], 20)    # wrong type -> default
        self.assertEqual(cfg["currency"], "EUR")   # valid str kept
        self.assertEqual(cfg["symbol_position"], "after")

    def test_load_config_does_not_alias_defaults(self):
        cfg = L.load_config()
        cfg["fx"]["EUR"] = 1.09                     # mutate the loaded copy
        self.assertEqual(L.DEFAULT_CONFIG["fx"], {})        # default untouched
        self.assertEqual(L.load_config()["fx"], {})         # next load is clean

    def test_runway_limited(self):
        d = json.loads(self._main(["runway", "--balance", "6000",
                                   "--monthly-net", "-1500", "--json"]))
        self.assertEqual(d["status"], "limited")
        self.assertEqual(d["months"], 4.0)
        self.assertIsNotNone(d["depletion_date"])

    def test_runway_positive_when_not_burning(self):
        d = json.loads(self._main(["runway", "--balance", "1000",
                                   "--monthly-net", "250", "--json"]))
        self.assertEqual(d["status"], "positive")
        self.assertIsNone(d["months"])
        self.assertIsNone(d["depletion_date"])

    def test_runway_already_depleted(self):
        d = json.loads(self._main(["runway", "--balance", "0",
                                   "--monthly-net", "-100", "--json"]))
        self.assertEqual(d["status"], "depleted")
        self.assertEqual(d["months"], 0.0)

    def test_target_on_track(self):
        d = json.loads(self._main(
            ["target", "1000", "--monthly", "250", "--start", "100", "--json"]))
        self.assertEqual(d["status"], "on_track")
        self.assertEqual(d["remaining"], 900.0)
        self.assertEqual(d["months"], 4)       # ceil(900 / 250)
        self.assertIsNotNone(d["reach_date"])

    def test_target_already_reached(self):
        d = json.loads(self._main(
            ["target", "500", "--monthly", "100", "--start", "500", "--json"]))
        self.assertEqual(d["status"], "reached")
        self.assertEqual(d["months"], 0)

    def test_target_unreachable_when_not_saving(self):
        d = json.loads(self._main(
            ["target", "1000", "--monthly", "0", "--start", "0", "--json"]))
        self.assertEqual(d["status"], "unreachable")
        self.assertIsNone(d["months"])
        self.assertIsNone(d["reach_date"])

    def test_target_default_rate_from_history(self):
        # one income-only month -> positive average net drives the estimate
        self._main(["income", "600", "salary", "x",
                    "--date", date.today().isoformat()])
        d = json.loads(self._main(["target", "300", "--start", "0", "--json"]))
        self.assertEqual(d["status"], "on_track")
        self.assertGreater(d["monthly"], 0)

    def test_fire_number(self):
        # explicit annual spending: 40k at 4% -> 1,000,000 (25x)
        d = json.loads(self._main(["fire", "--spending", "40000", "--json"]))
        self.assertEqual(d["rate"], 4.0)
        self.assertEqual(d["annual_spending"], 40000.0)
        self.assertEqual(d["fire_number"], 1000000.0)
        self.assertIsNone(d["projection"])
        # with savings + contributions -> a bounded projection
        p = json.loads(self._main(["fire", "--spending", "24000", "--saved",
                                   "100000", "--monthly", "2000", "--return",
                                   "6", "--json"]))
        self.assertEqual(p["fire_number"], 600000.0)
        self.assertIsNotNone(p["projection"]["months"])
        self.assertGreater(p["projection"]["years"], 0)
        # spending derived from the ledger when not given
        self._main(["add", "500", "food", "x", "--date", "2026-04-10"])
        led = json.loads(self._main(["fire", "--json"]))
        self.assertEqual(led["annual_spending"], 6000.0)   # 500/mo * 12

    def test_fire_months_to_fi_pure(self):
        self.assertEqual(L._months_to_fi(1000, 1000, 0, 0), 0)      # already there
        self.assertIsNone(L._months_to_fi(1000, 0, 0, 0))          # never
        self.assertEqual(L._months_to_fi(1200, 0, 100, 0), 12)     # no growth

    def test_rule72(self):
        d = json.loads(self._main(["rule72", "--rate", "6", "--json"]))
        self.assertEqual(d["years_to_double"], 12.0)
        d2 = json.loads(self._main(["rule72", "--years", "9", "--json"]))
        self.assertEqual(d2["rate_to_double"], 8.0)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["rule72", "--rate", "6", "--years", "9"])

    def test_lattefactor(self):
        # $5/day for 10 years: annual 1825, plain 18250; 0% return = plain spend
        d = json.loads(self._main(["lattefactor", "5", "--years", "10",
                                   "--return", "0", "--json"]))
        self.assertEqual(d["annual"], 1825.0)
        self.assertEqual(d["spent"], 18250.0)
        self.assertEqual(d["invested"], 18250.0)   # no growth at 0%
        self.assertEqual(d["growth"], 0.0)
        # a positive return grows it beyond plain spend
        g = json.loads(self._main(["lattefactor", "5", "--years", "10",
                                   "--return", "6", "--json"]))
        self.assertGreater(g["invested"], g["spent"])
        # per-week scales the annual figure
        w = json.loads(self._main(["lattefactor", "10", "--per", "week",
                                   "--json"]))
        self.assertEqual(w["annual"], 520.0)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["lattefactor", "0"])

    def test_future_value_pure(self):
        self.assertEqual(L._future_value(100, 0, 1), 1200.0)   # no growth
        self.assertGreater(L._future_value(100, 6, 1), 1200.0)

    def test_inflation(self):
        # flat 10%/yr over 2 years: 100 -> 121
        d = json.loads(self._main(["inflation", "100", "--from", "2000",
                                   "--to", "2002", "--rate", "10", "--json"]))
        self.assertEqual(d["adjusted"], 121.0)
        self.assertEqual(d["change"], 21.0)
        # same year is a no-op
        s = json.loads(self._main(["inflation", "50", "--from", "2010",
                                   "--to", "2010", "--json"]))
        self.assertEqual(s["adjusted"], 50.0)
        # going backward deflates (target earlier than source)
        b = json.loads(self._main(["inflation", "121", "--from", "2002",
                                   "--to", "2000", "--rate", "10", "--json"]))
        self.assertEqual(b["adjusted"], 100.0)
        # built-in table raises a past amount toward today
        t = json.loads(self._main(["inflation", "100", "--from", "2000",
                                   "--to", "2020", "--json"]))
        self.assertGreater(t["adjusted"], 100.0)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["inflation", "0", "--from", "2000"])

    def test_inflation_factor_pure(self):
        self.assertEqual(L._inflation_factor(2000, 2000), 1.0)
        self.assertAlmostEqual(L._inflation_factor(2000, 2002, 10), 1.21)
        # reciprocal when direction reverses
        self.assertAlmostEqual(
            L._inflation_factor(2002, 2000, 10) * L._inflation_factor(2000, 2002, 10),
            1.0)
        # unknown years fall back to the default rate
        self.assertAlmostEqual(
            L._inflation_factor(1800, 1802), (1 + L.DEFAULT_INFLATION / 100) ** 2)

    def test_int_to_words_pure(self):
        self.assertEqual(L._int_to_words(0), "zero")
        self.assertEqual(L._int_to_words(19), "nineteen")
        self.assertEqual(L._int_to_words(42), "forty-two")
        self.assertEqual(L._int_to_words(100), "one hundred")
        self.assertEqual(L._int_to_words(1234),
                         "one thousand two hundred thirty-four")
        self.assertEqual(L._int_to_words(1_000_000), "one million")

    def test_amount_to_words_pure(self):
        self.assertEqual(L._amount_to_words(0), "Zero dollars")
        self.assertEqual(L._amount_to_words(1), "One dollar")
        self.assertEqual(L._amount_to_words(1.01), "One dollar and one cent")
        self.assertEqual(L._amount_to_words(1234.56),
                         "One thousand two hundred thirty-four dollars "
                         "and fifty-six cents")
        # rounds to whole cents (0.999 -> 1.00)
        self.assertEqual(L._amount_to_words(0.999), "One dollar")

    def test_words_command(self):
        d = json.loads(self._main(["words", "25.40", "--json"]))
        self.assertEqual(d["amount"], 25.40)
        self.assertEqual(d["words"], "Twenty-five dollars and forty cents")
        self.assertEqual(self._main(["words", "5"]).strip(), "Five dollars")

    def test_receipt(self):
        self._main(["add", "12", "lunch", "wrap", "--date", "2026-03-02"])
        self._main(["add", "3", "coffee", "--date", "2026-03-02"])
        self._main(["income", "100", "gift", "--date", "2026-03-02"])
        d = json.loads(self._main(["receipt", "--date", "2026-03-02", "--json"]))
        self.assertEqual(d["spent"], 15.0)
        self.assertEqual(d["received"], 100.0)
        self.assertEqual(len(d["items"]), 3)
        self.assertEqual(d["total_words"], "Fifteen dollars")
        # text form draws a bordered box
        txt = self._main(["receipt", "--date", "2026-03-02"])
        self.assertIn("RECEIPT", txt)
        self.assertIn("TOTAL", txt)
        # single entry by id
        one = json.loads(self._main(["receipt", "--id", "1", "--json"]))
        self.assertEqual(len(one["items"]), 1)
        # empty day is a clean message, not a crash
        self.assertIn("no entries", self._main(["receipt", "--date", "2000-01-01"]))
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["receipt", "--id", "9999"])

    def test_wrap_pure(self):
        self.assertEqual(L._wrap("", 10), [""])
        self.assertEqual(L._wrap("a b c", 10), ["a b c"])
        self.assertEqual(L._wrap("aaa bbb ccc", 7), ["aaa bbb", "ccc"])

    def test_spending_persona_pure(self):
        # empty -> blank slate, no crash
        blank = L.spending_persona([])
        self.assertEqual(blank["count"], 0)
        self.assertEqual(blank["archetype"], "The Blank Slate")
        # one dominant category on a Saturday -> weekend read, metrics line up
        rows = [{"amount": 100.0, "category": "fun", "date": "2026-03-07"},   # Sat
                {"amount": 20.0, "category": "food", "date": "2026-03-07"}]
        p = L.spending_persona(rows)
        self.assertEqual(p["count"], 2)
        self.assertEqual(p["total"], 120.0)
        self.assertEqual(p["top_category"], "fun")
        self.assertEqual(p["busiest_weekday"], "Sat")
        self.assertEqual(p["weekend_share"], 1.0)
        self.assertEqual(p["archetype"], "The Weekender")
        # many small weekday buys -> impulse read
        imp = L.spending_persona(
            [{"amount": 1.0, "category": "c", "date": "2026-03-04"}] * 4
            + [{"amount": 100.0, "category": "c", "date": "2026-03-04"}])
        self.assertGreaterEqual(imp["impulse_score"], 65)
        self.assertEqual(imp["archetype"], "The Impulse Grazer")

    def test_persona_command(self):
        self._main(["add", "40", "rent", "--date", "2026-03-02"])
        d = json.loads(self._main(["persona", "--json"]))
        self.assertEqual(d["top_category"], "rent")
        self.assertIn("archetype", d)
        txt = self._main(["persona"])
        self.assertIn("Spending personality", txt)
        # empty month is a clean read, not a crash
        self.assertIn("Blank Slate", self._main(["persona", "--month", "1999-01"]))

    def test_days_until_pure(self):
        self.assertEqual(L._days_until("2026-01-10", "2026-01-01"), 9)
        self.assertEqual(L._days_until("2026-01-01", "2026-01-01"), 0)
        self.assertEqual(L._days_until("2025-12-30", "2026-01-01"), -2)

    def test_days_phrase_pure(self):
        self.assertEqual(L._days_phrase(0), "today")
        self.assertEqual(L._days_phrase(1), "in 1 day")
        self.assertEqual(L._days_phrase(5), "in 5 days")
        self.assertEqual(L._days_phrase(-1), "1 day ago")
        self.assertEqual(L._days_phrase(-3), "3 days ago")

    def test_countdown_date(self):
        d = json.loads(self._main(["countdown", "2999-01-01", "--json"]))
        self.assertEqual(d["date"], "2999-01-01")
        self.assertGreater(d["days"], 0)
        self.assertIsNone(d["remaining"])
        txt = self._main(["countdown", "2999-01-01", "--label", "trip"])
        self.assertIn("trip", txt)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["countdown"])   # neither a date nor a pot

    def test_countdown_pot(self):
        self._main(["pot", "vacation", "--target", "1000"])
        self._main(["pot", "vacation", "--add", "200"])
        self._main(["pot", "vacation", "--by", "2999-01-01"])
        d = json.loads(self._main(["countdown", "--pot", "vacation", "--json"]))
        self.assertEqual(d["remaining"], 800.0)
        self.assertGreater(d["days"], 0)
        self.assertIsNotNone(d["per_day"])
        # a pot with no target date is a clean error
        self._main(["pot", "nodate", "--target", "50"])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["countdown", "--pot", "nodate"])

    def test_read_commands_survive_empty_data(self):
        # Every no-argument command must run on an empty store without an
        # unhandled exception (division by zero, max() of empty, etc.).
        # Commands needing positionals exit via argparse (SystemExit) -> fine.
        import argparse
        import contextlib
        parser = L.build_parser()
        names = []
        for a in parser._actions:
            if isinstance(a, argparse._SubParsersAction):
                names = list(a.choices.keys())
        self.assertIn("summary", names)   # sanity: we actually found commands
        for name in names:
            if name in ("web", "gui"):    # start a server / open a window; skip
                continue
            try:
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    L.main([name])
            except SystemExit:
                pass
            except Exception as exc:       # noqa: BLE001 - we want any crash
                self.fail(f"'{name}' crashed on empty data: {exc!r}")

    def test_list_filter_by_tag(self):
        self._main(["add", "10", "food", "lunch #work", "--date", "2026-01-01"])
        self._main(["add", "20", "food", "dinner", "--date", "2026-01-02"])
        self._main(["add", "30", "travel", "cab #work", "--date", "2026-01-03"])
        rows = json.loads(self._main(["list", "--tag", "work", "--json"]))
        self.assertEqual(sorted(e["amount"] for e in rows), [10.0, 30.0])
        # leading # is accepted too
        rows2 = json.loads(self._main(["list", "--tag", "#work", "--json"]))
        self.assertEqual(len(rows2), 2)

    def test_top_filter_by_tag(self):
        self._main(["add", "10", "food", "a #work"])
        self._main(["add", "99", "food", "b"])
        self._main(["add", "50", "travel", "c #work"])
        rows = json.loads(self._main(["top", "--tag", "work", "--json"]))
        self.assertEqual([e["amount"] for e in rows], [50.0, 10.0])  # largest first

    def test_list_sort_amount(self):
        self._main(["add", "30", "food", "a", "--date", "2026-01-01"])
        self._main(["add", "10", "food", "b", "--date", "2026-01-02"])
        self._main(["add", "20", "food", "c", "--date", "2026-01-03"])
        asc = json.loads(self._main(["list", "--sort", "amount", "--json"]))
        self.assertEqual([e["amount"] for e in asc], [10.0, 20.0, 30.0])
        desc = json.loads(self._main(["list", "--sort", "amount", "--desc",
                                      "--json"]))
        self.assertEqual([e["amount"] for e in desc], [30.0, 20.0, 10.0])
        # largest-N: desc + limit keeps the top N of the sorted order
        top2 = json.loads(self._main(["list", "--sort", "amount", "--desc",
                                      "--limit", "2", "--json"]))
        self.assertEqual([e["amount"] for e in top2], [30.0, 20.0])

    def test_catchup_commands_emit_valid_json(self):
        # Every read command invoked bare with --json must print parseable JSON
        # (not a plain-text message), so --json consumers never get a surprise.
        # Runs against this test's isolated empty store; commands needing a
        # positional exit via argparse and are skipped.
        import argparse
        bad = []
        parser = L.build_parser()
        next(a for a in parser._actions
             if isinstance(a, argparse._SubParsersAction))
        for name in sorted(L.CATCHUP_COMMANDS):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), \
                        contextlib.redirect_stderr(io.StringIO()):
                    L.main([name, "--json"])
            except SystemExit:
                continue   # needs a positional arg; can't run bare
            try:
                json.loads(buf.getvalue().strip())
            except Exception:
                bad.append(name)
        self.assertEqual(bad, [], "non-JSON under --json: %s" % bad)

    def test_add_tag_flag(self):
        self._main(["add", "10", "food", "lunch", "--tag", "work", "--tag", "food"])
        e = json.loads(self._main(["list", "--json"]))[0]
        self.assertEqual(sorted(e["tags"]), ["food", "work"])
        self.assertIn("#work", e["note"])
        # income supports it too, and the tag filter finds it
        self._main(["income", "500", "salary", "--tag", "bonus"])
        hits = json.loads(self._main(["search", "--income", "--tag", "bonus",
                                      "--json"]))
        self.assertEqual(len(hits), 1)

    def test_list_search_payee_filter(self):
        t = date.today().isoformat()
        self._main(["add", "15.99", "ent", "Netflix", "--date", t])
        self._main(["add", "9.99", "ent", "Netflix #fun", "--date", t])
        self._main(["add", "12", "food", "Spotify lunch", "--date", t])
        # case-insensitive substring on the payee (note minus #tags)
        d = json.loads(self._main(["list", "--payee", "netflix", "--json"]))
        self.assertEqual(len(d), 2)
        self.assertTrue(all("netflix" in e["note"].lower() for e in d))
        # search honours it too
        s = json.loads(self._main(["search", "--payee", "spotify", "--json"]))
        self.assertEqual([e["amount"] for e in s], [12.0])

    def test_list_recurring_manual_filter(self):
        # one manual entry, plus a recurring rule that generates one
        self._main(["add", "10", "food", "manual lunch", "--date",
                    date.today().isoformat()])
        self._main(["recur", "add", "50", "rent", "flat", "--every", "month",
                    "--start", date.today().isoformat()])
        rec = json.loads(self._main(["list", "--recurring", "--json"]))
        self.assertTrue(rec and all(e.get("recur_id") for e in rec))
        self.assertTrue(all(e["category"] == "rent" for e in rec))
        man = json.loads(self._main(["list", "--manual", "--json"]))
        self.assertTrue(man and all(not e.get("recur_id") for e in man))
        self.assertTrue(all(e["category"] == "food" for e in man))
        # mutually exclusive
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["list", "--recurring", "--manual"])
        # search honours it too
        s = json.loads(self._main(["search", "--recurring", "--json"]))
        self.assertTrue(all(e.get("recur_id") for e in s))

    def test_recent_by_insertion_order(self):
        # add out of date order; recent ranks by id (insertion), not date
        self._main(["add", "10", "food", "a", "--date", "2026-06-01"])
        self._main(["add", "20", "rent", "b", "--date", "2026-01-01"])  # backdated
        self._main(["add", "30", "fun", "c", "--date", "2026-03-01"])
        d = json.loads(self._main(["recent", "--json"]))
        self.assertEqual([e["id"] for e in d], [3, 2, 1])   # newest-recorded first
        # income excluded by default; --income scopes to it
        self._main(["income", "500", "salary", "s", "--date", "2026-02-01"])
        exp = json.loads(self._main(["recent", "--json"]))
        self.assertTrue(all(e["category"] != "salary" for e in exp))
        inc = json.loads(self._main(["recent", "--income", "--json"]))
        self.assertEqual([e["category"] for e in inc], ["salary"])
        # limit + empty store
        self.assertEqual(len(json.loads(self._main(["recent", "--limit", "1",
                                                    "--json"]))), 1)

    def test_recent_empty(self):
        self.assertIn("no entries yet", self._main(["recent"]))

    def test_list_amount_and_date_filters(self):
        self._main(["add", "5", "food", "a", "--date", "2026-01-01"])
        self._main(["add", "50", "food", "b", "--date", "2026-01-15"])
        self._main(["add", "500", "food", "c", "--date", "2026-02-01"])
        # amount bounds
        mid = json.loads(self._main(["list", "--min", "10", "--max", "100",
                                     "--json"]))
        self.assertEqual([e["amount"] for e in mid], [50.0])
        # date range
        jan = json.loads(self._main(["list", "--since", "2026-01-01",
                                     "--until", "2026-01-31", "--json"]))
        self.assertEqual(sorted(e["amount"] for e in jan), [5.0, 50.0])
        # combined date range and amount
        combo = json.loads(self._main(["list", "--since", "2026-01-10",
                                       "--until", "2026-01-31", "--min", "20",
                                       "--json"]))
        self.assertEqual([e["amount"] for e in combo], [50.0])
        # invalid bounds rejected
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["list", "--min", "100", "--max", "1"])

    def test_filter_range_amount_pure(self):
        rows = [{"amount": 5.0, "date": "2026-01-01"},
                {"amount": 50.0, "date": "2026-02-01"}]
        ns = Namespace(since=None, until=None, min=10, max=None)
        self.assertEqual(L._filter_range_amount(rows, ns), [rows[1]])
        ns2 = Namespace(since="2026-01-15", until=None, min=None, max=None)
        self.assertEqual(L._filter_range_amount(rows, ns2), [rows[1]])

    def test_list_default_order_unchanged(self):
        self._main(["add", "1", "food", "a", "--date", "2026-01-03"])
        self._main(["add", "2", "food", "b", "--date", "2026-01-01"])
        self._main(["add", "3", "food", "c", "--date", "2026-01-02"])
        # default is still chronological (most-recent window, shown oldest->newest)
        rows = json.loads(self._main(["list", "--json"]))
        self.assertEqual([e["date"] for e in rows],
                         ["2026-01-01", "2026-01-02", "2026-01-03"])

    def test_export_filters_by_kind_and_category(self):
        self._main(["add", "10", "food", "a", "--date", "2026-01-01"])
        self._main(["add", "20", "rent", "b", "--date", "2026-01-02"])
        self._main(["income", "500", "salary", "c", "--date", "2026-01-03"])
        self._main(["export", "--income", "--format", "json", "--file", "inc.json"])
        with open(os.path.join(L.EXPORT_DIR, "inc.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
        self.assertEqual([r["kind"] for r in rows], ["income"])
        self._main(["export", "--category", "food", "--format", "json",
                    "--file", "food.json"])
        with open(os.path.join(L.EXPORT_DIR, "food.json"), encoding="utf-8") as fh:
            rows = json.load(fh)
        self.assertEqual([r["category"] for r in rows], ["food"])

    def test_import_json_roundtrip(self):
        self._main(["add", "10", "food", "a", "--date", "2026-01-01"])
        self._main(["income", "500", "salary", "b", "--date", "2026-01-02"])
        self._main(["export", "--format", "json", "--file", "data.json"])
        # wipe and re-import from the JSON export
        L.save({"expenses": [], "budgets": {}, "recurring": [], "goal": None})
        out = self._main(["import", "--file", "data.json"])
        self.assertIn("added 2", out)
        rows = L.load()["expenses"]
        self.assertEqual(len(rows), 2)
        kinds = sorted(e["kind"] for e in rows)
        self.assertEqual(kinds, ["expense", "income"])
        # re-importing the same JSON is fully deduped
        out2 = self._main(["import", "--file", "data.json"])
        self.assertIn("added 0", out2)
        self.assertIn("skipped 2", out2)

    def test_import_json_rejects_non_array(self):
        os.makedirs(L.EXPORT_DIR, exist_ok=True)
        with open(os.path.join(L.EXPORT_DIR, "bad.json"), "w",
                  encoding="utf-8") as fh:
            fh.write('{"not": "an array"}')
        with self.assertRaises(SystemExit):
            self._main(["import", "--file", "bad.json"])

    def test_export_rejects_both_kinds(self):
        self._main(["add", "10", "food", "a"])
        with self.assertRaises(SystemExit):
            self._main(["export", "--income", "--expenses"])

    def test_report_json(self):
        t = date.today().isoformat()
        self._main(["add", "40", "food", "a", "--date", t])
        self._main(["income", "100", "salary", "b", "--date", t])
        d = json.loads(self._main(["report", "--months", "3", "--json"]))
        self.assertEqual(len(d["months"]), 3)
        this_month = date.today().isoformat()[:7]
        cur = [m for m in d["months"] if m["month"] == this_month][0]
        self.assertEqual(cur["spending"], 40.0)
        self.assertEqual(cur["income"], 100.0)
        self.assertEqual(cur["net"], 60.0)
        self.assertEqual(d["total_spending"], 40.0)
        self.assertEqual(d["net"], 60.0)

    def test_summary_json(self):
        t = date.today().isoformat()
        self._main(["add", "30", "food", "a", "--date", t])
        self._main(["add", "10", "food", "b", "--date", t])
        self._main(["add", "60", "rent", "c", "--date", t])
        d = json.loads(self._main(["summary", "--json"]))
        self.assertEqual(d["month"], date.today().isoformat()[:7])
        self.assertEqual(d["total"], 100.0)
        # sorted by total desc: rent (60) before food (40)
        self.assertEqual([c["category"] for c in d["categories"]],
                         ["rent", "food"])
        self.assertEqual(d["categories"][0]["total"], 60.0)
        self.assertEqual(d["categories"][0]["percent"], 60.0)

    def test_summary_json_empty(self):
        d = json.loads(self._main(["summary", "--json"]))
        self.assertEqual(d["total"], 0)
        self.assertEqual(d["categories"], [])

    def test_summary_tag_filter(self):
        t = date.today().isoformat()
        self._main(["add", "40", "travel", "flight #work", "--date", t])
        self._main(["add", "10", "food", "lunch #work", "--date", t])
        self._main(["add", "25", "food", "dinner", "--date", t])   # no tag
        d = json.loads(self._main(["summary", "--tag", "work", "--json"]))
        self.assertEqual(d["tag"], "work")
        self.assertEqual(d["total"], 50.0)             # only #work entries
        cats = {c["category"]: c["total"] for c in d["categories"]}
        self.assertEqual(cats, {"travel": 40.0, "food": 10.0})
        # leading '#' tolerated; heading reflects the tag
        self.assertIn("#work", self._main(["summary", "--tag", "#work"]))

    def test_summary_limit_rolls_up_other(self):
        t = date.today().isoformat()
        for amt, cat in [(100, "rent"), (40, "food"), (20, "fun"), (10, "misc")]:
            self._main(["add", str(amt), cat, "x", "--date", t])
        d = json.loads(self._main(["summary", "--limit", "2", "--json"]))
        cats = d["categories"]
        self.assertEqual([c["category"] for c in cats], ["rent", "food", "(other)"])
        other = cats[-1]
        self.assertEqual(other["total"], 30.0)        # fun + misc
        self.assertEqual(other["rolled_up"], 2)
        self.assertEqual(d["total"], 170.0)           # grand total unchanged
        # no roll-up when limit >= category count
        full = json.loads(self._main(["summary", "--limit", "10", "--json"]))
        self.assertTrue(all(c["category"] != "(other)" for c in full["categories"]))

    def test_budget_json_view(self):
        self._main(["budget", "--category", "food", "--amount", "100"])
        self._main(["add", "30", "food", "a", "--date", date.today().isoformat()])
        d = json.loads(self._main(["budget", "--json"]))
        self.assertEqual(d["month"], date.today().isoformat()[:7])
        row = d["budgets"][0]
        self.assertEqual(row["category"], "food")
        self.assertEqual(row["limit"], 100.0)
        self.assertEqual(row["spent"], 30.0)
        self.assertEqual(row["remaining"], 70.0)
        self.assertFalse(row["over"])

    def test_budget_totals(self):
        t = date.today().isoformat()
        self._main(["budget", "--category", "food", "--amount", "100"])
        self._main(["budget", "--category", "rent", "--amount", "500"])
        self._main(["add", "30", "food", "a", "--date", t])
        self._main(["add", "550", "rent", "b", "--date", t])   # over its limit
        d = json.loads(self._main(["budget", "--json"]))
        tot = d["totals"]
        self.assertEqual(tot["limit"], 600.0)
        self.assertEqual(tot["spent"], 580.0)
        self.assertEqual(tot["remaining"], 20.0)
        self.assertFalse(tot["over"])          # under total despite rent breach
        self.assertIn("TOTAL", self._main(["budget"]))

    def test_budget_json_empty(self):
        d = json.loads(self._main(["budget", "--json"]))
        self.assertEqual(d["budgets"], [])

    def test_check_clean(self):
        self._main(["add", "10", "food", "x"])
        d = json.loads(self._main(["check", "--json"]))
        self.assertTrue(d["ok"])
        self.assertEqual(d["issues"], [])

    def test_check_finds_problems(self):
        L.save({"expenses": [
            {"id": 1, "amount": -5, "category": "food", "note": "",
             "date": "2026-01-01", "tags": [], "kind": "expense"},
            {"id": 1, "amount": 10, "category": "", "note": "",
             "date": "not-a-date", "tags": [], "kind": "expense",
             "recur_id": 99},
        ], "budgets": {"rent": -100}, "recurring": [], "goal": None})
        d = json.loads(self._main(["check", "--json"]))
        self.assertFalse(d["ok"])
        kinds = {i["kind"] for i in d["issues"]}
        for expected in ("duplicate_id", "bad_amount", "bad_date",
                         "empty_category", "orphan_recur_id", "bad_budget"):
            self.assertIn(expected, kinds)

    def test_check_fix_repairs_safe_issues(self):
        L.save({"expenses": [
            {"id": 1, "amount": 5.0, "category": "food", "note": "",
             "date": "2026-01-01", "tags": [], "kind": "expense"},
            {"id": 1, "amount": 10.0, "category": "", "note": "",
             "date": "2026-01-02", "tags": [], "kind": "expense",
             "recur_id": 99},
        ], "budgets": {"rent": -100}, "recurring": [], "goal": None})
        d = json.loads(self._main(["check", "--fix", "--json"]))
        self.assertTrue(d["ok"])                 # all auto-fixable issues gone
        self.assertEqual(d["issues"], [])
        self.assertTrue(len(d["fixed"]) >= 4)
        data = L.load()
        ids = [e["id"] for e in data["expenses"]]
        self.assertEqual(len(ids), len(set(ids)))        # ids now unique
        self.assertNotIn("recur_id", data["expenses"][1])  # orphan unlinked
        self.assertEqual(data["expenses"][1]["category"], "uncategorized")
        self.assertEqual(data["budgets"], {})            # invalid budget removed

    def test_check_flags_and_fixes_bad_accounts_and_snapshots(self):
        L.save({"expenses": [], "budgets": {}, "recurring": [], "goal": None,
                "accounts": {"good": {"amount": 100.0, "debt": False},
                             "bad1": {"amount": "lots"},   # non-numeric
                             "bad2": "not-a-dict"},
                "networth_history": [
                    {"date": "2026-01-01", "net": 50.0},   # good
                    {"date": "nope", "net": 5.0},          # bad date
                    {"date": "2026-02-01", "net": "x"}]})  # bad net
        d = json.loads(self._main(["check", "--json"]))
        kinds = [i["kind"] for i in d["issues"]]
        self.assertEqual(kinds.count("bad_account"), 2)
        self.assertEqual(kinds.count("bad_snapshot"), 2)
        # --fix removes the malformed ones, keeps the good ones
        d2 = json.loads(self._main(["check", "--fix", "--json"]))
        self.assertTrue(d2["ok"])
        data = L.load()
        self.assertEqual(list(data["accounts"]), ["good"])
        self.assertEqual([s["date"] for s in data["networth_history"]],
                         ["2026-01-01"])

    def test_load_coerces_corrupt_account_sections(self):
        # wrong container types in a hand-edited file must not crash later
        L.save({"expenses": [], "budgets": {}, "recurring": [], "goal": None,
                "accounts": "garbage", "networth_history": "garbage"})
        data = L.load()
        self.assertEqual(data["accounts"], {})
        self.assertEqual(data["networth_history"], [])
        # networth still runs on the coerced data
        out = self._main(["networth"])
        self.assertIn("Net worth", out)

    def test_load_coerces_every_container_section(self):
        # a thoroughly corrupt file: every container is the wrong type / missing
        import os as _os
        _os.makedirs(L.HOME_DIR, exist_ok=True)
        with open(L.DATA_FILE, "w", encoding="utf-8") as fh:
            fh.write('{"expenses": null, "budgets": 5, "recurring": "x", '
                     '"pots": [1, 2]}')
        data = L.load()
        self.assertEqual(data["expenses"], [])
        self.assertEqual(data["budgets"], {})
        self.assertEqual(data["recurring"], [])
        self.assertEqual(data["pots"], {})
        self.assertIsNone(data["goal"])
        # read commands run cleanly on the coerced data (no crash, some output)
        self.assertTrue(self._main(["list"]).strip())
        self.assertTrue(self._main(["summary"]).strip())

    def test_check_fix_leaves_unfixable(self):
        L.save({"expenses": [
            {"id": 1, "amount": -5.0, "category": "food", "note": "",
             "date": "nope", "tags": [], "kind": "expense"},
        ], "budgets": {}, "recurring": [], "goal": None})
        d = json.loads(self._main(["check", "--fix", "--json"]))
        kinds = {i["kind"] for i in d["issues"]}
        self.assertIn("bad_amount", kinds)       # not auto-fixable -> remains
        self.assertIn("bad_date", kinds)
        self.assertFalse(d["ok"])

    def test_check_fix_is_undoable(self):
        L.save({"expenses": [
            {"id": 1, "amount": 5.0, "category": "", "note": "",
             "date": "2026-01-01", "tags": [], "kind": "expense"},
        ], "budgets": {}, "recurring": [], "goal": None})
        self._main(["check", "--fix"])
        self.assertEqual(L.load()["expenses"][0]["category"], "uncategorized")
        self._main(["undo"])
        self.assertEqual(L.load()["expenses"][0]["category"], "")  # restored

    def test_check_handles_non_integer_ids(self):
        # A corrupt data file with a null and a string id must not crash check;
        # it reports bad_id, and --fix reassigns them to valid integer ids.
        L.save({"expenses": [
            {"id": None, "amount": 5.0, "category": "food", "note": "",
             "date": "2026-01-01", "tags": [], "kind": "expense"},
            {"id": "oops", "amount": 6.0, "category": "rent", "note": "",
             "date": "2026-01-02", "tags": [], "kind": "expense"},
        ], "budgets": {}, "recurring": [], "goal": None})
        d = json.loads(self._main(["check", "--json"]))          # no crash
        self.assertIn("bad_id", {i["kind"] for i in d["issues"]})
        self._main(["check", "--fix"])
        ids = [e["id"] for e in L.load()["expenses"]]
        self.assertTrue(all(isinstance(i, int) for i in ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(json.loads(self._main(["check", "--json"]))["issues"], [])

    def test_upcoming_excludes_paused_and_skipped(self):
        today = date.today().isoformat()
        self._main(["recur", "add", "15", "subs", "music", "--every", "week",
                    "--start", today])
        self._main(["recur", "add", "99", "gym", "x", "--every", "week",
                    "--start", today])
        self._main(["recur", "pause", "2"])                      # gym paused
        d = json.loads(self._main(["upcoming", "--days", "30", "--json"]))
        cats = {i["category"] for i in d["items"]}
        self.assertIn("subs", cats)
        self.assertNotIn("gym", cats)                            # paused -> excluded
        # now skip the subs rule's next occurrence and confirm it's dropped
        first = sorted(i["date"] for i in d["items"] if i["category"] == "subs")[0]
        self._main(["recur", "skip", "1", "--date", first])
        d2 = json.loads(self._main(["upcoming", "--days", "30", "--json"]))
        self.assertNotIn(first, [i["date"] for i in d2["items"]
                                 if i["category"] == "subs"])

    def test_check_flags_invalid_rule(self):
        L.save({"expenses": [], "budgets": {}, "recurring": [
            {"id": 1, "amount": 10.0, "category": "x", "note": "",
             "every": "fortnight", "start": "nope", "count": 0},
        ], "goal": None})
        kinds = {i["kind"] for i in json.loads(
            self._main(["check", "--json"]))["issues"]}
        self.assertIn("bad_frequency", kinds)
        self.assertIn("bad_rule_start", kinds)
        self.assertIn("bad_rule_count", kinds)

    def test_fx_set_list_convert(self):
        self._main(["fx", "set", "usd", "1"])
        self._main(["fx", "set", "eur", "1.09"])
        listed = json.loads(self._main(["fx", "list", "--json"]))
        self.assertEqual(listed["rates"], {"USD": 1.0, "EUR": 1.09})
        conv = json.loads(self._main(["fx", "convert", "100", "eur", "usd",
                                      "--json"]))
        self.assertEqual(conv["from"], "EUR")
        self.assertEqual(conv["to"], "USD")
        self.assertEqual(conv["result"], 109.0)  # 100 * 1.09 / 1

    def test_fx_convert_to_all(self):
        self._main(["fx", "set", "usd", "1"])
        self._main(["fx", "set", "eur", "1.09"])
        self._main(["fx", "set", "gbp", "1.27"])
        d = json.loads(self._main(["fx", "convert", "100", "usd", "--json"]))
        self.assertEqual(d["from"], "USD")
        tos = {c["to"]: c["result"] for c in d["conversions"]}
        self.assertEqual(set(tos), {"EUR", "GBP"})       # every other currency
        self.assertEqual(tos["EUR"], round(100 * 1 / 1.09, 2))
        self.assertEqual(tos["GBP"], round(100 * 1 / 1.27, 2))

    def test_fx_convert_to_all_needs_others(self):
        self._main(["fx", "set", "usd", "1"])
        with self.assertRaises(SystemExit):
            self._main(["fx", "convert", "100", "usd"])   # nothing else set

    def test_fx_convert_unknown_code(self):
        self._main(["fx", "set", "usd", "1"])
        with self.assertRaises(SystemExit):
            self._main(["fx", "convert", "10", "usd", "jpy"])

    def test_fx_rm_and_persistence(self):
        self._main(["fx", "set", "gbp", "1.27"])
        self.assertEqual(L.load_config().get("fx"), {"GBP": 1.27})
        self._main(["fx", "rm", "gbp"])
        self.assertEqual(L.load_config().get("fx"), {})

    def test_net_all_time_and_month(self):
        self._main(["add", "40", "food", "a", "--date", "2026-01-10"])
        self._main(["income", "100", "salary", "b", "--date", "2026-01-15"])
        self._main(["add", "10", "food", "c", "--date", "2026-02-01"])
        allt = json.loads(self._main(["net", "--json"]))
        self.assertEqual(allt["scope"], "all time")
        self.assertEqual(allt["income"], 100.0)
        self.assertEqual(allt["spending"], 50.0)
        self.assertEqual(allt["net"], 50.0)
        self.assertEqual(allt["savings_rate"], 50.0)
        jan = json.loads(self._main(["net", "--month", "2026-01", "--json"]))
        self.assertEqual(jan["net"], 60.0)      # 100 income - 40 expense
        self.assertEqual(jan["savings_rate"], 60.0)

    def test_net_date_range(self):
        self._main(["add", "40", "food", "a", "--date", "2026-01-10"])
        self._main(["income", "100", "salary", "b", "--date", "2026-01-15"])
        self._main(["add", "10", "food", "c", "--date", "2026-02-01"])
        d = json.loads(self._main(["net", "--since", "2026-01-01",
                                   "--until", "2026-01-31", "--json"]))
        self.assertEqual(d["scope"], "2026-01-01 to 2026-01-31")
        self.assertEqual(d["income"], 100.0)
        self.assertEqual(d["spending"], 40.0)         # Feb entry excluded
        self.assertEqual(d["net"], 60.0)
        self.assertEqual(d["savings_rate"], 60.0)
        with self.assertRaises(SystemExit):           # month + range is an error
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["net", "--month", "2026-01", "--since", "2026-01-01"])

    def test_net_no_income_rate_is_null(self):
        self._main(["add", "10", "food", "a"])
        d = json.loads(self._main(["net", "--json"]))
        self.assertEqual(d["net"], -10.0)
        self.assertIsNone(d["savings_rate"])

    def test_average_json(self):
        self._main(["add", "100", "food", "a", "--date", "2026-01-01"])
        self._main(["add", "100", "food", "b", "--date", "2026-01-11"])  # 11-day span
        self._main(["income", "9999", "salary", "x", "--date", "2026-01-05"])  # ignored
        d = json.loads(self._main(["average", "--json"]))
        self.assertEqual(d["days"], 11)
        self.assertEqual(d["total"], 200.0)
        self.assertEqual(d["per_day"], round(200 / 11, 2))
        self.assertEqual(d["first"], "2026-01-01")
        self.assertEqual(d["last"], "2026-01-11")
        self.assertEqual(d["transactions"], 2)
        self.assertEqual(d["per_transaction"], 100.0)
        self.assertEqual(d["median_transaction"], 100.0)

    def test_average_median_robust_to_outlier(self):
        for amt in ("5", "5", "5", "500"):          # one big outlier
            self._main(["add", amt, "food", "x"])
        d = json.loads(self._main(["average", "--json"]))
        self.assertEqual(d["transactions"], 4)
        self.assertEqual(d["median_transaction"], 5.0)   # median ignores the 500
        self.assertGreater(d["per_transaction"], d["median_transaction"])

    def test_average_empty(self):
        out = self._main(["average"])
        self.assertIn("no expenses to average", out)

    def test_average_month_scope(self):
        self._main(["add", "30", "food", "a", "--date", "2026-01-10"])
        self._main(["add", "90", "rent", "b", "--date", "2026-02-10"])
        d = json.loads(self._main(["average", "--month", "2026-01", "--json"]))
        self.assertEqual(d["total"], 30.0)           # only January
        self.assertEqual(d["transactions"], 1)
        # a month with no spending is a clean message
        self.assertIn("no expenses to average for 2026-03",
                      self._main(["average", "--month", "2026-03"]))

    def test_top_json(self):
        for amt, cat in [(10, "food"), (500, "rent"), (30, "food"),
                         (200, "travel"), (5, "coffee")]:
            self._main(["add", str(amt), cat, "x"])
        self._main(["income", "9999", "salary", "y"])   # must be excluded
        d = json.loads(self._main(["top", "--limit", "3", "--json"]))
        self.assertEqual([e["amount"] for e in d], [500.0, 200.0, 30.0])
        self.assertTrue(all(e["category"] != "salary" for e in d))

    def test_top_category_filter(self):
        self._main(["add", "10", "food", "a"])
        self._main(["add", "40", "food", "b"])
        self._main(["add", "500", "rent", "c"])
        d = json.loads(self._main(["top", "--category", "food", "--json"]))
        self.assertEqual([e["amount"] for e in d], [40.0, 10.0])

    def test_daily_totals_pure(self):
        rows = [{"amount": 10.0, "date": "2026-01-01"},
                {"amount": 5.0, "date": "2026-01-01"},
                {"amount": 20.0, "date": "2026-01-02"}]
        agg = L.daily_totals(rows)
        self.assertEqual(agg["2026-01-01"], {"total": 15.0, "count": 2})
        self.assertEqual(agg["2026-01-02"], {"total": 20.0, "count": 1})
        self.assertEqual(L.daily_totals([]), {})

    def test_topdays(self):
        self._main(["add", "10", "food", "a", "--date", "2026-01-01"])
        self._main(["add", "5", "food", "b", "--date", "2026-01-01"])
        self._main(["add", "40", "rent", "c", "--date", "2026-01-02"])
        self._main(["income", "999", "salary", "d", "--date", "2026-01-03"])
        d = json.loads(self._main(["topdays", "--json"]))
        # ranked by daily expense total; income excluded by default
        self.assertEqual([r["date"] for r in d], ["2026-01-02", "2026-01-01"])
        self.assertEqual(d[0]["total"], 40.0)
        self.assertEqual(d[1]["count"], 2)
        # limit + text form
        one = json.loads(self._main(["topdays", "--limit", "1", "--json"]))
        self.assertEqual(len(one), 1)
        self.assertIn("spending day", self._main(["topdays"]))
        # empty scope is a clean message
        self.assertIn("no matching", self._main(["topdays", "--month", "1999-01"]))

    def test_top_date_and_amount_filters(self):
        self._main(["add", "5", "food", "a", "--date", "2026-01-01"])
        self._main(["add", "80", "food", "b", "--date", "2026-01-20"])
        self._main(["add", "900", "rent", "c", "--date", "2026-02-01"])
        # amount floor
        d = json.loads(self._main(["top", "--min", "50", "--json"]))
        self.assertEqual([e["amount"] for e in d], [900.0, 80.0])
        # date range narrows to January, still ranked by amount
        jan = json.loads(self._main(["top", "--since", "2026-01-01",
                                     "--until", "2026-01-31", "--json"]))
        self.assertEqual([e["amount"] for e in jan], [80.0, 5.0])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["top", "--min", "9", "--max", "1"])

    def test_payees_json(self):
        # two charges to "netflix", one to "spotify"; income excluded
        self._main(["add", "15.99", "ent", "netflix", "--date", "2026-01-05"])
        self._main(["add", "15.99", "ent", "netflix #fun", "--date", "2026-02-05"])
        self._main(["add", "9.99", "ent", "spotify", "--date", "2026-01-20"])
        self._main(["income", "3000", "salary", "payday"])   # must be excluded
        d = json.loads(self._main(["payees", "--json"]))
        self.assertEqual(d["count"], 2)                      # netflix, spotify
        by = {p["payee"]: p for p in d["payees"]}
        # #tags are stripped so both netflix charges group together
        self.assertEqual(by["netflix"]["count"], 2)
        self.assertEqual(by["netflix"]["total"], 31.98)
        self.assertEqual(by["netflix"]["average"], 15.99)
        self.assertEqual(by["netflix"]["first"], "2026-01-05")
        self.assertEqual(by["netflix"]["last"], "2026-02-05")
        # ranked by total, so netflix comes before spotify
        self.assertEqual([p["payee"] for p in d["payees"]], ["netflix", "spotify"])
        self.assertNotIn("payday", by)                       # income left out

    def test_payees_limit_and_month(self):
        self._main(["add", "100", "a", "alpha", "--date", "2026-01-10"])
        self._main(["add", "50", "b", "beta", "--date", "2026-02-10"])
        # --limit caps the rows but the count reflects all payees
        d = json.loads(self._main(["payees", "--limit", "1", "--json"]))
        self.assertEqual(d["shown"], 1)
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["payees"][0]["payee"], "alpha")
        # --month scopes the data
        d2 = json.loads(self._main(["payees", "--month", "2026-02", "--json"]))
        self.assertEqual([p["payee"] for p in d2["payees"]], ["beta"])

    def test_payees_empty_category_fallback(self):
        # a blank note falls back to the (category) key
        self._main(["add", "12", "groceries", "", "--date", "2026-03-01"])
        d = json.loads(self._main(["payees", "--json"]))
        self.assertEqual(d["payees"][0]["payee"], "(groceries)")

    def test_trend_json(self):
        this = date.today().isoformat()[:7]
        last = L.add_months(date.today().replace(day=1), -1).isoformat()[:7]
        self._main(["add", "100", "food", "a", "--date", f"{last}-05"])
        self._main(["add", "150", "food", "b"])          # this month
        self._main(["add", "999", "rent", "c"])          # different category
        d = json.loads(self._main(["trend", "food", "--months", "2", "--json"]))
        self.assertEqual(d["category"], "food")
        self.assertEqual([m["month"] for m in d["months"]], [last, this])
        self.assertEqual([m["total"] for m in d["months"]], [100.0, 150.0])
        self.assertEqual(d["total"], 250.0)
        self.assertEqual(d["average"], 125.0)

    def test_trend_empty(self):
        out = self._main(["trend", "food", "--months", "3"])
        self.assertIn("no spending on [food]", out)

    def test_compare_json(self):
        self._main(["add", "100", "food", "a", "--date", "2026-07-05"])
        self._main(["add", "50", "food", "b", "--date", "2026-08-05"])
        self._main(["add", "30", "transit", "c", "--date", "2026-08-06"])
        self._main(["income", "1000", "salary", "p", "--date", "2026-08-10"])
        d = json.loads(self._main(["compare", "2026-07", "2026-08", "--json"]))
        self.assertEqual((d["a"], d["b"]), ("2026-07", "2026-08"))
        self.assertEqual(d["a_totals"]["spending"], 100.0)
        self.assertEqual(d["b_totals"]["spending"], 80.0)
        self.assertEqual(d["b_totals"]["income"], 1000.0)
        self.assertEqual(d["b_totals"]["net"], 920.0)
        self.assertEqual(d["by_category"]["food"]["delta"], -50.0)
        self.assertEqual(d["by_category"]["transit"], {"a": 0.0, "b": 30.0,
                                                       "delta": 30.0})

    def test_compare_bad_month_exits(self):
        with self.assertRaises(SystemExit):
            self._main(["compare", "2026-13", "2026-08"])

    def test_sources_json(self):
        self._main(["income", "3000", "salary", "march"])
        self._main(["income", "2000", "salary", "april"])
        self._main(["income", "500", "freelance", "gig"])
        self._main(["add", "10", "food", "x"])       # expense excluded
        d = json.loads(self._main(["sources", "--json"]))
        self.assertEqual(d["salary"], {"count": 2, "total": 5000.0})
        self.assertEqual(d["freelance"]["total"], 500.0)
        self.assertNotIn("food", d)

    def test_sources_empty(self):
        self._main(["add", "10", "food", "x"])
        out = self._main(["sources"])
        self.assertIn("no income recorded", out)

    def test_categories_and_sources_have_bars(self):
        self._main(["add", "40", "food", "a"])
        self._main(["add", "10", "transit", "b"])
        self._main(["income", "3000", "salary", "c"])
        self._main(["income", "500", "freelance", "d"])
        cat_out = self._main(["categories"])
        food_line = next(l for l in cat_out.splitlines() if l.startswith("food"))
        self.assertIn("#" * 14, food_line)          # largest category: full bar
        src_out = self._main(["sources"])
        sal_line = next(l for l in src_out.splitlines() if l.startswith("salary"))
        self.assertIn("#" * 14, sal_line)           # largest source: full bar

    def test_untagged_json(self):
        self._main(["add", "10", "food", "tagged #x"])
        self._main(["add", "20", "food", "plain lunch"])
        self._main(["income", "100", "salary", "no tags here"])  # excluded (income)
        d = json.loads(self._main(["untagged", "--json"]))
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["note"], "plain lunch")

    def test_untagged_none(self):
        self._main(["add", "10", "food", "tagged #x"])
        out = self._main(["untagged"])
        self.assertIn("no untagged expenses", out)

    def test_tags_json(self):
        self._main(["add", "40", "food", "dinner #work #client"])
        self._main(["add", "10", "transit", "cab #work"])
        self._main(["add", "5", "food", "coffee"])           # no tags
        d = json.loads(self._main(["tags", "--json"]))
        self.assertEqual(d["work"], {"count": 2, "total": 50.0})
        self.assertEqual(d["client"], {"count": 1, "total": 40.0})
        self.assertNotIn("", d)

    def test_tags_empty(self):
        self._main(["add", "5", "food", "no tags here"])
        out = self._main(["tags"])
        self.assertIn("no tags yet", out)

    def test_tags_profile_tolerates_spaced_hash_name(self):
        # the leading-space + '#' case the old inline parse mishandled
        self._main(["add", "40", "food", "dinner #work"])
        d = json.loads(self._main(["tags", " #Work ", "--json"]))
        self.assertEqual(d["tag"], "work")
        self.assertEqual(d["count"], 1)

    def test_tags_text_has_bar(self):
        self._main(["add", "40", "food", "dinner #work"])
        self._main(["add", "10", "food", "cab #errand"])
        out = self._main(["tags"])
        self.assertIn("#work", out)
        self.assertIn("#", out)          # bar chart present
        # the largest tag's bar is full width
        work_line = next(l for l in out.splitlines() if l.startswith("#work"))
        self.assertIn("#" * 16, work_line)

    def test_tag_profile(self):
        # #work spans two categories; coffee is untagged (share denominator)
        self._main(["add", "40", "food", "dinner #work", "--date", "2026-07-02"])
        self._main(["add", "60", "travel", "cab #work", "--date", "2026-08-03"])
        self._main(["add", "100", "food", "coffee run"])      # untagged
        d = json.loads(self._main(["tags", "work", "--json"]))
        self.assertEqual(d["tag"], "work")
        self.assertEqual(d["total"], 100.0)              # 40 + 60
        self.assertEqual(d["count"], 2)
        self.assertEqual(d["average"], 50.0)
        self.assertEqual(d["median"], 50.0)
        self.assertEqual(d["share_pct"], 50.0)           # 100 of 200 all spending
        self.assertEqual(d["min"]["amount"], 40.0)
        self.assertEqual(d["max"]["amount"], 60.0)
        self.assertEqual(d["active_months"], 2)
        # the distinctive tag view: the categories it spans, biggest first
        self.assertEqual(d["by_category"],
                         [{"category": "travel", "total": 60.0},
                          {"category": "food", "total": 40.0}])
        # accepts a leading '#', and reports a clean miss for an unused tag
        self.assertEqual(json.loads(self._main(["tags", "#work", "--json"]))["total"],
                         100.0)
        miss = json.loads(self._main(["tags", "ghost", "--json"]))
        self.assertEqual((miss["count"], miss["by_category"]), (0, []))
        self.assertIn("no entries tagged #ghost", self._main(["tags", "ghost"]))

    def test_upcoming_forecasts_future_expenses(self):
        start = (date.today() - timedelta(days=1)).isoformat()
        self._main(["recur", "add", "10", "coffee", "--every", "day",
                    "--start", start])
        d = json.loads(self._main(["upcoming", "--days", "3", "--json"]))
        self.assertEqual(len(d["items"]), 3)          # next 3 days
        self.assertEqual(d["expense_total"], 30.0)
        self.assertEqual(d["net"], -30.0)
        self.assertTrue(all(i["date"] > date.today().isoformat()
                            for i in d["items"]))

    def test_upcoming_income_net(self):
        start = date.today().isoformat()
        self._main(["recur", "add", "100", "salary", "--every", "day",
                    "--income", "--start", start])
        d = json.loads(self._main(["upcoming", "--days", "2", "--json"]))
        self.assertEqual(d["income_total"], 200.0)
        self.assertEqual(d["net"], 200.0)

    def test_upcoming_empty(self):
        out = self._main(["upcoming"])
        self.assertIn("nothing scheduled", out)

    def test_bills_month_schedule(self):
        self._main(["recur", "add", "1200", "rent", "flat", "--every", "month",
                    "--start", "2026-01-10"])
        self._main(["recur", "add", "3000", "salary", "pay", "--every", "month",
                    "--income", "--start", "2026-01-01"])
        d = json.loads(self._main(["bills", "--month", "2026-07", "--json"]))
        self.assertEqual(d["month"], "2026-07")
        when = {i["category"]: i["date"] for i in d["items"]}
        self.assertEqual(when["rent"], "2026-07-10")
        self.assertEqual(when["salary"], "2026-07-01")
        self.assertEqual(d["expense_total"], 1200.0)
        self.assertEqual(d["income_total"], 3000.0)
        self.assertEqual(d["net"], 1800.0)
        # July 2026 is in the past (today is later), so every item has occurred
        self.assertTrue(all(i["occurred"] for i in d["items"]))
        # sorted by date: salary (01) before rent (10)
        self.assertEqual([i["category"] for i in d["items"]], ["salary", "rent"])

    def test_bills_excludes_skipped_and_empty(self):
        self._main(["recur", "add", "50", "gym", "--every", "month",
                    "--start", "2026-02-05"])                     # rule id 1
        self._main(["recur", "skip", "1", "--date", "2026-07-05"])
        d = json.loads(self._main(["bills", "--month", "2026-07", "--json"]))
        self.assertEqual(d["items"], [])
        self.assertIn("no recurring bills", self._main(["bills", "--month", "2026-07"]))

    def test_upcoming_ics_writes_file(self):
        self._main(["recur", "add", "10", "coffee", "latte", "--every", "day",
                    "--start", date.today().isoformat()])
        out = self._main(["upcoming", "--days", "3", "--ics", "bills.ics"])
        self.assertIn("wrote", out)
        path = os.path.join(L.EXPORT_DIR, "bills.ics")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("BEGIN:VCALENDAR", text)
        self.assertIn("BEGIN:VEVENT", text)
        self.assertIn("latte", text)

    def test_upcoming_ics_default_name_and_sandbox(self):
        self._main(["recur", "add", "5", "snack", "--every", "day",
                    "--start", date.today().isoformat()])
        # bare --ics uses the default filename
        self._main(["upcoming", "--days", "2", "--ics"])
        self.assertTrue(os.path.exists(os.path.join(L.EXPORT_DIR, "upcoming.ics")))
        # a path-traversal filename is reduced to its basename inside the folder
        self._main(["upcoming", "--days", "2", "--ics", "../escape.ics"])
        parent = os.path.dirname(L.EXPORT_DIR)
        self.assertFalse(os.path.exists(os.path.join(parent, "escape.ics")))
        self.assertTrue(os.path.exists(os.path.join(L.EXPORT_DIR, "escape.ics")))

    def test_completion_bash(self):
        out = self._main(["completion", "bash"])
        self.assertIn("complete -F _ledgerling ledgerling", out)
        self.assertIn("income)", out)       # a subcommand case arm exists
        self.assertIn("--income", out)      # an option is completed

    def test_completion_zsh(self):
        out = self._main(["completion", "zsh"])
        self.assertIn("#compdef ledgerling", out)
        self.assertIn("compdef _ledgerling ledgerling", out)

    def test_completion_default_is_bash(self):
        self.assertEqual(self._main(["completion"]), self._main(["completion", "bash"]))

    def test_weekday_json(self):
        self._main(["add", "10", "food", "a", "--date", "2026-06-01"])
        self._main(["add", "20", "food", "b", "--date", "2026-06-08"])  # +7d, same wd
        self._main(["add", "5", "food", "c", "--date", "2026-06-02"])   # next day
        d = json.loads(self._main(["weekday", "--json"]))
        self.assertEqual(len(d["weekdays"]), 7)
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        same = names[date(2026, 6, 1).weekday()]
        other = names[date(2026, 6, 2).weekday()]
        wds = {w["day"]: w for w in d["weekdays"]}
        self.assertEqual(wds[same]["total"], 30.0)
        self.assertEqual(wds[same]["count"], 2)
        self.assertEqual(wds[same]["average"], 15.0)
        self.assertEqual(wds[other]["total"], 5.0)

    def test_streak_json(self):
        self._main(["add", "10", "food", "a", "--date", "2026-06-01"])
        self._main(["add", "10", "food", "b", "--date", "2026-06-03"])
        d = json.loads(self._main(["streak", "--month", "2026-06", "--json"]))
        self.assertEqual(d["days_considered"], 30)     # past month = full
        self.assertEqual(d["spend_days"], 2)
        self.assertEqual(d["no_spend_days"], 28)
        self.assertEqual(d["longest_no_spend"], 27)    # days 4..30
        self.assertEqual(d["current_no_spend"], 27)

    def test_forecast_json(self):
        self._main(["add", "100", "food", "a"])       # today (current year)
        self._main(["income", "500", "salary", "b"])
        d = json.loads(self._main(["forecast", "--json"]))
        self.assertEqual(d["year"], date.today().year)
        self.assertEqual(d["spending"], 100.0)
        self.assertEqual(d["income"], 500.0)
        self.assertEqual(d["net"], 400.0)
        # projections extrapolate forward, so never below the amount so far
        self.assertGreaterEqual(d["projected_spending"], 100.0)
        self.assertGreaterEqual(d["projected_income"], 500.0)
        self.assertIn("day_of_year", d)

    def test_quarter_json(self):
        self._main(["add", "100", "food", "a", "--date", "2026-02-15"])   # Q1
        self._main(["add", "200", "food", "b", "--date", "2026-05-15"])   # Q2
        self._main(["income", "1000", "salary", "c", "--date", "2026-02-20"])  # Q1
        self._main(["add", "50", "food", "d", "--date", "2025-02-01"])    # other yr
        d = json.loads(self._main(["quarter", "2026", "--json"]))
        self.assertEqual(d["year"], 2026)
        self.assertEqual(len(d["quarters"]), 4)
        q = d["quarters"]
        self.assertEqual((q[0]["spending"], q[0]["income"], q[0]["net"]),
                         (100.0, 1000.0, 900.0))
        self.assertEqual(q[1]["spending"], 200.0)
        self.assertEqual(q[3]["spending"], 0.0)

    def test_balance_json(self):
        self._main(["income", "1000", "salary", "a", "--date", "2026-01-10"])
        self._main(["add", "300", "food", "b", "--date", "2026-01-15"])   # Jan net +700
        self._main(["add", "200", "food", "c", "--date", "2026-02-05"])   # Feb net -200
        d = json.loads(self._main(["balance", "--json"]))
        self.assertEqual(d["months"], [
            {"month": "2026-01", "net": 700.0, "balance": 700.0},
            {"month": "2026-02", "net": -200.0, "balance": 500.0},
        ])

    def test_commitments_json(self):
        self._main(["recur", "add", "1200", "rent", "flat",
                    "--every", "month", "--start", "2026-01-01"])
        self._main(["recur", "add", "70", "food", "groceries",
                    "--every", "week", "--start", "2026-01-01"])
        self._main(["recur", "add", "3000", "salary", "pay",
                    "--every", "month", "--start", "2026-01-01", "--income"])
        d = json.loads(self._main(["commitments", "--json"]))
        self.assertEqual(d["monthly_income"], 3000.0)
        # rent 1200/mo + groceries 70*52/12 = 303.33
        self.assertEqual(d["monthly_expense"], round(1200 + 70 * 52 / 12, 2))
        self.assertEqual(d["monthly_net"],
                         round(3000 - (1200 + 70 * 52 / 12), 2))
        self.assertEqual(d["annual_income"], round(3000 * 12, 2))
        self.assertEqual(len(d["rules"]), 3)

    def test_subscriptions_json_and_text(self):
        for d in ("2026-01-10", "2026-02-10", "2026-03-10"):
            self._main(["add", "9.99", "ent", "spotify", "--date", d])
        d = json.loads(self._main(["subscriptions", "--json"]))
        self.assertEqual(d["count"], 1)
        s = d["subscriptions"][0]
        self.assertEqual(s["payee"], "spotify")
        self.assertEqual(s["cadence"], "monthly")
        self.assertEqual(d["monthly"], 9.99)
        self.assertEqual(d["annual"], round(9.99 * 12, 2))
        # text mode names the payee and the per-year total
        out = self._main(["subscriptions"])
        self.assertIn("spotify", out)
        self.assertIn("monthly", out)

    def test_subscriptions_json_empty(self):
        d = json.loads(self._main(["subscriptions", "--json"]))
        self.assertEqual(d, {"subscriptions": [], "count": 0,
                             "monthly": 0.0, "annual": 0.0})

    def test_savings_json(self):
        self._main(["income", "1000", "salary", "a", "--date", "2026-01-10"])
        self._main(["add", "250", "food", "b", "--date", "2026-01-15"])   # Jan 75%
        self._main(["add", "400", "food", "c", "--date", "2026-02-05"])   # Feb no income
        d = json.loads(self._main(["savings", "--json"]))
        self.assertEqual(d["months"], [
            {"month": "2026-01", "income": 1000.0, "spending": 250.0,
             "net": 750.0, "rate": 75.0},
            {"month": "2026-02", "income": 0.0, "spending": 400.0,
             "net": -400.0, "rate": None},
        ])

    def test_dedupe_dry_run_then_apply(self):
        for _ in range(3):
            self._main(["add", "10", "food", "lunch", "--date", "2026-01-05"])
        self._main(["add", "10", "food", "other", "--date", "2026-01-05"])  # unique
        # dry run: reports ids to remove but changes nothing
        d = json.loads(self._main(["dedupe", "--dry-run", "--json"]))
        self.assertEqual(d["removed"], [2, 3])
        self.assertTrue(d["dry_run"])
        self.assertEqual(len(json.loads(self._main(["list", "--json"]))), 4)
        # apply: removes the two extras, keeping the lowest id (#1)
        d = json.loads(self._main(["dedupe", "--json"]))
        self.assertEqual(d["removed"], [2, 3])
        ids = sorted(e["id"] for e in json.loads(self._main(["list", "--json"])))
        self.assertEqual(ids, [1, 4])
        # undoable
        self._main(["undo"])
        self.assertEqual(len(json.loads(self._main(["list", "--json"]))), 4)

    def test_dedupe_none(self):
        self._main(["add", "5", "food", "a", "--date", "2026-01-01"])
        self._main(["add", "6", "food", "b", "--date", "2026-01-01"])
        d = json.loads(self._main(["dedupe", "--json"]))
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["count"], 0)

    def test_heatmap_json(self):
        self._main(["add", "10", "food", "a", "--date", "2026-03-01"])
        self._main(["add", "5", "food", "b", "--date", "2026-03-01"])
        self._main(["add", "40", "food", "c", "--date", "2026-03-15"])
        self._main(["income", "999", "salary", "d", "--date", "2026-03-10"])  # excluded
        d = json.loads(self._main(["heatmap", "--month", "2026-03", "--json"]))
        self.assertEqual(d["month"], "2026-03")
        self.assertEqual(len(d["days"]), 31)          # March
        self.assertEqual(d["total"], 55.0)            # income excluded
        self.assertEqual(d["max"], 40.0)
        self.assertEqual(d["busiest"], {"date": "2026-03-15", "spending": 40.0})
        d1 = next(x for x in d["days"] if x["date"] == "2026-03-01")
        self.assertEqual(d1["spending"], 15.0)

    def test_suggest_json(self):
        this = date.today().isoformat()[:7]
        self._main(["add", "80", "food", "a", "--date", f"{this}-05"])
        self._main(["add", "40", "food", "b", "--date", f"{this}-06"])
        self._main(["budget", "--category", "food", "--amount", "100"])
        d = json.loads(self._main(["suggest", "--months", "1", "--json"]))
        s = next(x for x in d["suggestions"] if x["category"] == "food")
        self.assertEqual(s["average"], 120.0)   # one active month, 80 + 40
        self.assertEqual(s["current"], 100.0)
        # suggested rounds average*1.1 up to a friendly step, so >= average
        self.assertGreaterEqual(s["suggested"], s["average"])

    def test_insights_json(self):
        self._main(["income", "2000", "salary", "pay", "--date", "2026-04-01"])
        self._main(["add", "300", "food", "groceries", "--date", "2026-04-05"])
        self._main(["add", "100", "food", "dining", "--date", "2026-04-06"])
        self._main(["add", "50", "transit", "metro", "--date", "2026-04-07"])
        self._main(["add", "200", "food", "prev", "--date", "2026-03-10"])  # last month
        self._main(["budget", "--category", "food", "--amount", "250"])     # will be over
        d = json.loads(self._main(["insights", "--month", "2026-04", "--json"]))
        self.assertEqual(d["month"], "2026-04")
        self.assertEqual(d["metrics"],
                         {"income": 2000.0, "spending": 450.0, "net": 1550.0,
                          "prev_spending": 200.0})
        blob = " ".join(d["insights"])
        self.assertIn("saved", blob)                 # savings insight
        self.assertIn("biggest category", blob)      # top category (food)
        self.assertIn("Over budget on food", blob)   # budget breach
        self.assertIn("Largest expense", blob)       # biggest single expense
        self.assertIn("up", blob)                    # up vs last month

    def test_insights_empty_month(self):
        d = json.loads(self._main(["insights", "--month", "2020-01", "--json"]))
        self.assertEqual(d["insights"], ["Nothing recorded for 2020-01 yet."])

    def test_scorecard_strong_month(self):
        # 25% saved, only budgeted category on track, many no-spend days.
        self._main(["income", "2000", "salary", "pay", "--date", "2026-04-01"])
        self._main(["add", "500", "food", "groceries", "--date", "2026-04-05"])
        self._main(["budget", "--category", "food", "--amount", "600"])
        d = json.loads(self._main(["scorecard", "--month", "2026-04", "--json"]))
        self.assertEqual(d["month"], "2026-04")
        comp = {c["name"]: c for c in d["components"]}
        # 25% savings rate exceeds the 20% full-marks threshold.
        self.assertEqual(comp["Savings rate"]["points"], 40)
        # The single budget is on track -> full budget-adherence credit.
        self.assertEqual(comp["Budget adherence"]["points"], 35)
        # One spend day in a 30-day month -> plenty of no-spend days -> full.
        self.assertEqual(comp["Spending habits"]["points"], 25)
        self.assertEqual(d["score"], 100)
        self.assertEqual(d["grade"], "A")
        self.assertEqual(len(d["tips"]), 1)

    def test_scorecard_overspent_month(self):
        # Spent more than earned and blew the budget -> failing grade.
        self._main(["income", "1000", "salary", "pay", "--date", "2026-05-01"])
        for day in range(2, 20):
            self._main(["add", "100", "food", f"d{day}", "--date", f"2026-05-{day:02d}"])
        self._main(["budget", "--category", "food", "--amount", "200"])
        d = json.loads(self._main(["scorecard", "--month", "2026-05", "--json"]))
        comp = {c["name"]: c for c in d["components"]}
        self.assertEqual(comp["Savings rate"]["points"], 0)   # negative net
        self.assertEqual(comp["Budget adherence"]["points"], 0)  # way over
        self.assertLess(d["score"], 60)
        self.assertEqual(d["grade"], "F")

    def test_scorecard_empty_month(self):
        d = json.loads(self._main(["scorecard", "--month", "2020-02", "--json"]))
        self.assertEqual(d["grade"], "-")
        self.assertEqual(d["score"], 0)
        txt = self._main(["scorecard", "--month", "2020-02"])
        self.assertIn("Nothing recorded", txt)

    def test_mascot_moods(self):
        # pure mood mapping from a score result
        self.assertEqual(L._mascot_for({"has_data": True, "score": 95,
                                        "grade": "A"})["mood"], "ecstatic")
        self.assertEqual(L._mascot_for({"has_data": True, "score": 72,
                                        "grade": "C"})["mood"], "content")
        self.assertEqual(L._mascot_for({"has_data": True, "score": 10,
                                        "grade": "F"})["mood"], "sad")
        sleepy = L._mascot_for({"has_data": False, "score": 0, "grade": "-"})
        self.assertEqual(sleepy["mood"], "sleepy")
        self.assertIsNone(sleepy["score"])
        # strong month -> happy/ecstatic mascot, empty month -> sleepy
        self._main(["income", "3000", "salary", "p", "--date", "2026-04-01"])
        self._main(["add", "100", "food", "g", "--date", "2026-04-02"])
        d = json.loads(self._main(["mascot", "--month", "2026-04", "--json"]))
        self.assertIn(d["mood"], ("ecstatic", "happy"))
        self.assertEqual(d["grade"], "A")
        e = json.loads(self._main(["mascot", "--month", "2020-01", "--json"]))
        self.assertEqual(e["mood"], "sleepy")

    def test_scoretrend_window_and_average(self):
        # Build two strong months, then chart a window that includes them plus
        # an empty leading month. The window ends at the current month, so we
        # record into the two months immediately before it.
        prev1 = L.add_months(date.today().replace(day=1), -1).isoformat()[:7]
        prev2 = L.add_months(date.today().replace(day=1), -2).isoformat()[:7]
        for m in (prev1, prev2):
            self._main(["income", "2000", "salary", "pay", "--date", f"{m}-01"])
            self._main(["add", "100", "food", "g", "--date", f"{m}-02"])
        d = json.loads(self._main(["scoretrend", "--months", "3", "--json"]))
        self.assertEqual(len(d["months"]), 3)
        by = {r["month"]: r for r in d["months"]}
        # Both recorded months: 95% saved (40) + neutral budgets (24.5) + lots
        # of no-spend days (25) = 89.5 -> 90, grade A.
        self.assertEqual(by[prev1]["grade"], "A")
        self.assertEqual(by[prev2]["grade"], "A")
        self.assertEqual(by[prev1]["score"], 90)
        # Average counts only the two active months.
        self.assertEqual(d["average_grade"], "A")
        self.assertEqual(d["average_score"], 90.0)

    def test_scoretrend_empty_window(self):
        d = json.loads(self._main(["scoretrend", "--months", "2", "--json"]))
        self.assertEqual(len(d["months"]), 2)
        self.assertIsNone(d["average_score"])
        self.assertEqual(d["average_grade"], "-")
        self.assertTrue(all(not r["has_data"] for r in d["months"]))

    def test_tagtrend_json(self):
        this = date.today().isoformat()[:7]
        prev = L.add_months(date.today().replace(day=1), -1).isoformat()[:7]
        self._main(["add", "40", "food", "lunch #work", "--date", f"{this}-05"])
        self._main(["add", "60", "travel", "cab #work", "--date", f"{this}-06"])
        self._main(["add", "25", "food", "lunch #work", "--date", f"{prev}-05"])
        self._main(["add", "99", "food", "personal", "--date", f"{this}-07"])  # untagged
        d = json.loads(self._main(["tagtrend", "work", "--months", "2", "--json"]))
        self.assertEqual(d["tag"], "work")
        self.assertEqual(len(d["months"]), 2)
        by = {m["month"]: m["total"] for m in d["months"]}
        self.assertEqual(by[this], 100.0)   # 40 + 60, spans categories
        self.assertEqual(by[prev], 25.0)
        self.assertEqual(d["total"], 125.0)

    def test_range_json(self):
        self._main(["add", "10", "food", "a", "--date", "2026-05-01"])   # before? no, in
        self._main(["add", "20", "food", "b", "--date", "2026-05-10"])
        self._main(["add", "30", "transit", "c", "--date", "2026-05-20"])
        self._main(["income", "500", "salary", "d", "--date", "2026-05-15"])
        self._main(["add", "99", "food", "out", "--date", "2026-06-01"])  # out of range
        d = json.loads(self._main(["range", "2026-05-05", "2026-05-25", "--json"]))
        self.assertEqual((d["start"], d["end"], d["days"]),
                         ("2026-05-05", "2026-05-25", 21))
        self.assertEqual(d["spending"], 50.0)   # 20 + 30 (10 is before start)
        self.assertEqual(d["income"], 500.0)
        self.assertEqual(d["net"], 450.0)
        self.assertEqual(d["by_category"], {"transit": 30.0, "food": 20.0})

    def test_range_swaps_reversed_dates(self):
        self._main(["add", "12", "food", "a", "--date", "2026-05-10"])
        d = json.loads(self._main(["range", "2026-05-31", "2026-05-01", "--json"]))
        self.assertEqual((d["start"], d["end"]), ("2026-05-01", "2026-05-31"))
        self.assertEqual(d["spending"], 12.0)

    def test_matrix_json(self):
        this = date.today().isoformat()[:7]
        prev = L.add_months(date.today().replace(day=1), -1).isoformat()[:7]
        self._main(["add", "40", "food", "a", "--date", f"{this}-05"])
        self._main(["add", "10", "food", "b", "--date", f"{prev}-05"])
        self._main(["add", "25", "transit", "c", "--date", f"{this}-06"])
        d = json.loads(self._main(["matrix", "--months", "2", "--json"]))
        self.assertEqual(d["months"], [prev, this])
        food = next(r for r in d["rows"] if r["category"] == "food")
        self.assertEqual((food[prev], food[this], food["total"]), (10.0, 40.0, 50.0))
        transit = next(r for r in d["rows"] if r["category"] == "transit")
        self.assertEqual((transit[prev], transit[this]), (0.0, 25.0))
        self.assertEqual(d["totals"], {prev: 10.0, this: 65.0})
        self.assertEqual(d["total"], 75.0)
        # food sorts first (larger total)
        self.assertEqual(d["rows"][0]["category"], "food")

    def test_tag_and_untag(self):
        self._main(["add", "10", "food", "lunch", "--date", "2026-05-01"])
        # add two tags
        self._main(["tag", "1", "work", "#reimbursable"])
        e = json.loads(self._main(["list", "--json"]))[0]
        self.assertEqual(e["tags"], ["reimbursable", "work"])
        self.assertIn("#work", e["note"])
        self.assertIn("#reimbursable", e["note"])
        # adding an existing tag is a no-op (still there, no duplicate)
        self._main(["tag", "1", "work"])
        e = json.loads(self._main(["list", "--json"]))[0]
        self.assertEqual(e["note"].count("#work"), 1)
        # remove one tag, note text cleaned up
        self._main(["untag", "1", "work"])
        e = json.loads(self._main(["list", "--json"]))[0]
        self.assertEqual(e["tags"], ["reimbursable"])
        self.assertNotIn("#work", e["note"])
        self.assertEqual(e["note"], "lunch #reimbursable")

    def test_tag_unknown_id_errors(self):
        with self.assertRaises(SystemExit):
            self._main(["tag", "999", "work"])

    def test_weekly_json(self):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        this_wk = monday.isoformat()
        last_wk = (monday - timedelta(weeks=1)).isoformat()
        self._main(["add", "20", "food", "a", "--date", this_wk])
        self._main(["add", "15", "food", "b",
                    "--date", (monday - timedelta(weeks=1)).isoformat()])
        self._main(["income", "500", "salary", "c", "--date", this_wk])  # excluded
        d = json.loads(self._main(["weekly", "--weeks", "2", "--json"]))
        self.assertEqual([w["week_start"] for w in d["weeks"]], [last_wk, this_wk])
        by = {w["week_start"]: w["total"] for w in d["weeks"]}
        self.assertEqual(by[this_wk], 20.0)     # income excluded
        self.assertEqual(by[last_wk], 15.0)
        self.assertEqual(d["total"], 35.0)

    def test_top_income_scope(self):
        self._main(["add", "100", "food", "a", "--date", "2026-05-01"])
        self._main(["income", "3000", "salary", "b", "--date", "2026-05-02"])
        self._main(["income", "200", "gift", "c", "--date", "2026-05-03"])
        # default: expenses only
        d = json.loads(self._main(["top", "--json"]))
        self.assertEqual([e["amount"] for e in d], [100.0])
        # income only, largest first
        d = json.loads(self._main(["top", "--income", "--json"]))
        self.assertEqual([e["amount"] for e in d], [3000.0, 200.0])
        # all, largest first
        d = json.loads(self._main(["top", "--all", "--json"]))
        self.assertEqual([e["amount"] for e in d], [3000.0, 200.0, 100.0])

    def test_search_sort(self):
        self._main(["add", "10", "food", "a", "--date", "2026-05-03"])
        self._main(["add", "80", "food", "b", "--date", "2026-05-01"])
        self._main(["add", "30", "food", "c", "--date", "2026-05-02"])
        # default: date ascending
        d = json.loads(self._main(["search", "--json"]))
        self.assertEqual([e["date"] for e in d],
                         ["2026-05-01", "2026-05-02", "2026-05-03"])
        # amount descending
        d = json.loads(self._main(["search", "--sort", "amount", "--desc", "--json"]))
        self.assertEqual([e["amount"] for e in d], [80.0, 30.0, 10.0])

    def test_search_regex(self):
        self._main(["add", "10", "food", "coffee at cafe", "--date", "2026-05-01"])
        self._main(["add", "20", "food", "tea at cafe", "--date", "2026-05-02"])
        self._main(["add", "30", "rent", "flat", "--date", "2026-05-03"])
        # regex alternation matches either note
        d = json.loads(self._main(["search", "coffee|tea", "--regex", "--json"]))
        self.assertEqual(sorted(e["amount"] for e in d), [10.0, 20.0])
        # anchored regex on category
        r = json.loads(self._main(["search", "^rent$", "--regex", "--json"]))
        self.assertEqual([e["category"] for e in r], ["rent"])
        # without --regex, the pipe is a literal substring (matches nothing here)
        self.assertEqual(json.loads(self._main(["search", "coffee|tea", "--json"])), [])
        # an invalid regex is a clean error
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                L.main(["search", "(unclosed", "--regex"])

    def test_sources_month_scope(self):
        self._main(["income", "3000", "salary", "a", "--date", "2026-05-01"])
        self._main(["income", "200", "freelance", "b", "--date", "2026-05-15"])
        self._main(["income", "999", "salary", "c", "--date", "2026-06-01"])
        d = json.loads(self._main(["sources", "--month", "2026-05", "--json"]))
        self.assertEqual(set(d), {"salary", "freelance"})
        self.assertEqual(d["salary"]["total"], 3000.0)   # June salary excluded
        self.assertEqual(d["freelance"]["total"], 200.0)

    def test_categories_and_tags_month_scope(self):
        self._main(["add", "40", "food", "a #work", "--date", "2026-05-05"])
        self._main(["add", "10", "food", "b", "--date", "2026-05-06"])
        self._main(["add", "99", "transit", "c #work", "--date", "2026-06-01"])
        cats = json.loads(self._main(["categories", "--month", "2026-05", "--json"]))
        self.assertEqual(set(cats), {"food"})            # transit is in June
        self.assertEqual(cats["food"]["total"], 50.0)
        self.assertEqual(cats["food"]["count"], 2)
        tags = json.loads(self._main(["tags", "--month", "2026-05", "--json"]))
        self.assertEqual(set(tags), {"work"})
        self.assertEqual(tags["work"]["total"], 40.0)    # June #work excluded
        # all-time still sees both months
        allcats = json.loads(self._main(["categories", "--json"]))
        self.assertEqual(set(allcats), {"food", "transit"})

    def test_autobudget_applies_and_is_undoable(self):
        this = date.today().isoformat()[:7]
        self._main(["add", "80", "food", "a", "--date", f"{this}-05"])
        self._main(["add", "40", "transit", "b", "--date", f"{this}-06"])
        # dry run changes nothing
        d = json.loads(self._main(["autobudget", "--months", "1", "--dry-run", "--json"]))
        self.assertTrue(d["dry_run"])
        self.assertEqual({r["category"] for r in d["set"]}, {"food", "transit"})
        self.assertEqual(L.load()["budgets"], {})
        # apply
        self._main(["autobudget", "--months", "1", "--json"])
        budgets = L.load()["budgets"]
        self.assertEqual(set(budgets), {"food", "transit"})
        self.assertGreaterEqual(budgets["food"], 80.0)   # >= average, rounded up
        # undoable
        self._main(["undo"])
        self.assertEqual(L.load()["budgets"], {})

    def test_autobudget_keeps_existing_without_replace(self):
        this = date.today().isoformat()[:7]
        self._main(["add", "80", "food", "a", "--date", f"{this}-05"])
        self._main(["budget", "--category", "food", "--amount", "500"])
        # without --replace, the existing budget is kept
        self._main(["autobudget", "--months", "1"])
        self.assertEqual(L.load()["budgets"]["food"], 500.0)
        # with --replace, it is overwritten by the suggestion
        self._main(["autobudget", "--months", "1", "--replace"])
        self.assertNotEqual(L.load()["budgets"]["food"], 500.0)

    def test_unbudget_removes_one_and_all(self):
        self._main(["budget", "--category", "food", "--amount", "200"])
        self._main(["budget", "--category", "rent", "--amount", "1200"])
        # remove one
        self._main(["unbudget", "food"])
        self.assertEqual(set(L.load()["budgets"]), {"rent"})
        # undoable
        self._main(["undo"])
        self.assertEqual(set(L.load()["budgets"]), {"food", "rent"})
        # removing an unknown budget errors
        with self.assertRaises(SystemExit):
            self._main(["unbudget", "nope"])
        # --all clears everything
        self._main(["unbudget", "--all"])
        self.assertEqual(L.load()["budgets"], {})

    def test_note_set_append_clear(self):
        self._main(["add", "10", "food", "lunch", "--date", "2026-05-01"])
        # set replaces
        self._main(["note", "1", "team lunch #work"])
        e = json.loads(self._main(["list", "--json"]))[0]
        self.assertEqual(e["note"], "team lunch #work")
        self.assertEqual(e["tags"], ["work"])
        # append adds on
        self._main(["note", "1", "#reimbursable", "--append"])
        e = json.loads(self._main(["list", "--json"]))[0]
        self.assertEqual(e["note"], "team lunch #work #reimbursable")
        self.assertEqual(e["tags"], ["reimbursable", "work"])
        # clear empties note and tags
        self._main(["note", "1", "--clear"])
        e = json.loads(self._main(["list", "--json"]))[0]
        self.assertEqual(e["note"], "")
        self.assertEqual(e["tags"], [])

    def test_note_view_and_conflicts(self):
        self._main(["add", "5", "food", "snack", "--date", "2026-05-01"])
        # viewing (no text) leaves it unchanged
        self._main(["note", "1"])
        self.assertEqual(json.loads(self._main(["list", "--json"]))[0]["note"], "snack")
        # text + --clear is an error
        with self.assertRaises(SystemExit):
            self._main(["note", "1", "x", "--clear"])

    def test_cumulative_json(self):
        self._main(["add", "10", "food", "a", "--date", "2026-05-02"])
        self._main(["add", "20", "food", "b", "--date", "2026-05-05"])
        self._main(["add", "30", "food", "c", "--date", "2026-05-05"])
        self._main(["income", "999", "salary", "d", "--date", "2026-05-05"])  # excluded
        d = json.loads(self._main(["cumulative", "--month", "2026-05", "--json"]))
        self.assertEqual(d["month"], "2026-05")
        self.assertEqual(len(d["days"]), 31)          # full past month
        self.assertEqual(d["total"], 60.0)            # income excluded
        by = {r["date"]: r for r in d["days"]}
        self.assertEqual(by["2026-05-02"]["cumulative"], 10.0)
        self.assertEqual(by["2026-05-04"]["cumulative"], 10.0)   # flat, no spend
        self.assertEqual(by["2026-05-05"]["spending"], 50.0)     # 20 + 30
        self.assertEqual(by["2026-05-05"]["cumulative"], 60.0)

    def test_allowance_json(self):
        this = date.today().isoformat()[:7]
        self._main(["budget", "--category", "food", "--amount", "300"])
        self._main(["budget", "--category", "transit", "--amount", "100"])
        self._main(["add", "120", "food", "a", "--date", f"{this}-02"])
        self._main(["add", "40", "transit", "b", "--date", f"{this}-03"])
        d = json.loads(self._main(["allowance", "--json"]))
        self.assertEqual(d["month"], this)
        self.assertEqual(d["categories"]["food"]["remaining"], 180.0)
        self.assertEqual(d["categories"]["transit"]["remaining"], 60.0)
        self.assertEqual(d["total_remaining"], 240.0)
        self.assertGreater(d["days_left"], 0)         # current month
        self.assertIsNotNone(d["daily_allowance"])

    def test_allowance_past_month_no_daily(self):
        self._main(["budget", "--category", "food", "--amount", "300"])
        self._main(["add", "50", "food", "a", "--date", "2020-01-10"])
        d = json.loads(self._main(["allowance", "--month", "2020-01", "--json"]))
        self.assertEqual(d["days_left"], 0)
        self.assertIsNone(d["daily_allowance"])
        self.assertEqual(d["categories"]["food"]["remaining"], 250.0)

    def test_refund_full_and_partial(self):
        self._main(["add", "50", "electronics", "cable", "--date", "2026-05-01"])
        # full refund creates an offsetting income for the same category
        self._main(["refund", "1", "--date", "2026-05-03"])
        rows = json.loads(self._main(["list", "--all", "--json"]))
        refund = next(r for r in rows if r["kind"] == "income")
        self.assertEqual(refund["amount"], 50.0)
        self.assertEqual(refund["category"], "electronics")
        self.assertIn("refund of #1", refund["note"])
        # net for the month is zero (50 spent, 50 refunded)
        m = json.loads(self._main(["month", "--month", "2026-05", "--json"]))
        self.assertEqual(m["net"], 0.0)
        # partial refund
        self._main(["add", "80", "food", "party", "--date", "2026-05-04"])
        self._main(["refund", "3", "--amount", "30"])
        rows = json.loads(self._main(["list", "--all", "--json"]))
        self.assertTrue(any(r["kind"] == "income" and r["amount"] == 30.0
                            for r in rows))

    def test_refund_rejects_income(self):
        self._main(["income", "100", "salary", "pay", "--date", "2026-05-01"])
        with self.assertRaises(SystemExit):
            self._main(["refund", "1"])

    def test_recur_skip_next_and_undo(self):
        self._main(["recur", "add", "15", "subscriptions", "music",
                    "--every", "month", "--start", "2026-01-01"])
        self._main(["recur", "skip", "1"])
        rule = L.load()["recurring"][0]
        self.assertEqual(len(rule["skips"]), 1)
        self.assertGreater(rule["skips"][0], date.today().isoformat())  # future
        # explicit date, and undo reverts the skip
        self._main(["recur", "skip", "1", "--date", "2027-01-01"])
        self.assertIn("2027-01-01", L.load()["recurring"][0]["skips"])
        self._main(["undo"])
        self.assertNotIn("2027-01-01", L.load()["recurring"][0]["skips"])

    def test_recur_from_entry(self):
        self._main(["add", "1200", "rent", "flat #home", "--date", "2026-01-01"])
        self._main(["recur", "from", "1", "--every", "month", "--start", "2026-01-01"])
        rules = L.load()["recurring"]
        self.assertEqual(len(rules), 1)
        r = rules[0]
        self.assertEqual((r["amount"], r["category"], r["every"], r["kind"]),
                         (1200.0, "rent", "month", "expense"))
        self.assertEqual(r["start"], "2026-01-01")
        # it catches up and generates the recurring rent entries (tags flow through)
        gen = [e for e in L.load()["expenses"] if e.get("recur_id") == r["id"]]
        self.assertTrue(gen)
        self.assertIn("home", gen[0]["tags"])

    def test_recur_from_defaults_start_to_entry_date(self):
        self._main(["income", "3000", "salary", "pay", "--date", "2026-02-01"])
        self._main(["recur", "from", "1", "--every", "month"])
        r = L.load()["recurring"][0]
        self.assertEqual(r["start"], "2026-02-01")   # defaults to the entry's date
        self.assertEqual(r["kind"], "income")

    def test_recur_pause_and_resume(self):
        self._main(["recur", "add", "15", "subscriptions", "music",
                    "--every", "month", "--start", "2026-01-01"])
        before = len(json.loads(self._main(["list", "--json"])))
        # pause: running catch-up generates nothing more
        self._main(["recur", "pause", "1"])
        self.assertTrue(L.load()["recurring"][0]["paused"])
        self._main(["recur", "run"])
        self.assertEqual(len(json.loads(self._main(["list", "--json"]))), before)
        # resume: no backfill of the paused gap (last advanced to today)
        self._main(["recur", "resume", "1"])
        self.assertFalse(L.load()["recurring"][0]["paused"])
        self.assertEqual(L.load()["recurring"][0]["last"], date.today().isoformat())
        self._main(["recur", "run"])
        self.assertEqual(len(json.loads(self._main(["list", "--json"]))), before)

    def test_recur_unskip(self):
        self._main(["recur", "add", "15", "subscriptions", "music",
                    "--every", "month", "--start", "2026-01-01"])
        self._main(["recur", "skip", "1", "--date", "2027-01-01"])
        self._main(["recur", "skip", "1", "--date", "2027-02-01"])
        self.assertEqual(len(L.load()["recurring"][0]["skips"]), 2)
        # unskip one date
        self._main(["recur", "unskip", "1", "--date", "2027-01-01"])
        self.assertEqual(L.load()["recurring"][0]["skips"], ["2027-02-01"])
        # unskipping a date that isn't skipped errors
        with self.assertRaises(SystemExit):
            self._main(["recur", "unskip", "1", "--date", "2099-01-01"])
        # --all clears the rest
        self._main(["recur", "unskip", "1", "--all"])
        self.assertEqual(L.load()["recurring"][0]["skips"], [])

    def test_config_symbol_position(self):
        out = self._main(["config", "--currency", "kr", "--symbol-position", "after"])
        self.assertIn("1,234.50 kr", out)          # sample formats symbol after
        # and it persists / applies to command output
        self._main(["add", "10", "food", "x", "--date", "2026-09-01"])
        self.assertIn("10.00 kr", self._main(["list"]))

    def test_clamp_ui_scale_pure(self):
        self.assertEqual(L._clamp_ui_scale(1.25), 1.25)
        self.assertEqual(L._clamp_ui_scale(1), 1.0)
        self.assertEqual(L._clamp_ui_scale(99), L.UI_SCALE_MAX)   # clamped high
        self.assertEqual(L._clamp_ui_scale(0.1), L.UI_SCALE_MIN)  # clamped low
        self.assertEqual(L._clamp_ui_scale(1.2345), 1.23)         # rounded 2dp

    def test_config_json(self):
        # reading: pure JSON of the current settings, no stray text
        out = self._main(["config", "--json"])
        cfg = json.loads(out)
        self.assertEqual(cfg["currency"], "$")
        self.assertIn("ui_scale", cfg)
        self.assertIn("fx", cfg)
        # writing + --json returns the updated settings as JSON (no "updated" line)
        out2 = self._main(["config", "--currency", "kr", "--json"])
        self.assertEqual(json.loads(out2)["currency"], "kr")
        self.assertNotIn("config updated", out2)

    def test_config_ui_scale(self):
        out = self._main(["config", "--ui-scale", "1.25"])
        self.assertIn("ui_scale", out)
        self.assertIn("1.25", out)
        # persists and is clamped on readback
        self.assertEqual(L.load_config()["ui_scale"], 1.25)
        self._main(["config", "--ui-scale", "50"])               # over max
        self.assertEqual(L.load_config()["ui_scale"], L.UI_SCALE_MAX)
        # default when unset
        self._main(["config", "--reset"])
        self.assertEqual(L.load_config()["ui_scale"], 1.0)
        # the desktop schema exposes it for the GUI to read
        from ledgerling import web
        self.assertEqual(web.describe()["ui_scale"], 1.0)

    def test_where_json(self):
        self._main(["add", "10", "food", "a"])   # creates the data file
        d = json.loads(self._main(["where", "--json"]))
        self.assertEqual(d["home"], L.HOME_DIR)
        self.assertTrue(d["items"]["data file"]["exists"])
        self.assertGreater(d["items"]["data file"]["bytes"], 0)
        self.assertEqual(d["items"]["backups"]["path"], L.BACKUP_DIR)
        # backups dir not created yet -> reported as not existing, not an error
        self.assertFalse(d["items"]["backups"]["exists"])

    def test_tagmatrix_json(self):
        this = date.today().isoformat()[:7]
        prev = L.add_months(date.today().replace(day=1), -1).isoformat()[:7]
        self._main(["add", "40", "food", "a #work", "--date", f"{this}-05"])
        self._main(["add", "60", "travel", "b #work", "--date", f"{this}-06"])
        self._main(["add", "25", "food", "c #work", "--date", f"{prev}-05"])
        self._main(["add", "10", "food", "d #fun", "--date", f"{this}-07"])
        d = json.loads(self._main(["tagmatrix", "--months", "2", "--json"]))
        self.assertEqual(d["months"], [prev, this])
        work = next(r for r in d["rows"] if r["tag"] == "work")
        self.assertEqual((work[prev], work[this], work["total"]), (25.0, 100.0, 125.0))
        self.assertEqual(d["rows"][0]["tag"], "work")   # sorted by total desc
        self.assertEqual(d["totals"][this], 110.0)      # 100 work + 10 fun

    def test_years_json(self):
        self._main(["add", "100", "food", "a", "--date", "2025-03-01"])
        self._main(["income", "500", "salary", "b", "--date", "2025-04-01"])
        self._main(["add", "200", "food", "c", "--date", "2026-01-01"])
        d = json.loads(self._main(["years", "--json"]))
        by = {r["year"]: r for r in d["years"]}
        self.assertEqual([r["year"] for r in d["years"]], ["2025", "2026"])
        self.assertEqual((by["2025"]["spending"], by["2025"]["income"],
                          by["2025"]["net"]), (100.0, 500.0, 400.0))
        self.assertEqual((by["2026"]["spending"], by["2026"]["net"]), (200.0, -200.0))

    def test_year_json(self):
        self._main(["add", "100", "food", "a", "--date", "2026-01-15"])
        self._main(["add", "200", "food", "b", "--date", "2026-03-10"])
        self._main(["income", "5000", "salary", "c", "--date", "2026-01-20"])
        self._main(["add", "999", "food", "d", "--date", "2025-12-01"])  # other yr
        d = json.loads(self._main(["year", "2026", "--json"]))
        self.assertEqual(d["year"], 2026)
        self.assertEqual(len(d["months"]), 12)
        self.assertEqual(d["spending"], 300.0)
        self.assertEqual(d["income"], 5000.0)
        self.assertEqual(d["net"], 4700.0)
        jan = next(m for m in d["months"] if m["month"] == "2026-01")
        self.assertEqual(jan["spending"], 100.0)

    def test_year_empty(self):
        out = self._main(["year", "1999"])
        self.assertIn("nothing recorded in 1999", out)

    def test_day_json(self):
        self._main(["add", "10", "food", "a", "--date", "2026-09-15"])
        self._main(["income", "100", "salary", "b", "--date", "2026-09-15"])
        self._main(["add", "5", "food", "c", "--date", "2026-09-16"])
        d = json.loads(self._main(["day", "--date", "2026-09-15", "--json"]))
        self.assertEqual(d["date"], "2026-09-15")
        self.assertEqual(len(d["entries"]), 2)
        self.assertEqual(d["spending"], 10.0)
        self.assertEqual(d["income"], 100.0)
        self.assertEqual(d["net"], 90.0)

    def test_day_empty(self):
        out = self._main(["day", "--date", "1999-01-01"])
        self.assertIn("no entries", out)

    def test_onthisday_flashback(self):
        # same day-of-month (the 2nd) across earlier months, plus noise
        self._main(["add", "10", "food", "a", "--date", "2026-03-02"])
        self._main(["add", "20", "food", "b", "--date", "2026-05-02"])
        self._main(["income", "99", "salary", "c", "--date", "2026-05-02"])
        self._main(["add", "7", "food", "other", "--date", "2026-05-09"])  # wrong day
        d = json.loads(self._main(["onthisday", "--date", "2026-06-02", "--json"]))
        self.assertEqual(d["day"], 2)
        self.assertEqual([m["month"] for m in d["months"]], ["2026-05", "2026-03"])
        self.assertEqual(d["entry_count"], 3)              # 2 expenses + 1 income
        self.assertEqual(d["total_spent"], 30.0)           # 10 + 20 (income excl.)
        may = next(m for m in d["months"] if m["month"] == "2026-05")
        self.assertEqual(may["spent"], 20.0)
        # the ref month itself is excluded (strictly earlier months only)
        d2 = json.loads(self._main(["onthisday", "--date", "2026-05-02", "--json"]))
        self.assertEqual([m["month"] for m in d2["months"]], ["2026-03"])

    def test_onthisday_empty(self):
        out = self._main(["onthisday", "--date", "2026-06-15"])
        self.assertIn("nothing recorded on the 15th", out)

    def test_week_json(self):
        monday = date.today() - timedelta(days=date.today().weekday())
        d0 = monday.isoformat()
        d1 = (monday + timedelta(days=1)).isoformat()
        self._main(["add", "10", "food", "a", "--date", d0])
        self._main(["add", "20", "food", "b", "--date", d1])
        self._main(["income", "100", "salary", "c", "--date", d0])
        d = json.loads(self._main(["week", "--json"]))
        self.assertEqual(len(d["days"]), 7)
        self.assertEqual(d["spending"], 30.0)
        self.assertEqual(d["income"], 100.0)
        self.assertEqual(d["net"], 70.0)
        self.assertEqual(d["start"], d0)

    def test_week_offset_excludes_this_week(self):
        self._main(["add", "50", "food", "now"])          # this week
        d = json.loads(self._main(["week", "--offset", "1", "--json"]))
        self.assertEqual(d["spending"], 0.0)              # last week is empty

    def test_month_dashboard_json(self):
        self._main(["income", "3000", "salary", "pay", "--date", "2026-05-01"])
        self._main(["add", "1200", "rent", "flat", "--date", "2026-05-02"])
        self._main(["add", "300", "food", "groceries", "--date", "2026-05-10"])
        self._main(["budget", "--category", "food", "--amount", "400"])
        self._main(["goal", "--amount", "1000"])
        d = json.loads(self._main(["month", "--month", "2026-05", "--json"]))
        self.assertEqual(d["income"], 3000.0)
        self.assertEqual(d["spending"], 1500.0)
        self.assertEqual(d["net"], 1500.0)
        self.assertEqual(d["expense_count"], 2)
        self.assertEqual(d["income_count"], 1)
        self.assertEqual(d["by_category"], {"rent": 1200.0, "food": 300.0})
        self.assertEqual(d["budgets"]["food"], {"spent": 300.0, "limit": 400.0})
        self.assertEqual(d["goal"], 1000.0)

    def test_month_empty(self):
        out = self._main(["month", "--month", "1999-01"])
        self.assertIn("nothing recorded", out)

    def test_goal_set_and_progress(self):
        out = self._main(["goal", "--amount", "500"])
        self.assertIn("500", out)
        self.assertEqual(L.load()["goal"], 500.0)
        # net this month = 800 income - 200 expense = 600 >= 500 goal
        self._main(["income", "800", "salary", "pay"])
        self._main(["add", "200", "food", "groceries"])
        d = json.loads(self._main(["stats", "--json"]))
        self.assertEqual(d["goal"], 500.0)
        self.assertEqual(d["goal_month_net"], 600.0)
        out = self._main(["goal"])
        self.assertIn("met", out)

    def test_goal_clear(self):
        self._main(["goal", "--amount", "300"])
        self._main(["goal", "--clear"])
        self.assertIsNone(L.load()["goal"])
        self._main(["add", "10", "food", "x"])
        d = json.loads(self._main(["stats", "--json"]))
        self.assertIsNone(d["goal"])

    def test_clear_reconcile_unclear(self):
        self._main(["income", "1000", "salary", "pay"])     # #1
        self._main(["add", "200", "food", "groceries"])     # #2
        self._main(["add", "50", "transit", "bus"])         # #3
        # clear the income and one expense
        out = self._main(["clear", "1", "2"])
        self.assertIn("cleared", out)
        d = json.loads(self._main(["reconcile", "--json"]))
        self.assertEqual(d["cleared_count"], 2)
        self.assertEqual(d["pending_count"], 1)
        self.assertEqual(d["cleared_net"], 800.0)           # 1000 - 200
        self.assertEqual(d["pending_net"], -50.0)           # -bus
        self.assertEqual(d["projected_balance"], 750.0)
        # unclear the expense; re-clearing is idempotent
        self._main(["unclear", "2"])
        d2 = json.loads(self._main(["reconcile", "--json"]))
        self.assertEqual(d2["cleared_count"], 1)
        self.assertEqual(d2["cleared_net"], 1000.0)
        self.assertFalse(L.load()["expenses"][1]["cleared"])

    def test_list_search_cleared_pending_filters(self):
        self._main(["add", "10", "food", "a"])   # #1
        self._main(["add", "20", "food", "b"])   # #2
        self._main(["add", "30", "food", "c"])   # #3
        self._main(["clear", "1", "3"])
        # list --pending shows only the uncleared one
        pend = json.loads(self._main(["list", "--pending", "--json"]))
        self.assertEqual([e["id"] for e in pend], [2])
        clr = json.loads(self._main(["list", "--cleared", "--json"]))
        self.assertEqual(sorted(e["id"] for e in clr), [1, 3])
        # search honours the same filters (combined with other criteria)
        s = json.loads(self._main(["search", "food", "--pending", "--json"]))
        self.assertEqual([e["id"] for e in s], [2])
        # mutually exclusive
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                L.main(["list", "--cleared", "--pending"])

    def test_clear_rejects_missing_id(self):
        self._main(["add", "10", "food", "x"])
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                L.main(["clear", "1", "999"])
        # the valid id must not have been changed (all-or-nothing)
        self.assertFalse(L.load()["expenses"][0].get("cleared"))

    def test_reconcile_empty(self):
        d = json.loads(self._main(["reconcile", "--json"]))
        self.assertEqual(d["projected_balance"], 0.0)

    def test_statement_json_and_save(self):
        m = "2026-05"
        self._main(["income", "2000", "salary", "pay", "--date", f"{m}-01"])
        self._main(["add", "300", "food", "groceries", "--date", f"{m}-03"])
        self._main(["add", "120", "food", "dinner", "--date", f"{m}-10"])
        self._main(["add", "80", "transit", "bus", "--date", f"{m}-12"])
        self._main(["budget", "--category", "food", "--amount", "350"])
        d = json.loads(self._main(["statement", "--month", m, "--json"]))
        self.assertEqual(d["income"], 2000.0)
        self.assertEqual(d["spending"], 500.0)
        self.assertEqual(d["net"], 1500.0)
        self.assertEqual(d["savings_rate"], 75.0)
        # by_category sorted descending; food (420) before transit (80)
        self.assertEqual([c["category"] for c in d["by_category"]],
                         ["food", "transit"])
        food = next(b for b in d["budgets"] if b["category"] == "food")
        self.assertEqual(food["spent"], 420.0)
        self.assertEqual(food["over"], 70.0)          # 420 - 350
        self.assertEqual(d["top"][0]["amount"], 300.0)  # largest expense first
        # --save writes a Markdown file into exports/
        out = self._main(["statement", "--month", m, "--save"])
        self.assertIn("wrote statement", out)
        path = os.path.join(L.EXPORT_DIR, f"statement_{m}.md")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn(f"# Ledgerling statement — {m}", md)
        self.assertIn("## Spending by category", md)

    def test_statement_empty_month(self):
        out = self._main(["statement", "--month", "2099-01"])
        self.assertIn("nothing recorded", out)

    def test_pot_lifecycle(self):
        self._main(["pot", "vacation", "--target", "2000"])
        self._main(["pot", "vacation", "--add", "500"])
        self._main(["pot", "vacation", "--add", "300"])
        self._main(["pot", "vacation", "--take", "100"])   # 500+300-100 = 700
        d = json.loads(self._main(["pot", "vacation", "--json"]))
        p = d["pots"][0]
        self.assertEqual(p["saved"], 700.0)
        self.assertEqual(p["target"], 2000.0)
        self.assertEqual(p["remaining"], 1300.0)
        self.assertEqual(p["progress_pct"], 35.0)
        # a second pot, then the list totals across pots
        self._main(["pot", "laptop", "--add", "250"])
        allp = json.loads(self._main(["pot", "--json"]))
        self.assertEqual(allp["total_saved"], 950.0)
        self.assertEqual([x["name"] for x in allp["pots"]], ["laptop", "vacation"])
        # take never goes below zero
        self._main(["pot", "laptop", "--take", "999"])
        self.assertEqual(L.load()["pots"]["laptop"]["saved"], 0.0)
        # remove
        self._main(["pot", "vacation", "--remove"])
        self.assertNotIn("vacation", L.load()["pots"])

    def test_pot_due_date_projection(self):
        # target + due date set in one call; projection to hit it in time
        future = (date.today() + timedelta(days=120)).isoformat()
        self._main(["pot", "vacation", "--target", "1200", "--by", future])
        self._main(["pot", "vacation", "--add", "200"])      # 1000 to go
        p = json.loads(self._main(["pot", "vacation", "--json"]))["pots"][0]
        self.assertEqual(p["due"], future)
        self.assertEqual(p["status"], "on_target")
        self.assertGreater(p["months_left"], 0)
        # required_monthly finishes the remaining balance over the months left
        self.assertEqual(p["required_monthly"],
                         round(p["remaining"] / p["months_left"], 2))
        # fully funded pots report "funded" regardless of the date
        self._main(["pot", "vacation", "--add", "1000"])
        self.assertEqual(json.loads(self._main(["pot", "vacation", "--json"]))
                         ["pots"][0]["status"], "funded")

    def test_pot_due_overdue_and_clear(self):
        past = (date.today() - timedelta(days=5)).isoformat()
        self._main(["pot", "car", "--target", "500", "--by", past])
        self._main(["pot", "car", "--add", "100"])           # still short, past due
        p = json.loads(self._main(["pot", "car", "--json"]))["pots"][0]
        self.assertEqual(p["status"], "overdue")
        self.assertEqual(p["months_left"], 0)
        # clearing the date drops it and returns to plain "saving"
        self._main(["pot", "car", "--clear-by"])
        p2 = json.loads(self._main(["pot", "car", "--json"]))["pots"][0]
        self.assertIsNone(p2["due"])
        self.assertEqual(p2["status"], "saving")
        with self.assertRaises(SystemExit):                  # by + clear-by clash
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                L.main(["pot", "car", "--by", past, "--clear-by"])

    def test_savingsplan_aggregates_dated_goals(self):
        d90 = (date.today() + timedelta(days=90)).isoformat()
        d60 = (date.today() + timedelta(days=60)).isoformat()
        past = (date.today() - timedelta(days=3)).isoformat()
        self._main(["pot", "vacation", "--target", "1200", "--by", d90])
        self._main(["pot", "laptop", "--target", "900", "--by", d60])
        self._main(["pot", "phone", "--target", "500", "--by", past])  # overdue
        self._main(["pot", "gift", "--target", "100"])                 # no date
        self._main(["pot", "done", "--target", "50"])
        self._main(["pot", "done", "--add", "50"])                     # funded
        d = json.loads(self._main(["savingsplan", "--json"]))
        self.assertEqual(d["dated"], 2)        # vacation + laptop
        self.assertEqual(d["overdue"], 1)      # phone
        self.assertEqual(d["funded"], 1)       # done
        # total monthly is the sum of the two dated goals' required contributions
        by = {g["name"]: g for g in d["goals"]}
        expect = round((by["vacation"]["required_monthly"]
                        + by["laptop"]["required_monthly"]), 2)
        self.assertEqual(d["total_monthly"], expect)
        # ordered by urgency: overdue first, then on-target by soonest due,
        # then undated, then funded last
        self.assertEqual([g["name"] for g in d["goals"]],
                         ["phone", "laptop", "vacation", "gift", "done"])

    def test_savingsplan_empty(self):
        d = json.loads(self._main(["savingsplan", "--json"]))
        self.assertEqual(d["goals"], [])
        self.assertEqual(d["total_monthly"], 0.0)
        self.assertIn("no savings goals", self._main(["savingsplan"]))

    def test_transfer_between_pots(self):
        self._main(["pot", "vacation", "--add", "500"])
        self._main(["pot", "laptop", "--add", "100"])
        d = json.loads(self._main(["transfer", "150", "vacation", "laptop",
                                   "--json"]))
        self.assertEqual(d["from_saved"], 350.0)
        self.assertEqual(d["to_saved"], 250.0)
        self.assertEqual(L.load()["pots"]["vacation"]["saved"], 350.0)

    def test_transfer_validation(self):
        self._main(["pot", "a", "--add", "50"])
        self._main(["pot", "b", "--add", "10"])
        for argv in (["transfer", "100", "a", "b"],    # insufficient funds
                     ["transfer", "10", "a", "a"],     # same pot
                     ["transfer", "-5", "a", "b"],     # non-positive
                     ["transfer", "10", "a", "nope"]):  # unknown pot
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    L.main(argv)
        # nothing changed after the failed attempts
        self.assertEqual(L.load()["pots"]["a"]["saved"], 50.0)

    def test_pot_validation(self):
        for argv in (["pot", "x", "--add", "-5"],
                     ["pot", "--target", "100"],      # action without a name
                     ["pot", "x", "--target", "10", "--add", "5"],  # two actions
                     ["pot", "nope"]):                # show a missing pot
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    L.main(argv)

    def test_template_add_list_use(self):
        self._main(["template", "add", "Coffee", "4.50", "Food", "flat white #treat"])
        d = json.loads(self._main(["template", "list", "--json"]))
        self.assertEqual(len(d["templates"]), 1)
        t = d["templates"][0]
        self.assertEqual(t["name"], "coffee")          # normalized lowercase
        self.assertEqual(t["amount"], 4.5)
        self.assertEqual(t["category"], "food")
        self.assertEqual(t["kind"], "expense")
        # use it: creates a real expense entry carrying the note's tags
        u = json.loads(self._main(["template", "use", "coffee",
                                   "--date", "2026-05-10", "--json"]))
        e = u["entry"]
        self.assertEqual(e["amount"], 4.5)
        self.assertEqual(e["category"], "food")
        self.assertEqual(e["date"], "2026-05-10")
        self.assertEqual(e["kind"], "expense")
        self.assertEqual(e["tags"], ["treat"])
        self.assertEqual(L.kind_of(L.load()["expenses"][-1]), "expense")

    def test_template_overrides_and_income(self):
        self._main(["template", "add", "paycheck", "2000", "salary",
                    "monthly pay", "--income"])
        # per-entry overrides for amount and note; income kind is preserved
        u = json.loads(self._main(["template", "use", "paycheck",
                                   "--amount", "2100", "--note", "bonus month",
                                   "--date", "2026-06-01", "--json"]))
        e = u["entry"]
        self.assertEqual(e["amount"], 2100.0)
        self.assertEqual(e["note"], "bonus month")
        self.assertEqual(e["kind"], "income")
        # adding the same name again updates in place (no duplicate)
        self._main(["template", "add", "paycheck", "2500", "salary"])
        d = json.loads(self._main(["template", "list", "--json"]))
        self.assertEqual(len(d["templates"]), 1)
        self.assertEqual(d["templates"][0]["amount"], 2500.0)

    def test_template_use_quantity(self):
        self._main(["template", "add", "coffee", "4.50", "food", "latte"])
        u = json.loads(self._main(["template", "use", "coffee", "--qty", "3",
                                   "--date", "2026-05-11", "--json"]))
        self.assertEqual(u["qty"], 3)
        e = u["entry"]
        self.assertEqual(e["amount"], 13.5)              # 4.50 x 3
        self.assertEqual(e["note"], "latte (x3)")        # quantity annotated
        # qty combines with a per-unit --amount override
        u2 = json.loads(self._main(["template", "use", "coffee", "--qty", "2",
                                    "--amount", "5", "--json"]))
        self.assertEqual(u2["entry"]["amount"], 10.0)
        # qty 1 leaves the note untouched
        u3 = json.loads(self._main(["template", "use", "coffee", "--json"]))
        self.assertEqual(u3["entry"]["note"], "latte")
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                L.main(["template", "use", "coffee", "--qty", "0"])

    def test_template_rename(self):
        self._main(["template", "add", "coffee", "4.50", "food", "latte"])
        self._main(["template", "rename", "coffee", "espresso"])
        names = [t["name"] for t in
                 json.loads(self._main(["template", "list", "--json"]))["templates"]]
        self.assertEqual(names, ["espresso"])
        # fields are preserved under the new name
        t = json.loads(self._main(["template", "list", "--json"]))["templates"][0]
        self.assertEqual((t["amount"], t["category"], t["note"]),
                         (4.5, "food", "latte"))
        self._main(["template", "add", "tea", "2", "food"])
        for argv in (["template", "rename", "ghost", "x"],       # missing source
                     ["template", "rename", "espresso", "tea"],  # target exists
                     ["template", "rename", "tea", "tea"]):       # same name
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    L.main(argv)

    def test_template_remove_and_validation(self):
        self._main(["template", "add", "lunch", "12", "food"])
        self._main(["template", "remove", "lunch"])
        self.assertEqual(json.loads(self._main(["template", "list", "--json"]))
                         ["templates"], [])
        for argv in (["template", "add", "x", "-1", "food"],   # bad amount
                     ["template", "use", "ghost"],              # missing template
                     ["template", "remove", "ghost"]):          # missing template
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    L.main(argv)

    def test_check_flags_bad_pot(self):
        L.save({"expenses": [], "budgets": {}, "recurring": [], "goal": None,
                "pots": {"ok": {"target": 100, "saved": 10.0},
                         "bad": {"saved": "lots"}}})
        kinds = [i["kind"] for i in json.loads(self._main(["check", "--json"]))["issues"]]
        self.assertIn("bad_pot", kinds)
        self._main(["check", "--fix"])
        self.assertEqual(list(L.load()["pots"]), ["ok"])

    def test_check_flags_and_fixes_bad_pot_fields(self):
        # A sound pot shell (valid saved) but a bad due date and a bad target.
        L.save({"expenses": [], "budgets": {}, "recurring": [], "goal": None,
                "pots": {"trip": {"saved": 50.0, "target": -5,
                                  "due": "not-a-date"}}})
        issues = json.loads(self._main(["check", "--json"]))["issues"]
        kinds = [i["kind"] for i in issues]
        self.assertEqual(kinds.count("bad_pot_field"), 2)
        self.assertNotIn("bad_pot", kinds)   # the pot itself is kept, not flagged
        self._main(["check", "--fix"])
        pot = L.load()["pots"]["trip"]
        self.assertEqual(pot["saved"], 50.0)   # balance preserved
        self.assertNotIn("target", pot)
        self.assertNotIn("due", pot)
        # clean afterwards
        self.assertTrue(json.loads(self._main(["check", "--json"]))["ok"])

    def test_check_accepts_valid_pot_fields(self):
        L.save({"expenses": [], "budgets": {}, "recurring": [], "goal": None,
                "pots": {"trip": {"saved": 50.0, "target": 500,
                                  "due": "2030-01-01"}}})
        self.assertTrue(json.loads(self._main(["check", "--json"]))["ok"])

    def test_networth_totals_pure(self):
        acc = {"checking": {"amount": 2500.0, "debt": False},
               "card": {"amount": 800.0, "debt": True},
               "savings": {"amount": 1000.0}}
        self.assertEqual(L.networth_totals(acc), (3500.0, 800.0, 2700.0))
        self.assertEqual(L.networth_totals({}), (0.0, 0.0, 0.0))

    def test_networth_set_remove_and_summary(self):
        self._main(["networth", "--set", "Checking", "--amount", "2500"])
        self._main(["networth", "--set", "card", "--amount", "800", "--debt"])
        d = json.loads(self._main(["networth", "--json"]))
        self.assertEqual(d["assets"], 2500.0)
        self.assertEqual(d["debts"], 800.0)
        self.assertEqual(d["net_worth"], 1700.0)
        labels = {a["label"]: a for a in d["accounts"]}
        self.assertTrue(labels["card"]["debt"])         # stored, lowercased
        self.assertFalse(labels["checking"]["debt"])
        self.assertEqual(labels["checking"]["updated"], date.today().isoformat())
        # cash context reflects the ledger's all-time net
        self._main(["income", "100", "salary", "x"])
        self.assertEqual(json.loads(self._main(["networth", "--json"]))["cash"],
                         100.0)
        # remove
        self._main(["networth", "--remove", "card"])
        self.assertNotIn("card", L.load()["accounts"])

    def test_networth_snapshot_and_worthtrend(self):
        self._main(["networth", "--set", "checking", "--amount", "1000"])
        self._main(["networth", "--set", "card", "--amount", "400", "--debt"])
        out = self._main(["networth", "--snapshot"])
        self.assertIn("snapshot saved", out)
        hist = L.load()["networth_history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["net"], 600.0)
        self.assertEqual(hist[0]["date"], date.today().isoformat())
        # a second same-day snapshot replaces rather than appends
        self._main(["networth", "--set", "checking", "--amount", "1500"])
        self._main(["networth", "--snapshot"])
        hist = L.load()["networth_history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["net"], 1100.0)
        # worthtrend reports it
        d = json.loads(self._main(["worthtrend", "--json"]))
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["snapshots"][0]["net"], 1100.0)
        self.assertIsNone(d["snapshots"][0]["change"])   # first has no delta

    def test_worthtrend_empty(self):
        self.assertIn("no net-worth snapshots", self._main(["worthtrend"]))

    def test_networth_validation(self):
        # --set needs --amount; negative amount rejected; removing a missing one
        for argv in (["networth", "--set", "x"],
                     ["networth", "--set", "x", "--amount", "-5"],
                     ["networth", "--remove", "nope"]):
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    L.main(argv)

    def test_income_survives_csv_roundtrip(self):
        self._main(["add", "10", "food", "a"])
        self._main(["income", "50", "gift", "b"])
        self._main(["export", "--file", "rt.csv"])
        # re-import: both rows are duplicates (kind is part of the dedupe key)
        out = self._main(["import", "--file", "rt.csv"])
        self.assertIn("added 0", out)
        self.assertEqual(len(income_kind := L.income_only(L.load()["expenses"])), 1)
        self.assertEqual(income_kind[0]["category"], "gift")

    def test_cleared_survives_export_import_roundtrip(self):
        # cleared status should round-trip through both CSV and JSON
        self._main(["add", "10", "food", "a"])      # #1
        self._main(["add", "20", "food", "b"])      # #2 (stays pending)
        self._main(["clear", "1"])
        for fmt, fname in (("csv", "rt.csv"), ("json", "rt.json")):
            self._main(["export", "--format", fmt, "--file", fname])
            # fresh store, import the file back
            L.save({"expenses": [], "budgets": {}, "recurring": [], "goal": None})
            self._main(["import", "--file", fname])
            rows = {(e["category"], e["amount"]): e for e in L.load()["expenses"]}
            self.assertTrue(rows[("food", 10.0)].get("cleared"))    # cleared kept
            self.assertFalse(rows[("food", 20.0)].get("cleared"))   # pending kept


if __name__ == "__main__":
    unittest.main(verbosity=2)
