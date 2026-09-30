#!/usr/bin/env python3
"""Local playground for the mcp-s.wewix.net MCP servers.

Reuses the OAuth token mcp-s-cli already stored, so there is no auth flow here.
Servers are JSON files in mcps/ (see README.md).

Run:     python3 serve.py                       then open http://localhost:8791
Manage:  python3 serve.py list | inspect <slug> | add <slug> [--name N] [--description D] | remove <id>
Test:    python3 serve.py --selftest
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

GATEWAY = "https://mcp-s.wewix.net"
AUTH_FILE = Path.home() / ".config/mcp-s-cli/auth.json"
HERE = Path(__file__).parent
PORT = int(os.environ.get("PORT", 8791))

# Server registry: one JSON file per server in mcps/, read on every request, so
# adding or removing a file takes effect without a restart. Two types:
#   gateway - an MCP server behind the mcp-s gateway; "id" is its catalog slug.
#   rest    - a plain REST API modeled as synthetic tools (base_url + tools[] with
#             method/path/inputSchema), so it rides the same tool-list/run UI.
MCPS_DIR = HERE / "mcps"
TYPES = ("gateway", "rest")


def validate(cfg):
    """Problems with one server config, as a list of strings (empty = valid)."""
    errs = []
    if not isinstance(cfg, dict):
        return ["top level must be a JSON object"]
    for key in ("id", "name", "type"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            errs.append(f"missing string field {key!r}")
    if cfg.get("type") not in TYPES:
        errs.append(f"type must be one of {TYPES}")
    if cfg.get("type") == "rest":
        if not str(cfg.get("base_url", "")).startswith("http"):
            errs.append("rest server needs base_url")
        tools = cfg.get("tools")
        if not isinstance(tools, list) or not tools:
            errs.append("rest server needs a non-empty tools list")
        for t in tools or []:
            missing = [k for k in ("name", "method", "path", "inputSchema") if k not in t]
            if missing:
                errs.append(f"tool {t.get('name', '?')!r} missing {missing}")
    return errs


def load_mcps():
    """(configs by id, problems by file name). Invalid files are skipped, never fatal."""
    servers, problems = {}, {}
    for f in sorted(MCPS_DIR.glob("*.json")):
        try:
            cfg = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError) as e:
            problems[f.name] = [f"unreadable: {e}"]
            continue
        errs = validate(cfg)
        if not errs and cfg["id"] in servers:
            errs = [f"duplicate id {cfg['id']!r} (also in {servers[cfg['id']]['file']})"]
        if errs:
            problems[f.name] = errs
            continue
        servers[cfg["id"]] = {**cfg, "file": f.name}
    return servers, problems


def file_for(server_id):
    """mcps/<name>.json for a server id: the catalog slug, minus any user@ prefix."""
    return MCPS_DIR / (re.sub(r"[^a-z0-9._-]+", "-", server_id.split(":")[-1].lower()).strip("-") + ".json")


def rest_call(cfg, tool, args):
    """Plain REST call for a "rest" server - no MCP, no auth.

    Some gateways (context-store-serverless) always answer transport HTTP 200 and
    put the real status in `statusCode` plus a JSON-string `body` envelope; decode
    that when present instead of trusting the transport code. Returns (payload, ok).
    """
    t = next((x for x in cfg["tools"] if x["name"] == tool), None)
    if t is None:
        return f"tool {tool!r} is not defined in mcps/{cfg['file']}", False
    method = t["method"].upper()
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + t["path"],
        data=json.dumps(args).encode() if method != "GET" else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            envelope, status = maybe_json(r.read().decode(errors="replace")), r.status
    except urllib.error.HTTPError as e:
        envelope, status = maybe_json(e.read().decode(errors="replace")), e.code
    if isinstance(envelope, dict) and "statusCode" in envelope and "body" in envelope:
        body = envelope["body"]
        return (maybe_json(body) if isinstance(body, str) else body), envelope["statusCode"] < 400
    return envelope, status < 400


def public(cfg):
    """What the browser needs to know about a server (rest tools are served by /api/tools)."""
    return {k: cfg.get(k) for k in ("id", "name", "type", "description", "notes", "file")}

# File-based store for logs and call history, so both survive a restart -
# a single sqlite3 file next to this script. ponytail: a fresh connection per
# call, not a pooled/shared one; this is a single-user local tool, not a
# service worth connection-pooling.
DB_PATH = HERE / "mcphub.db"


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, msg TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL, server TEXT, tool TEXT, "
        "args TEXT, result TEXT, failed INTEGER)"
    )
    try:  # run duration, added after the table first shipped
        conn.execute("ALTER TABLE history ADD COLUMN ms REAL")
    except sqlite3.OperationalError:
        pass
    return conn


def log(msg):
    print(msg)
    with db() as conn:
        conn.execute("INSERT INTO logs (msg) VALUES (?)", (msg,))
        # ponytail: fixed 2000-row cap, raise if you want deeper log history
        conn.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 2000)")


def recent_logs(limit=200):
    with db() as conn:
        rows = conn.execute("SELECT msg FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [r[0] for r in reversed(rows)]


def add_history(entry):
    with db() as conn:
        conn.execute(
            "INSERT INTO history (t, server, tool, args, result, failed, ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry["t"], entry["server"], entry["tool"], json.dumps(entry["args"]),
             json.dumps(entry["result"]), int(bool(entry["failed"])), entry.get("ms")),
        )
        # ponytail: fixed 500-row cap, raise if you want deeper call history
        conn.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 500)")


def recent_history(limit=200):
    with db() as conn:
        rows = conn.execute(
            "SELECT t, server, tool, args, result, failed, ms FROM history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {"t": t, "server": s, "tool": tool, "args": json.loads(a), "result": json.loads(r), "failed": bool(f), "ms": ms}
        for t, s, tool, a, r, f, ms in rows
    ]


class AuthError(Exception):
    pass


def token():
    # Read per request, so `mcp-s-cli login` takes effect without a restart.
    try:
        return json.loads(AUTH_FILE.read_text())["tokens"][GATEWAY]["access_token"]
    except (OSError, KeyError, json.JSONDecodeError) as e:
        raise AuthError(f"no token in {AUTH_FILE} ({e}) - run: mcp-s-cli login")


def rpc(server, method, params):
    """One JSON-RPC call. The gateway is stateless, so no initialize handshake."""
    req = urllib.request.Request(
        f"{GATEWAY}/mcp?mcp={urllib.parse.quote(server, safe='')}",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:2000]
        if e.code in (401, 403):
            raise AuthError(f"{e.code} from gateway - token expired? run: mcp-s-cli login\n{body}")
        return {"error": {"code": e.code, "message": e.reason, "data": body}}


def maybe_json(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def unwrap(result):
    """Peel MCP content[].text envelopes down to the payload.

    Two shapes show up here. The gateway double-wraps, so a single text part
    often holds a whole second envelope - peel until a layer stops being one.
    Other tools emit one text part per record (list_dora_agents does), so a
    multi-part layer becomes a list. Returns (payload, is_error), with is_error
    true if any layer flagged it.
    """
    is_error = False
    for _ in range(5):  # ponytail: fixed depth; gateway uses 2, raise if that changes
        if not isinstance(result, dict):
            break
        is_error = is_error or bool(result.get("isError"))
        parts = result.get("content")
        if not isinstance(parts, list):
            break
        texts = [p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        if not texts:
            break
        if len(texts) > 1:
            return [maybe_json(t) for t in texts], is_error
        peeled = maybe_json(texts[0])
        if isinstance(peeled, str):  # plain prose, nothing left to peel
            return peeled, is_error
        result = peeled
    return result, is_error


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        # This file gets edited and reloaded constantly - a plain browser
        # reload silently serving stale JS (no validators, so it can still
        # get cached) is a real trap, not a hypothetical one.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        qs = urllib.parse.parse_qs(url.query)
        if url.path == "/api/servers":
            servers, problems = load_mcps()
            self._send(200, {"servers": [public(c) for c in servers.values()], "problems": problems})
        elif url.path == "/api/logs":
            self._send(200, recent_logs())
        elif url.path == "/api/history":
            self._send(200, recent_history())
        elif url.path == "/api/tools":
            server = qs.get("server", [""])[0]
            cfg = load_mcps()[0].get(server)
            if cfg is None:
                self._send(404, {"error": f"no server {server!r} in mcps/"})
            elif cfg["type"] == "rest":
                tools = [{k: v for k, v in t.items() if k not in ("method", "path")} for t in cfg["tools"]]
                self._send(200, {"result": {"tools": tools}, "isError": False})
            else:
                self._guarded(lambda: rpc(server, "tools/list", {}))
        elif url.path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        url = urllib.parse.urlsplit(self.path)
        if not url.path.startswith("/api/servers/"):
            return self._send(404, {"error": "not found"})
        server = urllib.parse.unquote(url.path[len("/api/servers/"):])
        try:
            removed = remove_server(server)
        except KeyError as e:
            return self._send(404, {"error": str(e)})
        log(f"[server-removed] {server} ({removed})")
        self._send(200, {"ok": True, "removed": removed})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/history":
            add_history(body)
            return self._send(200, {"ok": True})
        log(f"[request] server={body.get('server')} tool={body.get('tool')} "
            f"args={json.dumps(body.get('args', {}))[:2000]}")
        cfg = load_mcps()[0].get(body.get("server"))
        if cfg is None:
            return self._send(200, {"error": f"no server {body.get('server')!r} in mcps/ - was it removed?"})
        if cfg["type"] == "rest":
            try:
                payload, ok = rest_call(cfg, body["tool"], body.get("args", {}))
            except Exception as e:  # noqa: BLE001 - surface anything to the browser
                log(f"[exception] {type(e).__name__}: {e}")
                return self._send(200, {"error": f"{type(e).__name__}: {e}"})
            log(f"[response] isError={not ok} result={json.dumps(payload)[:4000]}")
            return self._send(200, {"result": payload, "isError": not ok})
        self._guarded(
            lambda: rpc(body["server"], "tools/call",
                        {"name": body["tool"], "arguments": body.get("args", {})})
        )

    def _guarded(self, fn):
        try:
            out = fn()
        except AuthError as e:
            log(f"[auth-error] {e}")
            return self._send(401, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - surface anything to the browser
            log(f"[exception] {type(e).__name__}: {e}")
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        if "result" in out:
            raw = out["result"]
            out["result"], out["isError"] = unwrap(raw)
            if out["result"] is not raw:  # pre-unwrap envelope, for the UI's Raw view
                out["raw"] = raw
            # full response always, plus the pre-unwrap envelope on isError -
            # that's the exact "tool succeeded but UI went red" case to root-cause
            log(f"[response] isError={out['isError']} result={json.dumps(out['result'])[:4000]}")
            if out["isError"]:
                log(f"[isError] raw result before unwrap:\n{json.dumps(raw, indent=2)[:4000]}")
        elif "error" in out:
            log(f"[rpc-error] {json.dumps(out['error'])[:2000]}")
        self._send(200, out)


# ---------------------------------------------------------------- server management CLI

def list_tools(server_id):
    """Live tools/list for a gateway slug; raises with a readable message on any failure."""
    out = rpc(server_id, "tools/list", {})
    if "error" in out:
        raise RuntimeError(f"gateway error for {server_id!r}: {json.dumps(out['error'])[:500]}")
    tools = (out.get("result") or {}).get("tools")
    if not tools:
        raise RuntimeError(f"{server_id!r} returned no tools - wrong slug, or no access?")
    return tools


def tool_summary(tools):
    """Counts by MCP annotation: read-only, destructive, and the rest."""
    ann = [t.get("annotations") or {} for t in tools]
    ro = sum(1 for a in ann if a.get("readOnlyHint"))
    de = sum(1 for a in ann if a.get("destructiveHint") and not a.get("readOnlyHint"))
    return {"tools": len(tools), "read_only": ro, "destructive": de, "other": len(tools) - ro - de}


def add_server(server_id, name=None, description="", force=False):
    """Verify a gateway slug live, then write mcps/<slug>.json. Returns (path, summary)."""
    servers, _ = load_mcps()
    path = file_for(server_id)
    if server_id in servers and not force:
        raise FileExistsError(f"{server_id!r} is already added ({servers[server_id]['file']}); pass --force to overwrite")
    if path.exists() and not force:
        raise FileExistsError(f"{path.name} already exists; pass --force to overwrite")
    tools = list_tools(server_id)  # the verification: no file is written unless this works
    summary = tool_summary(tools)
    cfg = {"id": server_id, "name": name or server_id.replace("-", " ").title(), "type": "gateway",
           "description": description, "added": time.strftime("%Y-%m-%d"), "verified_tools": summary["tools"]}
    assert not validate(cfg), validate(cfg)
    MCPS_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return path, summary


def remove_server(server_id):
    """Delete the config file for a server id. Returns the removed file name."""
    servers, _ = load_mcps()
    if server_id not in servers:
        raise KeyError(f"no server {server_id!r} in mcps/")
    (MCPS_DIR / servers[server_id]["file"]).unlink()
    return servers[server_id]["file"]


def first_line(text, width=110):
    line = " ".join((text or "").strip().split("\n\n")[0].split())
    return line if len(line) <= width else line[: width - 1] + "…"


def cli(argv):
    """list | inspect <slug> | add <slug> [--name N] [--description D] [--force] | remove <id>. Exit 0 on success."""
    cmd, args = argv[0], argv[1:]
    opt = lambda flag: args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None
    pos = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--name", "--description"))]
    try:
        if cmd == "list":
            servers, problems = load_mcps()
            for c in servers.values():
                print(f"{c['id']:<42} {c['type']:<8} {c['name']:<24} mcps/{c['file']}")
            for f, errs in problems.items():
                print(f"INVALID mcps/{f}: {'; '.join(errs)}", file=sys.stderr)
            return 1 if problems else 0
        if cmd == "inspect":
            tools = list_tools(pos[0])
            s = tool_summary(tools)
            print(f"{pos[0]}: {s['tools']} tools ({s['read_only']} read-only, {s['destructive']} destructive, {s['other']} unmarked)")
            for t in tools:
                a = t.get("annotations") or {}
                tag = "RO " if a.get("readOnlyHint") else "DEL" if a.get("destructiveHint") else "   "
                req = (t.get("inputSchema") or {}).get("required") or []
                print(f"  {tag} {t['name']}({', '.join(req)})  {first_line(t.get('description'))}")
            return 0
        if cmd == "add":
            path, s = add_server(pos[0], opt("--name"), opt("--description") or "", "--force" in args)
            print(f"added {pos[0]} -> {path.relative_to(HERE)}: {s['tools']} tools "
                  f"({s['read_only']} read-only, {s['destructive']} destructive, {s['other']} unmarked)")
            return 0
        if cmd == "remove":
            print(f"removed {pos[0]} (mcps/{remove_server(pos[0])})")
            return 0
    except IndexError:
        pass
    except (AuthError, RuntimeError, FileExistsError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 4 if isinstance(e, AuthError) else 1
    print(cli.__doc__, file=sys.stderr)
    return 2


def selftest():
    text = lambda s, err=False: {"content": [{"type": "text", "text": s}], "isError": err}
    assert unwrap(text('{"a":1}')) == ({"a": 1}, False)
    assert unwrap(text("hi")) == ("hi", False)
    assert unwrap({"structured": 1}) == ({"structured": 1}, False)
    # the double wrap the gateway actually sends, with isError only on the inner layer
    assert unwrap(text(json.dumps(text('{"a":1}', err=True)))) == ({"a": 1}, True)
    # one content part per record, as list_dora_agents sends
    many = {"content": [{"type": "text", "text": '{"id":1}'}, {"type": "text", "text": "raw"}]}
    assert unwrap(many) == ([{"id": 1}, "raw"], False)

    # registry: every file valid, and the validator catches the common mistakes
    servers, problems = load_mcps()
    assert not problems, problems
    assert "context-search" in servers and servers["context-search"]["type"] == "rest"
    assert validate({"id": "x", "name": "X", "type": "rest"}) and validate({"id": "x", "type": "gateway"})
    assert not validate({"id": "x", "name": "X", "type": "gateway"})
    assert file_for("jonathanbik@wix.com:dora-preview-1-35-0").name == "dora-preview-1-35-0.json"

    tools = list_tools("dora")
    assert all(t["name"].startswith("dora__") for t in tools), [t["name"] for t in tools]
    assert all("inputSchema" in t for t in tools)

    caps, ok = rest_call(servers["context-search"], "context__capabilities", {})
    assert ok and isinstance(caps, dict) and caps, caps

    add_history({"t": 0, "server": "__selftest__", "tool": "y", "args": {"a": 1}, "result": {"b": 2}, "failed": False})
    h = recent_history(1)
    assert h and h[0]["server"] == "__selftest__" and h[0]["args"] == {"a": 1}, h
    with db() as conn:
        conn.execute("DELETE FROM history WHERE server = '__selftest__'")

    print(f"ok - unwrap, {len(servers)} valid server files, {len(tools)} dora tools, "
          f"context API reachable, sqlite history round-trips")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    else:
        print(f"http://localhost:{PORT}   servers: {', '.join(load_mcps()[0])}")
        ThreadingHTTPServer(("localhost", PORT), Handler).serve_forever()
