# 08 — Development guide

## Setup

The managed environment lives in `.mcp/` (already present in this checkout):

```bash
.mcp/bin/python3 -m pytest -q
```

To create your own environment instead:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Dependencies: `pybatfish`, `fastmcp>=4,<5`, `pandas`, `pyyaml`, `networkx`.
Tests additionally need `pytest>=8`.

## Running the server

```bash
# from the repository root, without installing
PYTHONPATH=src .mcp/bin/python3 -m batfish_mcp.server

# or, after pip install -e .
batfish-q-mcp
```

It speaks stdio JSON-RPC and prints nothing to stdout. A working server waits
silently for input. Batfish must be reachable for Batfish-backed tools:

```bash
docker run --rm -p 9996:9996 -p 9997:9997 batfish/allinone
export BATFISH_SERVER=127.0.0.1
```

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `BATFISH_SERVER` | `127.0.0.1` | Coordinator host passed to `pybatfish.Session` |

Everything else is per-call (`network`, `snapshot`, `max_rows`, `trace_detail`,
`debug`).

## Common recipes

### Add an MCP tool

1. Add a `@mcp.tool()` function in `server.py`. Keep the signature typed and give
   it a docstring; FastMCP uses both for the schema/description.
2. If it touches Batfish:
   - wrap the body in `with _lock:`,
   - call `get_session()`,
   - `set_network(network)` when the caller passed one,
   - call `_ensure_snapshots(bf, ...)` before queuing work.
3. Return JSON-safe data (dict/list/str/int/bool/None). Reuse
   `_serialize_answer`, `_result_to_dict`, or `_graph_to_dict` where appropriate.
4. Convert expected failures into `errors.error_payload(...)`; raise `ValueError`
   for contract violations (unknown name, bad enum).
5. Add the tool name to the expected set in
   `tests/test_server.py::test_tools_round_trip_over_the_mcp_wire`, plus behavior
   tests.
6. Document it in `README.md` (Tools table) and
   [04-tools.md](04-tools.md).

### Change a response shape

Response shapes are contracts consumed by LLMs and tests. Treat changes as
breaking:

1. Change the producer (`_serialize_answer`, `_result_to_dict`, etc.).
2. Update the tool docstring in `server.py` and [04-tools.md](04-tools.md).
3. Update `README.md` / `HOW-TO.md` if user-visible.
4. Update tests that assert exact keys/values.
5. Prefer adding a field over renaming/removing one; if you must break, note it in
   the release notes.

### Add an error code

1. Add a `(code, needles, message)` tuple to `_ERROR_PATTERNS` in `errors.py`.
   Order matters: **first match wins**, so put specific patterns before generic
   ones.
2. Use lowercase needles; matching is case-insensitive.
3. If the error is better prevented, add a validation in
   `_validate_question_options` and return `errors.error_payload` directly.
4. Add a test asserting the code, and that the raw log appears only with
   `debug=True`.
5. Document the code in [04-tools.md](04-tools.md#error-shape) and `README.md` if
   user-facing.

### Add a question

See [05-question-catalog.md](05-question-catalog.md#how-to-add-or-change-a-question).
Short version:

1. Add it to `catalog.py` (category, `fun`, name, inputs).
2. Regenerate `question_metadata.json` against a Batfish that knows it.
3. Add type aliases/hints if needed.
4. Run the metadata coverage tests and the full suite.

### Add an assertion

See [06-assertions.md](06-assertions.md#how-to-add-an-assertion). Short version:

1. Add a spec to `assertion_specs.py`.
2. If `source: builtin`, implement it in `assertions.CUSTOM`.
3. The signature-sync test validates pybatfish-backed specs automatically.
4. Add runner tests for special behavior.

### Change how traces are shown

1. `presenter.compact_traces` / `summarize_trace` define the behavior.
2. `presenter.TRACE_DETAIL_MODES` is the validation source used by
   `batfish_run_question`.
3. Update `server._INSTRUCTIONS`, the tool docstring, `README.md`, `HOW-TO.md`,
   and the trace tests.

### Regenerate question metadata

```bash
.mcp/bin/python3 scripts/extract_output_columns.py
.mcp/bin/python3 scripts/generate_question_metadata.py --host 127.0.0.1
.mcp/bin/python3 -m pytest -q
```

Requires network access for the docs notebook download and a live Batfish with all
catalog questions. Review the `coverage` block in the generated file for gaps.

## Debugging

### A tool returns an unexpected error

1. Re-run the same call with `debug=true` to get the raw exception/log.
2. Check the `code` against `_ERROR_PATTERNS` in `errors.py`.
3. If it is a validation code (`INVALID_ARGUMENT`, `INVALID_PORT_SPEC`,
   `BIDIRECTIONAL_REQUIRES_DST`), inspect `_validate_question_options`.
4. Reproduce with a fake in a test. Most tool bugs can be reproduced with
   `FakeBatfish` + `FakeAnswer` without a server.

### The server does not respond to a client

- Run it by hand (`PYTHONPATH=src .mcp/bin/python3 -m batfish_mcp.server`). A
  working process waits silently.
- Check that nothing is printed to stdout (it would corrupt JSON-RPC).
- Check the client's MCP log; for Claude Desktop on Windows that is
  `%APPDATA%\Claude\logs\mcp-server-batfish-q-mcp.log`.

### `Network must be set`

A fresh process has no network. Pass `network` on the first Batfish-backed call
(`list_snapshots`, `run_question`, assertions, topology, upload, fork).

### A snapshot is not found

`_ensure_snapshots` lists the network's snapshots and raises a `ValueError` with
the available names. Check the sanitized name (`safe_snapshot_name` drops the
extension and replaces invalid characters).

### A question fails only on real Batfish

- Compare with `debug=true` output.
- Check the question inputs with `batfish_get_question(fun)`.
- Remember Batfish-specific rules: string ports, integer `maxTraces`, ICMP default,
  `dstIps` required for bidirectional reachability, representative flows for broad
  headers.

### Performance

- Discovery is local and cached.
- All Batfish-backed tools serialize on one lock. A long question blocks other
  Batfish-backed calls, not discovery.
- `max_rows` caps response size, but Batfish still computes the full answer.
- `trace_detail="summary"` is the main lever for large reachability answers.

## Known issues and deliberate limitations

### `supports_differential` is hardcoded

`server.py` sets `supports_differential: True` in three places:

1. `_question_search_index`
2. `batfish_list_questions`
3. `batfish_get_question`

The catalog has no verified per-question exclusion list, and the generated
metadata contains a `differential` field that is currently **not** used. Do not
change these literals unless you also:

- establish a verified exclusion list,
- make all three tools agree,
- add tests that assert the agreement.

Until then, the docs and instructions say "most questions can run differentially;
two are differential-only".

### `scope` is not enforced

`assertion_io` validates and preserves `scope`, but the runner ignores it. All
assertions run against the supplied snapshot.

### One shared session, one network at a time

`session.get_session()` returns a process-wide pybatfish `Session`, and tool
bodies are serialized by `server._lock`. Two server processes can still interfere
through Batfish's own network state if they target the same network.

### Snapshot upload is base64 through the LLM

Practical only for small lab snapshots. For large snapshots, load them into
Batfish directly and refer to them by `snapshot` + `network`.

### No authentication/encryption

The stdio transport inherits the client's security model. Do not expose it as a
network service without adding authentication.

### Dead / legacy code

Retained from Bat-Q but unused by the MCP server:

- `presenter.py`: `format_result`, `dict_to_str`, `json_to_dataframe`,
  `flatten_trace_data`
- `utils.py`: `get_same_prefix`, `get_highest_number`, `get_next_item`,
  `sort_qlist_by_schema`, `convert_template`
- `queries.py`: `get_node_properties`, `get_interface_properties` (tests use
  `set_snapshot`; the server does not)
- `assertions.py`: `drop_snapshot_results`, `rename_snapshot_results`,
  `result_key`
- `topology.py`: commented-out Streamlit plotting
- `tests/fakes.py`: `page_path` (refers to the removed `views/` directory)

Safe to remove if you verify nothing external imports them. Do not delete
`assertions.py` helpers that tests still use without updating the tests.

## Build and release

- Version lives in `pyproject.toml` (`[project].version`).
- Console script: `batfish-q-mcp = "batfish_mcp.server:main"`.
- Package data includes `question_metadata.json`; if you add another data file,
  add it to `[tool.setuptools.package-data]`.
- Build artifacts in `dist/` are examples of a previous build; regenerate rather
  than editing them.
- `server.json` and the `.mcpregistry_*` files support MCP registry publishing;
  they are not needed for local development.

Typical release checks:

```bash
.mcp/bin/python3 -m pytest -q
PYTHONPATH=src .mcp/bin/python3 -c "import batfish_mcp.server as s; print(len(s._known_funs()))"
```

The second command should print `60`.

## Troubleshooting matrix

| Symptom | Likely cause | Fix |
|---|---|---|
| `Network must be set` | No network in the session | Pass `network` on the first call |
| `snapshot 'x' not found ... available: []` | Wrong network, unsanitized name, or not uploaded | Check `batfish_list_snapshots`; sanitize name |
| `zip_base64 is not valid base64: Incorrect padding` | Truncated paste | Re-encode with `base64 -w 0`, keep `=` padding |
| `BATFISH_UNAVAILABLE` | Coordinator down/wrong host | Start Batfish; check `BATFISH_SERVER` |
| `SNAPSHOT_INCOMPATIBLE` | Snapshot built by older Batfish | Re-upload with the current server |
| `INVALID_PORT_SPEC` | Numeric port | Use `"443"` / `"80,443"` |
| `BIDIRECTIONAL_REQUIRES_DST` | Missing `headers.dstIps` | Add a destination |
| Assertion `error` "Not evaluated" | pybatfish warning (missing node/VRF) | Fix the target; do not treat as pass |
| `VALIDATION_ERROR` | Bad assertion/suite | Read `errors[]` |
| Empty `rows` but `status: SUCCESS` | The question legitimately returned nothing | Check `initIssues`/`fileParseStatus` first |
| Huge answers | No row cap | Use `max_rows` and `trace_detail="summary"` |
