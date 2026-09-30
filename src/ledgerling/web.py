"""A local, sandboxed web UI for Ledgerling.

The UI generates itself from the CLI parser: every command (and every future
one) is introspected into a form, so the interface stays comprehensive without
per-command UI code. It is a thin front-end that runs Ledgerling's own
commands - no shell, no network egress - and binds only to 127.0.0.1.
"""

import argparse
import io
import json
import webbrowser
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cli as L


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
    return {"version": L.__version__, "commands": commands}


# --------------------------------------------------------------------------- #
# Running a command (captures output, never lets the server die)
# --------------------------------------------------------------------------- #

def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
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
    --bg:#f6f7f9; --panel:#ffffff; --ink:#1c2430; --muted:#67727e;
    --line:#e2e6ea; --accent:#2f6f4f; --accent-ink:#ffffff; --code:#0f1720;
    --code-ink:#d7e3d9;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#12161b; --panel:#1a2027; --ink:#e6ebf0; --muted:#9aa6b2;
      --line:#2a323c; --accent:#4f9e73; --accent-ink:#08120c; --code:#0c1116;
      --code-ink:#cfe6d8; }
  }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--bg); color:var(--ink); }
  header { display:flex; align-items:baseline; gap:10px; padding:14px 20px;
    border-bottom:1px solid var(--line); background:var(--panel); position:sticky; top:0; }
  header h1 { font-size:19px; margin:0; letter-spacing:.2px; }
  header .ver { color:var(--muted); font-size:13px; }
  header .tag { margin-left:auto; color:var(--muted); font-size:12px; }
  .wrap { display:grid; grid-template-columns:260px 1fr; gap:0;
    height:calc(100vh - 53px); }
  .side { border-right:1px solid var(--line); background:var(--panel);
    overflow:auto; padding:12px; }
  .side input { width:100%; padding:8px 10px; border:1px solid var(--line);
    border-radius:8px; background:var(--bg); color:var(--ink); margin-bottom:10px; }
  .cmd { padding:7px 10px; border-radius:8px; cursor:pointer; }
  .cmd:hover { background:var(--bg); }
  .cmd.active { background:var(--accent); color:var(--accent-ink); }
  .cmd .h { font-size:12px; color:var(--muted); }
  .cmd.active .h { color:var(--accent-ink); opacity:.85; }
  main { overflow:auto; padding:22px 26px; }
  h2 { margin:0 0 2px; font-size:20px; }
  .sub { color:var(--muted); margin:0 0 18px; }
  form { display:grid; gap:14px; max-width:560px; }
  .field label { display:block; font-weight:600; margin-bottom:4px; font-size:13px; }
  .field .help { color:var(--muted); font-size:12px; margin-bottom:5px; }
  .field input[type=text], .field input[type=number], .field select {
    width:100%; padding:8px 10px; border:1px solid var(--line); border-radius:8px;
    background:var(--panel); color:var(--ink); }
  .field.bool label { display:flex; align-items:center; gap:8px; font-weight:500; }
  .req { color:#c0492b; }
  button.run { justify-self:start; padding:9px 18px; border:0; border-radius:8px;
    background:var(--accent); color:var(--accent-ink); font-weight:600; cursor:pointer; }
  .out { margin-top:20px; }
  .out h3 { font-size:13px; text-transform:uppercase; letter-spacing:.06em;
    color:var(--muted); margin:0 0 6px; }
  pre { background:var(--code); color:var(--code-ink); padding:14px 16px;
    border-radius:10px; overflow:auto; font:13px/1.5 ui-monospace,SFMono-Regular,
    Menlo,Consolas,monospace; white-space:pre-wrap; word-break:break-word; min-height:40px; }
  pre.err { color:#ff9b8a; }
  .empty { color:var(--muted); padding:40px 0; }
  .tabs { display:flex; gap:6px; margin:6px 0 8px; }
  .tab { padding:5px 12px; border:1px solid var(--line); background:var(--panel);
    color:var(--muted); border-radius:7px; cursor:pointer; font-size:13px; }
  .tab.active { background:var(--accent); color:var(--accent-ink); border-color:var(--accent); }
  #outtable { overflow:auto; }
  table.data { border-collapse:collapse; width:100%; font-size:13px; }
  table.data th, table.data td { border:1px solid var(--line); padding:6px 9px;
    text-align:left; vertical-align:top; }
  table.data thead th { background:var(--bg); position:sticky; top:0; }
  table.data tbody th { background:var(--bg); white-space:nowrap; }
  .kv > div { padding:4px 2px; border-bottom:1px solid var(--line);
    font:13px ui-monospace,Menlo,Consolas,monospace; }
  .chart { display:flex; flex-direction:column; gap:6px; }
  .chart .metric { margin-bottom:6px; }
  .chart .metric select { padding:5px 8px; border:1px solid var(--line);
    border-radius:7px; background:var(--panel); color:var(--ink); }
  .crow { display:grid; grid-template-columns:130px 1fr 96px; align-items:center; gap:10px; }
  .clab { font-size:13px; color:var(--muted); white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; }
  .cbarwrap { background:var(--bg); border-radius:5px; height:20px; overflow:hidden; }
  .cbar { background:var(--accent); height:100%; border-radius:5px; min-width:2px; }
  .cval { font:12px ui-monospace,Menlo,Consolas,monospace; text-align:right; }
</style>
</head>
<body>
<header>
  <h1>Ledgerling</h1><span class="ver" id="ver"></span>
  <span class="tag">local &amp; sandboxed - data stays in your Ledgerling folder</span>
</header>
<div class="wrap">
  <nav class="side">
    <input id="filter" placeholder="Filter commands...">
    <div id="list"></div>
  </nav>
  <main id="main"><div class="empty">Loading commands...</div></main>
</div>
<script>
let COMMANDS = [], CURRENT = null;

async function boot() {
  const d = await fetch('/api/describe').then(r => r.json());
  COMMANDS = d.commands;
  document.getElementById('ver').textContent = 'v' + d.version;
  renderList('');
  if (COMMANDS.length) select(COMMANDS.find(c => c.name === 'month') || COMMANDS[0]);
  document.getElementById('filter').addEventListener('input', e => renderList(e.target.value));
}

function renderList(q) {
  q = q.trim().toLowerCase();
  const list = document.getElementById('list');
  list.innerHTML = '';
  COMMANDS.filter(c => !q || c.name.includes(q) || (c.help||'').toLowerCase().includes(q))
    .forEach(c => {
      const el = document.createElement('div');
      el.className = 'cmd' + (CURRENT && CURRENT.name === c.name ? ' active' : '');
      el.innerHTML = '<div>' + c.name + '</div><div class="h">' + esc(c.help||'') + '</div>';
      el.onclick = () => select(c);
      list.appendChild(el);
    });
}

function esc(s){ return s.replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }

function select(c) {
  CURRENT = c;
  renderList(document.getElementById('filter').value);
  const m = document.getElementById('main');
  m.innerHTML = '';
  const h = document.createElement('h2'); h.textContent = c.name; m.appendChild(h);
  const s = document.createElement('p'); s.className = 'sub'; s.textContent = c.help || ''; m.appendChild(s);
  const form = document.createElement('form');
  c.args.forEach(a => form.appendChild(fieldFor(a)));
  const btn = document.createElement('button'); btn.className = 'run'; btn.textContent = 'Run';
  form.appendChild(btn);
  form.onsubmit = ev => { ev.preventDefault(); runCmd(c, form); };
  m.appendChild(form);
  const out = document.createElement('div'); out.className = 'out'; out.id = 'out';
  out.innerHTML = '<h3>Output</h3><div class="tabs" id="tabs"></div>' +
    '<pre id="outpre">(run the command to see output)</pre>' +
    '<div id="outtable" style="display:none"></div>' +
    '<div id="outchart" style="display:none"></div>';
  m.appendChild(out);
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
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = '';
  const panels = {text: pre, table: tableEl, chart: chartEl};
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
  const cd = has ? chartData(data) : null;
  let active = tText;
  if (has) {
    tableEl.innerHTML = ''; tableEl.appendChild(renderData(data));
    active = mk('Table', 'table'); tabs.appendChild(active);
  }
  if (cd) {
    chartEl.innerHTML = ''; chartEl.appendChild(renderChart(cd));
    active = mk('Chart', 'chart'); tabs.appendChild(active);  // prefer chart
  }
  active.classList.add('active');
  show(active === tText ? 'text' : (cd ? 'chart' : 'table'));
}

function pickValueKey(numKeys) {
  const pref = ['total', 'spending', 'amount', 'balance', 'rate', 'net', 'value', 'count'];
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
