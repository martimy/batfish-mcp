# 03 — Module reference

This is a map of the code, not a replacement for reading it. Each section states
the module's job, its important functions, and the traps.

## `src/batfish_mcp/server.py` (largest file)

**Job:** define the FastMCP instance, all 12 tools, the LLM instructions, question
discovery/merge logic, and answer/assertion serialization.

### Groups inside the file

| Group | Names |
|---|---|
| MCP setup | `_INSTRUCTIONS`, `mcp`, `_lock`, `main` |
| Answer shaping | `_serialize_answer`, `_ensure_snapshots` |
| Catalog/metadata merge | `_question_metadata`, `_catalog_index`, `_TEMPLATE_TYPE_ALIASES`, `_template_type`, `_required`, `_input_type`, `_detail_input`, `_summary_input` |
| Search | `_SEARCH_FIELDS`, `_STOPWORDS`, `_search_text`, `_query_terms`, `_question_search_index`, `_field_hit` |
| Discovery tools | `batfish_find_questions`, `batfish_list_questions`, `batfish_get_question` |
| Snapshot tools | `batfish_list_snapshots`, `batfish_delete_snapshot`, `batfish_upload_snapshot`, `batfish_fork_snapshot` |
| Question execution | `_PORT_FIELDS`, `_walk_options`, `_validate_question_options`, `batfish_run_question` |
| Topology | `_graph_to_dict`, `batfish_get_topology` |
| Assertion tools | `_known_funs`, `_enriched_params`, `_answer_rows`, `_parse_record_details`, `_truncate_details`, `_result_to_dict`, `batfish_list_assertions`, `batfish_run_assertion`, `batfish_run_assertion_suite` |

### Caches

- `_question_metadata()` — `lru_cache(maxsize=1)` over
  `question_metadata.json["questions"]`. Missing file => `{}`.
- `_catalog_index()` — `lru_cache(maxsize=1)` mapping `fun -> (category, question)`.
- `_question_search_index()` — `lru_cache(maxsize=1)` pre-tokenized search text for
  every question.
- `_known_funs()` — `lru_cache(maxsize=1)` frozenset of catalog `fun` names.

Caches live for the process. Tests that mutate files would need
`cache_clear()`; the suite does not do that.

### Traps

- `supports_differential` is hardcoded `True` in `_question_search_index`,
  `batfish_list_questions`, and `batfish_get_question`. This is a known open item
  (see [08-development.md](08-development.md)).
- `_walk_options` recursively visits every dict/list in `options`. Port validation
  applies to **any** key named `srcPorts`/`dstPorts` at any depth, and
  `maxTraces` must be an int anywhere it appears.
- `_ensure_snapshots` calls `bf.list_snapshots()`, so it requires the session's
  current network to be set. This is why `network` must be supplied early.
- `_serialize_answer` swallows failures from `answer.frame()` and falls back to
  `answer.rows`; that fallback does not include `removed_columns`.
- `_result_to_dict` uses `ast.literal_eval` on `details`. Values with object
  reprs (full traces) are intentionally left as text and capped.

## `src/batfish_mcp/session.py`

**Job:** lazy process-wide pybatfish session singleton.

- `get_session()` — creates `Session(host=os.getenv("BATFISH_SERVER", "127.0.0.1"))`
  on first use (double-checked locking).
- `set_session(session)` — inject a fake or replacement.
- `clear_session()` — forget the session; next `get_session()` recreates it.

Only `session._lock` protects creation, not use. Use of the session is serialized
by `server._lock`.

## `src/batfish_mcp/queries.py`

**Job:** the only place that runs questions and forks snapshots.

- `run_query(question, snapshots=None, snapshot=None)` — `question` is
  `{"fun": ..., "options": {...}}`.
  - `snapshots=(reference, snapshot)` for differential:
    `fun(**opts).answer(snapshot=snapshot, reference_snapshot=reference)`.
  - `snapshot=...` for a single snapshot.
  - neither: `fun(**opts).answer()`.
  - Raises on failure; the server converts to a structured error.
- `fork_snapshot(active_snapshot, failed_nodes, failed_interfaces, name,
  overwrite=True)` — defaults name to `<base>_Fail`, normalizes interfaces via
  `utils.parse_interfaces`, calls `bf.fork_snapshot`.
- `get_node_properties`, `get_interface_properties`, `set_snapshot` — small
  helpers kept from Bat-Q; only `set_snapshot` is used (by tests).

**Trap:** argument order is reversed relative to pybatfish: the tuple is
`(reference, snapshot)` but `answer()` receives `snapshot=snapshots[1]`,
`reference_snapshot=snapshots[0]`.

## `src/batfish_mcp/presenter.py`

**Job:** pure DataFrame/trace shaping.

- `TRACE_COLUMNS = {"Traces", "Snapshot_Traces", "Reference_Traces"}`
- `TRACE_DETAIL_MODES = {"summary", "none", "full"}`
- `summarize_trace(trace)` -> `{disposition, path, hop_count}`; works with objects
  or dicts.
- `summarize_trace_value(value)` -> list of summaries, or the value unchanged.
- `compact_traces(df, mode)` -> `(df, removed_columns)`.
- `format_result_lite(df)` -> `(df, removed_columns)`; drops columns that are
  entirely empty (blank strings and empty containers count as empty).
- Legacy, currently unused: `format_result`, `dict_to_str`, `json_to_dataframe`,
  `flatten_trace_data`. They came from the Streamlit app; safe to remove after
  confirming nothing external imports them.

## `src/batfish_mcp/errors.py`

**Job:** map Batfish exception text to `(code, actionable_message)`.

- `classify_message(text)` — ordered substring match over `_ERROR_PATTERNS`;
  fallback `BATFISH_ERROR` includes the first non-empty line.
- `classify_exception(exc)` — convenience wrapper.
- `error_payload(...)` — `batfish_run_question`-shaped error dict.
- `exception_payload(exc, ..., debug=False)` — adds `debug: {exception, log}` only
  when asked.

**Trap:** first matching pattern wins. Put specific patterns before generic ones.
Needles are matched case-insensitively.

## `src/batfish_mcp/utils.py`

**Job:** small shared helpers.

- `safe_snapshot_name(file_name)` — drop extension, strip accents (NFKD/ASCII),
  replace runs of invalid characters with `_`, trim, cap at 100 chars, fall back
  to `"snapshot"`, and rename reserved `"settings"` to `"settings_1"`.
- `parse_interfaces(items)` — normalize `"node[iface]"` strings or
  `{"hostname", "interface"}` mappings to pybatfish `Interface` objects. Rejects
  bare node names with a `ValueError` that points at `failed_nodes`.
- Legacy numbering helpers: `get_same_prefix`, `get_highest_number`,
  `get_next_item` (used only by each other / `__main__`), and
  `sort_qlist_by_schema`, `convert_template` (from Bat-Q). Currently unused by the
  server; keep unless you are cleaning up.

## `src/batfish_mcp/topology.py`

**Job:** build `networkx.Graph` objects from answer frames.

- `get_topology(edges)` — `layer3Edges`; reads `Interface` and `Remote_Interface`
  (dicts or objects) and adds an edge with a `title`.
- `get_routing_topology(edges)` — `bgpEdges`; reads `Node` and `Remote_Node`.
- Commented-out Streamlit plotting code remains at the bottom; ignore it.

## `src/batfish_mcp/catalog.py`

**Job:** the curated question structure. Contains a large embedded JSON string and
`read_questions()`.

Shape:

```json
{"Batfish": [
  {"category": "...", "description": "...", "questions": [
    {"fun": "...", "name": "...", "input": [{"name": "...", "type": "bool"}],
     "type": "differential"}
  ]}
]}
```

- `input[].optional` defaults to `true`; `required` is derived.
- `type: "differential"` marks the two differential-only questions
  (`compareFilters`, `differentialReachability`).
- Categories and order are the display order used by the tools.

**Trap:** this is hand-maintained JSON-in-a-string. Edit carefully and run the
metadata coverage tests.

## `src/batfish_mcp/hints.py`

**Job:** `TYPE_HINTS`, keyed by public type name (`HeaderConstraints`,
`PathConstraints`, `NodeSpec`, `IpSpaceSpec`, `InterfaceSpec`, `IpSpec`,
`FilterSpec`, `RoutingPolicySpec`, `LocationSpec`, `BgpRoute`,
`BgpSessionProperties`, `RouteMatch`, `int`, `str`, `bool`).

Each entry may contain `hint`, `placeholder`, `example`, `fields`, `field_types`.
`server._detail_input` / `_summary_input` merge these into input descriptions so
the LLM can build complex dicts.

**Trap:** `TYPE_HINTS` is keyed by the **public** type after
`_TEMPLATE_TYPE_ALIASES` mapping. A new Java template type must be added there or
it degrades to `str`.

## `src/batfish_mcp/assertion_specs.py`

**Job:** the registry that drives assertion validation, listing, and execution.

Spec fields: `label`, `description`, `group`, `phase`, `source`
(`pybatfish`/`builtin`), `call` (`session`/`answer`), `answer_from`
(`question`/`routes`), `answer_param`, `params` (`{name, type, optional}`).

- `SESSION`, `ANSWER`, `FROM_QUESTION`, `FROM_ROUTES`
- `SCOPES = ("active", "alternate", "both", "failed")`, `DEFAULT_SCOPE="active"`
- `HIDDEN_PARAMS = ("soft", "snapshot", "session", "df_format")`
- `get_spec(name)`, `needs_question(spec)`, `specs_by_group(max_phase=None)`

16 entries: 6 snapshot checks, 3 filter checks, 2 reachability checks, 3 question
result checks, 2 routing checks.

**Trap:** tests compare every `source == "pybatfish"` spec against the installed
pybatfish signature, including required/optional. Upgrading pybatfish can fail the
suite; that failure is a signal to update the registry.

## `src/batfish_mcp/assertion_io.py`

**Job:** parse, validate, and dump assertion YAML.

- `SCHEMA_VERSION = 1`
- `_ALLOWED_KEYS = {"type", "name", "description", "scope", "params", "question"}`
- `_TYPE_CHECKS` — Python types accepted per registry type.
- `_header_fields()` — allowed `HeaderConstraints` fields from installed pybatfish.
- `new_assertion_id(alist)` — `a1`, `a2`, ...
- `validate_assertion(item, known_funs=None)` — returns a list of problems.
- `load_assertions(text, known_funs=None)` — returns `(alist, errors)`,
  all-or-nothing.
- `dump_assertions(alist)` — YAML text; ids are not emitted.

Validation covers unknown keys, unknown type, scope, `name`/`description` types,
unknown/missing params, param types (with a bool-vs-int guard), unknown header
fields, empty `RouteMatch`, and question rules (required/allowed/`options` type).

## `src/batfish_mcp/assertions.py`

**Job:** execute assertions and classify outcomes.

- `PASS`, `FAIL`, `ERROR` constants.
- `ANSWER_BASED` — `assert_zero_results`, `assert_num_results`,
  `assert_has_route`, `assert_has_no_route` (module-level pybatfish functions, not
  on `Session.asserts`).
- `assert_min_results` + `CUSTOM` — local implementation because pybatfish does
  not provide it.
- `AssertionResult` dataclass: `status`, `message`, `runtime`, `snapshot`,
  `ran_at`, `fun`, `answer`, `summary`, `details`.
- `list_assertions()` — live pybatfish names grouped `snapshot`/`answer`.
- `_prepare_params(params)` — converts a `headers` dict into
  `HeaderConstraints`, which also validates field names.
- `run_assertion(name, snapshot=None, **params)` — the outcome rules:
  - always `soft=False`,
  - session-bound assertions get `df_format="records"` by default,
  - `BatfishAssertWarning` => ERROR ("Not evaluated"),
  - `BatfishAssertException` => FAIL,
  - other exceptions => ERROR.
- `_fetch_answer(spec, item, params, snapshot)` — runs the chosen question, or the
  `routes` question limited to the node/VRF.
- `run_assertion_item(item, snapshot)` — spec lookup, optional answer fetch,
  execution, metadata (`snapshot`, `ran_at`, `fun`, `answer`).
- `drop_snapshot_results`, `rename_snapshot_results`, `result_key` — legacy
  helpers for a results registry; unused by the MCP server.

**Trap:** `assert_has_no_route` can "pass" when the node/VRF does not exist;
pybatfish emits a warning, and the runner correctly turns that into ERROR.

## `tests/fakes.py`

**Job:** test doubles; no Batfish needed.

- `FakeQuestion`, `FakeRoutes`, `FakeAnswer`, `trace`
- `fake_session(log, calls)` — a `SimpleNamespace` session used by older
  assertion tests.
- `FakeBatfish` — a fuller fake with `networks`, `q`, `asserts` not included
  (assertion tests use `fake_session`), `calls`, `answers`,
  `missing_questions`, `reject_uploads`, `fail_delete`.
- `page_path` refers to a `views/` directory that no longer exists (legacy from
  Bat-Q); unused.

See [07-testing.md](07-testing.md) for usage.
