"""A local, sandboxed web UI for Ledgerling.

The UI generates itself from the CLI parser: every command (and every future
one) is introspected into a form, so the interface stays comprehensive without
per-command UI code. It is a thin front-end that runs Ledgerling's own
commands - no shell, no network egress - and binds only to 127.0.0.1.
"""

import argparse
import io
import json
import threading
import webbrowser
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cli as L

# run_cli redirects the process-wide sys.stdout/stderr to capture a command's
# output, so concurrent requests would clobber each other's capture. Serialize
# them (commands are fast and purely local, so this is not a bottleneck).
_RUN_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Parser introspection -> UI schema
# --------------------------------------------------------------------------- #

def _describe_action(a):
    d = {"dest": a.dest, "help": a.help or ""}
    if isinstance(a, argparse._StoreTrueAction):
        d.update(kind="option", type="bool",
                 flag=next(o for o in a.option_strings if o.startswith("--")))
        return d
    if a.option_strings:
        flag = next((o for o in a.option_strings if o.startswith("--")),
                    a.option_strings[0])
        d.update(kind="option", flag=flag)
    else:
        d.update(kind="positional",
                 optional=a.nargs in ("?", "*"))
    if a.choices:
        d.update(type="choice", choices=list(a.choices))
    elif a.type is int:
        d["type"] = "int"
    elif a.type is float:
        d["type"] = "float"
    else:
        d["type"] = "str"
    return d


def describe():
    """Return the full command schema the UI renders from."""
    parser = L.build_parser()
    top = next(a for a in parser._actions
               if isinstance(a, argparse._SubParsersAction))
    commands = []

    def walk(sp_action, prefix):
        help_map = {ca.dest: ca.help for ca in sp_action._choices_actions}
        for name, sub in sp_action.choices.items():
            nested = [a for a in sub._actions
                      if isinstance(a, argparse._SubParsersAction)]
            if nested:
                walk(nested[0], prefix + [name])
                continue
            args = [_describe_action(a) for a in sub._actions
                    if not isinstance(a, (argparse._HelpAction,
                                          argparse._SubParsersAction))]
            commands.append({
                "name": " ".join(prefix + [name]),
                "argv": prefix + [name],
                "help": help_map.get(name, ""),
                "args": args,
            })

    walk(top, [])
    commands.sort(key=lambda c: c["name"])
    currency = L.load_config().get("currency", "$")
    return {"version": L.__version__, "currency": currency, "commands": commands}


# --------------------------------------------------------------------------- #
# Running a command (captures output, never lets the server die)
# --------------------------------------------------------------------------- #

def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with _RUN_LOCK:  # serialize: stdout/stderr capture is process-wide
        with redirect_stdout(out), redirect_stderr(err):
            try:
                L.main(list(argv))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
            except Exception as exc:  # never crash the request
                code = 1
                err.write(f"internal error: {exc}\n")
    return {"code": code, "stdout": out.getvalue(), "stderr": err.getvalue()}


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # keep the console quiet

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif self.path == "/api/describe":
            self._send(200, json.dumps(describe()))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/api/run":
            self._send(404, json.dumps({"error": "not found"}))
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            argv = payload.get("argv", [])
            if not isinstance(argv, list) or not all(isinstance(x, str)
                                                     for x in argv):
                raise ValueError("argv must be a list of strings")
        except (json.JSONDecodeError, ValueError) as exc:
            self._send(400, json.dumps({"error": str(exc)}))
            return
        self._send(200, json.dumps(run_cli(argv)))


def make_server(port=0):
    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


def serve(port=8730, open_browser=True):
    httpd = make_server(port)
    actual = httpd.server_address[1]
    url = f"http://127.0.0.1:{actual}/"
    print(f"Ledgerling web UI running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# The single-page UI (self-contained; renders itself from /api/describe)
# --------------------------------------------------------------------------- #

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ledgerling</title>
<style>
  :root {
    --bg:#eef1f4; --panel:#ffffff; --panel2:#f7f9fb; --ink:#1b2330; --muted:#6a7684;
    --line:#e0e5ea; --accent:#2f6f4f; --accent2:#3f8a63; --accent-ink:#ffffff;
    --pos:#2f8f5b; --neg:#c8503a; --code:#0f1720; --code-ink:#d7e3d9;
    --shadow:0 1px 2px rgba(20,30,45,.06), 0 6px 18px rgba(20,30,45,.06);
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0e1217; --panel:#171d24; --panel2:#1d242c; --ink:#e6ebf0;
      --muted:#98a4b0; --line:#2a323c; --accent:#4f9e73; --accent2:#5fae83;
      --accent-ink:#08120c; --pos:#5cba86; --neg:#e88b76; --code:#0b0f14;
      --code-ink:#cfe6d8; --shadow:0 1px 2px rgba(0,0,0,.3), 0 8px 22px rgba(0,0,0,.35); }
  }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--bg); color:var(--ink); }
  header { display:flex; align-items:center; gap:12px; padding:12px 20px;
    border-bottom:1px solid var(--line); background:var(--panel); position:sticky;
    top:0; z-index:5; }
  header .brand { display:flex; align-items:baseline; gap:9px; }
  header .logo { width:22px; height:22px; border-radius:7px; background:
    linear-gradient(135deg,var(--accent2),var(--accent)); display:inline-block;
    position:relative; top:3px; box-shadow:var(--shadow); }
  header h1 { font-size:19px; margin:0; letter-spacing:.2px; font-weight:700; }
  header .ver { color:var(--muted); font-size:12px; }
  header .tag { margin-left:auto; color:var(--muted); font-size:12px;
    display:flex; align-items:center; gap:6px; }
  header .tag::before { content:""; width:8px; height:8px; border-radius:50%;
    background:var(--pos); box-shadow:0 0 0 3px color-mix(in srgb,var(--pos) 25%,transparent); }
  .wrap { display:grid; grid-template-columns:264px 1fr; gap:0;
    height:calc(100vh - 52px); }
  .side { border-right:1px solid var(--line); background:var(--panel);
    overflow:auto; padding:14px 12px; }
  .side .newbtn { width:100%; padding:10px 12px; border:0; border-radius:10px;
    background:var(--accent); color:var(--accent-ink); font-weight:600; cursor:pointer;
    font-size:14px; margin-bottom:10px; box-shadow:var(--shadow); }
  .side .newbtn:hover { background:var(--accent2); }
  .side .filter { width:100%; padding:8px 11px; border:1px solid var(--line);
    border-radius:9px; background:var(--panel2); color:var(--ink); margin-bottom:12px;
    font-size:13px; }
  .navitem { padding:8px 11px; border-radius:9px; cursor:pointer; display:flex;
    flex-direction:column; gap:1px; }
  .navitem:hover { background:var(--panel2); }
  .navitem.active { background:var(--accent); color:var(--accent-ink); box-shadow:var(--shadow); }
  .navitem .n { font-size:13.5px; font-weight:500; }
  .navitem .h { font-size:11.5px; color:var(--muted); overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap; }
  .navitem.active .h { color:var(--accent-ink); opacity:.85; }
  .navitem.home .n { font-weight:600; }
  .grouphd { font-size:11px; text-transform:uppercase; letter-spacing:.07em;
    color:var(--muted); font-weight:700; margin:14px 8px 4px; }
  main { overflow:auto; padding:24px 28px 40px; }
  .page-h { display:flex; align-items:flex-end; gap:12px; flex-wrap:wrap;
    margin-bottom:18px; }
  .page-h h2 { margin:0; font-size:22px; font-weight:700; }
  .page-h .sub { color:var(--muted); margin:0; font-size:14px; }
  .page-h .spacer { flex:1; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:18px; box-shadow:var(--shadow); }
  .card h3 { margin:0 0 12px; font-size:13px; text-transform:uppercase;
    letter-spacing:.05em; color:var(--muted); font-weight:700; }
  .grid { display:grid; gap:16px; }
  .stat-grid { grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); }
  .dash-grid { grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); align-items:start; }
  .stat { }
  .stat .k { font-size:12px; text-transform:uppercase; letter-spacing:.05em;
    color:var(--muted); font-weight:700; margin-bottom:6px; }
  .stat .v { font-size:28px; font-weight:700; font-variant-numeric:tabular-nums;
    letter-spacing:-.5px; }
  .stat .m { font-size:12px; color:var(--muted); margin-top:3px; }
  .v.pos { color:var(--pos); } .v.neg { color:var(--neg); }
  .rowbar { display:grid; grid-template-columns:1fr auto; gap:4px 10px; align-items:center;
    margin-bottom:11px; }
  .rowbar:last-child { margin-bottom:0; }
  .rowbar .lab { font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .rowbar .amt { font:12px ui-monospace,Menlo,Consolas,monospace; text-align:right;
    color:var(--muted); }
  .rowbar .track { grid-column:1/3; height:8px; border-radius:6px; background:var(--panel2);
    overflow:hidden; }
  .rowbar .fill { height:100%; border-radius:6px; background:var(--accent);
    min-width:2px; transition:width .3s; }
  .rowbar .fill.over { background:var(--neg); }
  .pill { display:inline-block; padding:2px 9px; border-radius:20px; font-size:11px;
    font-weight:600; background:color-mix(in srgb,var(--accent) 15%,transparent);
    color:var(--accent2); }
  .pill.over { background:color-mix(in srgb,var(--neg) 16%,transparent); color:var(--neg); }
  .goalwrap { text-align:center; }
  .goalwrap .big { font-size:26px; font-weight:700; margin:4px 0; }
  .muted { color:var(--muted); }
  .empty { color:var(--muted); padding:36px 0; text-align:center; }
  .monthpick { padding:7px 10px; border:1px solid var(--line); border-radius:9px;
    background:var(--panel); color:var(--ink); font-size:13px; }
  form { display:grid; gap:14px; }
  .field label { display:block; font-weight:600; margin-bottom:4px; font-size:13px; }
  .field .help { color:var(--muted); font-size:12px; margin-bottom:5px; }
  .field input[type=text], .field input[type=number], .field select {
    width:100%; padding:9px 11px; border:1px solid var(--line); border-radius:9px;
    background:var(--panel2); color:var(--ink); font-size:14px; }
  .field input:focus, .field select:focus, .monthpick:focus {
    outline:2px solid color-mix(in srgb,var(--accent) 45%,transparent); outline-offset:1px;
    border-color:var(--accent); }
  .field.bool label { display:flex; align-items:center; gap:8px; font-weight:500; }
  .req { color:var(--neg); }
  button.run { justify-self:start; padding:10px 22px; border:0; border-radius:10px;
    background:var(--accent); color:var(--accent-ink); font-weight:600; cursor:pointer;
    font-size:14px; box-shadow:var(--shadow); }
  button.run:hover { background:var(--accent2); }
  .out { margin-top:18px; }
  pre { background:var(--code); color:var(--code-ink); padding:15px 17px;
    border-radius:11px; overflow:auto; font:13px/1.5 ui-monospace,SFMono-Regular,
    Menlo,Consolas,monospace; white-space:pre-wrap; word-break:break-word; min-height:40px;
    margin:0; }
  pre.err { color:#ff9b8a; }
  .tabs { display:flex; gap:6px; margin:0 0 12px; }
  .tab { padding:6px 14px; border:1px solid var(--line); background:var(--panel);
    color:var(--muted); border-radius:20px; cursor:pointer; font-size:13px; font-weight:500; }
  .tab:hover { color:var(--ink); }
  .tab.active { background:var(--accent); color:var(--accent-ink); border-color:var(--accent); }
  #outtable { overflow:auto; }
  table.data { border-collapse:collapse; width:100%; font-size:13px; }
  table.data th, table.data td { padding:7px 11px; text-align:left; vertical-align:top;
    border-bottom:1px solid var(--line); }
  table.data thead th { background:var(--panel2); position:sticky; top:0; font-weight:700;
    color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  table.data tbody tr:hover { background:var(--panel2); }
  table.data tbody th { background:var(--panel2); white-space:nowrap; }
  .kv > div { padding:5px 2px; border-bottom:1px solid var(--line);
    font:13px ui-monospace,Menlo,Consolas,monospace; }
  .chart { display:flex; flex-direction:column; gap:6px; }
  .chart .metric { margin-bottom:8px; }
  .chart .metric select { padding:6px 10px; border:1px solid var(--line);
    border-radius:9px; background:var(--panel2); color:var(--ink); }
  .crow { display:grid; grid-template-columns:130px 1fr 96px; align-items:center; gap:10px; }
  .clab { font-size:13px; color:var(--muted); white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; }
  .cbarwrap { background:var(--panel2); border-radius:6px; height:20px; overflow:hidden; }
  .cbar { background:var(--accent); height:100%; border-radius:6px; min-width:2px; }
  .cval { font:12px ui-monospace,Menlo,Consolas,monospace; text-align:right; }
  .cal { display:flex; flex-direction:column; gap:8px; max-width:440px; }
  .cal .cgrid { display:grid; grid-template-columns:repeat(7,1fr); gap:5px; }
  .cal .cdow { font-size:11px; color:var(--muted); text-align:center; padding:2px 0; font-weight:600; }
  .cal .ccell { aspect-ratio:1; border:1px solid var(--line); border-radius:8px;
    display:flex; flex-direction:column; justify-content:space-between; padding:5px 6px; }
  .cal .ccell.pad { border:none; background:transparent; }
  .cal .ccell .dnum { font-size:11px; color:var(--muted); }
  .cal .ccell .damt { font:11px ui-monospace,Menlo,Consolas,monospace; text-align:right; }
  .cal .clegend { display:flex; align-items:center; gap:6px; font-size:12px;
    color:var(--muted); }
  .cal .cswatch { width:16px; height:16px; border-radius:5px; border:1px solid var(--line); }
  .inslist { display:flex; flex-direction:column; gap:10px; }
  .insrow { display:flex; gap:11px; align-items:flex-start; padding:11px 13px;
    background:var(--panel2); border:1px solid var(--line); border-radius:11px; }
  .insrow .dot { width:8px; height:8px; border-radius:50%; background:var(--accent);
    margin-top:7px; flex:none; }
  .insrow .txt { font-size:14px; }
  .recent { display:flex; flex-direction:column; }
  .recrow { display:grid; grid-template-columns:auto 1fr auto; gap:10px;
    align-items:baseline; padding:8px 2px; border-bottom:1px solid var(--line); }
  .recrow:last-child { border-bottom:0; }
  .recrow .rdate { font:12px ui-monospace,Menlo,Consolas,monospace; color:var(--muted); }
  .recrow .rcat { font-size:13px; overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; }
  .recrow .ramt { font:12px ui-monospace,Menlo,Consolas,monospace; text-align:right;
    font-weight:600; }
  .recrow .ramt.pos { color:var(--pos); }
  @media (max-width:720px) {
    .wrap { grid-template-columns:1fr; height:auto; }
    .side { border-right:0; border-bottom:1px solid var(--line); max-height:40vh; }
    header .tag span { display:none; }
  }
</style>
</head>
<body>
<header>
  <div class="brand"><span class="logo"></span><h1>Ledgerling</h1>
    <span class="ver" id="ver"></span></div>
  <span class="tag"><span>local &amp; sandboxed &mdash; data stays in your Ledgerling folder</span></span>
</header>
<div class="wrap">
  <nav class="side">
    <button class="newbtn" id="newbtn">+ New expense</button>
    <input class="filter" id="filter" placeholder="Filter commands...">
    <div id="list"></div>
  </nav>
  <main id="main"><div class="empty">Loading&hellip;</div></main>
</div>
<script>
let COMMANDS = [], CURRENT = null, CURRENCY = '$', ACTIVE = 'home';

// Command groups for the sidebar. Any command not listed here (e.g. a newly
// added one) still shows up automatically under "More", so the nav stays
// comprehensive without per-command edits.
const GROUP_DEFS = [
  ['Record', ['add', 'income', 'edit', 'delete', 'clone', 'note', 'refund']],
  ['Analyze', ['month', 'insights', 'range', 'summary', 'report', 'stats', 'week', 'day', 'year',
    'quarter', 'weekday', 'trend', 'tagtrend', 'matrix', 'cumulative', 'top', 'compare', 'average', 'distribution',
    'balance', 'savings', 'heatmap', 'streak', 'pace', 'forecast', 'sources',
    'categories', 'tags', 'untagged', 'search', 'list']],
  ['Budgets & goals', ['budget', 'unbudget', 'allowance', 'goal', 'suggest', 'autobudget', 'commitments', 'upcoming']],
  ['Recurring', ['recur add', 'recur list', 'recur edit', 'recur remove', 'recur run']],
  ['Data', ['export', 'import', 'backup', 'restore', 'dedupe', 'duplicates',
    'retag', 'tag', 'untag', 'recategorize', 'undo']],
  ['Settings', ['config', 'version', 'completion', 'web']],
];
function groupOf(name) {
  for (const [g, names] of GROUP_DEFS) if (names.includes(name)) return g;
  return 'More';
}

function esc(s){ return String(s).replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }
function findCmd(name) { return COMMANDS.find(c => c.name === name); }
function curMonth() {
  const n = new Date();
  return n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0');
}
function money(n) {
  const neg = n < 0;
  const s = CURRENCY + Math.abs(Number(n) || 0).toLocaleString(undefined,
    {minimumFractionDigits: 2, maximumFractionDigits: 2});
  return neg ? '-' + s : s;
}

async function boot() {
  const d = await fetch('/api/describe').then(r => r.json());
  COMMANDS = d.commands; CURRENCY = d.currency || '$';
  document.getElementById('ver').textContent = 'v' + d.version;
  document.getElementById('filter').addEventListener('input',
    e => renderList(e.target.value));
  document.getElementById('newbtn').onclick = () => {
    const a = findCmd('add'); if (a) selectCmd(a);
  };
  renderList('');
  showDashboard();
}

function navItem(name, help, active, onclick) {
  const el = document.createElement('div');
  el.className = 'navitem' + (active ? ' active' : '');
  el.innerHTML = '<div class="n">' + esc(name) + '</div>' +
    (help ? '<div class="h">' + esc(help) + '</div>' : '');
  el.onclick = onclick;
  return el;
}

function renderList(q) {
  q = (q || '').trim().toLowerCase();
  const list = document.getElementById('list');
  list.innerHTML = '';
  if (!q || 'dashboard'.includes(q)) {
    const h = navItem('Dashboard', 'This month at a glance', ACTIVE === 'home',
      () => showDashboard());
    h.classList.add('home'); list.appendChild(h);
  }
  const match = COMMANDS.filter(c => !q || c.name.includes(q) ||
    (c.help || '').toLowerCase().includes(q));
  const groups = {};
  match.forEach(c => { (groups[groupOf(c.name)] = groups[groupOf(c.name)] || []).push(c); });
  GROUP_DEFS.map(g => g[0]).concat(['More']).forEach(g => {
    const items = groups[g]; if (!items || !items.length) return;
    const hd = document.createElement('div'); hd.className = 'grouphd';
    hd.textContent = g; list.appendChild(hd);
    items.forEach(c => list.appendChild(
      navItem(c.name, c.help || '', ACTIVE === c.name, () => selectCmd(c))));
  });
}

function selectCmd(c) {
  ACTIVE = c.name; CURRENT = c;
  renderList(document.getElementById('filter').value);
  const m = document.getElementById('main'); m.innerHTML = '';
  const ph = document.createElement('div'); ph.className = 'page-h';
  ph.innerHTML = '<div><h2>' + esc(c.name) + '</h2><p class="sub">' +
    esc(c.help || '') + '</p></div>';
  m.appendChild(ph);
  const fcard = document.createElement('div'); fcard.className = 'card';
  const form = document.createElement('form');
  c.args.forEach(a => form.appendChild(fieldFor(a)));
  if (!c.args.length) {
    const p = document.createElement('p'); p.className = 'muted';
    p.textContent = 'No options - just run it.'; form.appendChild(p);
  }
  const btn = document.createElement('button'); btn.className = 'run';
  btn.textContent = 'Run ' + c.name; form.appendChild(btn);
  form.onsubmit = ev => { ev.preventDefault(); runCmd(c, form); };
  fcard.appendChild(form); m.appendChild(fcard);
  const out = document.createElement('div'); out.className = 'out card'; out.id = 'out';
  out.innerHTML = '<div class="tabs" id="tabs"></div>' +
    '<pre id="outpre">(run the command to see output)</pre>' +
    '<div id="outtable" style="display:none"></div>' +
    '<div id="outchart" style="display:none"></div>' +
    '<div id="outcal" style="display:none"></div>';
  m.appendChild(out);
}

// --- Dashboard (bespoke home view, built from `month --json`) --------------- //
function muted(t) { const p = document.createElement('p'); p.className = 'muted';
  p.textContent = t; return p; }

function statCard(k, v, sub, cls) {
  const c = document.createElement('div'); c.className = 'card stat';
  c.innerHTML = '<div class="k">' + esc(k) + '</div>' +
    '<div class="v ' + cls + '">' + esc(v) + '</div>' +
    '<div class="m">' + esc(sub) + '</div>';
  return c;
}

function barRow(label, amt, frac, over) {
  const r = document.createElement('div'); r.className = 'rowbar';
  const l = document.createElement('div'); l.className = 'lab'; l.textContent = label;
  const a = document.createElement('div'); a.className = 'amt'; a.textContent = amt;
  const t = document.createElement('div'); t.className = 'track';
  const f = document.createElement('div'); f.className = 'fill' + (over ? ' over' : '');
  f.style.width = Math.max(0, Math.min(1, frac || 0)) * 100 + '%';
  t.appendChild(f); r.appendChild(l); r.appendChild(a); r.appendChild(t);
  return r;
}

function topCatCard(byCat) {
  const c = document.createElement('div'); c.className = 'card';
  const h = document.createElement('h3'); h.textContent = 'Top categories'; c.appendChild(h);
  const entries = Object.entries(byCat || {}).sort((a, b) => b[1] - a[1]).slice(0, 6);
  if (!entries.length) { c.appendChild(muted('No spending yet.')); return c; }
  const max = Math.max.apply(null, entries.map(e => e[1]));
  entries.forEach(([cat, amt]) => c.appendChild(barRow(cat, money(amt), amt / max, false)));
  return c;
}

function budgetCard(budgets) {
  const c = document.createElement('div'); c.className = 'card';
  const h = document.createElement('h3'); h.textContent = 'Budgets'; c.appendChild(h);
  Object.keys(budgets).sort().forEach(cat => {
    const b = budgets[cat]; const frac = b.limit ? b.spent / b.limit : 0;
    c.appendChild(barRow(cat, money(b.spent) + ' / ' + money(b.limit), frac, b.spent > b.limit));
  });
  return c;
}

function goalCard(goal, net) {
  const c = document.createElement('div'); c.className = 'card';
  const h = document.createElement('h3'); h.textContent = 'Savings goal'; c.appendChild(h);
  if (goal === null || goal === undefined) {
    c.appendChild(muted('No monthly goal set.'));
    const b = document.createElement('button'); b.className = 'run';
    b.style.marginTop = '10px'; b.textContent = 'Set a goal';
    b.onclick = () => { const g = findCmd('goal'); if (g) selectCmd(g); };
    c.appendChild(b); return c;
  }
  const met = net >= goal;
  const frac = goal > 0 ? net / goal : (net >= 0 ? 1 : 0);
  const w = document.createElement('div'); w.className = 'goalwrap';
  w.innerHTML = '<div class="big">' + esc(money(net)) + '</div>' +
    '<div class="muted">of ' + esc(money(goal)) + ' goal</div>';
  c.appendChild(w);
  c.appendChild(barRow('progress', Math.round(Math.max(0, frac) * 100) + '%', frac, false));
  const p = document.createElement('div'); p.style.marginTop = '10px';
  p.style.textAlign = 'center';
  p.innerHTML = met ? '<span class="pill">met (+' + esc(money(net - goal)) + ')</span>'
    : '<span class="pill over">' + esc(money(goal - net)) + ' to go</span>';
  c.appendChild(p); return c;
}

async function showDashboard(month) {
  ACTIVE = 'home'; CURRENT = null;
  renderList(document.getElementById('filter').value);
  month = month || curMonth();
  const m = document.getElementById('main'); m.innerHTML = '';
  const ph = document.createElement('div'); ph.className = 'page-h';
  ph.innerHTML = '<div><h2>Dashboard</h2><p class="sub">Your money at a glance</p></div>' +
    '<div class="spacer"></div>';
  const pick = document.createElement('input'); pick.type = 'month';
  pick.className = 'monthpick'; pick.value = month;
  pick.onchange = () => showDashboard(pick.value);
  ph.appendChild(pick); m.appendChild(ph);
  const body = document.createElement('div'); body.appendChild(muted('Loading...'));
  m.appendChild(body);

  const res = await postRun(['month', '--month', month, '--json']);
  let d = null; if (res.code === 0) { try { d = JSON.parse(res.stdout); } catch (e) {} }
  body.innerHTML = '';
  if (!d) { const c = document.createElement('div'); c.className = 'card empty';
    c.textContent = 'Could not load this month.'; body.appendChild(c); return; }

  if (!d.expense_count && !d.income_count) {
    const c = document.createElement('div'); c.className = 'card empty';
    const p = document.createElement('p');
    p.textContent = 'Nothing recorded for ' + d.month + ' yet.'; c.appendChild(p);
    const b = document.createElement('button'); b.className = 'run';
    b.textContent = '+ Add your first expense';
    b.onclick = () => { const a = findCmd('add'); if (a) selectCmd(a); };
    c.appendChild(b); body.appendChild(c); return;
  }

  const stats = document.createElement('div'); stats.className = 'grid stat-grid';
  stats.appendChild(statCard('Income', money(d.income),
    d.income_count + (d.income_count === 1 ? ' entry' : ' entries'), 'pos'));
  stats.appendChild(statCard('Spending', money(d.spending),
    d.expense_count + (d.expense_count === 1 ? ' expense' : ' expenses'), ''));
  stats.appendChild(statCard('Net', money(d.net),
    d.net >= 0 ? 'saved this month' : 'over this month', d.net >= 0 ? 'pos' : 'neg'));
  body.appendChild(stats);

  // Insights strip + recent activity, fetched in parallel.
  const [insRes, listRes] = await Promise.all([
    postRun(['insights', '--month', month, '--json']),
    postRun(['list', '--all', '--month', month, '--limit', '8', '--json']),
  ]);
  let insD = null, recent = null;
  try { insD = JSON.parse(insRes.stdout); } catch (e) {}
  try { recent = JSON.parse(listRes.stdout); } catch (e) {}

  if (insD && Array.isArray(insD.insights) && insD.insights.length) {
    const c = insightsCard(insD.insights.slice(0, 4));
    c.style.marginTop = '16px'; body.appendChild(c);
  }

  const dg = document.createElement('div'); dg.className = 'grid dash-grid';
  dg.style.marginTop = '16px';
  dg.appendChild(topCatCard(d.by_category));
  if (d.budgets && Object.keys(d.budgets).length) dg.appendChild(budgetCard(d.budgets));
  dg.appendChild(goalCard(d.goal, d.net));
  if (Array.isArray(recent) && recent.length) dg.appendChild(recentCard(recent));
  body.appendChild(dg);
}

function insightsCard(list) {
  const c = document.createElement('div'); c.className = 'card';
  const h = document.createElement('h3'); h.textContent = 'Insights'; c.appendChild(h);
  c.appendChild(renderInsights(list));
  return c;
}

function recentCard(rows) {
  const c = document.createElement('div'); c.className = 'card';
  const h = document.createElement('h3'); h.textContent = 'Recent activity'; c.appendChild(h);
  const list = document.createElement('div'); list.className = 'recent';
  rows.slice().reverse().forEach(e => {
    const income = e.kind === 'income';
    const row = document.createElement('div'); row.className = 'recrow';
    const d = document.createElement('div'); d.className = 'rdate'; d.textContent = e.date;
    const cat = document.createElement('div'); cat.className = 'rcat';
    cat.textContent = e.category + (e.note ? ' - ' + e.note : '');
    const amt = document.createElement('div');
    amt.className = 'ramt' + (income ? ' pos' : '');
    amt.textContent = (income ? '+' : '') + money(e.amount);
    row.appendChild(d); row.appendChild(cat); row.appendChild(amt); list.appendChild(row);
  });
  c.appendChild(list);
  return c;
}

function cmdHasJson(c) { return c.args.some(a => a.flag === '--json'); }

async function postRun(argv) {
  return fetch('/api/run', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({argv})
  }).then(r => r.json());
}

function fieldFor(a) {
  const wrap = document.createElement('div');
  wrap.className = 'field' + (a.type === 'bool' ? ' bool' : '');
  wrap.dataset.dest = a.dest;
  wrap.dataset.kind = a.kind;
  wrap.dataset.flag = a.flag || '';
  wrap.dataset.type = a.type;
  const req = (a.kind === 'positional' && !a.optional);
  if (a.type === 'bool') {
    const lab = document.createElement('label');
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.className = 'inp';
    lab.appendChild(cb); lab.appendChild(document.createTextNode(a.flag + ' - ' + (a.help||'')));
    wrap.appendChild(lab);
    return wrap;
  }
  const lab = document.createElement('label');
  lab.innerHTML = (a.flag || a.dest) + (req ? ' <span class="req">*</span>' : '');
  wrap.appendChild(lab);
  if (a.help) { const hp = document.createElement('div'); hp.className='help'; hp.textContent=a.help; wrap.appendChild(hp); }
  let inp;
  if (a.type === 'choice') {
    inp = document.createElement('select');
    if (!req) inp.appendChild(new Option('(none)', ''));
    a.choices.forEach(ch => inp.appendChild(new Option(ch, ch)));
  } else {
    inp = document.createElement('input');
    inp.type = (a.type === 'int' || a.type === 'float') ? 'number' : 'text';
    if (a.type === 'float') inp.step = 'any';
  }
  inp.className = 'inp';
  wrap.appendChild(inp);
  return wrap;
}

function buildArgv(c, form) {
  const positionals = [], options = [];
  form.querySelectorAll('.field').forEach(f => {
    const t = f.dataset.type, kind = f.dataset.kind, flag = f.dataset.flag;
    const inp = f.querySelector('.inp');
    if (t === 'bool') { if (inp.checked) options.push(flag); return; }
    const v = inp.value.trim();
    if (kind === 'positional') { if (v !== '') positionals.push(v); }
    else if (v !== '') { options.push(flag, v); }
  });
  return c.argv.concat(positionals, options);
}

async function runCmd(c, form) {
  const pre = document.getElementById('outpre');
  const tableEl = document.getElementById('outtable');
  const tabs = document.getElementById('tabs');
  pre.className = ''; pre.textContent = 'running...';
  tableEl.style.display = 'none'; tabs.innerHTML = '';
  const argv = buildArgv(c, form);
  const res = await postRun(argv);
  const chartEl = document.getElementById('outchart');
  chartEl.style.display = 'none';
  document.getElementById('outcal').style.display = 'none';
  pre.className = res.code === 0 ? '' : 'err';
  pre.textContent = res.code === 0 ? (res.stdout || '(no output)')
    : (res.stderr || res.stdout || 'error');

  let data = null;
  if (res.code === 0 && argv.includes('--json')) {
    try { data = JSON.parse(res.stdout); } catch (e) {}
  } else if (res.code === 0 && cmdHasJson(c)) {
    const jr = await postRun(argv.concat(['--json']));
    if (jr.code === 0) { try { data = JSON.parse(jr.stdout); } catch (e) {} }
  }
  buildTabs(data);
}

function buildTabs(data) {
  const pre = document.getElementById('outpre');
  const tableEl = document.getElementById('outtable');
  const chartEl = document.getElementById('outchart');
  const calEl = document.getElementById('outcal');
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = '';
  const panels = {text: pre, table: tableEl, chart: chartEl, cal: calEl};
  const show = which => Object.entries(panels).forEach(
    ([k, el]) => el.style.display = (k === which ? '' : 'none'));
  const mk = (label, which) => {
    const b = document.createElement('button'); b.className = 'tab'; b.textContent = label;
    b.onclick = () => { [...tabs.children].forEach(x => x.classList.remove('active'));
      b.classList.add('active'); show(which); };
    return b;
  };
  const tText = mk('Text', 'text'); tabs.appendChild(tText);
  const has = data !== null &&
    (Array.isArray(data) ? data.length : Object.keys(data).length);
  const ins = has ? insightsData(data) : null;
  const cal = has ? calendarData(data) : null;
  const cd = (has && !ins) ? chartData(data) : null;
  let active = tText, prefer = 'text';
  if (has) {
    tableEl.innerHTML = '';
    if (ins) { tableEl.appendChild(renderInsights(ins)); active = mk('Insights', 'table'); }
    else { tableEl.appendChild(renderData(data)); active = mk('Table', 'table'); }
    tabs.appendChild(active); prefer = 'table';
  }
  if (cd) {
    chartEl.innerHTML = ''; chartEl.appendChild(renderChart(cd));
    active = mk('Chart', 'chart'); tabs.appendChild(active); prefer = 'chart';
  }
  if (cal) {
    calEl.innerHTML = ''; calEl.appendChild(renderCalendar(cal));
    active = mk('Calendar', 'cal'); tabs.appendChild(active); prefer = 'cal';
  }
  active.classList.add('active');
  show(prefer);
}

function insightsData(data) {
  // insights: {month, insights:[strings], metrics:{...}}
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  if (!Array.isArray(data.insights) || !data.insights.length) return null;
  if (!data.insights.every(x => typeof x === 'string')) return null;
  return data.insights;
}

function renderInsights(arr) {
  const box = document.createElement('div'); box.className = 'inslist';
  arr.forEach(s => {
    const row = document.createElement('div'); row.className = 'insrow';
    const dot = document.createElement('div'); dot.className = 'dot';
    const txt = document.createElement('div'); txt.className = 'txt'; txt.textContent = s;
    row.appendChild(dot); row.appendChild(txt); box.appendChild(row);
  });
  return box;
}

function calendarData(data) {
  // heatmap: {month:"YYYY-MM", days:[{date, day, spending}], max}
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  if (typeof data.month !== 'string' || !Array.isArray(data.days)) return null;
  const d0 = data.days[0];
  if (!d0 || typeof d0.spending !== 'number' || typeof d0.date !== 'string')
    return null;
  return data;
}

function renderCalendar(data) {
  const wrap = document.createElement('div'); wrap.className = 'cal';
  const max = Number(data.max) || 0;
  const shade = amt => {
    if (max <= 0 || amt <= 0) return 'transparent';
    return 'color-mix(in srgb, var(--accent) ' +
      Math.round(18 + (amt / max) * 82) + '%, transparent)';
  };
  const grid = document.createElement('div'); grid.className = 'cgrid';
  ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach(n => {
    const h = document.createElement('div'); h.className = 'cdow'; h.textContent = n;
    grid.appendChild(h);
  });
  // Monday-first offset from the first day's weekday (parse as UTC to avoid TZ drift)
  const first = new Date(data.days[0].date + 'T00:00:00Z');
  const pad = (first.getUTCDay() + 6) % 7;
  for (let i = 0; i < pad; i++) {
    const c = document.createElement('div'); c.className = 'ccell pad'; grid.appendChild(c);
  }
  data.days.forEach(d => {
    const c = document.createElement('div'); c.className = 'ccell';
    c.style.background = shade(d.spending);
    c.title = d.date + ': ' + d.spending;
    const n = document.createElement('div'); n.className = 'dnum';
    n.textContent = d.day != null ? d.day : Number(d.date.slice(8));
    const a = document.createElement('div'); a.className = 'damt';
    a.textContent = d.spending ? d.spending : '';
    c.appendChild(n); c.appendChild(a); grid.appendChild(c);
  });
  wrap.appendChild(grid);
  const legend = document.createElement('div'); legend.className = 'clegend';
  legend.appendChild(document.createTextNode('less'));
  [0, 0.33, 0.66, 1].forEach(f => {
    const s = document.createElement('div'); s.className = 'cswatch';
    s.style.background = f === 0 ? 'transparent' : shade(f * (max || 1));
    legend.appendChild(s);
  });
  legend.appendChild(document.createTextNode('more'));
  if (max > 0) legend.appendChild(document.createTextNode('  (peak ' + max + ')'));
  wrap.appendChild(legend);
  return wrap;
}

function pickValueKey(numKeys) {
  const pref = ['total', 'cumulative', 'spending', 'amount', 'balance', 'rate', 'net', 'value', 'count'];
  for (const p of pref) if (numKeys.includes(p)) return p;  // by preference order
  return numKeys[0];
}

function fromArray(arr) {
  const keys = Object.keys(arr[0]);
  const labelKey = keys.find(k => typeof arr[0][k] === 'string');
  const numKeys = keys.filter(k => typeof arr[0][k] === 'number');
  if (labelKey === undefined || !numKeys.length) return null;
  return {arr, labelKey, numKeys, valueKey: pickValueKey(numKeys)};
}

function chartData(data) {
  // array of objects (list/search/top/... and nested series)
  if (Array.isArray(data) && data.length && typeof data[0] === 'object'
      && !Array.isArray(data[0])) return fromArray(data);
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;

  // nested array value (trend.months, distribution.buckets, year, week, quarter)
  for (const k of Object.keys(data)) {
    const v = data[k];
    if (Array.isArray(v) && v.length && typeof v[0] === 'object' && !Array.isArray(v[0]))
      return fromArray(v);
  }
  const vals = Object.values(data);
  if (!vals.length) return null;
  // map of numbers (stats.by_tag)
  if (vals.every(v => typeof v === 'number')) {
    const arr = Object.entries(data).map(([k, v]) => ({label: k, value: v}));
    return {arr, labelKey: 'label', numKeys: ['value'], valueKey: 'value'};
  }
  // map of objects with numeric fields (categories, sources, tags)
  if (vals.every(v => v && typeof v === 'object' && !Array.isArray(v))) {
    const numKeys = Object.keys(vals[0]).filter(k => typeof vals[0][k] === 'number');
    if (!numKeys.length) return null;
    const arr = Object.entries(data).map(([k, v]) => Object.assign({label: k}, v));
    return {arr, labelKey: 'label', numKeys, valueKey: pickValueKey(numKeys)};
  }
  return null;
}

function renderChart(cd) {
  const wrap = document.createElement('div'); wrap.className = 'chart';
  const bars = document.createElement('div'); bars.className = 'bars';
  const draw = key => {
    bars.innerHTML = '';
    const vals = cd.arr.map(r => Number(r[key]) || 0);
    const max = Math.max(1, ...vals.map(Math.abs));
    cd.arr.forEach((r, i) => {
      const row = document.createElement('div'); row.className = 'crow';
      const l = document.createElement('div'); l.className = 'clab';
      l.textContent = String(r[cd.labelKey]);
      const bw = document.createElement('div'); bw.className = 'cbarwrap';
      const b = document.createElement('div'); b.className = 'cbar';
      b.style.width = (Math.abs(vals[i]) / max * 100) + '%'; bw.appendChild(b);
      const v = document.createElement('div'); v.className = 'cval'; v.textContent = vals[i];
      row.appendChild(l); row.appendChild(bw); row.appendChild(v);
      bars.appendChild(row);
    });
  };
  if (cd.numKeys.length > 1) {
    const md = document.createElement('div'); md.className = 'metric';
    const sel = document.createElement('select');
    cd.numKeys.forEach(k => sel.appendChild(new Option(k, k)));
    sel.value = cd.valueKey;
    sel.onchange = () => draw(sel.value);
    md.appendChild(document.createTextNode('metric: ')); md.appendChild(sel);
    wrap.appendChild(md);
  }
  wrap.appendChild(bars);
  draw(cd.valueKey);
  return wrap;
}

function fmtCell(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function renderData(data) {
  if (Array.isArray(data)) {
    if (data.length && typeof data[0] === 'object' && !Array.isArray(data[0]))
      return objArrayTable(data);
    const box = document.createElement('div'); box.className = 'kv';
    data.forEach(v => { const d = document.createElement('div');
      d.textContent = fmtCell(v); box.appendChild(d); });
    return box;
  }
  return fieldTable(data);
}

function objArrayTable(rows) {
  const cols = [];
  rows.forEach(r => Object.keys(r).forEach(k => { if (!cols.includes(k)) cols.push(k); }));
  const t = document.createElement('table'); t.className = 'data';
  const thead = document.createElement('thead'); const htr = document.createElement('tr');
  cols.forEach(c => { const th = document.createElement('th'); th.textContent = c; htr.appendChild(th); });
  thead.appendChild(htr); t.appendChild(thead);
  const tb = document.createElement('tbody');
  rows.forEach(r => { const tr = document.createElement('tr');
    cols.forEach(c => { const td = document.createElement('td'); td.textContent = fmtCell(r[c]); tr.appendChild(td); });
    tb.appendChild(tr); });
  t.appendChild(tb); return t;
}

function fieldTable(obj) {
  const t = document.createElement('table'); t.className = 'data';
  const tb = document.createElement('tbody');
  Object.entries(obj).forEach(([k, v]) => {
    const tr = document.createElement('tr');
    const th = document.createElement('th'); th.textContent = k;
    const td = document.createElement('td'); td.textContent = fmtCell(v);
    tr.appendChild(th); tr.appendChild(td); tb.appendChild(tr);
  });
  t.appendChild(tb); return t;
}

boot();
</script>
</body>
</html>
"""
