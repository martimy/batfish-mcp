# batfish-mcp

An MCP (Model Context Protocol) server that exposes [Batfish](https://batfish.org)
as tools for AI assistants. Point it at a snapshot of your network configs and
ask questions in plain language, without writing Batfish queries yourself.

With it, an assistant can:

* run any of **60 curated Batfish questions** (node properties, routing, ACLs, reachability, parse issues, and more)
* run **assertions**, such as "no forwarding loops", singly or as a YAML suite
* **fork a snapshot** with failed nodes or interfaces and **diff** the result against the original
* return **layer 3 and routing topology** as nodes and edges

> This project is an offshoot of [Bat-Q](https://github.com/martimy/Bat-Q), a
> Streamlit GUI over Batfish. See [Acknowledgements](#acknowledgements).

## Example prompts

Once the server is connected to your MCP client and a snapshot is loaded, try:

* "List the snapshots and tell me whether the configs parsed cleanly."
* "Are there any forwarding loops in this snapshot?"
* "Show me the layer 3 topology."
* "What happens to reachability from r2 to r3 if r1 fails?"
* "Which nodes have BGP configured, and what are their peers?"

Behind the scenes the assistant calls the [tools](#tools) listed below.

## Quick start

### 1. Start Batfish

The server needs a running Batfish instance. The quickest way is Docker:

```bash
docker run --rm -p 9996:9996 -p 9997:9997 batfish/allinone
```

The server connects to `127.0.0.1` by default. If Batfish runs elsewhere, set
`BATFISH_SERVER` (for example `BATFISH_SERVER=<batfish-host>`).

### 2. Install batfish-mcp

Requires Python `>=3.10`.

```bash
git clone https://github.com/martimy/batfish-mcp.git
cd batfish-mcp

# Recommended: use a virtual environment
python3 -m venv .batmcp
source .batmcp/bin/activate
pip install -e .
```

Alternatives:

```bash
pip install -r requirements.txt
pip install -e ".[dev]"   # includes test dependencies
```

This installs a console script, `batfish-mcp`, which starts the MCP server over stdio.

### 3. Connect your MCP client

#### Claude Desktop, Cursor, and other stdio clients

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

If you installed into a virtual environment, use the absolute path to the script
instead (find it with `which batfish-mcp` while the environment is active):

```json
{
  "mcpServers": {
    "batfish-mcp": {
      "command": "/path/to/batfish-mcp/.batmcp/bin/batfish-mcp",
      "env": { "BATFISH_SERVER": "127.0.0.1" }
    }
  }
}
```

Restart the client so it picks up the new server.

#### OpenCode

OpenCode uses `opencode.json` (schema `https://opencode.ai/config.json`) with
`mcp.servers`, `type: "local"`, and `command` given as an array:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "batfish-mcp": {
        "type": "local",
        "command": ["/path/to/batfish-mcp/.batmcp/bin/batfish-mcp"],
        "environment": {
          "BATFISH_SERVER": "127.0.0.1"
        }
      }
    }
  }
}
```

Put the file in your project root (or `~/.config/opencode/`), then start or
restart OpenCode from that directory so it reloads MCP servers.

> **Note:** OpenCode Code Mode isolates tools. There are no direct `batfish_*`
> tools; call them through the `execute` tool, for example:
>
> ```js
> await tools["batfish-mcp"].batfish_list_questions({});
> ```

### 4. Load a snapshot and ask

Package your configs as a snapshot zip (see [Providing a snapshot](#providing-a-snapshot)),
put it in your workspace, and tell the assistant the filename. The assistant
uploads it and runs the questions you ask.

> A fresh server process has no Batfish network set yet. Pass `network`
> (for example `"NETWORK"`) on the first call that needs snapshots; it then
> stays set for the life of the process. Calling `batfish_list_snapshots`
> without `network` fails with `Network must be set`.

### Running the server manually (optional)

To check that the server starts outside a client:

```bash
export BATFISH_SERVER=127.0.0.1
batfish-mcp
# or
python -m batfish_mcp.server
```

It speaks stdio JSON-RPC (FastMCP) and does not print logs to stdout.

## Providing a snapshot

* **Format:** one Batfish zip containing configs, plus optional `batfish/` and `hosts/` folders.
  For example, `abc_net.zip` contains `abc_net/configs/R1.cfg`, `R2.cfg`, `R3.cfg`, `FW.cfg` and `hosts/server_*.json`.
* **Where:** drop the zip in your workspace and tell the assistant the filename.
* **Upload:** the assistant base64-encodes the zip and calls `batfish_upload_snapshot(name, zip_base64, network?, overwrite?)`.
  * `name` is sanitized (`safe_snapshot_name`). Collisions are made unique (`name_1`, ...) unless `overwrite=true`.
  * `zip_base64` must be valid base64 with correct padding (`=` or `==`). Validation is enforced server-side.
* **Already on the Batfish server:** give the assistant the `snapshot` and `network` names; no upload is needed.
* **Shell access:** you can also upload directly with pybatfish:

  ```bash
  PYTHONPATH=src python3 - <<'PY'
  import os
  from pybatfish.client.session import Session
  bf = Session(host=os.getenv("BATFISH_SERVER", "127.0.0.1"))
  bf.set_network("NETWORK")
  bf.init_snapshot("abc_net.zip", name="abc_net", overwrite=True)
  print(bf.list_snapshots())
  PY
  ```

## Tools

The server provides 9 tools. `batfish_run_question` gives access to the full
60-question catalog.

| Tool | Description |
|---|---|
| `batfish_list_questions(category?)` | The 60-question curated catalog, with input types and `TYPE_HINTS` descriptions. No Batfish server needed. |
| `batfish_list_snapshots(network?)` | Snapshots of a network. |
| `batfish_upload_snapshot(name, zip_base64, network?, overwrite?)` | Upload a snapshot zip (base64). The name is sanitized (`safe_snapshot_name`); collisions are made unique (`name_1`, ...). |
| `batfish_run_question(fun, options?, snapshot?, reference_snapshot?, network?)` | Run any catalog question. Giving `reference_snapshot` and `snapshot` runs a differential question. Returns JSON-safe `{fun, snapshot, reference_snapshot, status, rows, removed_columns}`. |
| `batfish_list_assertions()` | Live pybatfish assertion names plus the 16-entry registry (`specs`) with parameter descriptions. |
| `batfish_run_assertion(name, params?, question?, snapshot?, network?)` | Validate and run one assertion (answer-based ones fetch the answer first). Returns `{type, status, message, snapshot, ran_at, runtime, fun?, answer_rows?}`. |
| `batfish_run_assertion_suite(suite_yaml, snapshot?, network?)` | Validate (all-or-nothing) and run a YAML suite; `examples/assertions.yaml` shows the schema. Returns `results[]`, `errors[]`, `summary`. |
| `batfish_fork_snapshot(base_name, name?, failed_nodes?, failed_interfaces?, overwrite?, network?)` | Fork a snapshot, optionally failing nodes or interfaces, then diff the fork against the base with a differential question. |
| `batfish_get_topology(kind, snapshot?, network?)` | `kind="layer3"` (from `layer3Edges`, with interface titles) or `"routing"` (from `bgpEdges`). Returns `{kind, snapshot, node_count, edge_count, nodes, edges}`. |

All tools share one Batfish session (`batfish_mcp.session`, using
`BATFISH_SERVER`, default `127.0.0.1`), serialized with a lock.

## Tool call examples

For reference, this is roughly what the assistant calls when you ask the
[example prompts](#example-prompts). Upload `abc_net.zip`, then:

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

In OpenCode, wrap these in `execute`, for example:

```js
await tools["batfish-mcp"].batfish_run_question({
  fun: "initIssues", snapshot: "abc_net", network: "NETWORK"
});
```

See [HOW-TO.md](HOW-TO.md) for a longer walkthrough.

## Troubleshooting

* **`Network must be set`**: pass `network="NETWORK"` on the first call.
* **`zip_base64 is not valid base64: Incorrect padding`**: re-encode with `base64 -w 0 abc_net.zip` (no newlines) and keep the trailing `=` or `==`. Do not trim by hand.
* **Snapshot already exists**: pass `overwrite: true`, or accept the `name_1` suffix.
* **No parse issues but wrong IPs?** Run `batfish_run_question(fun="initIssues")` and `fileParseStatus` first.
* **Reachability to `240.254.254.1` fails but `8.8.8.8` works**: `240.0.0.0/4` is reserved, and an ISP egress filter such as `Block outgoing traffic using reserved addresses` drops it. Use a public IP to test internet egress.

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
  hints.py            # TYPE_HINTS: tool-parameter descriptions
  topology.py         # L3 + routing graph construction from answers
tests/
  fakes.py            # FakeBatfish + assertion fake (no server needed)
  test_queries.py
  test_assertions.py
  test_server.py      # MCP tools + wire round-trip
examples/assertions.yaml
HOW-TO.md             # mini tutorial
```

## Tests

```bash
pip install -e ".[dev]"
pytest          # no Batfish server required (fakes + one in-memory MCP round-trip)
```

`tests/test_assertions.py` also checks that `assertion_specs.py` stays in sync
with the installed pybatfish (scripted-assertion signatures).

## Acknowledgements

batfish-mcp started as an offshoot of [Bat-Q](https://github.com/martimy/Bat-Q),
a Streamlit GUI over Batfish. The reusable headless core (question catalog,
assertion registry and runner, answer serialization, snapshot tooling) came
from there; the GUI layer was dropped and replaced with the MCP transport.
Both projects are Apache-2.0.

## License

Apache-2.0 (see `LICENSE`).
