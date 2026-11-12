# 02 — Architecture

## Layers

```
┌────────────────────────────────────────────────────────────────────┐
│ Transport: FastMCP over stdio (server.mcp, server.main)            │
├────────────────────────────────────────────────────────────────────┤
│ Tool layer: server.py                                              │
│   validation • locking • orchestration • JSON shaping              │
├────────────────────────────────────────────────────────────────────┤
│ Domain services                                                    │
│   queries.py      run questions / fork snapshots                   │
│   assertions.py   execute assertions, classify outcomes            │
│   topology.py     answers -> networkx graphs                       │
│   assertion_io.py parse/validate YAML suites                       │
│   assertion_specs.py  registry of supported assertions             │
├────────────────────────────────────────────────────────────────────┤
│ Pure helpers                                                       │
│   presenter.py    DataFrame cleanup + trace compaction             │
│   errors.py       exception/message -> stable error payloads       │
│   utils.py        name sanitizing, interface parsing               │
│   hints.py        TYPE_HINTS for complex parameter types           │
│   catalog.py      curated question structure                       │
│   question_metadata.json  generated per-question metadata          │
├────────────────────────────────────────────────────────────────────┤
│ Integration: session.py -> pybatfish.client.session.Session        │
└────────────────────────────────────────────────────────────────────┘
```

The tool layer is intentionally thin but not empty: it owns the *policy* decisions
that shape the LLM experience (row caps, trace detail, early snapshot checks,
structured errors).

## Module dependency graph

```
server.py
 ├── assertion_io.py ──► assertion_specs.py
 ├── assertion_specs.py
 ├── assertions.py ─────► assertion_specs.py, session.py
 ├── catalog.py
 ├── errors.py
 ├── hints.py
 ├── presenter.py
 ├── queries.py ─────────► session.py, utils.py
 ├── session.py ─────────► pybatfish
 ├── topology.py ────────► networkx
 └── utils.py

catalog.py            (no internal deps; parses an embedded JSON string)
question_metadata.json (data file loaded by server.py)
```

Notable properties:

- `session.py` is the only module that constructs a real pybatfish `Session`.
- `queries.py` and `assertions.py` are the only modules that call Batfish
  questions/assertions, and both go through `session.get_session()`.
- `presenter.py`, `errors.py`, `hints.py`, `topology.py` and `utils.py` are pure
  or near-pure and easy to unit test.
- There are no circular imports.

## Runtime flow by tool category

### Discovery tools (no Batfish)

`batfish_list_questions`, `batfish_find_questions`, `batfish_get_question` read
`catalog.read_questions()` and `server._question_metadata()` (an `lru_cache` over
`question_metadata.json`). They do **not** acquire the tool lock and never touch
the session. This is why they work without a server and are fast.

### Snapshot tools

`batfish_list_snapshots`, `batfish_delete_snapshot`, `batfish_upload_snapshot`,
`batfish_fork_snapshot`:

1. acquire `server._lock`,
2. `get_session()`,
3. optionally `set_network(network)`,
4. validate/verify the snapshot (`_ensure_snapshots` for delete/fork),
5. call the pybatfish session,
6. return a small dict (or a list for `list_snapshots`).

Name collisions are handled by a simple suffix loop (`name`, `name_1`, ...) unless
`overwrite=True`. Upload decodes base64 with `validate=True` and stages bytes in a
temporary `.zip` file because `init_snapshot` takes a path.

### Question execution

`batfish_run_question` is the most complex tool. The order matters:

```
lock
 └─ session / network
     └─ reference_snapshot requires snapshot          (ValueError)
         └─ _ensure_snapshots(snapshot, reference)    (ValueError)
             └─ trace_detail / max_rows validation    (error payload)
                 └─ _validate_question_options        (error payload)
                     └─ queries.run_query             (exception -> error payload)
                         └─ _serialize_answer
```

Validation before execution is deliberate: these checks turn opaque Java
work-item failures into short, stable errors the LLM can act on.

### Assertions

`batfish_run_assertion` and `batfish_run_assertion_suite`:

```
lock
 └─ session / network / _ensure_snapshots
     └─ assertion_io.validate_assertion or load_assertions
         ├─ problems -> {status:"error", code:"VALIDATION_ERROR", errors:[...]}
         └─ assertions.run_assertion_item (per item)
             └─ server._result_to_dict -> short message + rows/details
```

Suite loading is **all-or-nothing**: if any item is invalid, `load_assertions`
returns `([], errors)` and nothing runs.

### Topology

`batfish_get_topology` runs one of two questions and converts the answer frame:

| `kind` | Question | Builder | Edge attributes |
|---|---|---|---|
| `layer3` | `layer3Edges` | `topology.get_topology` | `title` = `"r1 (Gi0/0) <---> r2 (Gi0/0)"` |
| `routing` | `bgpEdges` | `topology.get_routing_topology` | none |

`server._graph_to_dict` stringifies nodes and edge attributes so the result is
JSON-safe.

## State and concurrency

### The shared session

`session.py` holds a module-level singleton:

```python
_session = None

def get_session() -> Session:
    if _session is None:
        with _lock:
            if _session is None:
                host = os.getenv("BATFISH_SERVER", "127.0.0.1")
                _session = Session(host=host)
    return _session
```

- Creation is double-checked under `session._lock`.
- The session stores the **current network**; `set_network` mutates it.
- Tests inject a fake with `session.set_session(fake)` and reset with
  `clear_session()`.

### The tool lock

`server.py` defines `_lock = threading.RLock()`. Every tool that talks to Batfish
wraps its body in `with _lock:`. Consequences:

- Tool calls are serialized process-wide. There is no intra-process concurrency
  against Batfish, which matches the mutable session.
- `RLock` (not `Lock`) is used so a helper can re-enter if needed without
  deadlocking.
- Discovery tools do not take the lock, so they remain responsive while a long
  question runs.
- The lock does **not** coordinate with other processes. Two server processes
  pointed at the same Batfish network can still interfere through shared network
  state in Batfish.

### State ownership

| State | Owner | Lifetime |
|---|---|---|
| Current network | pybatfish `Session` (`session._session`) | Process |
| Snapshot contents | Batfish coordinator | Until deleted in Batfish |
| Loaded catalog/metadata | `lru_cache` in `server.py` | Process (first use) |
| Upload temp files | `server.batfish_upload_snapshot` | Deleted in `finally` |
| Assertion results | Not persisted | Per call |

## Serialization pipeline

`server._serialize_answer` turns a pybatfish answer into the public JSON shape.

```
Answer
  │ answer["status"]                 (string, e.g. "SUCCESS")
  │ answer.frame()                   (pandas DataFrame)
  ▼
presenter.format_result_lite(frame)
  │ drop columns whose every cell is "empty"
  │ empty = None/NaN, blank/whitespace string, or empty list/dict/set
  ▼
presenter.compact_traces(frame, mode)
  │ columns: Traces, Snapshot_Traces, Reference_Traces
  │ mode "summary": {disposition, path, hop_count}
  │ mode "none":    drop the columns (counts remain)
  │ mode "full":    leave untouched
  ▼
DataFrame.to_json(orient="records", date_format="iso")
  │ fallback: to_dict + json.dumps(default=str) for nested cells
  ▼
apply max_rows (0 = unlimited)
  ▼
{fun, snapshot, reference_snapshot, status, rows, removed_columns,
 total_rows, returned_rows, truncated}
```

Special case: for `differentialReachability` with a reference snapshot,
`_serialize_answer` adds `trace_columns`:

```json
{"Snapshot_Traces": "<snapshot>", "Reference_Traces": "<reference_snapshot>"}
```

This prevents the two sides being read backwards.

### Trace compaction details

`presenter.summarize_trace` handles both pybatfish `Trace` objects and their dict
form, so it works on real answers and on test fakes. It keeps:

- `disposition` (e.g. `ACCEPTED`, `DENIED_IN`, `NO_ROUTE`),
- `path` (hop node names, in order),
- `hop_count` (number of hops).

Everything else (per-hop steps, routes, filter details) is dropped. Use
`trace_detail="full"` when that detail is needed.

## Error model

Two kinds of failures are deliberately distinguished:

### 1. Structured tool results (`status: "error"`)

Batfish failures and option validation return a normal tool result instead of
raising. This keeps the conversation going and gives the LLM a stable `code`.

| Code | Produced by | Meaning |
|---|---|---|
| `SNAPSHOT_INCOMPATIBLE` | `errors.classify_message` | Snapshot built by a different Batfish version; re-upload. |
| `BIDIRECTIONAL_REQUIRES_DST` | option validation or classifier | `bidirectionalReachability` needs `headers.dstIps`. |
| `INVALID_PORT_SPEC` | option validation or classifier | `srcPorts`/`dstPorts` must be strings. |
| `INVALID_ARGUMENT` | option validation or classifier | Bad option types/values. |
| `BATFISH_UNAVAILABLE` | classifier | Cannot reach the coordinator. |
| `BATFISH_ERROR` | classifier fallback | Unclassified Batfish failure. |
| `VALIDATION_ERROR` | assertion tools | The assertion/suite did not validate; nothing ran. |

`debug=True` attaches the raw exception type/log under `debug` for question and
assertion failures. Without it, the raw Java log is never returned.

Classification is **first-match-wins** over ordered `_ERROR_PATTERNS` in
`errors.py`. Add new patterns above broader ones, and add a test.

### 2. Raised exceptions

These propagate to FastMCP and appear as tool errors:

- `ValueError` for unknown question names, missing snapshots, bad topology kind,
  missing `snapshot` when `reference_snapshot` is given, invalid interface specs,
  invalid base64.
- Programming errors (bugs) should not be caught and hidden.

Rule of thumb: if the LLM can fix it by changing arguments, prefer a structured
error payload; if it is a caller/contract violation (unknown name, bad kind),
raise a `ValueError` with the valid options listed.

## Configuration

| Setting | Where | Default |
|---|---|---|
| `BATFISH_SERVER` | environment, read in `session.get_session` | `127.0.0.1` |
| Batfish ports | pybatfish/Batfish | `9996` (coordinator), `9997` (client) |
| `max_rows` | per `batfish_run_question` call | `50` (`0` = unlimited) |
| `trace_detail` | per `batfish_run_question` call | `"summary"` |
| metadata path | `server._METADATA_PATH` | next to `server.py` |

There is no config file, no database, and no persistent server-side state.

## Extension points

| Goal | Touch |
|---|---|
| New MCP tool | `server.py` (`@mcp.tool()`), tests, tool docs |
| New question category/entry | `catalog.py`; then regenerate metadata |
| Better question descriptions/types | `scripts/generate_question_metadata.py` + regenerate |
| New assertion type | `assertion_specs.py`, maybe `assertions.py`, tests |
| New error code | `errors.py` + tests |
| New response field | `server.py` serializer + tool docs + tests |
| Different transport/auth | `server.main` / FastMCP configuration |

See [08-development.md](08-development.md) for step-by-step recipes.
