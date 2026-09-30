#!/usr/bin/env python3
"""Local playground for the mcp-s.wewix.net MCP servers.

Reuses the OAuth token mcp-s-cli already stored, so there is no auth flow here.
Run:  python3 serve.py        then open http://localhost:8791
"""

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

GATEWAY = "https://mcp-s.wewix.net"
AUTH_FILE = Path.home() / ".config/mcp-s-cli/auth.json"
SERVERS = ["dora", "trino", "wix-connect", "domains-context", "jonathanbik@wix.com:dora-preview-1-35-0",
           "context-search", "openmetadata"]
HERE = Path(__file__).parent
PORT = int(os.environ.get("PORT", 8791))

# The Context Search API isn't MCP - plain REST, no auth, on the Wix network.
# Modeled as 3 fixed synthetic "tools" so it rides the existing tool-list/args/run
# UI unchanged. See https://bo.wix.com/stash/dora-context-api-demo/
CONTEXT_BASE = "https://wix-bo.com/_api/context-store-serverless"
CONTEXT_PATHS = {
    "context__capabilities": ("GET", "/api/context/capabilities"),
    "context__domains": ("POST", "/api/context/domains"),
    "context__search": ("POST", "/api/context/search"),
}
CONTEXT_TOOLS = [
    {
        "name": "context__capabilities",
        "description": (
            "GET, no body, no auth. Call once per session, before searching: returns "
            "domain_count / searchable_domain_count, the kb_types vocabulary (product, "
            "business, analytical are searchable; reports and rules exist but are never "
            "embedded, so filtering to them always returns nothing), doc_kinds "
            "(object/property/rel), max_domain_ids, and doc_id_format. The response also "
            "embeds the full request JSON-schema for both POST endpoints under "
            "result.filters, straight from the source docstring - read that instead of any "
            "external doc, it can't drift from the code.\n\n"
            "Example: no args needed - just Run."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "context__domains",
        "description": (
            "POST. Where does this question live? Ranking only, no content retrieved - "
            "cheaper than context__search when you just want to see or confirm routing. "
            "Most callers should call context__search directly instead, since it routes "
            "internally too.\n\n"
            "Ranks ALL domains, not just searchable ones: considered[] includes "
            "is_searchable:false entries mixed in below the searchable ones, and `rank` is "
            "each domain's absolute position across every domain, not a 0..limit-1 index - "
            "a limit:3 response can show ranks 1, 3, 4.\n\n"
            "Example: {\"query\": \"How many active restaurant order sites were there last "
            "month\", \"limit\": 5} -> routing.selected includes \"restaurants\"."
        ),
        "inputSchema": {
            "type": "object", "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
                "caller": {"type": "string", "default": "mcphub"},
                "conversation_id": {"type": "string"},
            },
        },
    },
    {
        "name": "context__search",
        "description": (
            "POST. Routes (or uses domain_ids to skip routing) and retrieves matching "
            "context in one call - the one endpoint most callers actually need.\n\n"
            "domain_ids supplied -> routing is skipped, exactly those domains are searched; "
            "if one is_searchable:false (check via context__capabilities first), you get "
            "statusCode 400 DOMAIN_NOT_SEARCHABLE - verified live with domain_ids:[\"ab_test\"]: "
            "{\"success\":false,\"error\":\"Domain(s) have no published content to search: "
            "ab_test\",\"error_type\":\"DOMAIN_NOT_SEARCHABLE\"}.\n\n"
            "KPIs only: kb_types:[\"analytical\"], doc_kinds:[\"object\"] - the analytical KB "
            "embeds its relationships under the same kb_type, so doc_kinds is what narrows it.\n\n"
            "Table names only ever arrive as prose inside a KPI's \"Sources:\" line (verified "
            "live) - there is no structured table field to filter or read.\n\n"
            "include_answer:true adds an answer object {text, model, cited_doc_ids, "
            "chunks_used} synthesized over the retrieved chunks (live model: "
            "deepseek-v4-flash-0731). cited_doc_ids is the checked intersection of what the "
            "model cited and what it was actually given - safe to render as provenance. Off "
            "by default: summarizing passages beforehand throws away context you could have "
            "used yourself.\n\n"
            "Gotcha: the gateway always answers transport HTTP 200 - the real status is "
            "`statusCode` inside the response; this tool (context_call() in serve.py) already "
            "decodes that, so isError in this UI reflects the real status.\n\n"
            "Example: {\"query\": \"How many active restaurant order sites were there last "
            "month\", \"limit\": 5} -> results[0] is the KPI active_restaurants_orders_sites, "
            "Sources: prod.rest.monthly_churn, prod.rest.restaurants_gpv_dim."
        ),
        "inputSchema": {
            "type": "object", "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
                "caller": {"type": "string", "default": "mcphub"},
                "conversation_id": {"type": "string"},
                "domain_ids": {"type": "array", "items": {"type": "string"}},
                "kb_types": {"type": "array", "items": {"type": "string"}},
                "doc_kinds": {"type": "array", "items": {"type": "string"}},
                "include_answer": {"type": "boolean", "default": False},
            },
        },
    },
]


def context_call(tool, args):
    """Plain REST call to the context-store-serverless gateway - no MCP, no auth.

    It always answers transport HTTP 200; the real status is `statusCode` inside
    a JSON-string `body` envelope (the API's own documented gotcha) - decode that
    instead of trusting the transport code. Returns (payload, ok).
    """
    method, path = CONTEXT_PATHS[tool]
    req = urllib.request.Request(
        CONTEXT_BASE + path,
        data=json.dumps(args).encode() if method == "POST" else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        envelope = json.loads(r.read())
    body = envelope.get("body")
    payload = json.loads(body) if isinstance(body, str) else body
    return payload, envelope.get("statusCode", 200) < 400

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
        f"{GATEWAY}/mcp?mcp={server}",
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
        if self.path == "/api/servers":
            self._send(200, SERVERS)
        elif self.path == "/api/logs":
            self._send(200, recent_logs())
        elif self.path == "/api/history":
            self._send(200, recent_history())
        elif self.path.startswith("/api/tools"):
            server = self.path.split("server=")[-1]
            if server == "context-search":
                self._send(200, {"result": {"tools": CONTEXT_TOOLS}, "isError": False})
            else:
                self._guarded(lambda: rpc(server, "tools/list", {}))
        elif self.path.split("?")[0] in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/history":
            add_history(body)
            return self._send(200, {"ok": True})
        log(f"[request] server={body.get('server')} tool={body.get('tool')} "
            f"args={json.dumps(body.get('args', {}))[:2000]}")
        if body.get("server") == "context-search":
            try:
                payload, ok = context_call(body["tool"], body.get("args", {}))
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

    tools = rpc("dora", "tools/list", {})["result"]["tools"]
    assert any(t["name"] == "dora__list_dora_agents" for t in tools), tools
    assert all("inputSchema" in t for t in tools)

    live, err = unwrap(rpc("domains-context", "tools/call",
                           {"name": "domains-context__list_domains", "arguments": {}})["result"])
    assert not err and isinstance(live, dict) and "domains" in live, live

    caps, ok = context_call("context__capabilities", {})
    assert ok and isinstance(caps, dict) and caps, caps

    add_history({"t": 0, "server": "__selftest__", "tool": "y", "args": {"a": 1}, "result": {"b": 2}, "failed": False})
    h = recent_history(1)
    assert h and h[0]["server"] == "__selftest__" and h[0]["args"] == {"a": 1}, h
    with db() as conn:
        conn.execute("DELETE FROM history WHERE server = '__selftest__'")

    print(f"ok - unwrap, {len(tools)} dora tools, {live['count']} domains unwrapped, "
          f"context API reachable, sqlite history round-trips")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        print(f"http://localhost:{PORT}   servers: {', '.join(SERVERS)}")
        ThreadingHTTPServer(("localhost", PORT), Handler).serve_forever()
