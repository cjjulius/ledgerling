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
                 optional=a.nargs in ("?", "*"),
                 variadic=a.nargs in ("+", "*"))
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
    cfg = L.load_config()
    return {"version": L.__version__, "currency": cfg.get("currency", "$"),
            "symbol_position": cfg.get("symbol_position", "before"),
            "commands": commands}


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
                # argparse errors print to stderr and exit non-zero; a bare
                # sys.exit("error: ...") instead carries the message as .code,
                # so surface that string (else the UI shows a blank error).
                if isinstance(exc.code, int):
                    code = exc.code
                elif exc.code is None:
                    code = 0
                else:
                    code = 1
                    err.write(str(exc.code).rstrip("\n") + "\n")
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
        try:
            length = int(self.headers.get("Content-Length", 0))
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
  /* Dark tokens: applied when the OS is dark (unless the user forced light),
     or whenever the user explicitly picks dark via the header toggle. */
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) { --bg:#0e1217; --panel:#171d24; --panel2:#1d242c;
      --ink:#e6ebf0; --muted:#98a4b0; --line:#2a323c; --accent:#4f9e73; --accent2:#5fae83;
      --accent-ink:#08120c; --pos:#5cba86; --neg:#e88b76; --code:#0b0f14;
      --code-ink:#cfe6d8; --shadow:0 1px 2px rgba(0,0,0,.3), 0 8px 22px rgba(0,0,0,.35); }
  }
  :root[data-theme="dark"] { --bg:#0e1217; --panel:#171d24; --panel2:#1d242c;
    --ink:#e6ebf0; --muted:#98a4b0; --line:#2a323c; --accent:#4f9e73; --accent2:#5fae83;
    --accent-ink:#08120c; --pos:#5cba86; --neg:#e88b76; --code:#0b0f14;
    --code-ink:#cfe6d8; --shadow:0 1px 2px rgba(0,0,0,.3), 0 8px 22px rgba(0,0,0,.35); }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--bg); color:var(--ink); }
  /* Accessibility: visually-hidden text still read by screen readers. */
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
    overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  /* Skip link: off-screen until focused, then pinned top-left over the header. */
  .skiplink { position:absolute; left:8px; top:-48px; z-index:20;
    background:var(--accent); color:var(--accent-ink); padding:8px 14px;
    border-radius:8px; text-decoration:none; font-weight:600;
    transition:top .12s ease; }
  .skiplink:focus { top:8px; }
  /* Clear, consistent keyboard-focus ring on every interactive element. */
  a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible,
  [tabindex]:focus-visible {
    outline:2px solid var(--accent); outline-offset:2px; border-radius:6px; }
  main#main:focus { outline:none; }
  /* Respect users who ask for less motion: drop transitions, animations and
     smooth scrolling (the focus ring and layout still work exactly the same). */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration:.001ms !important; animation-iteration-count:1 !important;
      transition-duration:.001ms !important; scroll-behavior:auto !important; }
  }
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
  header .themebtn { margin-left:14px; width:32px; height:32px; flex:none;
    border:1px solid var(--line); border-radius:9px; background:var(--panel2);
    color:var(--ink); cursor:pointer; font-size:15px; line-height:1; }
  header .themebtn:hover { border-color:var(--accent); }
  .wrap { display:grid; grid-template-columns:264px 1fr; gap:0;
    height:calc(100vh - 52px); }
  .side { border-right:1px solid var(--line); background:var(--panel);
    overflow:auto; padding:14px 12px; }
  .side .newbtn { width:100%; padding:10px 12px; border:0; border-radius:10px;
    background:var(--accent); color:var(--accent-ink); font-weight:600; cursor:pointer;
    font-size:14px; margin-bottom:10px; box-shadow:var(--shadow); }
  .side .newbtn:hover { background:var(--accent2); }
  .side .newbtn.alt { background:var(--panel2); color:var(--ink);
    border:1px solid var(--line); box-shadow:none; }
  .side .newbtn.alt:hover { background:var(--bg); border-color:var(--accent); }
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
    color:var(--muted); font-weight:700; margin:14px 0 4px; padding:2px 8px;
    cursor:pointer; user-select:none; border-radius:6px; }
  .grouphd:hover { color:var(--ink); background:var(--panel2); }
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
  .rowbar.clickable, .recrow.clickable { cursor:pointer; }
  .rowbar.clickable { border-radius:8px; padding:6px 8px; margin:0 -8px 11px; }
  .rowbar.clickable:hover, .recrow.clickable:hover { background:var(--panel2); }
  .recrow.clickable { border-radius:8px; padding-left:6px; padding-right:6px;
    margin:0 -6px; }
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
  .amtbox { display:flex; align-items:stretch; border:1px solid var(--line);
    border-radius:9px; overflow:hidden; background:var(--panel2); }
  .amtbox:focus-within { outline:2px solid color-mix(in srgb,var(--accent) 45%,transparent);
    outline-offset:1px; border-color:var(--accent); }
  .amtbox .amtpfx { display:flex; align-items:center; padding:0 11px;
    background:color-mix(in srgb,var(--accent) 12%,transparent); color:var(--muted);
    font-weight:600; }
  .amtbox input { border:0; outline:0; background:transparent; }
  .req { color:var(--neg); }
  button.run { justify-self:start; padding:10px 22px; border:0; border-radius:10px;
    background:var(--accent); color:var(--accent-ink); font-weight:600; cursor:pointer;
    font-size:14px; box-shadow:var(--shadow); }
  button.run:hover { background:var(--accent2); }
  button.run:disabled { opacity:.6; cursor:default; }
  .out { margin-top:18px; position:relative; }
  .copybtn { position:absolute; top:0; right:0; z-index:2; padding:4px 10px;
    border:1px solid var(--line); background:var(--panel2); color:var(--muted);
    border-radius:8px; cursor:pointer; font-size:12px; }
  .copybtn:hover { color:var(--ink); border-color:var(--accent); }
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
  table.data th.sortable { cursor:pointer; user-select:none; }
  table.data th.sortable:hover { color:var(--ink); }
  table.data th.sortable .arrow { color:var(--accent); font-size:10px; }
  table.data tbody tr:hover { background:var(--panel2); }
  table.data td.num { text-align:right; font-variant-numeric:tabular-nums;
    font-family:ui-monospace,Menlo,Consolas,monospace; }
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
  .cbar.neg { background:var(--neg); }
  .cval { font:12px ui-monospace,Menlo,Consolas,monospace; text-align:right; }
  .cval.neg { color:var(--neg); }
  .cashflow { display:flex; flex-direction:column; gap:12px; }
  .cfhead { display:flex; flex-wrap:wrap; gap:10px; }
  .cfstat { flex:1 1 110px; background:var(--panel2); border:1px solid var(--line);
    border-radius:10px; padding:8px 12px; }
  .cfstat .cfk { font-size:11px; color:var(--muted); text-transform:uppercase;
    letter-spacing:.4px; }
  .cfstat .cfv { font:15px ui-monospace,Menlo,Consolas,monospace; margin-top:2px; }
  .cfstat .cfv.pos { color:var(--pos); }
  .cfstat .cfv.neg { color:var(--neg); }
  .cfwarn { background:color-mix(in srgb,var(--neg) 14%,transparent);
    border:1px solid var(--neg); color:var(--neg); border-radius:9px;
    padding:8px 12px; font-size:13px; font-weight:600; }
  .cfchart { background:var(--panel2); border:1px solid var(--line);
    border-radius:10px; padding:8px; }
  .cfsvg { width:100%; height:auto; display:block; }
  .cfaxis { font:11px ui-monospace,Menlo,Consolas,monospace; fill:var(--muted); }
  .cfaxis.neg { fill:var(--neg); }
  .health { display:flex; flex-direction:column; gap:12px; }
  .health .hgroup { display:flex; flex-direction:column; gap:2px; }
  .health .hkind { font-size:12px; font-weight:700; text-transform:capitalize;
    color:var(--neg); letter-spacing:.3px; }
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
  .upfoot { margin-top:10px; padding-top:9px; border-top:1px solid var(--line);
    font-size:12px; color:var(--muted); }
  .upfoot .pos { color:var(--pos); font-weight:600; }
  .upfoot .neg { color:var(--neg); font-weight:600; }
  @media (max-width:720px) {
    .wrap { grid-template-columns:1fr; height:auto; }
    .side { border-right:0; border-bottom:1px solid var(--line); max-height:40vh; }
    header .tag span { display:none; }
  }
</style>
</head>
<body>
<a class="skiplink" href="#main">Skip to main content</a>
<header>
  <div class="brand"><span class="logo" aria-hidden="true"></span><h1>Ledgerling</h1>
    <span class="ver" id="ver"></span></div>
  <span class="tag"><span>local &amp; sandboxed &mdash; data stays in your Ledgerling folder</span></span>
  <button class="themebtn" id="themebtn" aria-label="Toggle light or dark theme"
    aria-pressed="false" title="Toggle light / dark">&#9789;</button>
</header>
<div class="wrap">
  <nav class="side" aria-label="Commands">
    <button class="newbtn" id="newbtn">+ New expense</button>
    <button class="newbtn alt" id="incbtn">+ New income</button>
    <label class="sr-only" for="filter">Filter commands</label>
    <input class="filter" id="filter" placeholder="Filter commands...  ( / )"
      aria-label="Filter commands" aria-controls="list">
    <div id="list" aria-label="Available commands"></div>
  </nav>
  <main id="main" tabindex="-1"><div class="empty">Loading&hellip;</div></main>
</div>
<script>
let COMMANDS = [], CURRENT = null, CURRENCY = '$', ACTIVE = 'home', CATEGORIES = [];
let SYMPOS = 'before';
let COLLAPSED = new Set();
try { COLLAPSED = new Set(JSON.parse(localStorage.getItem('ll_collapsed') || '[]')); }
catch (e) {}

// Theme: apply a saved manual override immediately (else follow the OS).
(function () {
  try { const t = localStorage.getItem('ll_theme');
    if (t) document.documentElement.dataset.theme = t; } catch (e) {}
})();
function syncThemeButton() {
  const btn = document.getElementById('themebtn');
  if (!btn) return;
  const osDark = matchMedia('(prefers-color-scheme: dark)').matches;
  const dark = (document.documentElement.dataset.theme || (osDark ? 'dark' : 'light')) === 'dark';
  btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
}
function toggleTheme() {
  const root = document.documentElement;
  const osDark = matchMedia('(prefers-color-scheme: dark)').matches;
  const cur = root.dataset.theme || (osDark ? 'dark' : 'light');
  const next = cur === 'dark' ? 'light' : 'dark';
  root.dataset.theme = next;
  try { localStorage.setItem('ll_theme', next); } catch (e) {}
  syncThemeButton();
}
function toggleGroup(g) {
  if (COLLAPSED.has(g)) COLLAPSED.delete(g); else COLLAPSED.add(g);
  try { localStorage.setItem('ll_collapsed', JSON.stringify([...COLLAPSED])); }
  catch (e) {}
  renderList(document.getElementById('filter').value);
}

// Command groups for the sidebar. Any command not listed here (e.g. a newly
// added one) still shows up automatically under "More", so the nav stays
// comprehensive without per-command edits.
const GROUP_DEFS = [
  ['Record', ['add', 'income', 'edit', 'delete', 'split', 'clone', 'note', 'refund',
    'template use', 'template add', 'template list', 'template remove', 'template rename']],
  ['Analyze', ['today', 'month', 'insights', 'scorecard', 'scoretrend', 'range', 'summary', 'report', 'statement', 'stats', 'week', 'weekly', 'day', 'year', 'years',
    'quarter', 'weekday', 'trend', 'tagtrend', 'matrix', 'tagmatrix', 'cumulative', 'top', 'compare', 'average', 'distribution',
    'balance', 'savings', 'heatmap', 'streak', 'pace', 'forecast', 'sources', 'anomalies', 'cashflow', 'net', 'subscriptions',
    'categories', 'category', 'payees', 'worthtrend', 'tags', 'untagged', 'search', 'list']],
  ['Budgets & goals', ['budget', 'unbudget', 'allowance', 'overbudget', 'goal', 'networth', 'pot', 'transfer', 'savingsplan', 'suggest', 'autobudget', 'commitments', 'upcoming', 'bills']],
  ['Calculators', ['tip', 'interest', 'loan', 'target', 'runway', 'roundup',
    'fx convert', 'fx set', 'fx list', 'fx rm']],
  ['Recurring', ['recur add', 'recur from', 'recur list', 'recur edit', 'recur remove', 'recur run', 'recur skip', 'recur unskip', 'recur pause', 'recur resume']],
  ['Data', ['export', 'import', 'backup', 'restore', 'dedupe', 'duplicates',
    'retag', 'tag', 'untag', 'recategorize', 'clear', 'unclear', 'reconcile',
    'check', 'undo']],
  ['Settings', ['config', 'where', 'version', 'completion', 'web', 'gui']],
  ['Almanac', ['fortune', 'horoscope', 'weather', 'eightball']],
];
function groupOf(name) {
  for (const [g, names] of GROUP_DEFS) if (names.includes(name)) return g;
  return 'More';
}

function esc(s){ return String(s).replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }
function findCmd(name) { return COMMANDS.find(c => c.name === name); }
// --- URL-hash routing: deep-link / reload / back-forward to a command -------
function currentRoute() {
  try { return decodeURIComponent((location.hash || '').replace(/^#/, '')); }
  catch (e) { return ''; }
}
function setHash(name) {
  const h = name ? '#' + encodeURIComponent(name) : '';
  if ((location.hash || '') !== h) location.hash = h;
}
function routeFromHash() {
  // Called on hashchange; only act when the target differs from the view.
  const name = currentRoute();
  const c = name ? findCmd(name) : null;
  const target = c ? name : 'home';
  if (target === ACTIVE) return;
  if (c) selectCmd(c); else showDashboard();
}
// Friendly Title-Case label for a command name (search still uses the raw name).
function prettyName(name) {
  return name.split(' ')
    .map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}
function curMonth() {
  const n = new Date();
  return n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0');
}
function todayISO() {
  const n = new Date();
  return n.getFullYear() + '-' + String(n.getMonth() + 1).padStart(2, '0') +
    '-' + String(n.getDate()).padStart(2, '0');
}
// Human label for a form field: "--list-limit" -> "List limit", "amount" -> "Amount".
function humanize(a) {
  let s = (a.flag ? a.flag.replace(/^--/, '') : a.dest).replace(/[-_]/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}
// Pick a friendly input widget from the field's name.
function widgetType(a) {
  if (a.dest === 'month' || a.flag === '--month') return 'month';
  if (['date', 'start', 'end'].includes(a.dest) ||
      ['--date', '--start', '--end'].includes(a.flag)) return 'date';
  if (a.dest === 'amount' || a.flag === '--amount') return 'amount';
  if (a.dest === 'category' || a.flag === '--category') return 'category';
  return null;
}
async function refreshCategories() {
  const r = await postRun(['categories', '--json']);
  if (r && r.code === 0) {
    try { CATEGORIES = Object.keys(JSON.parse(r.stdout)); } catch (e) {}
  }
}
function money(n) {
  const neg = n < 0;
  const num = Math.abs(Number(n) || 0).toLocaleString(undefined,
    {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const s = SYMPOS === 'after' ? num + ' ' + CURRENCY : CURRENCY + num;
  return neg ? '-' + s : s;
}

async function boot() {
  const d = await fetch('/api/describe').then(r => r.json());
  COMMANDS = d.commands; CURRENCY = d.currency || '$';
  SYMPOS = d.symbol_position || 'before';
  document.getElementById('ver').textContent = 'v' + d.version;
  const filter = document.getElementById('filter');
  filter.addEventListener('input', e => renderList(e.target.value));
  filter.addEventListener('keydown', e => {
    if (e.key === 'Enter') {          // open the first matching command
      const q = filter.value.trim().toLowerCase();
      const first = COMMANDS.find(c => !q || c.name.includes(q) ||
        (c.help || '').toLowerCase().includes(q));
      if (first) { e.preventDefault(); selectCmd(first); }
    } else if (e.key === 'Escape') {  // clear and unfocus
      filter.value = ''; renderList(''); filter.blur();
    }
  });
  document.getElementById('newbtn').onclick = () => {
    const a = findCmd('add'); if (a) selectCmd(a);
  };
  document.getElementById('incbtn').onclick = () => {
    const a = findCmd('income'); if (a) selectCmd(a);
  };
  document.getElementById('themebtn').onclick = toggleTheme;
  syncThemeButton();
  // Keep the toggle's pressed state correct if the OS theme flips while open.
  try { matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncThemeButton); }
  catch (e) {}
  // Press "/" anywhere (outside a field) to jump to the command filter.
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(
        (document.activeElement || {}).tagName)) {
      e.preventDefault(); const f = document.getElementById('filter');
      f.focus(); f.select();
    }
  });
  await refreshCategories();
  renderList('');
  // Deep-link support: open the command named in the URL hash, else dashboard.
  window.addEventListener('hashchange', routeFromHash);
  const initial = currentRoute();
  const c0 = initial ? findCmd(initial) : null;
  if (c0) selectCmd(c0); else showDashboard();
}

function navItem(name, help, active, onclick) {
  const el = document.createElement('div');
  el.className = 'navitem' + (active ? ' active' : '');
  el.innerHTML = '<div class="n">' + esc(name) + '</div>' +
    (help ? '<div class="h">' + esc(help) + '</div>' : '');
  // Make the item operable by keyboard: focusable + Enter/Space activate it.
  el.setAttribute('role', 'button');
  el.tabIndex = 0;
  if (active) el.setAttribute('aria-current', 'true');
  el.setAttribute('aria-label', name + (help ? '. ' + help : ''));
  el.onclick = onclick;
  el.onkeydown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onclick(); }
  };
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
    const collapsed = !q && COLLAPSED.has(g);   // search always reveals items
    const hd = document.createElement('div'); hd.className = 'grouphd';
    hd.textContent = (collapsed ? '▸ ' : '▾ ') + g + '  (' + items.length + ')';
    hd.setAttribute('role', 'button');
    hd.tabIndex = 0;
    hd.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    hd.setAttribute('aria-label', g + ' group, ' + items.length + ' commands');
    hd.onclick = () => toggleGroup(g);
    hd.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleGroup(g); }
    };
    list.appendChild(hd);
    if (collapsed) return;
    items.forEach(c => list.appendChild(
      navItem(prettyName(c.name), c.help || '', ACTIVE === c.name, () => selectCmd(c))));
  });
}

function selectCmd(c) {
  ACTIVE = c.name; CURRENT = c;
  setHash(c.name);
  renderList(document.getElementById('filter').value);
  const m = document.getElementById('main'); m.innerHTML = '';
  const ph = document.createElement('div'); ph.className = 'page-h';
  ph.innerHTML = '<div><h2>' + esc(prettyName(c.name)) + '</h2><p class="sub">' +
    esc(c.help || '') + '</p></div>';
  m.appendChild(ph);
  const fcard = document.createElement('div'); fcard.className = 'card';
  const form = document.createElement('form');
  // The UI fetches JSON itself, so don't expose the --json flag as a field.
  const fields = c.args.filter(a => a.flag !== '--json');
  fields.forEach(a => form.appendChild(fieldFor(a)));
  if (!fields.length) {
    const p = document.createElement('p'); p.className = 'muted';
    p.textContent = 'No options - just run it.'; form.appendChild(p);
  }
  if (fields.some(a => widgetType(a) === 'category')) {
    const dl = document.createElement('datalist'); dl.id = 'catlist';
    CATEGORIES.forEach(c2 => dl.appendChild(new Option(c2)));
    form.appendChild(dl);
  }
  const btn = document.createElement('button'); btn.className = 'run';
  btn.textContent = 'Run ' + prettyName(c.name); form.appendChild(btn);
  form.onsubmit = ev => { ev.preventDefault(); runCmd(c, form); };
  fcard.appendChild(form); m.appendChild(fcard);
  const firstInput = form.querySelector('.inp');
  if (firstInput) firstInput.focus();   // ready to type immediately
  const out = document.createElement('div'); out.className = 'out card'; out.id = 'out';
  out.setAttribute('role', 'region'); out.setAttribute('aria-label', 'Command output');
  out.setAttribute('aria-live', 'polite'); out.setAttribute('aria-atomic', 'false');
  out.innerHTML = '<div class="tabs" id="tabs"></div>' +
    '<pre id="outpre">(run the command to see output)</pre>' +
    '<div id="outtable" style="display:none"></div>' +
    '<div id="outchart" style="display:none"></div>' +
    '<div id="outcal" style="display:none"></div>';
  // A Copy button grabs the text output (handy for JSON / results).
  const copyBtn = document.createElement('button');
  copyBtn.type = 'button'; copyBtn.className = 'copybtn';
  copyBtn.textContent = 'Copy';
  copyBtn.setAttribute('aria-label', 'Copy the output text to the clipboard');
  copyBtn.onclick = () => {
    const text = document.getElementById('outpre').textContent || '';
    const flash = () => {
      copyBtn.textContent = 'Copied!';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1200);
    };
    try {
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(flash, flash);
      else flash();
    } catch (e) { flash(); }
  };
  out.insertBefore(copyBtn, out.firstChild);
  m.appendChild(out);
  // A separate assertive status line (outside the polite output region) so a
  // failed command is announced to screen-reader users right away, even while
  // focus stays on the Run button.
  const status = document.createElement('div');
  status.id = 'runstatus'; status.className = 'sr-only';
  status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'assertive');
  m.appendChild(status);
  // A polite companion announces a concise success confirmation (what ran, and
  // roughly how much output), so screen-reader users hear that the command
  // completed instead of only ever hearing failures.
  const okstatus = document.createElement('div');
  okstatus.id = 'runok'; okstatus.className = 'sr-only';
  okstatus.setAttribute('role', 'status'); okstatus.setAttribute('aria-live', 'polite');
  m.appendChild(okstatus);
}

// Open a command, optionally pre-filling fields (by dest) and running it.
// Used for dashboard drill-downs (click a category -> its expenses, etc.).
function openCommand(name, prefill, run) {
  const c = findCmd(name); if (!c) return;
  selectCmd(c);
  const form = document.querySelector('#main form');
  if (form && prefill) {
    Object.entries(prefill).forEach(([dest, val]) => {
      const f = [...form.querySelectorAll('.field')].find(x => x.dataset.dest === dest);
      const inp = f && f.querySelector('.inp');
      if (inp) inp.value = val;
    });
    if (run) runCmd(c, form);
  }
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
  entries.forEach(([cat, amt]) => {
    const row = barRow(cat, money(amt), amt / max, false);
    row.classList.add('clickable'); row.title = 'View ' + cat + ' expenses';
    row.onclick = () => openCommand('list', {category: cat}, true);
    c.appendChild(row);
  });
  return c;
}

function budgetCard(budgets) {
  const c = document.createElement('div'); c.className = 'card';
  const h = document.createElement('h3'); h.textContent = 'Budgets'; c.appendChild(h);
  Object.keys(budgets).sort().forEach(cat => {
    const b = budgets[cat]; const frac = b.limit ? b.spent / b.limit : 0;
    const row = barRow(cat, money(b.spent) + ' / ' + money(b.limit), frac,
                       b.spent > b.limit);
    row.classList.add('clickable'); row.title = 'View ' + cat + ' expenses';
    row.onclick = () => openCommand('list', {category: cat}, true);
    c.appendChild(row);
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
  setHash('');
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
  const rate = d.income > 0 ? Math.round(d.net / d.income * 100) : null;
  const netSub = rate === null
    ? (d.net >= 0 ? 'saved this month' : 'over this month')
    : (d.net >= 0 ? rate + '% of income saved' : Math.abs(rate) + '% over income');
  stats.appendChild(statCard('Net', money(d.net), netSub, d.net >= 0 ? 'pos' : 'neg'));
  body.appendChild(stats);

  // Insights strip + recent activity + upcoming, fetched in parallel.
  const [insRes, listRes, upRes] = await Promise.all([
    postRun(['insights', '--month', month, '--json']),
    postRun(['list', '--all', '--month', month, '--limit', '8', '--json']),
    postRun(['upcoming', '--days', '30', '--json']),
  ]);
  let insD = null, recent = null, upc = null;
  try { insD = JSON.parse(insRes.stdout); } catch (e) {}
  try { recent = JSON.parse(listRes.stdout); } catch (e) {}
  try { upc = JSON.parse(upRes.stdout); } catch (e) {}

  if (insD && Array.isArray(insD.insights) && insD.insights.length) {
    const c = insightsCard(insD.insights.slice(0, 4));
    c.style.marginTop = '16px'; body.appendChild(c);
  }

  const dg = document.createElement('div'); dg.className = 'grid dash-grid';
  dg.style.marginTop = '16px';
  dg.appendChild(topCatCard(d.by_category));
  if (d.budgets && Object.keys(d.budgets).length) dg.appendChild(budgetCard(d.budgets));
  dg.appendChild(goalCard(d.goal, d.net));
  if (upc && Array.isArray(upc.items) && upc.items.length) {
    dg.appendChild(upcomingCard(upc));
  }
  if (Array.isArray(recent) && recent.length) dg.appendChild(recentCard(recent));
  body.appendChild(dg);
}

function upcomingCard(data) {
  const c = document.createElement('div'); c.className = 'card';
  const h = document.createElement('h3');
  h.textContent = 'Upcoming (' + data.days + ' days)'; c.appendChild(h);
  const list = document.createElement('div'); list.className = 'recent';
  data.items.slice(0, 6).forEach(i => {
    const income = i.kind === 'income';
    const row = document.createElement('div'); row.className = 'recrow clickable';
    row.title = 'Open the cash-flow projection';
    row.onclick = () => openCommand('cashflow', {days: data.days}, true);
    const d = document.createElement('div'); d.className = 'rdate';
    d.textContent = i.date;
    const cat = document.createElement('div'); cat.className = 'rcat';
    cat.textContent = i.category + (i.note ? ' - ' + i.note : '');
    const amt = document.createElement('div');
    amt.className = 'ramt' + (income ? ' pos' : '');
    amt.textContent = (income ? '+' : '-') + money(i.amount);
    row.appendChild(d); row.appendChild(cat); row.appendChild(amt);
    list.appendChild(row);
  });
  c.appendChild(list);
  const foot = document.createElement('div'); foot.className = 'upfoot';
  const cls = data.net >= 0 ? 'pos' : 'neg';
  const more = data.items.length > 6
    ? ' &middot; ' + data.items.length + ' scheduled' : '';
  foot.innerHTML = 'projected net <span class="' + cls + '">' +
    (data.net >= 0 ? '+' : '') + esc(money(data.net)) + '</span>' + more;
  c.appendChild(foot);
  return c;
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
    const row = document.createElement('div'); row.className = 'recrow clickable';
    row.title = 'Edit #' + e.id;
    row.onclick = () => openCommand('edit', {id: e.id}, false);
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

let FIELD_SEQ = 0;
function fieldFor(a) {
  const wrap = document.createElement('div');
  wrap.className = 'field' + (a.type === 'bool' ? ' bool' : '');
  wrap.dataset.dest = a.dest;
  wrap.dataset.kind = a.kind;
  wrap.dataset.flag = a.flag || '';
  wrap.dataset.type = a.type;
  if (a.variadic) wrap.dataset.variadic = '1';
  const req = (a.kind === 'positional' && !a.optional);
  const fid = 'fld' + (++FIELD_SEQ);
  const helpId = a.help ? fid + '-help' : '';
  // Associate help text and required state with an input for assistive tech.
  const wire = (inp) => {
    inp.id = fid;
    if (helpId) inp.setAttribute('aria-describedby', helpId);
    if (req) { inp.required = true; inp.setAttribute('aria-required', 'true'); }
    return inp;
  };
  if (a.type === 'bool') {
    const lab = document.createElement('label');
    const cb = wire(document.createElement('input'));
    cb.type = 'checkbox'; cb.className = 'inp';
    lab.htmlFor = fid;
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(' ' + humanize(a) +
      (a.help ? ' - ' + a.help : '')));
    wrap.appendChild(lab);
    return wrap;
  }
  const lab = document.createElement('label');
  lab.htmlFor = fid;
  lab.innerHTML = humanize(a) + (req ? ' <span class="req" aria-hidden="true">*</span>' : '');
  wrap.appendChild(lab);
  if (a.help) {
    const hp = document.createElement('div'); hp.className = 'help';
    hp.id = helpId;
    hp.textContent = a.help; wrap.appendChild(hp);
  }
  const w = widgetType(a);
  if (w === 'amount') {   // currency-prefixed number field
    const box = document.createElement('div'); box.className = 'amtbox';
    const pfx = document.createElement('span'); pfx.className = 'amtpfx';
    pfx.textContent = CURRENCY; pfx.setAttribute('aria-hidden', 'true');
    const inp = wire(document.createElement('input')); inp.className = 'inp';
    inp.type = 'number'; inp.step = '0.01'; inp.min = '0'; inp.placeholder = '0.00';
    inp.setAttribute('aria-label', humanize(a) + ' in ' + CURRENCY);
    box.appendChild(pfx); box.appendChild(inp); wrap.appendChild(box);
    return wrap;
  }
  let inp;
  if (a.type === 'choice') {
    inp = document.createElement('select');
    if (!req) inp.appendChild(new Option('(any)', ''));
    a.choices.forEach(ch => inp.appendChild(new Option(ch, ch)));
  } else if (w === 'month') {
    inp = document.createElement('input'); inp.type = 'month';
  } else if (w === 'date') {
    inp = document.createElement('input'); inp.type = 'date';
  } else if (w === 'category') {
    inp = document.createElement('input'); inp.type = 'text';
    inp.setAttribute('list', 'catlist'); inp.placeholder = 'e.g. food';
  } else {
    inp = document.createElement('input');
    inp.type = (a.type === 'int' || a.type === 'float') ? 'number' : 'text';
    if (a.type === 'float') inp.step = 'any';
  }
  inp.className = 'inp';
  if (a.variadic && inp.tagName === 'INPUT' && !inp.placeholder) {
    inp.placeholder = 'space-separated values';
  }
  wire(inp);
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
    if (kind === 'positional') {
      if (v === '') return;
      // A variadic positional (nargs + / *) is several argv tokens, so split
      // the field on whitespace instead of passing one quoted blob.
      if (f.dataset.variadic === '1') positionals.push(...v.split(/\s+/));
      else positionals.push(v);
    } else if (v !== '') { options.push(flag, v); }
  });
  return c.argv.concat(positionals, options);
}

async function runCmd(c, form) {
  const pre = document.getElementById('outpre');
  const tableEl = document.getElementById('outtable');
  const tabs = document.getElementById('tabs');
  const btn = form.querySelector('button.run');
  const out = document.getElementById('out');
  const status = document.getElementById('runstatus');
  const okstatus = document.getElementById('runok');
  const btnLabel = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
  // aria-busy holds the polite region quiet until the result is in, so the
  // reader announces the finished output once rather than the interim state.
  if (out) out.setAttribute('aria-busy', 'true');
  if (status) status.textContent = '';
  if (okstatus) okstatus.textContent = '';
  pre.className = ''; pre.textContent = 'running...';
  tableEl.style.display = 'none'; tabs.innerHTML = '';
  const argv = buildArgv(c, form);
  let res;
  try {
    res = await postRun(argv);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btnLabel; }
    if (out) out.setAttribute('aria-busy', 'false');
  }
  const chartEl = document.getElementById('outchart');
  chartEl.style.display = 'none';
  document.getElementById('outcal').style.display = 'none';
  pre.className = res.code === 0 ? '' : 'err';
  pre.textContent = res.code === 0 ? (res.stdout || '(no output)')
    : (res.stderr || res.stdout || 'error');
  if (status && res.code !== 0) {
    const msg = (res.stderr || res.stdout || 'error').trim().split('\n')[0];
    status.textContent = 'Command failed: ' + msg;
  }
  if (okstatus && res.code === 0) {
    const txt = (res.stdout || '').trim();
    const lines = txt ? txt.split('\n').length : 0;
    okstatus.textContent = prettyName(c.name) + ' completed'
      + (lines ? ' — ' + lines + ' line' + (lines === 1 ? '' : 's')
                 + ' of output' : '');
  }

  let data = null;
  if (res.code === 0 && argv.includes('--json')) {
    try { data = JSON.parse(res.stdout); } catch (e) {}
  } else if (res.code === 0 && cmdHasJson(c)) {
    const jr = await postRun(argv.concat(['--json']));
    if (jr.code === 0) { try { data = JSON.parse(jr.stdout); } catch (e) {} }
  }
  buildTabs(data);
  if (res.code === 0) refreshCategories();  // keep autocomplete current
  // Rapid entry: after a successful add/income, clear the form for the next one.
  if (res.code === 0 && (c.name === 'add' || c.name === 'income')) {
    form.querySelectorAll('.inp').forEach(i => {
      if (i.type === 'checkbox') i.checked = false; else i.value = '';
    });
    const first = form.querySelector('.inp'); if (first) first.focus();
  }
}

let TAB_SEQ = 0;
function buildTabs(data) {
  const pre = document.getElementById('outpre');
  const tableEl = document.getElementById('outtable');
  const chartEl = document.getElementById('outchart');
  const calEl = document.getElementById('outcal');
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = '';
  // Proper ARIA tabs: a tablist of tabs controlling tabpanels, with roving
  // tabindex and arrow-key navigation for keyboard/screen-reader users.
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute('aria-label', 'Result views');
  const panels = {text: pre, table: tableEl, chart: chartEl, cal: calEl};
  Object.values(panels).forEach(el => {
    el.setAttribute('role', 'tabpanel');
    el.tabIndex = 0;
  });
  const tabBtns = [];
  const select = (btn, focus) => {
    tabBtns.forEach(b => {
      const on = b === btn;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
    });
    Object.entries(panels).forEach(([k, el]) => {
      el.style.display = (k === btn.dataset.which ? '' : 'none');
    });
    const panel = panels[btn.dataset.which];
    if (panel) panel.setAttribute('aria-labelledby', btn.id);
    if (focus) btn.focus();
  };
  const mk = (label, which) => {
    const b = document.createElement('button'); b.className = 'tab';
    b.textContent = label;
    b.id = 'tab-' + (++TAB_SEQ);
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', 'false');
    b.tabIndex = -1;
    b.dataset.which = which;
    const panel = panels[which];
    if (panel && panel.id) b.setAttribute('aria-controls', panel.id);
    b.onclick = () => select(b, false);
    tabBtns.push(b);
    return b;
  };
  tabs.onkeydown = (e) => {
    const i = tabBtns.indexOf(document.activeElement);
    if (i < 0) return;
    let j = null;
    if (e.key === 'ArrowRight') j = (i + 1) % tabBtns.length;
    else if (e.key === 'ArrowLeft') j = (i - 1 + tabBtns.length) % tabBtns.length;
    else if (e.key === 'Home') j = 0;
    else if (e.key === 'End') j = tabBtns.length - 1;
    if (j !== null) { e.preventDefault(); select(tabBtns[j], true); }
  };
  const tText = mk('Text', 'text'); tabs.appendChild(tText);
  const has = data !== null &&
    (Array.isArray(data) ? data.length : Object.keys(data).length);
  const ins = has ? insightsData(data) : null;
  const chk = has ? checkData(data) : null;
  const cal = has ? calendarData(data) : null;
  const cf = has ? cashflowData(data) : null;
  const cd = (has && !ins && !cf && !chk) ? chartData(data) : null;
  let active = tText, prefer = 'text';
  if (has) {
    tableEl.innerHTML = '';
    if (chk) { tableEl.appendChild(renderCheck(chk)); active = mk('Health', 'table'); }
    else if (ins) { tableEl.appendChild(renderInsights(ins)); active = mk('Insights', 'table'); }
    else { tableEl.appendChild(renderData(data)); active = mk('Table', 'table'); }
    tabs.appendChild(active); prefer = 'table';
  }
  if (cf) {
    chartEl.innerHTML = ''; chartEl.appendChild(renderCashflow(cf));
    active = mk('Projection', 'chart'); tabs.appendChild(active); prefer = 'chart';
  } else if (cd) {
    chartEl.innerHTML = ''; chartEl.appendChild(renderChart(cd));
    active = mk('Chart', 'chart'); tabs.appendChild(active); prefer = 'chart';
  }
  if (cal) {
    calEl.innerHTML = ''; calEl.appendChild(renderCalendar(cal));
    active = mk('Calendar', 'cal'); tabs.appendChild(active); prefer = 'cal';
  }
  select(active, false);
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
  // The grid conveys data, so label it and let each day carry its own
  // accessible name; the weekday headers and numeric overlays are decorative
  // (every cell's aria-label already names its full date and amount).
  grid.setAttribute('role', 'group');
  grid.setAttribute('aria-label', 'Daily spending for ' + data.month);
  ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach(n => {
    const h = document.createElement('div'); h.className = 'cdow'; h.textContent = n;
    h.setAttribute('aria-hidden', 'true');
    grid.appendChild(h);
  });
  // Monday-first offset from the first day's weekday (parse as UTC to avoid TZ drift)
  const first = new Date(data.days[0].date + 'T00:00:00Z');
  const pad = (first.getUTCDay() + 6) % 7;
  for (let i = 0; i < pad; i++) {
    const c = document.createElement('div'); c.className = 'ccell pad';
    c.setAttribute('aria-hidden', 'true'); grid.appendChild(c);
  }
  data.days.forEach(d => {
    const c = document.createElement('div'); c.className = 'ccell';
    c.style.background = shade(d.spending);
    const label = d.date + ': ' + (d.spending ? money(d.spending) : 'no spending');
    c.title = label;
    c.setAttribute('role', 'img');
    c.setAttribute('aria-label', label);
    const n = document.createElement('div'); n.className = 'dnum';
    n.setAttribute('aria-hidden', 'true');
    n.textContent = d.day != null ? d.day : Number(d.date.slice(8));
    const a = document.createElement('div'); a.className = 'damt';
    a.setAttribute('aria-hidden', 'true');
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
  if (max > 0) legend.appendChild(document.createTextNode('  (peak ' + money(max) + ')'));
  wrap.appendChild(legend);
  return wrap;
}

function checkData(data) {
  // check: {ok: bool, count: number, issues: [{kind, id, detail}]}
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  if (typeof data.ok !== 'boolean' || !Array.isArray(data.issues)) return null;
  return data;
}

function renderCheck(data) {
  const wrap = document.createElement('div'); wrap.className = 'health';
  if (data.ok) {
    const ok = document.createElement('div'); ok.className = 'cfstat';
    ok.innerHTML = '<div class="cfk">status</div>' +
      '<div class="cfv pos">No problems found</div>';
    wrap.appendChild(ok);
    return wrap;
  }
  const warn = document.createElement('div'); warn.className = 'cfwarn';
  warn.textContent = data.count + ' problem' + (data.count === 1 ? '' : 's') +
    ' found';
  wrap.appendChild(warn);
  const groups = {};
  data.issues.forEach(i => { (groups[i.kind] = groups[i.kind] || []).push(i); });
  Object.keys(groups).sort().forEach(kind => {
    const sec = document.createElement('div'); sec.className = 'hgroup';
    const h = document.createElement('div'); h.className = 'hkind';
    h.textContent = kind.replace(/_/g, ' ') + ' (' + groups[kind].length + ')';
    sec.appendChild(h);
    const list = document.createElement('div'); list.className = 'recent';
    groups[kind].forEach(i => {
      const row = document.createElement('div'); row.className = 'recrow';
      const cell = document.createElement('div'); cell.className = 'rcat';
      cell.textContent = i.detail; row.appendChild(cell); list.appendChild(row);
    });
    sec.appendChild(list); wrap.appendChild(sec);
  });
  return wrap;
}

function cashflowData(data) {
  // cashflow: {start_balance, end_balance, net_change, low_balance, low_date,
  //            negative_on, events:[{date, amount, balance, ...}]}
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  if (typeof data.start_balance !== 'number' || !Array.isArray(data.events)) return null;
  if (!data.events.length || typeof data.events[0].balance !== 'number') return null;
  return data;
}

function renderCashflow(data) {
  const wrap = document.createElement('div'); wrap.className = 'cashflow';
  const head = document.createElement('div'); head.className = 'cfhead';
  const stat = (label, val, cls) => {
    const d = document.createElement('div'); d.className = 'cfstat';
    d.innerHTML = '<div class="cfk">' + esc(label) + '</div>' +
      '<div class="cfv ' + (cls || '') + '">' + esc(val) + '</div>';
    head.appendChild(d);
  };
  const chg = data.net_change;
  stat('start', money(data.start_balance));
  stat('end', money(data.end_balance), data.end_balance < 0 ? 'neg' : 'pos');
  stat('net change', (chg >= 0 ? '+' : '') + money(chg), chg < 0 ? 'neg' : 'pos');
  stat('lowest', money(data.low_balance), data.low_balance < 0 ? 'neg' : '');
  wrap.appendChild(head);
  if (data.negative_on) {
    const warn = document.createElement('div'); warn.className = 'cfwarn';
    warn.textContent = 'Balance goes negative on ' + data.negative_on;
    wrap.appendChild(warn);
  }
  // Series: a starting point ("now") plus the running balance after each event.
  const series = [{date: 'now', v: data.start_balance}].concat(
    data.events.map(e => ({date: e.date, v: e.balance})));
  const W = 680, H = 230, padL = 10, padR = 10, padT = 16, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const vals = series.map(s => s.v);
  let minV = Math.min(0, ...vals), maxV = Math.max(0, ...vals);
  if (minV === maxV) maxV = minV + 1;
  const n = series.length;
  const xFor = i => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const yFor = v => padT + (maxV - v) / (maxV - minV) * plotH;
  const zeroY = yFor(0);
  let line = '';
  series.forEach((s, i) => {
    line += (i === 0 ? 'M' : 'L') + xFor(i).toFixed(1) + ' ' + yFor(s.v).toFixed(1) + ' ';
  });
  const area = line + 'L' + xFor(n - 1).toFixed(1) + ' ' + zeroY.toFixed(1) +
    ' L' + xFor(0).toFixed(1) + ' ' + zeroY.toFixed(1) + ' Z';
  let dots = '';
  series.forEach((s, i) => {
    const neg = s.v < 0;
    dots += '<circle cx="' + xFor(i).toFixed(1) + '" cy="' + yFor(s.v).toFixed(1) +
      '" r="' + (i === 0 ? 3 : 2.4) + '" fill="' + (neg ? 'var(--neg)' : 'var(--accent)') +
      '"><title>' + esc(s.date + ': ' + money(s.v)) + '</title></circle>';
  });
  const svg =
    '<svg viewBox="0 0 ' + W + ' ' + H + '" class="cfsvg" ' +
    'preserveAspectRatio="xMidYMid meet" role="img" ' +
    'aria-label="Projected running balance over time">' +
    '<line x1="' + padL + '" y1="' + zeroY.toFixed(1) + '" x2="' + (W - padR) +
      '" y2="' + zeroY.toFixed(1) + '" stroke="var(--muted)" ' +
      'stroke-dasharray="4 3" stroke-width="1"/>' +
    '<path d="' + area + '" fill="var(--accent)" opacity="0.12"/>' +
    '<path d="' + line + '" fill="none" stroke="var(--accent)" stroke-width="2" ' +
      'stroke-linejoin="round" stroke-linecap="round"/>' +
    dots +
    '<text x="' + padL + '" y="' + (H - 7) + '" class="cfaxis">now</text>' +
    '<text x="' + (W - padR) + '" y="' + (H - 7) + '" text-anchor="end" ' +
      'class="cfaxis">' + esc(series[n - 1].date) + '</text>' +
    '<text x="' + (padL + 2) + '" y="' + (padT) + '" class="cfaxis">' +
      esc(money(maxV)) + '</text>' +
    (minV < 0 ? '<text x="' + (padL + 2) + '" y="' + (H - padB + 10) +
      '" class="cfaxis neg">' + esc(money(minV)) + '</text>' : '') +
    '</svg>';
  const box = document.createElement('div'); box.className = 'cfchart';
  box.innerHTML = svg; wrap.appendChild(box);
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
      const b = document.createElement('div');
      b.className = 'cbar' + (vals[i] < 0 ? ' neg' : '');
      b.style.width = (Math.abs(vals[i]) / max * 100) + '%'; bw.appendChild(b);
      const v = document.createElement('div');
      v.className = 'cval' + (vals[i] < 0 ? ' neg' : '');
      v.textContent = fmtNum(key, vals[i]);
      row.appendChild(l); row.appendChild(bw); row.appendChild(v);
      bars.appendChild(row);
    });
  };
  if (cd.numKeys.length > 1) {
    const md = document.createElement('div'); md.className = 'metric';
    const sel = document.createElement('select');
    cd.numKeys.forEach(k => sel.appendChild(new Option(humanize({dest: k}), k)));
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

// Format a numeric value for display given its field name (money / percent / plain).
function fmtNum(key, v) {
  if (/rate|share|percent|_pct|^pct/i.test(key)) return v + '%';
  if (isMoneyKey(key)) return money(v);
  return String(v);
}
// A numeric column whose name implies a money amount (not a count/rate/date).
function isMoneyKey(k) {
  if (/^\d{4}-\d{2}$/.test(k)) return true;   // matrix/tagmatrix month columns
  return /total|amount|spend|income|\bnet\b|balance|budget|spent|limit|remaining|average|cumulative|projected|annual|monthly|per_?(day|week|month)|value|^over$|_over$/i.test(k)
    && !/count|rate|share|days|year|\bid\b|day\b/i.test(k);
}
// Column header label: humanize names, but leave YYYY-MM month columns alone.
function colLabel(k) {
  return /^\d{4}-\d{2}$/.test(k) ? k : humanize({dest: k});
}
// Format one cell knowing its column name, so money reads as money and
// percentages get a % - numbers come back right-aligned via the 'num' flag.
function fmtValue(key, v) {
  if (v === null || v === undefined) return {text: '', num: false};
  if (typeof v === 'number') {
    if (/rate|share|percent|_pct|^pct/i.test(key)) return {text: v + '%', num: true};
    if (isMoneyKey(key)) return {text: money(v), num: true};
    return {text: String(v), num: true};
  }
  if (typeof v === 'object') return {text: JSON.stringify(v), num: false};
  return {text: String(v), num: false};
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
  // Object with a nested array-of-objects (matrix.rows, year.months,
  // heatmap.days, quarter.quarters...): render that array as the table and
  // list the remaining scalar fields as a small summary below.
  const arrKey = Object.keys(data).find(k => Array.isArray(data[k]) &&
    data[k].length && typeof data[k][0] === 'object' && !Array.isArray(data[k][0]));
  if (arrKey) {
    const box = document.createElement('div');
    box.appendChild(objArrayTable(data[arrKey]));
    const scalars = {};
    Object.entries(data).forEach(([k, v]) => {
      if (k !== arrKey && (v === null || typeof v !== 'object')) scalars[k] = v;
    });
    if (Object.keys(scalars).length) {
      const h = document.createElement('div'); h.className = 'muted';
      h.style.margin = '12px 2px 4px'; h.textContent = 'Summary';
      box.appendChild(h); box.appendChild(fieldTable(scalars));
    }
    return box;
  }
  return fieldTable(data);
}

// Coerce a cell value into something sortable: numbers (and money/percent
// strings) compare numerically, everything else case-insensitively.
function sortValue(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return v;
  if (typeof v === 'boolean') return v ? 1 : 0;
  const s = String(v);
  const n = parseFloat(s.replace(/[$,%\s]/g, ''));
  return isNaN(n) ? s.toLowerCase() : n;
}

function objArrayTable(rows) {
  const cols = [];
  rows.forEach(r => Object.keys(r).forEach(k => { if (!cols.includes(k)) cols.push(k); }));
  const t = document.createElement('table'); t.className = 'data';
  const thead = document.createElement('thead'); const htr = document.createElement('tr');
  const ths = {};
  let sortCol = null, sortDir = 1;
  const tb = document.createElement('tbody');

  function fill(data) {
    tb.innerHTML = '';
    data.forEach(r => { const tr = document.createElement('tr');
      cols.forEach(c => { const td = document.createElement('td');
        const cell = fmtValue(c, r[c]); td.textContent = cell.text;
        if (cell.num) td.className = 'num';
        tr.appendChild(td); });
      tb.appendChild(tr); });
  }
  function sortBy(c) {
    sortDir = (sortCol === c) ? -sortDir : 1;
    sortCol = c;
    const copy = rows.slice().sort((a, b) => {
      const av = sortValue(a[c]), bv = sortValue(b[c]);
      if (av < bv) return -sortDir;
      if (av > bv) return sortDir;
      return 0;
    });
    fill(copy);
    cols.forEach(k => {
      const on = k === c;
      ths[k].setAttribute('aria-sort', on ? (sortDir > 0 ? 'ascending'
        : 'descending') : 'none');
      ths[k].querySelector('.arrow').textContent =
        on ? (sortDir > 0 ? ' ▲' : ' ▼') : '';
    });
  }

  cols.forEach(c => {
    const th = document.createElement('th');
    th.scope = 'col'; th.tabIndex = 0; th.className = 'sortable';
    th.setAttribute('aria-sort', 'none');
    th.title = 'Sort by ' + colLabel(c);
    th.appendChild(document.createTextNode(colLabel(c)));
    const arrow = document.createElement('span');
    arrow.className = 'arrow'; arrow.setAttribute('aria-hidden', 'true');
    th.appendChild(arrow);
    th.onclick = () => sortBy(c);
    th.onkeydown = (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sortBy(c); }
    };
    ths[c] = th; htr.appendChild(th);
  });
  thead.appendChild(htr); t.appendChild(thead);
  fill(rows);
  t.appendChild(tb); return t;
}

function fieldTable(obj) {
  const t = document.createElement('table'); t.className = 'data';
  const tb = document.createElement('tbody');
  Object.entries(obj).forEach(([k, v]) => {
    const tr = document.createElement('tr');
    const th = document.createElement('th'); th.scope = 'row';
    th.textContent = colLabel(k);
    const td = document.createElement('td');
    const cell = fmtValue(k, v); td.textContent = cell.text;
    if (cell.num) td.className = 'num';
    tr.appendChild(th); tr.appendChild(td); tb.appendChild(tr);
  });
  t.appendChild(tb); return t;
}

boot();
</script>
</body>
</html>
"""
