# 04 — MCP tool surface

All tools are defined in `src/batfish_mcp/server.py` with `@mcp.tool()`. FastMCP
derives the JSON schema from the signature and docstring, then serializes the
returned Python dict/list as JSON.

There are **12 tools** in three groups:

| Group | Tools | Needs Batfish? |
|---|---|---|
| Discovery | `batfish_list_questions`, `batfish_find_questions`, `batfish_get_question` | No |
| Snapshots / questions | `batfish_list_snapshots`, `batfish_delete_snapshot`, `batfish_upload_snapshot`, `batfish_run_question`, `batfish_fork_snapshot`, `batfish_get_topology` | Yes |
| Assertions | `batfish_list_assertions`, `batfish_run_assertion`, `batfish_run_assertion_suite` | Yes (listing also reads installed pybatfish) |

## Discovery workflow

The server's `instructions` (and the README) tell the LLM to use this order:

```
batfish_list_snapshots          # confirm network/snapshot
        │
        ▼
batfish_list_questions          # browse the 60-question catalog
   or batfish_find_questions    # plain-language search when the name is unknown
        │
        ▼
batfish_get_question(fun)       # full inputs/outputs/defaults/allowed values
        │
        ▼
batfish_run_question(fun, options, snapshot, ...)
```

`batfish_find_questions` is designed for user wording such as "standby gateway",
"traffic going in circles", or "which firewall rule is blocking". It returns
ranked candidates with `score` and `matched_on` so the LLM can explain the choice.

---

## `batfish_list_questions`

```python
batfish_list_questions(category: Optional[str] = None) -> list[dict]
```

Returns one entry per question (optionally filtered by exact category name):

```json
{
  "category": "Packet Forwarding",
  "category_description": "...",
  "fun": "traceroute",
  "name": "Traceroute",
  "description": "Traces the path(s) for the specified flow.",
  "tags": ["dataplane", "traceroute"],
  "synonyms": ["trace path", "hop-by-hop path"],
  "supports_differential": true,
  "differential_only": false,
  "output_columns": ["Flow", "Traces", "TraceCount"],
  "inputs": [
    {"name": "startLocation", "type": "LocationSpec", "required": true,
     "hint": "...", "placeholder": "...", "example": "..."}
  ]
}
```

- `output_columns` is names only; use `batfish_get_question` for types and
  descriptions.
- Each input includes `TYPE_HINTS` fields (`hint`, `fields`, `placeholder`,
  `example`) when the type has them.
- `type` is present only on the two differential-only questions.
- `category` matching is exact and case-sensitive; unknown categories return `[]`.
- Offline: reads catalog + checked-in metadata.

## `batfish_find_questions`

```python
batfish_find_questions(query: str, limit: int = 5) -> dict
```

```json
{
  "query": "standby gateway",
  "count": 2,
  "total_matches": 2,
  "results": [
    {
      "fun": "hsrpProperties", "name": "HSRP Properties",
      "category": "Configuration Properties",
      "description": "...", "tags": ["..."], "synonyms": ["..."],
      "supports_differential": true, "differential_only": false,
      "score": 31.0, "matched_on": ["fun", "synonyms", "tags"]
    }
  ]
}
```

Scoring (`server._question_search_index` / `_SEARCH_FIELDS`):

| Field | Weight |
|---|---|
| `fun` | 6.0 |
| `name` | 5.0 |
| `synonyms` | 5.0 |
| `tags` | 4.0 |
| `description` | 4.0 |
| `category` | 2.0 |
| `long_description` | 2.0 |
| `inputs` | 2.0 |
| `outputs` | 1.0 |

- Whole-word hits score 1.0, substring hits 0.5.
- A multi-term query gets a +8 phrase bonus when the exact phrase appears.
- The total is multiplied by `0.5 + coverage` (fraction of query terms matched).
- Ties break by more matched terms, then by `fun` alphabetically.
- Stopwords (`_STOPWORDS`) are removed; if nothing remains, the raw tokens are
  used.
- Empty query or `limit < 1` raises `ValueError`.
- Offline.

## `batfish_get_question`

```python
batfish_get_question(fun: str) -> dict
```

Same fields as the list entry plus:

- `long_description`
- `output_columns`: list of `{name, type, description}`
- input details: `display_name`, `description`, `default`, `allowed_values`
  (strings or `{value, description}`), `field_specs`, `min_length`
- `metadata_complete`: `true` when the question exists in the generated metadata
- `type` for differential-only questions

If the catalog is missing an input that the generated metadata has, the tool
surfaces the template-only input (drift guard). Unknown `fun` raises `ValueError`
listing all known names.

## `batfish_list_snapshots`

```python
batfish_list_snapshots(network: Optional[str] = None) -> list[str]
```

Points the session at `network` first if given, then returns
`bf.list_snapshots()`.

**Gotcha:** with no `network` and a fresh session, Batfish fails with
`Network must be set`. Pass `network` on the first call of a process.

## `batfish_delete_snapshot`

```python
batfish_delete_snapshot(name: str, network: Optional[str] = None) -> dict
```

Sanitizes `name`, verifies it exists (`_ensure_snapshots`), deletes it, returns
`{"name": "<safe-name>"}`. A missing snapshot raises `ValueError` listing what is
available.

## `batfish_upload_snapshot`

```python
batfish_upload_snapshot(
    name: str,
    zip_base64: str,
    network: Optional[str] = None,
    overwrite: bool = False,
) -> dict
```

- `zip_base64` is decoded with `base64.b64decode(validate=True)`; bad padding or
  characters raise `ValueError` prefixed with `zip_base64 is not valid base64:`.
- `name` is sanitized with `safe_snapshot_name` (extension dropped, invalid
  characters replaced, accents stripped, max 100 chars, `settings` renamed).
- Without `overwrite`, collisions become `name_1`, `name_2`, ... and the tool
  returns the name actually used.
- The zip is written to a temporary file, uploaded with `bf.init_snapshot`, then
  deleted in a `finally` block.
- Returns `{"name": "<actual-name>"}`.

## `batfish_run_question`

```python
batfish_run_question(
    fun: str,
    options: Optional[dict] = None,
    snapshot: Optional[str] = None,
    reference_snapshot: Optional[str] = None,
    network: Optional[str] = None,
    debug: bool = False,
    trace_detail: str = "summary",
    max_rows: int = 50,
) -> dict
```

### Success shape

```json
{
  "fun": "traceroute",
  "snapshot": "abc_net",
  "reference_snapshot": null,
  "status": "SUCCESS",
  "rows": [{"Flow": "...", "Traces": [{"disposition": "...", "path": ["r1"], "hop_count": 1}], "TraceCount": 1}],
  "removed_columns": [],
  "total_rows": 1,
  "returned_rows": 1,
  "truncated": false
}
```

For `differentialReachability` with a reference snapshot, `trace_columns` is added:

```json
"trace_columns": {
  "Snapshot_Traces": "abc_net_Fail",
  "Reference_Traces": "abc_net"
}
```

### Error shape

```json
{
  "fun": "bidirectionalReachability",
  "snapshot": "abc_net",
  "reference_snapshot": null,
  "status": "error",
  "code": "BIDIRECTIONAL_REQUIRES_DST",
  "message": "...",
  "rows": [],
  "removed_columns": []
}
```

With `debug=true`, a `debug` key carries `{"exception": "...", "log": "..."}`.
Error payloads do not include `total_rows`/`returned_rows`/`truncated`.

### Validation order

1. `reference_snapshot` without `snapshot` -> `ValueError`.
2. Snapshot existence -> `ValueError` with available names.
3. `trace_detail` in `{"summary","none","full"}` -> else `INVALID_ARGUMENT`.
4. `max_rows` non-negative int (bool rejected) -> else `INVALID_ARGUMENT`.
5. `options` validation:
   - must be a dict,
   - `bidirectionalReachability` requires non-empty `headers.dstIps`,
   - any key named `srcPorts`/`dstPorts` must be a string,
   - any key named `maxTraces` must be an int (bool rejected).
6. Run, catching Batfish/transport exceptions into a structured error.

### Differential semantics

- `snapshot` is the snapshot **under test**.
- `reference_snapshot` is the **baseline**.
- In the answer, `Snapshot_*` columns come from `snapshot`, `Reference_*` from
  `reference_snapshot`.
- `compareFilters` and `differentialReachability` are differential-only; other
  questions may also be run differentially, but see the hardcoded
  `supports_differential` caveat in [08-development.md](08-development.md).

### Trace detail and row cap

- `summary` (default): trace columns become `{disposition, path, hop_count}`.
- `none`: trace columns are dropped; trace count columns remain.
- `full`: original hop-by-hop traces (can be large).
- `max_rows`: default 50; `0` means unlimited. `total_rows`, `returned_rows`,
  `truncated` always report the effect.

## `batfish_list_assertions`

```python
batfish_list_assertions() -> dict
```

```json
{
  "snapshot": ["assert_filter_denies", "..."],
  "answer": ["assert_has_no_route", "...", "assert_min_results"],
  "specs": {
    "assert_flows_succeed": {
      "label": "Flows succeed",
      "description": "...",
      "group": "Reachability",
      "phase": 2,
      "call": "session",
      "needs_question": false,
      "params": [
        {"name": "startLocation", "type": "LocationSpec", "optional": false, "hint": "..."}
      ]
    }
  }
}
```

- `snapshot` / `answer` come from the installed pybatfish
  (`assertions.list_assertions()`).
- `specs` is the curated 16-entry registry with TYPE_HINTS on params.
- Does not contact Batfish, but imports pybatfish to introspect names.

## `batfish_run_assertion`

```python
batfish_run_assertion(
    name: str,
    params: Optional[dict] = None,
    question: Optional[dict] = None,
    snapshot: Optional[str] = None,
    network: Optional[str] = None,
    debug: bool = False,
) -> dict
```

Success/failure shape:

```json
{
  "type": "assert_flows_succeed",
  "status": "pass",
  "message": "",
  "snapshot": "abc_net",
  "ran_at": "2026-11-11 12:00:00",
  "runtime": 1.234,
  "fun": "traceroute"
}
```

- `status` is `pass`, `fail`, or `error`.
- `message` is the one-line summary on failures.
- `answer_rows` holds failing rows when they can be extracted.
- `details` holds a capped records-style payload when rows cannot be parsed
  (`details_truncated: true` when the 8 KB cap applies).
- `code` is set on errors (classification or `VALIDATION_ERROR`).
- `debug=true` adds the raw message on classified errors.

Validation failure shape (nothing runs):

```json
{
  "type": "assert_num_results",
  "status": "error",
  "code": "VALIDATION_ERROR",
  "message": "missing required parameter: num",
  "errors": ["missing required parameter: num"],
  "snapshot": "abc_net"
}
```

`question` is required for answer-based assertions:
`{"fun": "unusedStructures", "options": {...}}`. For `assert_has_route` /
`assert_has_no_route`, the runner runs `routes(nodes=node, vrfs=vrf)` itself; do
not pass a question.

## `batfish_run_assertion_suite`

```python
batfish_run_assertion_suite(
    suite_yaml: str,
    snapshot: Optional[str] = None,
    network: Optional[str] = None,
    debug: bool = False,
) -> dict
```

```json
{
  "results": [ /* same shape as batfish_run_assertion */ ],
  "errors": [],
  "summary": {"passed": 3, "failed": 1, "error": 0}
}
```

Invalid suite:

```json
{
  "results": [],
  "errors": ["Assertion #2 (assert_nope): unknown assertion type: 'assert_nope'"],
  "summary": {"passed": 0, "failed": 0, "error": 0}
}
```

All-or-nothing: one bad item means nothing runs. See
[06-assertions.md](06-assertions.md) and `examples/assertions.yaml`.

## `batfish_fork_snapshot`

```python
batfish_fork_snapshot(
    base_name: str,
    name: Optional[str] = None,
    failed_nodes: Optional[list] = None,
    failed_interfaces: Optional[list] = None,
    overwrite: bool = False,
    network: Optional[str] = None,
) -> dict
```

- Default name: `<base_name>_Fail`, then sanitized.
- Without `overwrite`, collisions get `_1`, `_2`, ...
- `failed_nodes` is a list of node names.
- `failed_interfaces` accepts `"r1[Gi0/0]"` strings or
  `{"hostname": "r1", "interface": "Gi0/0"}` mappings. Bare node names are
  rejected with a `ValueError` pointing at `failed_nodes`.
- Returns `{"name": "<actual>", "base_name": "<base>"}`.

Failure-testing workflow: fork, then run a differential question with the fork as
`snapshot` and the base as `reference_snapshot`.

## `batfish_get_topology`

```python
batfish_get_topology(
    kind: str = "layer3",
    snapshot: Optional[str] = None,
    network: Optional[str] = None,
) -> dict
```

```json
{
  "kind": "layer3",
  "snapshot": "abc_net",
  "node_count": 9,
  "edge_count": 9,
  "nodes": ["fw", "internet", "..."],
  "edges": [
    {"source": "r1", "target": "r2", "title": "r1 (Gi0/0) <---> r2 (Gi0/0)"}
  ]
}
```

- `kind="layer3"`: runs `layer3Edges`.
- `kind="routing"`: runs `bgpEdges`; edges have no `title`.
- Any other `kind` raises `ValueError`.

## Cross-cutting gotchas

1. **Set the network first.** A fresh process has no network. Pass `network` on
   the first Batfish-backed call.
2. **Snapshots are verified before work is queued.** A typo returns a clear
   `ValueError` with available names.
3. **Ports are strings** in `srcPorts`/`dstPorts`; numbers are rejected.
4. **`maxTraces` is an integer.**
5. **Omitting `ipProtocols` tests ICMP.** Set `ipProtocols` + `dstPorts` for
   service-level checks.
6. **Broad headers produce representative flows.** Prefer node names, interface
   locations, or explicit host IPs over prefixes.
7. **`bidirectionalReachability` needs `dstIps`.**
8. **`reachability` returns examples, not a full matrix.** Use `traceroute` per
   destination.
