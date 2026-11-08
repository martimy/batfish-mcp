# batfish-mcp

An MCP (Model Context Protocol) server exposing [Batfish](https://batfish.org)
as tools for AI assistants. Adapted from [Bat-Q](https://github.com/)'s headless
core (`common/`), with the Streamlit GUI replaced by an MCP transport.

## Quickstart

Needs a running Batfish server (e.g. `batfish/allinone` on ports 9996/9997).

```bash
pip install -e .             # or: pip install -r requirements.txt
export BATFISH_SERVER=127.0.0.1
batfish-mcp                  # stdio MCP server (or: python -m batfish_mcp.server)
```

Configure your MCP client (stdio example):

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

> A fresh server process has no Batfish network set yet. Pass `network`
> (e.g. `"NETWORK"`) on the first call that needs snapshots; it then stays
> set on the shared session for the life of the process.

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
