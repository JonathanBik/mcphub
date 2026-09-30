---
name: mcphub-mcps
description: Add or remove an MCP server in mcphub (the local MCP tool explorer). Use for "add me Slack MCP to mcphub", "add <name> to mcphub", "remove/delete <name> from mcphub", or registering a REST API as synthetic tools.
---

# mcphub: add or remove an MCP server

mcphub reads one JSON file per server from `mcps/` on every request, so a new or deleted file shows up on the next page reload with no restart.
All commands below run from the mcphub repo root.
`python3 serve.py` with a subcommand is the only writer of gateway server files: `add` verifies the server live and refuses to write a file it could not verify.

Sources, in order of trust:

- `mcp-s-cli get-servers` - the gateway catalog: `slug:` line, then `Name - description`. It holds the same data as https://mcp-s-connect.wewix.net/mcp-servers, which is a login-only web page; open that page only for a human, or when the CLI is unavailable.
- `python3 serve.py inspect <slug>` - live `tools/list` through the same gateway call mcphub makes: tool names, required args, and annotations (`RO` read-only, `DEL` destructive, blank = unmarked).
- `mcp-s-cli info <tool>` - one tool's full description and input schema. Pass the full prefixed name that `inspect` prints, e.g. `slack__search-messages`.

**State-changing** means the tool sends, posts, triggers, clears, pauses, creates, updates or deletes something.
**Unmarked** tools carry no annotation, so the server made no promise either way: call an unmarked tool state-changing when its name or description says so; list auth helpers (`authenticate`, `complete_authentication`) separately.

A gateway server's `id` in mcphub is its catalog slug. A personal deployment's id is `<user>:<slug>` (for example `jonathanbik@wix.com:dora-preview-1-35-0`); compare slugs with the part after the `:`.

## Add a gateway server ("add me Slack MCP to mcphub")

1. **Auth.** Run `mcp-s-cli check-auth`. Exit 4 means the login expired: ask the user to run `! mcp-s-cli login`, then continue. Done when it exits 0.
2. **Resolve the slug.** Search the catalog for the name's most distinctive word, skipping generic words like "MCP" and "server" (for "airflow logs", search `airflow`), case-insensitively:
   `mcp-s-cli get-servers | grep -i -B1 -A1 "<word>"`
   Pick by the first rule that applies:
   - A slug equal to the name in hyphen form (`airflow logs` -> `airflow-logs`), or a display name equal to the name: that slug wins, even when other entries mention the word.
   - Several candidates and no exact match: list them with their descriptions and ask the user to pick one.
   - No hit: repeat with each other word of the name, and with the list of all slugs, `mcp-s-cli get-servers | grep '^slug:'`. Still nothing: say so, show up to 5 slugs that share a word or prefix with the name (or none, if nothing does), ask which server they meant, and stop.
   Done when one slug is chosen.
3. **Check it is new.** Run `python3 serve.py list`. If an id equals the slug (or ends in `:<slug>`), report the existing file and stop, unless the user asked to refresh it (then use `--force` in step 5).
4. **Explore.** Run `python3 serve.py inspect <slug>` and read every line. For the 2-3 tools a user is most likely to try first, run `mcp-s-cli info <tool>`. Done when you can state the tool count split into read-only, destructive and unmarked, name the unmarked tools that look state-changing, and say in one sentence what the server is for. Explore with `inspect` and `info` only: they list schemas and call no tool.
5. **Add.** Use the catalog's display name and description exactly as `get-servers` prints them; the catalog line is `Name - description`, split at the first ` - `:
   `python3 serve.py add <slug> --name "<Name>" --description "<catalog description>"`
   Done when it prints `added <slug> -> mcps/<file>.json: N tools (R read-only, D destructive, U unmarked)`. On `error:`, read the message: an auth error means going back to step 1; "returned no tools" means a wrong slug or no access, so go back to step 2.
6. **Verify.** Done when all of these hold:
   - `python3 serve.py list` shows the slug and prints no `INVALID` line.
   - `cat mcps/<file>.json` has `type: "gateway"` and `verified_tools` equal to the count from step 4.
   - If the hub is running, the first command lists the slug and the second prints the tool count from step 4:
     `curl -s localhost:8791/api/servers | python3 -c 'import json,sys; print([s["id"] for s in json.load(sys.stdin)["servers"]])'`
     `curl -s "localhost:8791/api/tools?server=<slug>" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["result"]["tools"]))'`
7. **Report** the file path, the tool count split into read-only / destructive / unmarked, the state-changing unmarked tools, 3-5 notable tools, and the deep link `http://localhost:8791/#/<slug>`.

## Remove a server

1. Run `python3 serve.py list` and match the user's name to one id. If it is ambiguous, ask.
2. Run `python3 serve.py remove <id>`. Done when it prints `removed <id> (mcps/<file>)`, `list` no longer shows the id, and (if the hub is running) the `/api/servers` command from Add step 6 no longer lists it.
3. Report the deleted file. Run history in `mcphub.db` stays. In the UI, the sidebar's Remove button does the same (two clicks).

## Register a REST API (not in the catalog)

A REST endpoint with no MCP server behind it is added by hand as a `rest` file, modeled on `mcps/context-search.json`:

```json
{
  "id": "my-api", "name": "My API", "type": "rest",
  "base_url": "https://host/_api/base",
  "description": "One line: what it is, auth, network.",
  "notes": "Optional. Findings from live runs, shown above the tool input.",
  "tools": [
    {"name": "my-api__search", "method": "POST", "path": "/search",
     "description": "What it does. Example: {\"query\": \"x\"}",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}
  ]
}
```

GET tools send no body; any other method sends the args as a JSON body.
A response shaped `{"statusCode", "body"}` is decoded, so `isError` reflects the real status.
An `Example: {...}` JSON in a tool description becomes a "Use example" button in the UI.
Done when `python3 serve.py list` prints no `INVALID` line and one real call through `curl -s localhost:8791/api/call -d '{"server":"my-api","tool":"my-api__search","args":{...}}'` returns the expected payload.

## Rules

- Keep exploration to schemas. Calling a tool marked `DEL`, or any state-changing tool, needs the user's explicit go-ahead for that call.
- Gateway files come only from `serve.py add`; hand edits are limited to `notes` and `description`.
- Commit `mcps/` changes only when the user asks.
