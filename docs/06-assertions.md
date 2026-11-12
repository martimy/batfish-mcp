# 06 — Assertions

Assertions are checks that return **pass**, **fail**, or **error**. The server
supports 16 curated assertion types, one at a time or in a YAML suite.

The design goal is that a broken run is never reported as a pass. In particular:

- pybatfish warnings about an assertion that could not really be evaluated become
  `error`, not `pass`;
- a question that fails before an answer-based assertion runs becomes `error`;
- invalid parameters are rejected before anything runs.

## The three assertion modules

| Module | Role |
|---|---|
| `assertion_specs.py` | The registry: what exists, how it is called, what parameters it takes. |
| `assertion_io.py` | YAML parsing, schema validation, all-or-nothing suite loading. |
| `assertions.py` | Execution and outcome classification. |

`server.py` adapts them to MCP tools and shapes the JSON.

## Registry (`assertion_specs.py`)

Each entry has:

```python
"assert_flows_succeed": {
    "label": "Flows succeed",
    "description": "...",
    "group": "Reachability",
    "phase": 2,                       # release phase, informational
    "source": "pybatfish",            # or "builtin"
    "call": "session",                # or "answer"
    "params": [
        {"name": "startLocation", "type": "LocationSpec", "optional": False},
        {"name": "headers", "type": "HeaderConstraints", "optional": False},
    ],
}
```

For answer-based assertions, two extra fields say where the answer comes from:

```python
"call": "answer",
"answer_from": "question",   # the caller supplies {"fun", "options"}
"answer_param": "answer",    # argument that receives the answer
```

or

```python
"answer_from": "routes",     # the runner runs routes(nodes=..., vrfs=...)
"answer_param": "routes",
```

Constants:

- `SESSION = "session"`, `ANSWER = "answer"`
- `FROM_QUESTION = "question"`, `FROM_ROUTES = "routes"`
- `SCOPES = ("active", "alternate", "both", "failed")`, `DEFAULT_SCOPE = "active"`
- `HIDDEN_PARAMS = ("soft", "snapshot", "session", "df_format")` — never exposed to
  the user; the runner supplies them.

Helpers: `get_spec(name)`, `needs_question(spec)`, `specs_by_group(max_phase=None)`.

### The 16 registered assertions

| Group | Types |
|---|---|
| Snapshot checks | `assert_no_undefined_references`, `assert_no_forwarding_loops`, `assert_no_unestablished_bgp_sessions`, `assert_no_incompatible_bgp_sessions`, `assert_no_incompatible_ospf_sessions`, `assert_no_duplicate_router_ids` |
| Filters | `assert_filter_has_no_unreachable_lines`, `assert_filter_permits`, `assert_filter_denies` |
| Reachability | `assert_flows_succeed`, `assert_flows_fail` |
| Question results | `assert_zero_results`, `assert_num_results`, `assert_min_results` |
| Routing | `assert_has_route`, `assert_has_no_route` |

`assert_min_results` is the only `source: "builtin"` entry; it is implemented in
`assertions.py` because pybatfish has no equivalent.

### Scope

`scope` is accepted and preserved by the YAML layer but **is not used by the
runner** in this MCP server. It is a reserved field from the Bat-Q origin. A
`scope` value only affects validation/dumping today; all assertions run against
the snapshot passed to the tool.

## YAML schema (`assertion_io.py`)

```yaml
version: 1
assertions:
  - type: assert_no_undefined_references
    description: Every referenced structure is defined.

  - type: assert_num_results
    name: Exactly two BGP edges
    question:
      fun: bgpEdges
    params:
      num: 2

  - type: assert_flows_succeed
    params:
      startLocation: as1border1
      headers:
        dstIps: 2.128.0.101
        ipProtocols: [TCP]
        dstPorts: "443"
```

Allowed keys per item: `type`, `name`, `description`, `scope`, `params`,
`question` (plus `id`, which is internal and never dumped).

Validation rules in `validate_assertion`:

1. Item must be a mapping.
2. Unknown keys are errors.
3. `type` must exist in the registry.
4. `scope` must be one of `SCOPES`.
5. `name` / `description`, if present, must be strings.
6. `params` must be a mapping.
7. Unknown params are errors; missing non-optional params are errors.
8. Param values are type-checked with `_TYPE_CHECKS` (note: `bool` is rejected for
   `int`).
9. `HeaderConstraints` params are checked against the field names of the installed
   pybatfish `HeaderConstraints`; unknown fields are errors.
10. `RouteMatch` must not be empty (an empty match would accept any route).
11. If the spec needs a question:
    - `question.fun` is required,
    - if `known_funs` is supplied, the fun must be in the catalog,
    - `question.options`, if present, must be a mapping.
12. If the spec does not need a question, supplying one is an error.

`load_assertions(text, known_funs=None)` returns `(alist, errors)` and is
**all-or-nothing**: any error yields `([], errors)`. Valid items get ids `a1`,
`a2`, ... in order. `dump_assertions(alist)` round-trips the content without ids.

`examples/assertions.yaml` contains 9 valid assertions and is exercised by the
tests.

## Execution (`assertions.py`)

### Two call styles

```
call == "session"
    bf.asserts.<name>(soft=False, snapshot=snapshot, df_format="records", **params)

call == "answer"
    answer = fetch(...)                 # question or routes
    pybatfish.client.asserts.<name>(soft=False, answer=answer, **params)
    # or CUSTOM[name](soft=False, ...) for builtins
```

`ANSWER_BASED` names are module-level functions in `pybatfish.client.asserts`,
not methods on `Session.asserts`:

- `assert_zero_results`
- `assert_num_results`
- `assert_has_route`
- `assert_has_no_route`

### Answer fetching

`_fetch_answer(spec, item, params, snapshot)`:

- `answer_from == "question"`: runs `getattr(bf.q, fun)(**options).answer(snapshot=snapshot)`.
- `answer_from == "routes"`: sets `vrf` default to `"default"` and runs
  `bf.q.routes(nodes=params["node"], vrfs=vrf).answer(snapshot=snapshot)`,
  returning the frame.

If fetching fails, `run_assertion_item` returns **ERROR** with
`Question '<fun>' failed: ...`. Nothing was checked, so it must not pass.

### Outcome classification

`run_assertion`:

```python
try:
    ... run with soft=False ...
except BatfishAssertException:
    status = FAIL
except Exception:
    status = ERROR
else:
    if any BatfishAssertWarning was emitted:
        status = ERROR   # "Not evaluated: ..."
    else:
        status = PASS
```

- `soft=False` means a failed assertion raises instead of returning `False`.
- Session-bound assertions get `df_format="records"` by default so failing rows
  are compact and parseable.
- `_prepare_params` converts a `headers` dict to `HeaderConstraints`, which also
  validates field names with a clear `TypeError`.

### Result object

`AssertionResult` carries:

| Field | Meaning |
|---|---|
| `status` | `PASS`, `FAIL`, `ERROR` |
| `message` | raw message from the assertion/exception |
| `summary` | first line of `message` |
| `details` | remaining lines (often a records-style table) |
| `runtime` | seconds |
| `snapshot` | snapshot name |
| `ran_at` | local timestamp string |
| `fun` | question used, if any |
| `answer` | that question's answer, kept for failing rows |

## Response shaping (`server._result_to_dict`)

```
AssertionResult
  ├─ type, status, snapshot, ran_at, runtime
  ├─ message = summary (first line)
  ├─ ERROR: classify message -> code + actionable message
  │         debug=True -> debug.raw_message
  ├─ fun (if any)
  ├─ answer_rows = rows from result.answer (JSON-safe)
  │     if empty and details exist: ast.literal_eval(details)
  └─ if no rows and not ERROR: details (capped at 8000 chars,
        details_truncated=true)
```

Why parse `details`? pybatfish's `df_format="records"` renders a DataFrame as
`str(list_of_dicts)`. Simple payloads can be recovered into `answer_rows`; values
containing object reprs (for example full traces) cannot, so they stay as text.

Why classify ERROR only? A failure message is the actionable assertion text; an
error is usually a Batfish/Java message that benefits from mapping to a stable
code.

## Suite semantics

`batfish_run_assertion_suite`:

1. validates the whole YAML first (`load_assertions`),
2. on any error returns `{results: [], errors: [...], summary: zeros}`,
3. otherwise runs every item in order,
4. returns per-item results plus `summary` counts of `pass`/`fail`/`error`.

A suite is **not** short-circuited: a failure does not stop later assertions. The
summary is how the caller decides overall success.

## How to add an assertion

1. Add an entry to `ASSERTION_SPECS` in `assertion_specs.py`:
   - choose `source` (`pybatfish`/`builtin`),
   - choose `call` (`session`/`answer`),
   - for answer-based, set `answer_from` and `answer_param`,
   - list user params with the same `{name, type, optional}` shape.
2. If `source == "builtin"`, implement the function in `assertions.CUSTOM`. It
   must accept `soft=False` plus the params and raise
   `pybatfish.client.asserts._raise_common(message, soft)` on failure (or simply
   raise `BatfishAssertException`).
3. Run the tests. For pybatfish-backed specs,
   `test_spec_matches_installed_pybatfish` automatically compares your spec with
   the installed function signature; this catches typos and pybatfish upgrades.
4. Add a runner test for any non-trivial behavior (answer fetching, warnings,
   status mapping).
5. If the assertion needs a new param type, add it to `_TYPE_CHECKS` in
   `assertion_io.py` and to `TYPE_HINTS` in `hints.py`.
6. Update the count in `README.md` / `HOW-TO.md` if the total changes.

## Common failures and what they mean

| Symptom | Meaning |
|---|---|
| `VALIDATION_ERROR` + `errors[]` | The item/suite is invalid; nothing ran. Fix params/question. |
| `status: "error"` + `code: "VALIDATION_ERROR"` | Same, for a single assertion. |
| `Not evaluated: ...` | pybatfish emitted `BatfishAssertWarning` (e.g. missing node/VRF). The check did not really happen. |
| `Question '...' failed: ...` | The answer needed by an answer-based assertion could not be fetched. |
| `BATFISH_UNAVAILABLE` | Coordinator unreachable. Check `BATFISH_SERVER`. |
| `details` instead of `answer_rows` | Failing rows contained object reprs and could not be parsed; the text is capped at 8 KB. |
