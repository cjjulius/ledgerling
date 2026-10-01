"""A native desktop UI for Ledgerling, built on Tkinter (Python stdlib).

Like the web UI, this generates itself from the CLI parser: every command (and
every future one) is introspected into a form via ``web.describe()`` and run
through ``web.run_cli()``, so the interface stays comprehensive with no
per-command UI code. It is a thin front-end over Ledgerling's own commands -
no shell, no network - and all data stays in the app's own folder.

Launch it with ``ledgerling gui`` (or the windowed ``ledgerling-gui`` exe).
"""

import queue
import threading

from . import cli as L
from . import web

# Sidebar grouping (mirrors the web UI's GROUP_DEFS). Anything not listed falls
# into "More", so a newly added command can never be lost from the tree.
GROUP_DEFS = [
    ("Record", ["add", "income", "edit", "delete", "split", "clone", "note",
                "refund"]),
    ("Analyze", ["month", "insights", "range", "summary", "report", "stats",
                 "week", "weekly", "day", "year", "years", "quarter", "weekday",
                 "trend", "tagtrend", "matrix", "tagmatrix", "cumulative", "top",
                 "compare", "average", "distribution", "balance", "savings",
                 "heatmap", "streak", "pace", "forecast", "sources", "anomalies",
                 "cashflow", "net", "subscriptions", "payees", "categories",
                 "tags", "untagged", "search", "list"]),
    ("Budgets & goals", ["budget", "unbudget", "allowance", "overbudget", "goal",
                         "suggest", "autobudget", "commitments", "upcoming"]),
    ("Calculators", ["tip", "interest", "loan", "target", "runway", "roundup",
                     "fx convert", "fx set", "fx list", "fx rm"]),
    ("Recurring", ["recur add", "recur from", "recur list", "recur edit",
                   "recur remove", "recur run", "recur skip", "recur unskip",
                   "recur pause", "recur resume"]),
    ("Data", ["export", "import", "backup", "restore", "dedupe", "duplicates",
              "retag", "tag", "untag", "recategorize", "check", "undo"]),
    ("Settings", ["config", "where", "version", "completion", "web", "gui"]),
]

# Quick-access toolbar: (glyph, command name, tooltip). Glyphs keep the look
# icon-like while staying dependency-free (no bundled image assets).
TOOLBAR = [
    ("➕", "add", "Record an expense"),
    ("\U0001f4b0", "income", "Record income"),
    ("\U0001f4ca", "summary", "Category summary"),
    ("\U0001f50d", "search", "Search entries"),
    ("\U0001f4c5", "upcoming", "Upcoming recurring charges"),
    ("⚙", "config", "Settings"),
]

# Two palettes; toggled at runtime. ttk needs explicit colors to look modern.
THEMES = {
    "dark": {
        "bg": "#14161a", "panel": "#1b1e24", "ink": "#e7e9ee",
        "muted": "#9aa3b2", "line": "#2a2f38", "accent": "#5b9cff",
        "accent_ink": "#0b1020", "err": "#ff9b8a", "field": "#0f1115",
        "sel": "#263040",
    },
    "light": {
        "bg": "#f4f5f7", "panel": "#ffffff", "ink": "#1c2330",
        "muted": "#5c6472", "line": "#dfe3ea", "accent": "#2f6bff",
        "accent_ink": "#ffffff", "err": "#b23b2e", "field": "#ffffff",
        "sel": "#dce7ff",
    },
}


def _group_of(name):
    for g, names in GROUP_DEFS:
        if name in names:
            return g
    return "More"


def _humanize(dest):
    return dest.replace("_", " ").strip().capitalize()


class LedgerlingGUI:
    def __init__(self, root, theme="dark"):
        import tkinter as tk  # local imports so importing this module is cheap
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = root
        self.schema = web.describe()
        self.commands = {c["name"]: c for c in self.schema["commands"]}
        self.currency = self.schema.get("currency", "$")
        self.theme_name = theme
        self.current = None       # selected command name
        self.fields = {}          # dest -> (widget, arg-spec)
        self._results = queue.Queue()

        root.title("Ledgerling")
        root.minsize(900, 560)
        try:
            root.geometry("1040x660")
        except Exception:
            pass

        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self.apply_theme(self.theme_name)
        self._bind_shortcuts()
        self._poll_results()

        # Open a friendly default command.
        self.open_command("summary" if "summary" in self.commands else
                          next(iter(self.commands)))

    # ----- layout ---------------------------------------------------------- #
    def _build_menu(self):
        tk = self.tk
        bar = tk.Menu(self.root)

        filem = tk.Menu(bar, tearoff=0)
        filem.add_command(label="Run command", accelerator="Ctrl+Enter",
                          command=self.run_current)
        filem.add_separator()
        filem.add_command(label="Open web UI", command=self._open_web)
        filem.add_separator()
        filem.add_command(label="Quit", accelerator="Ctrl+Q",
                          command=self.root.destroy)
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
        helpm.add_command(label="About Ledgerling", command=self._about)
        bar.add_cascade(label="Help", menu=helpm)

        self.root.config(menu=bar)

    def _build_toolbar(self):
        ttk = self.ttk
        self.toolbar = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(8, 6))
        self.toolbar.pack(side="top", fill="x")
        for glyph, name, tip in TOOLBAR:
            if name not in self.commands:
                continue
            b = ttk.Button(self.toolbar, text=f"{glyph}  {name}",
                           style="Tool.TButton",
                           command=lambda n=name: self.open_command(n))
            b.pack(side="left", padx=(0, 6))
            _Tooltip(b, tip)
        self.theme_btn = ttk.Button(self.toolbar, text="◑ theme",
                                    style="Tool.TButton", command=self.toggle_theme)
        self.theme_btn.pack(side="right")

    def _build_body(self):
        tk, ttk = self.tk, self.ttk
        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(side="top", fill="both", expand=True)

        # --- left: filter + grouped command tree ---
        left = ttk.Frame(body, style="Side.TFrame", padding=8)
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(left, textvariable=self.filter_var)
        self.filter_entry.pack(side="top", fill="x")
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse",
                                 style="Side.Treeview")
        self.tree.pack(side="top", fill="both", expand=True, pady=(8, 0))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        body.add(left, weight=1)
        # Wire the live filter only after the tree exists (the placeholder's
        # own insert would otherwise refill the tree before it's built).
        _Placeholder(self.filter_entry, "Filter commands  (Ctrl+K)")
        self.filter_var.trace_add("write", lambda *_: self._refill_tree())

        # --- right: form + output ---
        right = ttk.Frame(body, style="Main.TFrame", padding=12)
        self.cmd_title = ttk.Label(right, text="", style="Title.TLabel")
        self.cmd_title.pack(side="top", anchor="w")
        self.cmd_help = ttk.Label(right, text="", style="Muted.TLabel",
                                  wraplength=640, justify="left")
        self.cmd_help.pack(side="top", anchor="w", pady=(2, 10))

        self.form = ttk.Frame(right, style="Main.TFrame")
        self.form.pack(side="top", fill="x")

        runbar = ttk.Frame(right, style="Main.TFrame")
        runbar.pack(side="top", fill="x", pady=(10, 8))
        self.run_btn = ttk.Button(runbar, text="Run  (Ctrl+Enter)",
                                  style="Accent.TButton", command=self.run_current)
        self.run_btn.pack(side="left")
        ttk.Button(runbar, text="Copy output", style="Tool.TButton",
                   command=self._copy_output).pack(side="left", padx=(8, 0))

        outwrap = ttk.Frame(right, style="Main.TFrame")
        outwrap.pack(side="top", fill="both", expand=True)
        self.output = tk.Text(outwrap, wrap="none", height=14, borderwidth=0,
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

        body.add(right, weight=3)
        self._refill_tree()

    def _build_statusbar(self):
        ttk = self.ttk
        self.status = ttk.Label(
            self.root, style="Status.TLabel", anchor="w",
            text=f"Ledgerling {self.schema['version']}   •   "
                 f"currency {self.currency}   •   ready")
        self.status.pack(side="bottom", fill="x")

    # ----- tree ------------------------------------------------------------ #
    def _refill_tree(self):
        q = (self.filter_var.get() or "").strip().lower()
        if q == "filter commands  (ctrl+k)":   # placeholder text, treat as empty
            q = ""
        self.tree.delete(*self.tree.get_children())
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
            gid = self.tree.insert("", "end", text=group, open=True,
                                   tags=("group",))
            for n in listed:
                self.tree.insert(gid, "end", iid=n, text="   " + n,
                                 tags=("cmd",))
        self.tree.tag_configure("group", font=("Segoe UI", 9, "bold"))

    def _on_tree_select(self, _ev):
        sel = self.tree.selection()
        if sel and sel[0] in self.commands:
            self.open_command(sel[0])

    # ----- command form ---------------------------------------------------- #
    def open_command(self, name):
        if name not in self.commands:
            return
        self.current = name
        cmd = self.commands[name]
        self.cmd_title.config(text=name)
        self.cmd_help.config(text=cmd.get("help", ""))
        if self.tree.exists(name) and self.tree.selection() != (name,):
            self.tree.selection_set(name)
            self.tree.see(name)

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
                           style="Muted.TLabel").grid(row=0, column=0,
                                                      sticky="w")
        self.form.columnconfigure(1, weight=1)
        # Focus the first editable field for immediate typing.
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
        lbl = ttk.Label(parent, style="Field.TLabel",
                        text=label + (" *" if req else ""))
        lbl.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)

        if a.get("type") == "bool":
            var = tk.BooleanVar(value=False)
            chk = ttk.Checkbutton(parent, variable=var, style="Field.TCheckbutton")
            chk.grid(row=row, column=1, sticky="w", pady=4)
            chk._var = var
            return chk
        if a.get("type") == "choice":
            var = tk.StringVar(value="")
            combo = ttk.Combobox(parent, textvariable=var, state="readonly",
                                 values=[""] + [str(c) for c in a["choices"]])
            combo.grid(row=row, column=1, sticky="ew", pady=4)
            combo._var = var
            return combo
        var = tk.StringVar(value="")
        ent = ttk.Entry(parent, textvariable=var)
        ent.grid(row=row, column=1, sticky="ew", pady=4)
        ent._var = var
        if a.get("help"):
            _Tooltip(ent, a["help"])
        # Enter in any field runs the command.
        ent.bind("<Return>", lambda _e: self.run_current())
        return ent

    def _collect_argv(self):
        """Build argv from the form, mirroring the web UI's buildArgv."""
        cmd = self.commands[self.current]
        positionals, options = [], []
        for a in cmd["args"]:
            w, _ = self.fields.get(a["dest"], (None, None))
            if w is None:
                continue
            if a.get("type") == "bool":
                if bool(w._var.get()):
                    options += [a["flag"]]
                continue
            val = str(w._var.get()).strip()
            if a["kind"] == "positional":
                if not val:
                    continue
                if a.get("variadic"):
                    positionals += val.split()
                else:
                    positionals.append(val)
            else:
                if val:
                    options += [a["flag"], val]
        return list(cmd["argv"]) + positionals + options

    # ----- running --------------------------------------------------------- #
    def run_current(self):
        if not self.current:
            return
        argv = self._collect_argv()
        self.run_btn.config(state="disabled", text="Running…")
        self._set_status(f"running: {' '.join(argv)}")
        self._set_output("running…", err=False)

        def work():
            res = web.run_cli(argv)
            self._results.put((argv, res))

        threading.Thread(target=work, daemon=True).start()

    def _poll_results(self):
        try:
            while True:
                argv, res = self._results.get_nowait()
                self._show_result(argv, res)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_results)

    def _show_result(self, argv, res):
        self.run_btn.config(state="normal", text="Run  (Ctrl+Enter)")
        ok = res["code"] == 0
        if ok:
            text = res["stdout"] or "(no output)"
        else:
            text = res["stderr"] or res["stdout"] or "error"
        self._set_output(text, err=not ok)
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

    def _set_status(self, msg):
        self.status.config(text=f"Ledgerling {self.schema['version']}   "
                                f"•   {msg}")

    # ----- theme / shortcuts / misc ---------------------------------------- #
    def apply_theme(self, name):
        tk, ttk = self.tk, self.ttk
        c = THEMES.get(name, THEMES["dark"])
        self.theme_name = name
        style = ttk.Style()
        try:
            style.theme_use("clam")   # the most themable built-in ttk theme
        except Exception:
            pass
        self.root.configure(background=c["bg"])
        style.configure(".", background=c["bg"], foreground=c["ink"],
                        fieldbackground=c["field"], bordercolor=c["line"])
        style.configure("Toolbar.TFrame", background=c["panel"])
        style.configure("Side.TFrame", background=c["panel"])
        style.configure("Main.TFrame", background=c["bg"])
        style.configure("Title.TLabel", background=c["bg"], foreground=c["ink"],
                        font=("Segoe UI Semibold", 15))
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"])
        style.configure("Field.TLabel", background=c["bg"], foreground=c["ink"])
        style.configure("Field.TCheckbutton", background=c["bg"])
        style.configure("Status.TLabel", background=c["panel"],
                        foreground=c["muted"], padding=(10, 4))
        style.configure("Tool.TButton", padding=(10, 5))
        style.configure("Accent.TButton", padding=(14, 6),
                        background=c["accent"], foreground=c["accent_ink"])
        style.map("Accent.TButton",
                  background=[("active", c["accent"])])
        style.configure("Side.Treeview", background=c["panel"],
                        fieldbackground=c["panel"], foreground=c["ink"],
                        borderwidth=0, rowheight=24)
        style.map("Side.Treeview", background=[("selected", c["sel"])],
                  foreground=[("selected", c["ink"])])
        if hasattr(self, "output"):
            self.output.configure(background=c["field"], foreground=c["ink"],
                                  insertbackground=c["ink"],
                                  selectbackground=c["sel"])
            self.output.tag_configure("err", foreground=c["err"])

    def toggle_theme(self):
        self.apply_theme("light" if self.theme_name == "dark" else "dark")

    def _bind_shortcuts(self):
        self.root.bind("<Control-Return>", lambda _e: self.run_current())
        self.root.bind("<Control-q>", lambda _e: self.root.destroy())
        self.root.bind("<Control-t>", lambda _e: self.toggle_theme())
        self.root.bind("<Control-k>",
                       lambda _e: self.filter_entry.focus_set())

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


class _Tooltip:
    """A minimal hover tooltip (no dependencies)."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _e):
        if self.tip or not self.text:
            return
        import tkinter as tk
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", background="#222a35",
                 foreground="#e7e9ee", relief="solid", borderwidth=1,
                 font=("Segoe UI", 9), padx=6, pady=3).pack()

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
