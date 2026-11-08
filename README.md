# batfish-mcp

An MCP (Model Context Protocol) server exposing [Batfish](https://batfish.org)
as tools for AI assistants.

Lets an LLM ask 60 curated Batfish questions, run assertions, diff failures,
and describe topology/reachability from a snapshot zip.

> This project is an offshoot of Bat-Q, a Streamlit GUI over Batfish — see
> [Acknowledgements](#acknowledgements).

## Prerequisites

* Python `>=3.10`
* A running Batfish server (e.g. Batfish `allinone` on ports `9996/9997`)
  * Default host `127.0.0.1`. Set `BATFISH_SERVER` if remote, e.g. `BATFISH_SERVER=192.168.2.4`.
* Your network configs packaged as a Batfish snapshot zip (see **Providing a snapshot**).

## Installation

```bash
cd batfish-mcp   # or: git clone <URL-of-this-repo>

# Recommended: venv (avoids polluting system Python)
python3 -m venv .batmcp
source .batmcp/bin/activate
pip install -e .

# Alternatives:
# pip install -r requirements.txt
# pip install -e ".[dev]"   # for tests
```

After install you will have a console script:

```bash
which batfish-mcp
batfish-mcp --help   # starts stdio MCP server
```

### Run the server directly (stdlib / manual test)

```bash
export BATFISH_SERVER=127.0.0.1
batfish-mcp
# or
python -m batfish_mcp.server
```

It speaks stdio JSON-RPC (FastMCP). It should not print logs to stdout.

## Configure MCP clients

### Generic stdio clients (Claude Desktop, Cursor, etc.)

```json
{
  "mcpServers": {
    "batfish-mcp": {
      "command": "batfish-mcp",
      "env": { "BATFISH_SERVER": "127.0.0.1" }
    }
  }
}
```

If you use a venv, use its absolute path instead of `batfish-mcp`:

```json
{
  "mcpServers": {
    "batfish-mcp": {
      "command": "/home/user/batmcp-test/.batmcp/bin/batfish-mcp",
      "env": { "BATFISH_SERVER": "127.0.0.1" }
    }
  }
}
```

### OpenCode

OpenCode uses `opencode.json` (schema `https://opencode.ai/config.json`) with `mcp.servers` + `type: "local"` + `command` as an array.

Example **`/home/user/batmcp-test/opencode.json`** (this repo ships this, adjust paths):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "batfish-mcp": {
        "type": "local",
        "command": ["/home/user/batmcp-test/.batmcp/bin/batfish-mcp"],
        "environment": {
          "BATFISH_SERVER": "192.168.2.4"
        }
      }
    }
  }
}
```

Steps:

1. Create the venv and install as above.
2. Put that `opencode.json` in your project root (or `~/.config/opencode/`).
3. Start OpenCode from that directory / restart it so it reloads MCP servers.
4. Batfish tools appear under `execute` (Code Mode) as `tools["batfish-mcp"].*`.
   Example:
   ```js
   await tools["batfish-mcp"].batfish_list_questions({});
   ```

> **Note:** OpenCode Code Mode isolates tools. You *must* call Batfish tools via the `execute` tool, e.g. `return await tools["batfish-mcp"].batfish_run_question(...)`. There are no direct `batfish_*` tools.

## Quickstart / workflow

```bash
# 1. build a snapshot zip (see next section) and drop it in workspace
ls -lh abc_net.zip

# 2. in OpenCode, upload + query (network stays set for process lifetime)
```

In OpenCode (inside `execute`):

```js
// list available questions
await tools["batfish-mcp"].batfish_list_questions({});

// upload
await tools["batfish-mcp"].batfish_upload_snapshot({
  name: "abc_net.zip",
  zip_base64: "...",
  network: "NETWORK",
  overwrite: true
});

// verify parse
await tools["batfish-mcp"].batfish_run_question({
  fun: "initIssues", snapshot: "abc_net", network: "NETWORK"
});

// ask
await tools["batfish-mcp"].batfish_run_question({
  fun: "nodeProperties", snapshot: "abc_net"
});
await tools["batfish-mcp"].batfish_get_topology({
  kind: "layer3", snapshot: "abc_net"
});
```

> A fresh server process has no Batfish network set yet. Pass `network`
> (e.g. `"NETWORK"`) on the first call that needs snapshots; it then stays
> set on the shared session for the life of the process.
> `batfish_list_snapshots` without `network` fails with `Network must be set`.

## Providing a snapshot

* **Format:** one Batfish zip containing configs + optional `batfish/`, `hosts/`
  e.g. `abc_net.zip` → `abc_net/configs/R1.cfg`, `R2.cfg`, `R3.cfg`, `FW.cfg`, `hosts/server_*.json`
* **Where:** drop the zip in your workspace (e.g. `/home/user/batmcp-test/abc_net.zip`) and tell the assistant the filename.
* **Upload:** assistant base64-encodes and calls `batfish_upload_snapshot(name, zip_base64, network?, overwrite?)`.
  * `name` is sanitized (`safe_snapshot_name`); collisions are unique-ified (`name_1`, ...) unless `overwrite=true`.
  * `zip_base64` must be valid base64 with correct padding (`=`/`==`); `validate=True` is enforced server-side.
* **Already on server:** just give `snapshot` + `network` names; no upload needed.
* **Zip on disk vs upload:** if you have shell access you can also upload directly with pybatfish:
  ```bash
  PYTHONPATH=src python3 - <<'PY'
  import os
  from pybatfish.client.session import Session
  bf = Session(host=os.getenv("BATFISH_SERVER","127.0.0.1"))
  bf.set_network("NETWORK")
  bf.init_snapshot("abc_net.zip", name="abc_net", overwrite=True)
  print(bf.list_snapshots())
  PY
  ```

## Tools

| Tool | Description |
|---|---|
| `batfish_list_questions(category?)` | The 60-question curated catalog, with input types + `TYPE_HINTS` descriptions. No server needed. |
| `batfish_list_snapshots(network?)` | Snapshots of a network. |
| `batfish_upload_snapshot(name, zip_base64, network?, overwrite?)` | Upload a snapshot zip (base64). Name is sanitized (`safe_snapshot_name`); collisions are unique-ified (`name_1`, ...). |
| `batfish_run_question(fun, options?, snapshot?, reference_snapshot?, network?)` | Run any question; `reference_snapshot` + `snapshot` runs a differential question. Returns JSON-safe `{fun, snapshot, reference_snapshot, status, rows, removed_columns}`. |
| `batfish_list_assertions()` | Live pybatfish assertion names + the 16-entry registry (`specs`) with parameter descriptions. |
| `batfish_run_assertion(name, params?, question?, snapshot?, network?)` | Validate + run one assertion (answer-based ones fetch the answer first). Returns `{type, status, message, snapshot, ran_at, runtime, fun?, answer_rows?}`. |
| `batfish_run_assertion_suite(suite_yaml, snapshot?, network?)` | Validate (all-or-nothing) + run a YAML suite (`examples/assertions.yaml` shows the schema). Returns `results[]`, `errors[]`, `summary`. |
| `batfish_fork_snapshot(base_name, name?, failed_nodes?, failed_interfaces?, overwrite?, network?)` | Fork a snapshot, optionally failing nodes/interfaces (then diff fork vs base with a differential question). |
| `batfish_get_topology(kind, snapshot?, network?)` | `kind="layer3"` (from `layer3Edges`, with interface titles) or `"routing"` (from `bgpEdges`) as `{kind, snapshot, node_count, edge_count, nodes, edges}`. |

All tools share the singleton Batfish session (`batfish_mcp.session`,
`BATFISH_SERVER`, default `127.0.0.1`), serialized with a lock.

## Examples

Upload `abc_net.zip` and ask about it:

```
batfish_upload_snapshot(name="abc_net.zip", zip_base64="UEsDBB==", network="NETWORK")
batfish_run_question(fun="nodeProperties", snapshot="abc_net")
batfish_run_assertion(name="assert_no_forwarding_loops", snapshot="abc_net")
batfish_fork_snapshot(base_name="abc_net", failed_nodes=["r1"])
batfish_run_question(fun="differentialReachability",
    options={"pathConstraints": {"startLocation": "r2", "endLocation": "r3"},
             "headers": {"dstIps": "192.168.3.1"}},
    snapshot="abc_net_Fail", reference_snapshot="abc_net")
```

Topology:

```
batfish_get_topology(kind="layer3", snapshot="abc_net", network="NETWORK")
batfish_get_topology(kind="routing", snapshot="abc_net", network="NETWORK")
```

## Troubleshooting

* **Network must be set** → pass `network="NETWORK"` on first call.
* **zip_base64 is not valid base64: Incorrect padding** → re-encode with `base64 -w 0 abc_net.zip` (no newlines), keep trailing `=`/`==`. Do not hand-trim.
* **Snapshot already exists** → pass `overwrite: true` or accept `name_1`.
* **No parse issues but wrong IPs?** → run `batfish_run_question(fun="initIssues")` and `fileParseStatus` first.
* **Reachability to `240.254.254.1` fails but `8.8.8.8` works** → `240.0.0.0/4` is reserved; ISP egress on `Block outgoing traffic using reserved addresses` drops it. Use a public IP to test internet egress.

## Layout

```
src/batfish_mcp/
  server.py           # FastMCP tools over the headless core
  session.py          # thread-safe singleton Session
  catalog.py          # curated question catalog (60 questions)
  assertion_specs.py  # assertion registry (params as {name, type, optional})
  assertion_io.py     # assertion YAML load/validate/dump
  assertions.py       # assertion runner (session- vs answer-based, PASS/FAIL/ERROR)
  queries.py          # question runner (incl. differential snapshots)
  utils.py            # snapshot-name sanitizing, template helpers
  presenter.py        # pure DataFrame/trace serialization helpers
  hints.py            # TYPE_HINTS — tool-parameter descriptions
  topology.py         # L3 + routing graph construction from answers
tests/
  fakes.py            # FakeBatfish + assertion fake (no server needed)
  test_queries.py
  test_assertions.py
  test_server.py      # MCP tools + wire round-trip
examples/assertions.yaml
HOW-TO.md             # mini tutorial (see this file for a walkthrough)
```

## Acknowledgements

batfish-mcp started as an offshoot of Bat-Q, a Streamlit GUI over
Batfish. The reusable headless core (question catalog, assertion
registry and runner, answer serialization, snapshot tooling) came from
there; the GUI layer was dropped and replaced with the MCP transport.
Both projects are Apache-2.0.

## Tests

```bash
pip install -e ".[dev]"
pytest          # no Batfish server required (fakes + one in-memory MCP round-trip)
```

`tests/test_assertions.py` also checks that `assertion_specs.py` stays in
sync with the installed pybatfish (scripted-assertion signatures).

## License

Apache-2.0 (see `LICENSE`).
