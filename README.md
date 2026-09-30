# mcphub

A local playground for exploring MCP server tools in the browser.
Pick a server, browse its tools, fill inputs from a form generated from each tool's schema, run the tool, and explore the output.

## Run

```sh
python3 serve.py            # then open http://localhost:8791
PORT=9000 python3 serve.py  # other port
python3 serve.py --selftest # live checks against the gateway
```

Python 3 standard library only, no dependencies.

## Auth

The hub reuses the OAuth token that `mcp-s-cli` stores in `~/.config/mcp-s-cli/auth.json`.
It reads the token on every request, so run `mcp-s-cli login` when a call reports an expired session, then run again.
The `context-search` server is a plain REST API and needs no auth.

## Features

- Tool search (`/` or `⌘K`), arrow-key navigation, read-only and destructive badges.
- Form built from the input schema, kept in sync with a JSON editor, plus a Schema tab.
- Required-field and type checks before a call; destructive tools need a confirm click.
- Output as Tree, Table, Text, Raw, or Diff against a past run, with a filter and copy of values and paths.
- Run history in a local SQLite file (`mcphub.db`, git-ignored), restorable with args and output.
- The URL holds server, tool and args, so a call can be bookmarked.
- `⌘↵` runs from anywhere.

Servers are listed in `SERVERS` in `serve.py`.
