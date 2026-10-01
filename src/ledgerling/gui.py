"""A native desktop UI for Ledgerling, built on Tkinter (Python stdlib).

Like the web UI, this generates itself from the CLI parser: every command (and
every future one) is introspected into a form via ``web.describe()`` and run
through ``web.run_cli()``, so the interface stays comprehensive with no
per-command UI code. It is a thin front-end over Ledgerling's own commands -
no shell, no network - and all data stays in the app's own folder.

Launch it with ``ledgerling gui`` (or the windowed ``ledgerling-gui`` exe).
"""

import json
import os
import queue
import re
import threading

from . import cli as L
from . import web

# Sidebar grouping (mirrors the web UI's GROUP_DEFS). Anything not listed falls
# into "More", so a newly added command can never be lost from the nav.
GROUP_DEFS = [
    ("Record", ["add", "income", "edit", "delete", "split", "clone", "note",
                "refund"]),
    ("Analyze", ["today", "month", "insights", "range", "summary", "report",
                 "stats",
                 "week", "weekly", "day", "year", "years", "quarter", "weekday",
                 "trend", "tagtrend", "matrix", "tagmatrix", "cumulative", "top",
                 "compare", "average", "distribution", "balance", "savings",
                 "heatmap", "streak", "pace", "forecast", "sources", "anomalies",
                 "cashflow", "net", "subscriptions", "payees", "categories",
                 "tags", "untagged", "search", "list"]),
    ("Budgets & goals", ["budget", "unbudget", "allowance", "overbudget", "goal",
                         "networth", "suggest", "autobudget", "commitments",
                         "upcoming"]),
    ("Calculators", ["tip", "interest", "loan", "target", "runway", "roundup",
                     "fx convert", "fx set", "fx list", "fx rm"]),
    ("Recurring", ["recur add", "recur from", "recur list", "recur edit",
                   "recur remove", "recur run", "recur skip", "recur unskip",
                   "recur pause", "recur resume"]),
    ("Data", ["export", "import", "backup", "restore", "dedupe", "duplicates",
              "retag", "tag", "untag", "recategorize", "check", "undo"]),
    ("Settings", ["config", "where", "version", "completion", "web", "gui"]),
    ("Almanac", ["fortune", "horoscope", "weather", "eightball"]),
]

# A glyph per group (fallback) and per command (when a specific one reads well).
GROUP_ICONS = {
    "Record": "✍️", "Analyze": "\U0001f4ca",
    "Budgets & goals": "\U0001f3af", "Calculators": "\U0001f9ee",
    "Recurring": "\U0001f501", "Data": "\U0001f5c4️",
    "Settings": "⚙️", "Almanac": "\U0001f4d6", "More": "•",
}
ICONS = {
    "add": "➕", "income": "\U0001f4b0", "edit": "✏️",
    "delete": "\U0001f5d1️", "split": "✂️", "clone": "⧉",
    "note": "\U0001f4dd", "refund": "↩️",
    "today": "\U0001f305", "summary": "\U0001f4ca", "stats": "\U0001f4c8",
    "search": "\U0001f50d",
    "categories": "\U0001f3f7️", "tags": "#️⃣",
    "payees": "\U0001f3ea", "subscriptions": "\U0001f501",
    "heatmap": "\U0001f5d3️", "cashflow": "\U0001f4c9",
    "upcoming": "\U0001f4c5", "overbudget": "⚠️", "trend": "\U0001f4c8",
    "top": "\U0001f51d", "compare": "⚖️", "net": "\U0001f9ee",
    "savings": "\U0001f437", "forecast": "\U0001f52e", "insights": "\U0001f4a1",
    "budget": "\U0001f3af", "goal": "\U0001f945", "networth": "\U0001f3e6",
    "allowance": "\U0001fa99",
    "suggest": "\U0001f4a1", "autobudget": "\U0001f916",
    "commitments": "\U0001f4cc", "tip": "\U0001f9ee", "interest": "\U0001f4c8",
    "loan": "\U0001f3e6", "target": "\U0001f3af", "runway": "\U0001f6eb",
    "roundup": "\U0001fa99", "export": "⬆️", "import": "⬇️",
    "backup": "\U0001f4be", "restore": "♻️", "dedupe": "\U0001f9f9",
    "duplicates": "\U0001f46f", "check": "✅", "undo": "↶",
    "retag": "\U0001f516", "recategorize": "\U0001f504", "config": "⚙️",
    "where": "\U0001f4c1", "version": "ℹ️", "completion": "⌨️",
    "web": "\U0001f310", "gui": "\U0001f5a5️",
    "fortune": "\U0001f960", "horoscope": "✨", "weather": "⛅",
    "eightball": "\U0001f3b1",
}

# Quick-access toolbar: (command, tooltip).
TOOLBAR = [
    ("add", "Record an expense"),
    ("income", "Record income"),
    ("summary", "Category summary"),
    ("payees", "Spending by merchant"),
    ("search", "Search entries"),
    ("upcoming", "Upcoming recurring charges"),
]

# Two palettes. Light = light surface + green accent. Dark = deep green surface
# + light (mint) accent. Toggled at runtime with an animated colour crossfade.
THEMES = {
    "light": {
        "bg": "#eef3ee", "panel": "#ffffff", "ink": "#13241a",
        "muted": "#5f7068", "line": "#d6e0d8", "accent": "#1f9d55",
        "accent2": "#34c47a", "accent_ink": "#ffffff", "err": "#b23b2e",
        "field": "#ffffff", "sel": "#d6f0e0",
    },
    "dark": {
        "bg": "#0e1c14", "panel": "#13271b", "ink": "#e8f3ec",
        "muted": "#9ab8a6", "line": "#1f3a2a", "accent": "#7fe3a6",
        "accent2": "#a7f0c4", "accent_ink": "#06160d", "err": "#ff9b8a",
        "field": "#0b1710", "sel": "#1c3a28",
    },
}


def _group_of(name):
    for g, names in GROUP_DEFS:
        if name in names:
            return g
    return "More"


def _icon_for(name):
    return ICONS.get(name) or GROUP_ICONS.get(_group_of(name), "•")


def _humanize(dest):
    return dest.replace("_", " ").strip().capitalize()


def _lerp(c1, c2, t):
    """Blend two #rrggbb colours; t in [0,1]. Used for animated transitions."""
    c1, c2 = c1.lstrip("#"), c2.lstrip("#")
    try:
        a = tuple(int(c1[i:i + 2], 16) for i in (0, 2, 4))
        b = tuple(int(c2[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#" + c2
    m = tuple(round(a[i] + (b[i] - a[i]) * max(0.0, min(1.0, t))) for i in range(3))
    return "#%02x%02x%02x" % m


# --- JSON -> table formatting (mirrors the web UI's column heuristics) ------- #
_MONEY_RE = re.compile(
    r"total|amount|spend|income|\bnet\b|balance|budget|spent|limit|remaining|"
    r"average|cumulative|projected|annual|monthly|per_?(day|week|month)|value|"
    r"^over$|_over$", re.I)
_NOTMONEY_RE = re.compile(r"count|rate|share|days|year|\bid\b|day\b", re.I)
_PCT_RE = re.compile(r"rate|share|percent|_pct|^pct", re.I)
_MONTHCOL_RE = re.compile(r"^\d{4}-\d{2}$")


def _is_money_key(k):
    if _MONTHCOL_RE.match(k):
        return True
    return bool(_MONEY_RE.search(k)) and not _NOTMONEY_RE.search(k)


def _money(v, currency="$", after=False):
    neg = v < 0
    s = f"{abs(v):,.2f}"
    body = (f"{s} {currency}" if after else f"{currency}{s}")
    return ("-" + body) if neg else body


def _fmt_cell(key, v, currency="$", after=False):
    """Format one cell value given its column name (money/percent/plain)."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        if _PCT_RE.search(key):
            return f"{v}%"
        if _is_money_key(key):
            return _money(v, currency, after)
        return str(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def _cmd_has_json(cmd):
    """True if the command exposes a --json flag (so we can render a table)."""
    return any(a.get("flag") == "--json" for a in cmd.get("args", []))


def build_argv(cmd, values):
    """Assemble a command's argv from a {dest: value} mapping, mirroring the web
    UI's buildArgv. A bool value drives a store-true flag; a string fills a
    positional (split when variadic) or an option. Empty strings are omitted,
    and a dest absent from `values` is skipped. Pure - shared by the main window
    and pop-out command windows, and unit-tested without Tk."""
    positionals, options = [], []
    for a in cmd["args"]:
        dest = a["dest"]
        if dest not in values:
            continue
        if a.get("type") == "bool":
            if values[dest]:
                options += [a["flag"]]
            continue
        val = str(values[dest] or "").strip()
        if a["kind"] == "positional":
            if not val:
                continue
            positionals += val.split() if a.get("variadic") else [val]
        elif val:
            options += [a["flag"], val]
    return list(cmd["argv"]) + positionals + options


def _safe_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


# --- UI state persistence (theme + window geometry), kept in the data folder - #
def _state_path():
    return os.path.join(L.HOME_DIR, "gui_state.json")


def _load_state():
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(L.HOME_DIR, exist_ok=True)
        with open(_state_path(), "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except Exception:
        pass   # UI convenience only; never let a save failure crash the app


_GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")


def _valid_geometry(geo):
    return isinstance(geo, str) and bool(_GEOMETRY_RE.match(geo))


def _tabular(data):
    """Shape parsed JSON into (mode, columns, rows) for a table, or None.

    mode 'rows'    -> a list of record dicts (columns = union of keys)
    mode 'fields'  -> a flat dict shown as field/value pairs
    mode 'scalars' -> a list of plain values in one 'value' column
    """
    def cols_of(records):
        cols = []
        for r in records:
            if isinstance(r, dict):
                for k in r:
                    if k not in cols:
                        cols.append(k)
        return cols

    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return ("rows", cols_of(data), data)
        return ("scalars", ["value"], [{"value": v} for v in data])
    if isinstance(data, dict):
        arrkey = next((k for k, v in data.items()
                       if isinstance(v, list) and v and isinstance(v[0], dict)),
                      None)
        if arrkey:
            return ("rows", cols_of(data[arrkey]), data[arrkey])
        return ("fields", ["field", "value"],
                [{"field": k, "value": v} for k, v in data.items()])
    return None


class LedgerlingGUI:
    def __init__(self, root, theme="dark"):
        import tkinter as tk  # local imports so importing this module is cheap
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = root
        self.schema = web.describe()
        self.commands = {c["name"]: c for c in self.schema["commands"]}
        self.currency = self.schema.get("currency", "$")
        self.symbol_after = self.schema.get("symbol_position") == "after"
        self.current = None       # selected command name
        self.fields = {}          # dest -> (widget, arg-spec)
        self._results = queue.Queue()
        self._nav = {}            # command -> {row, bar, gl, tx}
        self._alive = True
        self._comet = 0.0
        self._spin = 0
        self._spinning = False
        self._windows = []        # secondary command windows
        self._drag = None         # in-flight drag {name, ghost, kind}
        self._pulse = 0.0

        state = _load_state()
        self.theme_name = state.get("theme") or theme
        self.colors = dict(THEMES.get(self.theme_name, THEMES["dark"]))
        self.pinned = [n for n in (state.get("pinned") or [])
                       if n in self.commands]
        self.onboarded = bool(state.get("onboarded"))

        root.title("Ledgerling")
        root.minsize(940, 580)
        geo = state.get("geometry")
        try:
            root.geometry(geo if _valid_geometry(geo) else "1100x680")
        except Exception:
            pass

        self._build_menu()
        self._build_header()
        self._build_toolbar()
        self._build_pinbar()
        self._build_body()
        self._build_statusbar()
        self.apply_theme(self.theme_name, animate=False)
        self._bind_shortcuts()
        self._poll_results()
        self._animate_header()
        self._animate_pulse()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.open_command("summary" if "summary" in self.commands else
                          next(iter(self.commands)))
        if not self.onboarded:
            self._after(350, self.show_assistant)

    # ----- scheduling helpers --------------------------------------------- #
    def _after(self, ms, fn):
        if self._alive:
            try:
                return self.root.after(ms, fn)
            except Exception:
                return None
        return None

    # ----- menu ------------------------------------------------------------ #
    def _build_menu(self):
        tk = self.tk
        bar = tk.Menu(self.root)

        filem = tk.Menu(bar, tearoff=0)
        filem.add_command(label="Run command", accelerator="Ctrl+Enter",
                          command=self.run_current)
        filem.add_separator()
        filem.add_command(label="New window", accelerator="Ctrl+N",
                          command=self.new_window)
        filem.add_command(label="Pop out current command",
                          command=lambda: self.new_window(self.current))
        filem.add_separator()
        filem.add_command(label="Open web UI", command=self._open_web)
        filem.add_separator()
        filem.add_command(label="Quit", accelerator="Ctrl+Q",
                          command=self._on_close)
        bar.add_cascade(label="File", menu=filem)

        cmds = tk.Menu(bar, tearoff=0)
        present = set(self.commands)
        for group, names in GROUP_DEFS + [("More", [])]:
            listed = [n for n in names if n in present]
            if group == "More":
                listed = sorted(n for n in present if _group_of(n) == "More")
            if not listed:
                continue
            sub = tk.Menu(cmds, tearoff=0)
            for n in listed:
                sub.add_command(label=n,
                                command=lambda n=n: self.open_command(n))
            cmds.add_cascade(label=group, menu=sub)
        bar.add_cascade(label="Commands", menu=cmds)

        viewm = tk.Menu(bar, tearoff=0)
        viewm.add_command(label="Toggle light / dark", accelerator="Ctrl+T",
                          command=self.toggle_theme)
        viewm.add_command(label="Focus command search", accelerator="Ctrl+K",
                          command=lambda: self.filter_entry.focus_set())
        bar.add_cascade(label="View", menu=viewm)

        helpm = tk.Menu(bar, tearoff=0)
        helpm.add_command(label="Getting started…", command=self.show_assistant)
        helpm.add_command(label="About Ledgerling", command=self._about)
        bar.add_cascade(label="Help", menu=helpm)

        self.root.config(menu=bar)

    # ----- header (animated) ---------------------------------------------- #
    def _build_header(self):
        tk = self.tk
        self.header = tk.Canvas(self.root, height=72, highlightthickness=0,
                                bd=0)
        self.header.pack(side="top", fill="x")
        self.header.bind("<Configure>", lambda _e: self._draw_header_static())

    def _draw_header_static(self):
        c, h = self.colors, self.header
        try:
            w = h.winfo_width() or 1100
            h.delete("static")
            h.configure(background=c["panel"])
            # a soft accent underline across the header bottom
            h.create_line(0, 70, w, 70, fill=c["line"], tags="static")
            h.create_text(22, 26, anchor="w", text="Ledgerling", tags="static",
                          fill=c["accent"], font=("Segoe UI Semibold", 20))
            h.create_text(24, 50, anchor="w", tags="static", fill=c["muted"],
                          font=("Segoe UI", 9),
                          text=f"v{self.schema['version']}  •  "
                               f"private local ledger  •  "
                               f"currency {self.currency}")
        except Exception:
            pass

    def _animate_header(self):
        if not self._alive:
            return
        c, h = self.colors, self.header
        try:
            w = h.winfo_width() or 1100
            h.delete("comet")
            self._comet = (self._comet + 0.006) % 1.0
            # ease-in-out so the dot glides rather than scrolls linearly
            t = self._comet
            ease = 0.5 - 0.5 * __import__("math").cos(t * 2 * 3.14159265)
            x = 20 + ease * (w - 40)
            for i, (dx, r, mix) in enumerate(
                    [(-18, 2, .15), (-12, 3, .3), (-6, 4, .55), (0, 5, 1.0)]):
                col = _lerp(c["panel"], c["accent2"], mix)
                h.create_oval(x + dx - r, 70 - r, x + dx + r, 70 + r,
                              fill=col, outline="", tags="comet")
        except Exception:
            pass
        self._hdr_after = self._after(40, self._animate_header)

    # ----- toolbar --------------------------------------------------------- #
    def _build_toolbar(self):
        ttk = self.ttk
        self.toolbar = ttk.Frame(self.root, style="Toolbar.TFrame",
                                 padding=(12, 8))
        self.toolbar.pack(side="top", fill="x")
        for name, tip in TOOLBAR:
            if name not in self.commands:
                continue
            b = ttk.Button(self.toolbar, text=f"{_icon_for(name)}  {name}",
                           style="Tool.TButton",
                           command=lambda n=name: self.open_command(n))
            b.pack(side="left", padx=(0, 6))
            _Tooltip(b, tip)
        self.theme_btn = ttk.Button(self.toolbar, text="◑  theme",
                                    style="Tool.TButton", command=self.toggle_theme)
        self.theme_btn.pack(side="right")
        _Tooltip(self.theme_btn, "Toggle light / dark  (Ctrl+T)")
        gbtn = ttk.Button(self.toolbar, text="❓  guide",
                          style="Tool.TButton", command=self.show_assistant)
        gbtn.pack(side="right", padx=(0, 6))
        _Tooltip(gbtn, "Getting-started guide")
        nbtn = ttk.Button(self.toolbar, text="\U0001f5d7  new window",
                          style="Tool.TButton", command=self.new_window)
        nbtn.pack(side="right", padx=(0, 6))
        _Tooltip(nbtn, "Open another command window  (Ctrl+N)")

    # ----- body ------------------------------------------------------------ #
    def _build_body(self):
        tk, ttk = self.tk, self.ttk
        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(side="top", fill="both", expand=True)

        # --- left: filter + scrollable icon-button nav ---
        left = ttk.Frame(body, style="Side.TFrame", padding=(8, 8))
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(left, textvariable=self.filter_var)
        self.filter_entry.pack(side="top", fill="x")

        navhost = tk.Frame(left, highlightthickness=0, bd=0)
        navhost.pack(side="top", fill="both", expand=True, pady=(8, 0))
        self.navcanvas = tk.Canvas(navhost, highlightthickness=0, bd=0)
        navscroll = ttk.Scrollbar(navhost, orient="vertical",
                                  command=self.navcanvas.yview)
        self.navcanvas.configure(yscrollcommand=navscroll.set)
        navscroll.pack(side="right", fill="y")
        self.navcanvas.pack(side="left", fill="both", expand=True)
        self.nav_inner = tk.Frame(self.navcanvas, highlightthickness=0, bd=0)
        self._nav_win = self.navcanvas.create_window((0, 0), window=self.nav_inner,
                                                     anchor="nw")
        self.nav_inner.bind(
            "<Configure>",
            lambda _e: self.navcanvas.configure(
                scrollregion=self.navcanvas.bbox("all")))
        self.navcanvas.bind(
            "<Configure>",
            lambda e: self.navcanvas.itemconfigure(self._nav_win, width=e.width))
        self._bind_wheel(self.navcanvas)
        body.add(left, weight=1)

        _Placeholder(self.filter_entry, "Filter commands  (Ctrl+K)")
        self.filter_var.trace_add("write", lambda *_: self._refill_nav())

        # --- right: command header, form, results ---
        right = ttk.Frame(body, style="Main.TFrame", padding=14)
        self.cmd_title = ttk.Label(right, text="", style="Title.TLabel")
        self.cmd_title.pack(side="top", anchor="w")
        self.cmd_help = ttk.Label(right, text="", style="Muted.TLabel",
                                  wraplength=680, justify="left")
        self.cmd_help.pack(side="top", anchor="w", pady=(2, 12))

        self.form = ttk.Frame(right, style="Main.TFrame")
        self.form.pack(side="top", fill="x")

        runbar = ttk.Frame(right, style="Main.TFrame")
        runbar.pack(side="top", fill="x", pady=(12, 8))
        self.run_btn = ttk.Button(runbar, text="▶  Run   (Ctrl+Enter)",
                                  style="Accent.TButton", command=self.run_current)
        self.run_btn.pack(side="left")
        self.spinner = tk.Canvas(runbar, width=22, height=22,
                                 highlightthickness=0, bd=0)
        self.spinner.pack(side="left", padx=(10, 0))
        ttk.Button(runbar, text="Copy output", style="Tool.TButton",
                   command=self._copy_output).pack(side="right")

        self.results = ttk.Notebook(right)
        self.results.pack(side="top", fill="both", expand=True)

        outwrap = ttk.Frame(self.results, style="Main.TFrame")
        self.output = tk.Text(outwrap, wrap="none", height=13, borderwidth=0,
                              font=("Consolas", 10), state="disabled")
        yscroll = ttk.Scrollbar(outwrap, orient="vertical",
                                command=self.output.yview)
        xscroll = ttk.Scrollbar(outwrap, orient="horizontal",
                                command=self.output.xview)
        self.output.configure(yscrollcommand=yscroll.set,
                              xscrollcommand=xscroll.set)
        self.output.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        outwrap.rowconfigure(0, weight=1)
        outwrap.columnconfigure(0, weight=1)
        self.results.add(outwrap, text="Output")

        tabwrap = ttk.Frame(self.results, style="Main.TFrame")
        self.table = ttk.Treeview(tabwrap, show="headings", selectmode="browse",
                                  style="Data.Treeview")
        tyscroll = ttk.Scrollbar(tabwrap, orient="vertical",
                                 command=self.table.yview)
        txscroll = ttk.Scrollbar(tabwrap, orient="horizontal",
                                 command=self.table.xview)
        self.table.configure(yscrollcommand=tyscroll.set,
                             xscrollcommand=txscroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        tyscroll.grid(row=0, column=1, sticky="ns")
        txscroll.grid(row=1, column=0, sticky="ew")
        tabwrap.rowconfigure(0, weight=1)
        tabwrap.columnconfigure(0, weight=1)
        self.results.add(tabwrap, text="Table")
        self._table_tab = tabwrap
        self._table_rows, self._table_cols, self._table_mode = [], [], "rows"
        self._sort_state = {}
        self.results.hide(self._table_tab)

        body.add(right, weight=3)
        self._refill_nav()

    def _bind_wheel(self, widget):
        def on_wheel(e):
            try:
                self.navcanvas.yview_scroll(int(-e.delta / 120), "units")
            except Exception:
                pass
        widget.bind("<MouseWheel>", on_wheel)

    def _build_statusbar(self):
        ttk = self.ttk
        self.status = ttk.Label(
            self.root, style="Status.TLabel", anchor="w",
            text=f"Ready  •  currency {self.currency}")
        self.status.pack(side="bottom", fill="x")

    # ----- nav (icon buttons) --------------------------------------------- #
    def _refill_nav(self):
        tk = self.tk
        q = (self.filter_var.get() or "").strip().lower()
        if q == "filter commands  (ctrl+k)":
            q = ""
        for child in self.nav_inner.winfo_children():
            child.destroy()
        self._nav = {}
        c = self.colors
        self.navcanvas.configure(background=c["panel"])
        self.nav_inner.configure(background=c["panel"])

        present = set(self.commands)
        groups = [(g, names) for g, names in GROUP_DEFS]
        more = sorted(n for n in present if _group_of(n) == "More")
        if more:
            groups.append(("More", more))
        for group, names in groups:
            listed = [n for n in names if n in present and
                      (not q or q in n or q in self.commands[n]["help"].lower())]
            if not listed:
                continue
            hdr = tk.Label(self.nav_inner, text=f"{GROUP_ICONS.get(group, '')}  "
                           f"{group.upper()}", bg=c["panel"], fg=c["muted"],
                           font=("Segoe UI", 8, "bold"), anchor="w")
            hdr.pack(fill="x", padx=8, pady=(10, 2))
            self._bind_wheel(hdr)
            for n in listed:
                self._make_nav_button(n)
        if self.current:
            self._highlight_nav(self.current)

    def _make_nav_button(self, name):
        tk, c = self.tk, self.colors
        row = tk.Frame(self.nav_inner, bg=c["panel"], cursor="hand2")
        bar = tk.Frame(row, bg=c["panel"], width=3)
        bar.pack(side="left", fill="y")
        gl = tk.Label(row, text=_icon_for(name), bg=c["panel"], fg=c["ink"],
                      font=("Segoe UI Emoji", 12), width=2)
        gl.pack(side="left", padx=(6, 4), pady=5)
        tx = tk.Label(row, text=name, bg=c["panel"], fg=c["ink"],
                      font=("Segoe UI", 10), anchor="w")
        tx.pack(side="left", fill="x", expand=True)
        row.pack(fill="x", padx=6, pady=1)
        btn = {"row": row, "bar": bar, "gl": gl, "tx": tx, "base": c["panel"]}
        self._nav[name] = btn
        for w in (row, bar, gl, tx):
            w.bind("<ButtonPress-1>",
                   lambda e, n=name: self._drag_start(n, e, "nav"))
            w.bind("<B1-Motion>", self._drag_motion)
            w.bind("<ButtonRelease-1>", self._drag_release)
            w.bind("<Enter>", lambda _e, n=name: self._hover_nav(n, True))
            w.bind("<Leave>", lambda _e, n=name: self._hover_nav(n, False))
            self._bind_wheel(w)
        tip = self.commands[name].get("help", "")
        if tip:
            _Tooltip(row, tip)

    def _nav_set_bg(self, btn, color):
        for key in ("row", "gl", "tx"):
            try:
                btn[key].configure(bg=color)
            except Exception:
                pass

    def _hover_nav(self, name, entering):
        if name == self.current:
            return
        btn = self._nav.get(name)
        if not btn:
            return
        target = _lerp(self.colors["panel"], self.colors["accent"],
                       0.16) if entering else self.colors["panel"]
        self._animate_nav_bg(btn, target)

    def _animate_nav_bg(self, btn, target, step=0):
        start = btn.get("_bg", btn["base"])
        if step == 0:
            btn["_from"] = start
        frm = btn["_from"]
        t = min(1.0, (step + 1) / 5)
        cur = _lerp(frm, target, t)
        btn["_bg"] = cur
        self._nav_set_bg(btn, cur)
        if step + 1 < 5:
            self._after(16, lambda: self._animate_nav_bg(btn, target, step + 1))

    def _highlight_nav(self, name):
        for n, btn in self._nav.items():
            active = n == name
            bg = _lerp(self.colors["panel"], self.colors["accent"],
                       0.22) if active else self.colors["panel"]
            btn["_bg"] = bg
            self._nav_set_bg(btn, bg)
            try:
                btn["bar"].configure(bg=self.colors["accent"] if active
                                     else self.colors["panel"])
                btn["tx"].configure(
                    fg=self.colors["accent"] if active else self.colors["ink"],
                    font=("Segoe UI", 10, "bold" if active else "normal"))
            except Exception:
                pass

    # ----- command form ---------------------------------------------------- #
    def open_command(self, name):
        if name not in self.commands:
            return
        self.current = name
        cmd = self.commands[name]
        self.cmd_title.config(text=f"{_icon_for(name)}  {name}")
        self.cmd_help.config(text=cmd.get("help", ""))
        self._highlight_nav(name)

        for child in self.form.winfo_children():
            child.destroy()
        self.fields = {}
        row = 0
        for a in cmd["args"]:
            w = self._build_field(self.form, a, row)
            if w is not None:
                self.fields[a["dest"]] = (w, a)
                row += 1
        if not cmd["args"]:
            self.ttk.Label(self.form, text="No options — just run it.",
                           style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.form.columnconfigure(1, weight=1)
        for _dest, (w, a) in self.fields.items():
            if a.get("type") != "bool":
                try:
                    w.focus_set()
                except Exception:
                    pass
                break

    def _build_field(self, parent, a, row):
        tk, ttk = self.tk, self.ttk
        label = _humanize(a["dest"])
        req = a["kind"] == "positional" and not a.get("optional")
        ttk.Label(parent, style="Field.TLabel",
                  text=label + (" *" if req else "")).grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=5)

        if a.get("type") == "bool":
            var = tk.BooleanVar(value=False)
            chk = ttk.Checkbutton(parent, variable=var, style="Field.TCheckbutton")
            chk.grid(row=row, column=1, sticky="w", pady=5)
            chk._var = var
            return chk
        if a.get("type") == "choice":
            var = tk.StringVar(value="")
            combo = ttk.Combobox(parent, textvariable=var, state="readonly",
                                 values=[""] + [str(c) for c in a["choices"]])
            combo.grid(row=row, column=1, sticky="ew", pady=5)
            combo._var = var
            return combo
        var = tk.StringVar(value="")
        ent = ttk.Entry(parent, textvariable=var)
        ent.grid(row=row, column=1, sticky="ew", pady=5)
        ent._var = var
        if a.get("help"):
            _Tooltip(ent, a["help"])
        ent.bind("<Return>", lambda _e: self.run_current())
        return ent

    def _collect_argv(self):
        """Build argv from the form via the shared, pure build_argv()."""
        cmd = self.commands[self.current]
        values = {d: w._var.get() for d, (w, _a) in self.fields.items()}
        return build_argv(cmd, values)

    # ----- running --------------------------------------------------------- #
    def run_current(self):
        if not self.current:
            return
        argv = self._collect_argv()
        cmd = self.commands[self.current]
        self.run_btn.config(state="disabled", text="Running…")
        self._start_spinner()
        self._set_status(f"running: {' '.join(argv)}")
        self._set_output("running…", err=False)

        def work():
            res = web.run_cli(argv)
            data = None
            if res["code"] == 0:
                if "--json" in argv:
                    data = _safe_json(res["stdout"])
                elif _cmd_has_json(cmd):
                    jr = web.run_cli(argv + ["--json"])
                    if jr["code"] == 0:
                        data = _safe_json(jr["stdout"])
            self._results.put((argv, res, data))

        threading.Thread(target=work, daemon=True).start()

    def _poll_results(self):
        try:
            while True:
                argv, res, data = self._results.get_nowait()
                self._show_result(argv, res, data)
        except queue.Empty:
            pass
        self._after(80, self._poll_results)

    def _show_result(self, argv, res, data=None):
        self._stop_spinner()
        self.run_btn.config(state="normal", text="▶  Run   (Ctrl+Enter)")
        ok = res["code"] == 0
        text = (res["stdout"] or "(no output)") if ok else \
            (res["stderr"] or res["stdout"] or "error")
        self._set_output(text, err=not ok)
        self._populate_table(data if ok else None)
        verb = "done" if ok else f"failed (exit {res['code']})"
        self._set_status(f"{' '.join(argv)}  —  {verb}")

    def _set_output(self, text, err=False):
        self.output.config(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.tag_remove("err", "1.0", "end")
        if err:
            self.output.tag_add("err", "1.0", "end")
        self.output.config(state="disabled")

    def _copy_output(self):
        text = self.output.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("output copied to clipboard")

    # ----- run spinner ----------------------------------------------------- #
    def _start_spinner(self):
        self._spinning = True
        self._spin_tick()

    def _stop_spinner(self):
        self._spinning = False
        try:
            self.spinner.delete("all")
        except Exception:
            pass

    def _spin_tick(self):
        if not self._spinning or not self._alive:
            return
        c = self.colors
        try:
            self.spinner.delete("all")
            self.spinner.configure(background=self.colors["bg"])
            self._spin = (self._spin + 24) % 360
            self.spinner.create_arc(3, 3, 19, 19, start=self._spin, extent=270,
                                    style="arc", outline=c["accent"], width=3)
        except Exception:
            pass
        self._after(45, self._spin_tick)

    # ----- table view ------------------------------------------------------ #
    def _populate_table(self, data):
        shaped = _tabular(data) if data is not None else None
        if not shaped or not shaped[2]:
            self.table.delete(*self.table.get_children())
            self._table_rows = []
            self.results.tab(self._table_tab, state="hidden")
            return
        mode, cols, rows = shaped
        self._table_mode, self._table_cols, self._table_rows = mode, cols, rows
        self._sort_state = {}
        self.table["columns"] = cols
        for col in cols:
            base = col if _MONTHCOL_RE.match(col) else _humanize(col)
            self.table.heading(col, text=base,
                               command=lambda col=col: self._sort_table(col))
            numeric = mode != "fields" and (_is_money_key(col)
                                            or _PCT_RE.search(col))
            self.table.column(col, anchor="e" if numeric else "w",
                              width=max(80, min(240, len(base) * 9 + 48)),
                              stretch=True)
        self._fill_rows(rows)
        self.results.tab(self._table_tab, state="normal")

    def _fill_rows(self, rows):
        self.table.delete(*self.table.get_children())
        mode, cols = self._table_mode, self._table_cols
        cur, after = self.currency, self.symbol_after
        for r in rows:
            if mode == "fields":
                key = r["field"]
                vals = [_humanize(key), _fmt_cell(key, r["value"], cur, after)]
            else:
                vals = [_fmt_cell(col, r.get(col), cur, after) for col in cols]
            self.table.insert("", "end", values=vals)

    def _sort_table(self, col):
        desc = not self._sort_state.get(col, False)
        self._sort_state = {col: desc}

        def sortkey(r):
            v = r.get(col)
            if isinstance(v, bool):
                return (0, int(v))
            if isinstance(v, (int, float)):
                return (0, float(v))
            if v is None:
                return (2, "")
            s = str(v)
            try:
                return (0, float(s.replace(",", "").replace("$", "")
                                 .replace("%", "")))
            except ValueError:
                return (1, s.lower())

        self._fill_rows(sorted(self._table_rows, key=sortkey, reverse=desc))
        for col2 in self._table_cols:
            base = col2 if _MONTHCOL_RE.match(col2) else _humanize(col2)
            arrow = (" ▼" if desc else " ▲") if col2 == col else ""
            self.table.heading(col2, text=base + arrow)

    def _set_status(self, msg):
        self.status.config(text=f"{msg}")

    # ----- theme ----------------------------------------------------------- #
    def apply_theme(self, name, animate=False):
        target = THEMES.get(name, THEMES["dark"])
        self.theme_name = name
        if animate and getattr(self, "colors", None):
            self._crossfade(dict(self.colors), target, 0)
        else:
            self.colors = dict(target)
            self._apply_colors()

    def _crossfade(self, start, target, step, frames=7):
        t = (step + 1) / frames
        self.colors = {k: _lerp(start[k], target[k], t) if isinstance(v, str)
                       and v.startswith("#") else v for k, v in target.items()}
        self._apply_colors()
        if step + 1 < frames:
            self._after(18, lambda: self._crossfade(start, target, step + 1,
                                                    frames))

    def _apply_colors(self):
        ttk = self.ttk
        c = self.colors
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.root.configure(background=c["bg"])
        style.configure(".", background=c["bg"], foreground=c["ink"],
                        fieldbackground=c["field"], bordercolor=c["line"])
        style.configure("Toolbar.TFrame", background=c["panel"])
        style.configure("Side.TFrame", background=c["panel"])
        style.configure("Main.TFrame", background=c["bg"])
        style.configure("Title.TLabel", background=c["bg"], foreground=c["ink"],
                        font=("Segoe UI Semibold", 16))
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
        style.configure("Field.TLabel", background=c["bg"], foreground=c["ink"])
        style.configure("Field.TCheckbutton", background=c["bg"])
        style.configure("Status.TLabel", background=c["panel"],
                        foreground=c["muted"], padding=(12, 5))
        style.configure("Tool.TButton", padding=(10, 5), relief="flat",
                        background=c["panel"], foreground=c["ink"])
        style.map("Tool.TButton",
                  background=[("active", _lerp(c["panel"], c["accent"], 0.2))])
        style.configure("Accent.TButton", padding=(16, 7), relief="flat",
                        background=c["accent"], foreground=c["accent_ink"],
                        font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton",
                  background=[("active", c["accent2"]),
                             ("disabled", c["line"])])
        style.configure("Data.Treeview", background=c["field"],
                        fieldbackground=c["field"], foreground=c["ink"],
                        borderwidth=0, rowheight=25)
        style.configure("Data.Treeview.Heading", background=c["panel"],
                        foreground=c["muted"], relief="flat",
                        font=("Segoe UI Semibold", 9))
        style.map("Data.Treeview", background=[("selected", c["sel"])],
                  foreground=[("selected", c["ink"])])
        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=c["panel"],
                        foreground=c["muted"], padding=(16, 7))
        style.map("TNotebook.Tab", background=[("selected", c["bg"])],
                  foreground=[("selected", c["accent"])])
        if hasattr(self, "output"):
            self.output.configure(background=c["field"], foreground=c["ink"],
                                  insertbackground=c["ink"],
                                  selectbackground=c["sel"])
            self.output.tag_configure("err", foreground=c["err"])
        if hasattr(self, "header"):
            self._draw_header_static()
        if hasattr(self, "nav_inner"):
            self._refill_nav()
        if hasattr(self, "pinbar"):
            self._render_pins()

    def toggle_theme(self):
        self.apply_theme("light" if self.theme_name == "dark" else "dark",
                         animate=True)
        self._persist()

    def _current_geometry(self):
        try:
            return self.root.winfo_geometry()
        except Exception:
            return None

    def _persist(self):
        _save_state({"theme": self.theme_name,
                     "geometry": self._current_geometry(),
                     "pinned": self.pinned, "onboarded": self.onboarded})

    def _on_close(self):
        self._alive = False
        self._persist()
        self.root.destroy()

    def _bind_shortcuts(self):
        self.root.bind("<Control-Return>", lambda _e: self.run_current())
        self.root.bind("<Control-q>", lambda _e: self._on_close())
        self.root.bind("<Control-t>", lambda _e: self.toggle_theme())
        self.root.bind("<Control-k>", lambda _e: self.filter_entry.focus_set())
        self.root.bind("<Control-n>", lambda _e: self.new_window())

    # ----- pinned favourites (drag & drop) -------------------------------- #
    def _build_pinbar(self):
        self.pinbar = self.tk.Frame(self.root, highlightthickness=0, bd=0)
        self.pinbar.pack(side="top", fill="x")
        self._pin_chips = {}
        self._render_pins()

    def _render_pins(self):
        tk, c = self.tk, self.colors
        for ch in self.pinbar.winfo_children():
            ch.destroy()
        self._pin_chips = {}
        self.pinbar.configure(background=c["panel"])
        tk.Label(self.pinbar, text="\U0001f4cc Pinned", background=c["panel"],
                 foreground=c["muted"], font=("Segoe UI", 8, "bold")).pack(
            side="left", padx=(12, 8), pady=5)
        if not self.pinned:
            tk.Label(self.pinbar, text="drag a command here to pin it",
                     background=c["panel"], foreground=c["muted"],
                     font=("Segoe UI", 8, "italic")).pack(side="left", pady=5)
            return
        for name in self.pinned:
            self._make_pin_chip(name)

    def _make_pin_chip(self, name):
        tk, c = self.tk, self.colors
        chip = tk.Frame(self.pinbar, background=_lerp(c["panel"], c["accent"], .14),
                        cursor="hand2")
        gl = tk.Label(chip, text=_icon_for(name),
                      background=chip["background"], foreground=c["ink"],
                      font=("Segoe UI Emoji", 10))
        gl.pack(side="left", padx=(8, 2), pady=2)
        tx = tk.Label(chip, text=name, background=chip["background"],
                      foreground=c["ink"], font=("Segoe UI", 9))
        tx.pack(side="left", padx=(0, 8), pady=2)
        chip.pack(side="left", padx=3, pady=4)
        self._pin_chips[name] = chip
        for w in (chip, gl, tx):
            w.bind("<ButtonPress-1>",
                   lambda e, n=name: self._drag_start(n, e, "pin"))
            w.bind("<B1-Motion>", self._drag_motion)
            w.bind("<ButtonRelease-1>", self._drag_release)
            w.bind("<Button-3>", lambda _e, n=name: self.unpin(n))
            _Tooltip(w, self.commands[name].get("help", "") +
                     "  •  drag to reorder, right-click to unpin")
        return chip

    def pin(self, name):
        if name in self.commands and name not in self.pinned:
            self.pinned.append(name)
            self._render_pins()
            self._persist()
            self._set_status(f"pinned {name}")

    def unpin(self, name):
        if name in self.pinned:
            self.pinned.remove(name)
            self._render_pins()
            self._persist()

    def _reorder_pin(self, name, x_root):
        if name not in self.pinned:
            return
        centers = []
        for n, chip in self._pin_chips.items():
            if n == name:
                continue
            try:
                cx = chip.winfo_rootx() + chip.winfo_width() / 2
            except Exception:
                cx = 0
            centers.append((cx, n))
        idx = sum(1 for cx, _ in centers if cx < x_root)
        order = [n for n in self.pinned if n != name]
        order.insert(idx, name)
        if order != self.pinned:
            self.pinned = order
            self._render_pins()
            self._persist()

    # ----- generic drag (nav button -> pin, or pin reorder) --------------- #
    def _drag_start(self, name, e, kind):
        self._drag = {"name": name, "x": e.x_root, "y": e.y_root,
                      "ghost": None, "moved": False, "kind": kind}

    def _drag_motion(self, e):
        d = self._drag
        if not d:
            return
        if not d["moved"] and max(abs(e.x_root - d["x"]),
                                  abs(e.y_root - d["y"])) > 6:
            d["moved"] = True
            d["ghost"] = self._make_ghost(d["name"])
        if d["moved"] and d["ghost"]:
            try:
                d["ghost"].geometry(f"+{e.x_root + 12}+{e.y_root + 12}")
            except Exception:
                pass

    def _drag_release(self, e):
        d = self._drag
        self._drag = None
        if not d:
            return
        if d["ghost"]:
            try:
                d["ghost"].destroy()
            except Exception:
                pass
        if not d["moved"]:
            self.open_command(d["name"])      # a plain click opens
            return
        if d["kind"] == "nav" and self._over_pinbar(e):
            self.pin(d["name"])
        elif d["kind"] == "pin":
            if self._over_pinbar(e):
                self._reorder_pin(d["name"], e.x_root)
            else:
                self.unpin(d["name"])         # dragged off the bar -> remove

    def _make_ghost(self, name):
        tk, c = self.tk, self.colors
        g = tk.Toplevel(self.root)
        g.overrideredirect(True)
        try:
            g.attributes("-alpha", 0.9)
            g.attributes("-topmost", True)
        except Exception:
            pass
        tk.Label(g, text=f"{_icon_for(name)}  {name}", background=c["accent"],
                 foreground=c["accent_ink"], font=("Segoe UI Semibold", 10),
                 padx=10, pady=5).pack()
        return g

    def _over_pinbar(self, e):
        w = getattr(self, "pinbar", None)
        if not w:
            return False
        try:
            x, y = w.winfo_rootx(), w.winfo_rooty()
            return (x <= e.x_root <= x + w.winfo_width()
                    and y <= e.y_root <= y + w.winfo_height())
        except Exception:
            return False

    # ----- pulse animation on the active command -------------------------- #
    def _animate_pulse(self):
        if not self._alive:
            return
        import math
        self._pulse = (self._pulse + 0.07) % 1.0
        mix = 0.5 - 0.5 * math.cos(self._pulse * 2 * math.pi)
        btn = self._nav.get(self.current)
        if btn:
            try:
                btn["bar"].configure(
                    background=_lerp(self.colors["accent"],
                                     self.colors["accent2"], mix))
            except Exception:
                pass
        self._after(90, self._animate_pulse)

    # ----- multiple windows + onboarding ---------------------------------- #
    def new_window(self, name=None):
        win = _CommandWindow(self, name if name in self.commands else None)
        self._windows.append(win)
        return win

    def show_assistant(self):
        _Assistant(self)

    def mark_onboarded(self):
        self.onboarded = True
        self._persist()

    def _open_web(self):
        import webbrowser
        port = 8730

        def serve():
            try:
                web.serve(port=port, open_browser=False)
            except Exception:
                pass
        threading.Thread(target=serve, daemon=True).start()
        webbrowser.open(f"http://127.0.0.1:{port}")
        self._set_status(f"web UI started at http://127.0.0.1:{port}")

    def _about(self):
        from tkinter import messagebox
        messagebox.showinfo(
            "About Ledgerling",
            f"Ledgerling {self.schema['version']}\n\n"
            "A local, private expense & income ledger.\n"
            "Native desktop UI (Tkinter) over the same commands as the CLI.\n"
            "All data stays in the app's own folder.")


class _CommandWindow:
    """A standalone Toplevel for running one command - lets you work in several
    windows at once (File -> New window, or pop out the current command)."""

    def __init__(self, app, name=None):
        import tkinter as tk
        from tkinter import ttk
        self.app, self.tk, self.ttk = app, tk, ttk
        self.commands = app.commands
        self.currency, self.symbol_after = app.currency, app.symbol_after
        c = app.colors
        self.top = tk.Toplevel(app.root)
        self.top.title("Ledgerling — command")
        self.top.configure(background=c["bg"])
        self.top.minsize(540, 440)
        self.fields = {}
        self._q = queue.Queue()
        self._alive = True

        names = sorted(self.commands)
        self.var = tk.StringVar(value=name if name in self.commands else names[0])
        wrap = ttk.Frame(self.top, style="Main.TFrame", padding=12)
        wrap.pack(fill="both", expand=True)
        bar = ttk.Frame(wrap, style="Main.TFrame")
        bar.pack(fill="x")
        ttk.Label(bar, text="Command:", style="Field.TLabel").pack(side="left")
        combo = ttk.Combobox(bar, textvariable=self.var, state="readonly",
                             values=names, width=26)
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._load())
        self.help = ttk.Label(wrap, text="", style="Muted.TLabel",
                              wraplength=640, justify="left")
        self.help.pack(fill="x", anchor="w", pady=(8, 4))
        self.form = ttk.Frame(wrap, style="Main.TFrame")
        self.form.pack(fill="x", pady=(2, 8))
        rb = ttk.Frame(wrap, style="Main.TFrame")
        rb.pack(fill="x")
        self.run_btn = ttk.Button(rb, text="▶  Run", style="Accent.TButton",
                                  command=self._run)
        self.run_btn.pack(side="left")
        self.out = tk.Text(wrap, height=12, font=("Consolas", 10),
                           state="disabled", background=c["field"],
                           foreground=c["ink"], bd=0, wrap="none")
        self.out.pack(fill="both", expand=True, pady=(10, 0))
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self._load()
        self._poll()

    def _load(self):
        ttk = self.ttk
        for ch in self.form.winfo_children():
            ch.destroy()
        self.fields = {}
        cmd = self.commands[self.var.get()]
        self.help.config(text=cmd.get("help", ""))
        row = 0
        for a in cmd["args"]:
            w = self._field(a, row)
            if w is not None:
                self.fields[a["dest"]] = (w, a)
                row += 1
        self.form.columnconfigure(1, weight=1)

    def _field(self, a, row):
        tk, ttk = self.tk, self.ttk
        req = a["kind"] == "positional" and not a.get("optional")
        ttk.Label(self.form, style="Field.TLabel",
                  text=_humanize(a["dest"]) + (" *" if req else "")).grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        if a.get("type") == "bool":
            var = tk.BooleanVar(value=False)
            w = ttk.Checkbutton(self.form, variable=var,
                                style="Field.TCheckbutton")
        elif a.get("type") == "choice":
            var = tk.StringVar(value="")
            w = ttk.Combobox(self.form, textvariable=var, state="readonly",
                             values=[""] + [str(x) for x in a["choices"]])
        else:
            var = tk.StringVar(value="")
            w = ttk.Entry(self.form, textvariable=var)
            w.bind("<Return>", lambda _e: self._run())
        w.grid(row=row, column=1, sticky="ew", pady=4)
        w._var = var
        return w

    def _collect(self):
        cmd = self.commands[self.var.get()]
        values = {d: w._var.get() for d, (w, _a) in self.fields.items()}
        return build_argv(cmd, values)

    def _run(self):
        argv = self._collect()
        self.run_btn.config(state="disabled", text="Running…")
        self._set("running…")

        def work():
            self._q.put(web.run_cli(argv))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if not self._alive:
            return
        try:
            res = self._q.get_nowait()
            self.run_btn.config(state="normal", text="▶  Run")
            ok = res["code"] == 0
            self._set((res["stdout"] or "(no output)") if ok
                      else (res["stderr"] or res["stdout"] or "error"))
        except queue.Empty:
            pass
        try:
            self.top.after(90, self._poll)
        except Exception:
            self._alive = False

    def _set(self, text):
        self.out.config(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)
        self.out.config(state="disabled")

    def _close(self):
        self._alive = False
        if self in self.app._windows:
            self.app._windows.remove(self)
        self.top.destroy()


class _Assistant:
    """A short, friendly guided tour for new users (Help -> Getting started)."""

    STEPS = [
        ("Welcome to Ledgerling",
         "Your private, local money ledger. Nothing leaves your computer "
         "(except live weather, if you ask for it).\n\nThis quick tour points "
         "out the essentials - you can reopen it any time from Help → "
         "Getting started."),
        ("Find any command",
         "The left sidebar lists every command as an icon button, grouped by "
         "purpose. Type in the filter box (or press Ctrl+K) to narrow the list, "
         "and click a command to open its form."),
        ("Run it",
         "Fill in the fields on the right and press Run (or Ctrl+Enter). Results "
         "appear below as text, and - when the command supports it - as a "
         "sortable Table you can click to re-sort."),
        ("Pin your favourites",
         "Drag a command from the sidebar onto the Pinned bar at the top to keep "
         "it handy. Drag pinned items to reorder them; right-click to unpin."),
        ("Work in several windows",
         "File → New window (Ctrl+N) opens another command window, so you "
         "can run things side by side. 'Pop out current command' detaches the "
         "one you're viewing."),
        ("Make it yours",
         "Toggle the light / green and dark-green themes with Ctrl+T. Explore "
         "the Almanac group - today's briefing, a fortune, a horoscope, and live "
         "weather. Enjoy!"),
    ]

    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk
        self.app, self.tk, self.ttk = app, tk, ttk
        self.i = 0
        c = app.colors
        self.top = tk.Toplevel(app.root)
        self.top.title("Getting started")
        self.top.configure(background=c["panel"])
        self.top.minsize(440, 300)
        self.top.transient(app.root)
        try:
            self.top.grab_set()
        except Exception:
            pass
        self.title = tk.Label(self.top, background=c["panel"],
                              foreground=c["accent"],
                              font=("Segoe UI Semibold", 15), anchor="w",
                              justify="left")
        self.title.pack(fill="x", padx=20, pady=(18, 6))
        self.body = tk.Label(self.top, background=c["panel"],
                             foreground=c["ink"], font=("Segoe UI", 10),
                             wraplength=400, justify="left", anchor="nw")
        self.body.pack(fill="both", expand=True, padx=20)
        self.dots = tk.Label(self.top, background=c["panel"],
                             foreground=c["muted"], font=("Segoe UI", 11))
        self.dots.pack(pady=(4, 2))
        navb = ttk.Frame(self.top, style="Toolbar.TFrame", padding=(12, 10))
        navb.pack(fill="x")
        self.skip = ttk.Button(navb, text="Don't show again", style="Tool.TButton",
                               command=self._dismiss)
        self.skip.pack(side="left")
        self.next_btn = ttk.Button(navb, text="Next →",
                                   style="Accent.TButton", command=self._next)
        self.next_btn.pack(side="right")
        self.back_btn = ttk.Button(navb, text="← Back", style="Tool.TButton",
                                   command=self._back)
        self.back_btn.pack(side="right", padx=(0, 6))
        self.top.protocol("WM_DELETE_WINDOW", self.top.destroy)
        self._render()

    def _render(self):
        title, body = self.STEPS[self.i]
        self.title.config(text=title)
        self.body.config(text=body)
        self.dots.config(text="  ".join("●" if j == self.i else "○"
                                        for j in range(len(self.STEPS))))
        self.back_btn.state(["!disabled"] if self.i else ["disabled"])
        self.next_btn.config(text="Finish" if self.i == len(self.STEPS) - 1
                             else "Next →")

    def _next(self):
        if self.i < len(self.STEPS) - 1:
            self.i += 1
            self._render()
        else:
            self._dismiss()

    def _back(self):
        if self.i:
            self.i -= 1
            self._render()

    def _dismiss(self):
        self.app.mark_onboarded()
        self.top.destroy()


class _Tooltip:
    """A minimal hover tooltip (no dependencies)."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _e):
        if self.tip or not self.text:
            return
        import tkinter as tk
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", background="#0d2117",
                 foreground="#e8f3ec", relief="solid", borderwidth=1,
                 font=("Segoe UI", 9), padx=7, pady=4, wraplength=320).pack()

    def _hide(self, _e):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class _Placeholder:
    """Grey placeholder text in an Entry that clears on focus."""

    def __init__(self, entry, text):
        self.entry, self.text, self.on = entry, text, True
        entry.insert(0, text)
        entry.bind("<FocusIn>", self._clear)
        entry.bind("<FocusOut>", self._restore)

    def _clear(self, _e):
        if self.on:
            self.entry.delete(0, "end")
            self.on = False

    def _restore(self, _e):
        if not self.entry.get():
            self.entry.insert(0, self.text)
            self.on = True


def launch(theme="dark"):
    """Open the desktop window and run the Tk event loop."""
    import tkinter as tk
    root = tk.Tk()
    LedgerlingGUI(root, theme=theme)
    root.mainloop()
