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
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
from ledgerling import cli as L  # noqa: E402


class PureLogic(unittest.TestCase):
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

    def test_median(self):
        self.assertEqual(L._median([]), 0)
        self.assertEqual(L._median([5]), 5)
        self.assertEqual(L._median([1, 3]), 2)          # even -> mean of middle two
        self.assertEqual(L._median([3, 1, 2]), 2)       # odd, unsorted input

    def test_bar_clamps(self):
        self.assertEqual(L.bar(0, width=4), "----")
        self.assertEqual(L.bar(1, width=4), "####")
        self.assertEqual(L.bar(2.0, width=4), "####")   # >1 clamped
        self.assertEqual(L.bar(-1, width=4), "----")    # <0 clamped

    def test_clean_category(self):
        self.assertEqual(L.clean_category("  Food  "), "food")
        with self.assertRaises(SystemExit):
            L.clean_category("   ")

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

    def test_clone_missing(self):
        with self.assertRaises(SystemExit):
            self._main(["clone", "99"])

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

    def test_average_empty(self):
        out = self._main(["average"])
        self.assertIn("no expenses to average", out)

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

    def test_income_survives_csv_roundtrip(self):
        self._main(["add", "10", "food", "a"])
        self._main(["income", "50", "gift", "b"])
        self._main(["export", "--file", "rt.csv"])
        # re-import: both rows are duplicates (kind is part of the dedupe key)
        out = self._main(["import", "--file", "rt.csv"])
        self.assertIn("added 0", out)
        self.assertEqual(len(income_kind := L.income_only(L.load()["expenses"])), 1)
        self.assertEqual(income_kind[0]["category"], "gift")


if __name__ == "__main__":
    unittest.main(verbosity=2)
