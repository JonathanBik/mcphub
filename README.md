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
REST servers (such as `context-search`) need no auth.

## Servers

Each server is one JSON file in `mcps/`, loaded on every request.
A new or deleted file shows up on the next page reload, with no restart.
Invalid files are skipped and listed under "Add a server" in the sidebar and by `python3 serve.py list`.

```sh
mcp-s-cli get-servers                         # the gateway catalog: slugs and descriptions
python3 serve.py inspect slack                # live tool list with read-only / destructive marks
python3 serve.py add slack --name "Slack" --description "..."   # verifies live, then writes mcps/slack.json
python3 serve.py list                         # registered servers; exits 1 if any file is invalid
python3 serve.py remove slack                 # deletes mcps/slack.json (run history is kept)
```

`add` calls the server's `tools/list` first and writes nothing if that fails.
The sidebar's Remove button deletes a server's file too (it asks for a second click).

Two types of file:

- `gateway`: an MCP server behind the mcp-s gateway. `id` is its catalog slug. Written by `serve.py add`.
- `rest`: a plain REST API modeled as synthetic tools (`base_url`, and `tools[]` with `method`, `path`, `inputSchema`). Written by hand; see `mcps/context-search.json`.

Optional `notes` text is shown above the tool input for that server.

### Agent skill

`.agents/skills/mcphub-mcps/SKILL.md` (also at `.claude/skills/` through a symlink) teaches a coding agent the verified flow.
Ask it, for example, "add me Slack MCP to mcphub" or "remove Slack from mcphub".
It resolves the slug from the catalog, explores the tools without calling them, adds the server with `serve.py add`, and verifies the result.
The catalog web page is https://mcp-s-connect.wewix.net/mcp-servers; `mcp-s-cli get-servers` serves the same data to agents.

## Features

- Tool search (`/` or `⌘K`), arrow-key navigation, read-only and destructive badges.
- Form built from the input schema, kept in sync with a JSON editor, plus a Schema tab.
- Required-field and type checks before a call; destructive tools need a confirm click.
- Output as Tree, Table, Text, Raw, or Diff against a past run, with a filter and copy of values and paths.
- Run history in a local SQLite file (`mcphub.db`, git-ignored), restorable with args and output.
- The URL holds server, tool and args, so a call can be bookmarked.
- `⌘↵` runs from anywhere.
