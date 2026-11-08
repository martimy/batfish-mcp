# batfish-mcp

An MCP (Model Context Protocol) server exposing [Batfish](https://batfish.org)
as tools for AI assistants. Adapted from Bat-Q's headless core (`common/`),
with the Streamlit GUI replaced by an MCP transport.

Lets an LLM ask 60 curated Batfish questions, run assertions, diff failures,
and describe topology/reachability from a snapshot zip.

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
| `batfish_list_questions(category?)` | The 60-question curated catalog (from Bat-Q's `bfqs.py`), with input types + `TYPE_HINTS` descriptions. No server needed. |
| `batfish_list_snapshots(network?)` | Snapshots of a network. |
| `batfish_upload_snapshot(name, zip_base64, network?, overwrite?)` | Upload a snapshot zip (base64). Name is sanitized (`safe_snapshot_name`); collisions are unique-ified (`name_1`, ...) like Bat-Q. |
| `batfish_run_question(fun, options?, snapshot?, reference_snapshot?, network?)` | Run any question; `reference_snapshot` + `snapshot` runs a differential question. Returns JSON-safe `{fun, snapshot, reference_snapshot, status, rows, removed_columns}`. |
| `batfish_list_assertions()` | Live pybatfish assertion names + the 16-entry registry (`specs`) with parameter descriptions. |
| `batfish_run_assertion(name, params?, question?, snapshot?, network?)` | Validate + run one assertion (answer-based ones fetch the answer first). Returns `{type, status, message, snapshot, ran_at, runtime, fun?, answer_rows?}`. |
| `batfish_run_assertion_suite(suite_yaml, snapshot?, network?)` | Validate (all-or-nothing) + run a YAML suite — same schema as Bat-Q's (`examples/assertions.yaml`). Returns `results[]`, `errors[]`, `summary`. |
| `batfish_fork_snapshot(base_name, name?, failed_nodes?, failed_interfaces?, overwrite?, network?)` | Fork a snapshot, optionally failing nodes/interfaces (then diff fork vs base with a differential question). |
| `batfish_get_topology(kind, snapshot?, network?)` | `kind="layer3"` (from `layer3Edges`, with interface titles) or `"routing"` (from `bgpEdges`) as `{kind, snapshot, node_count, edge_count, nodes, edges}`. |

All tools share the singleton Batfish session (`batfish_mcp.session`,
`BATFISH_SERVER`, default `127.0.0.1`), serialized with a lock.

## Examples

Upload `batq/abc_net.zip` and ask about it:

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

## Layout (mapped from Bat-Q)

```
src/batfish_mcp/
  server.py           # NEW — FastMCP tools over the headless core
  session.py          # NEW — thread-safe singleton Session (replaces st.session_state)
  catalog.py          # common/bfqs.py     (Streamlit @st.cache_data removed)
  assertion_specs.py  # common/assertion_specs.py, unchanged
  assertion_io.py     # common/assertion_io.py  (import path fixed)
  assertions.py       # common/assertions.py    (get_bf_session -> get_session)
  queries.py          # common/queries.py       (get_bf_session -> get_session; errors raise)
  utils.py            # common/utils.py         (safe_snapshot_name; no init_session_state)
  presenter.py        # common/presenter.py     (pure format_result*/json_to_dataframe only)
  hints.py            # common/inputs.py TYPE_HINTS only
  topology.py         # common/plotting.py      (get_topology/get_routing_topology)
tests/
  fakes.py            # Bat-Q's FakeBatfish + assertion fake (no server needed)
  test_queries.py     # ported
  test_assertions.py  # ported
  test_server.py      # MCP tools + wire round-trip
examples/assertions.yaml
HOW-TO.md             # mini tutorial (see this file for a walkthrough)
```

Dropped (GUI-only): `Home.py`, `views/*`, `common/spreadsheet.py`,
`common/htmlreport.py`, `common/assertion_report.py`,
`common/presenter.py:display_*`, `common/inputs.py:generate_input_fields`.

## Tests

```bash
pip install -e ".[dev]"
pytest          # no Batfish server required (fakes + one in-memory MCP round-trip)
```

`tests/test_assertions.py` also checks that `assertion_specs.py` stays in
sync with the installed pybatfish (scripted-assertion signatures).

## License

Apache-2.0 (see `LICENSE`), carried over from Bat-Q.
