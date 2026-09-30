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
  out.innerHTML = '<h3>Output</h3><pre id="outpre">(run the command to see output)</pre>';
  m.appendChild(out);
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
  pre.className = ''; pre.textContent = 'running...';
  const argv = buildArgv(c, form);
  const res = await fetch('/api/run', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({argv})
  }).then(r => r.json());
  if (res.code === 0) {
    pre.className = '';
    pre.textContent = res.stdout || '(no output)';
  } else {
    pre.className = 'err';
    pre.textContent = (res.stderr || res.stdout || 'error') ;
  }
}

boot();
</script>
</body>
</html>
"""
