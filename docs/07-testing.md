# 07 — Testing

The suite runs **without a Batfish server**. It uses fakes for the pybatfish
session and, for discovery, only the checked-in catalog/metadata.

```bash
.mcp/bin/python3 -m pytest -q
# 103 passed in ~3s
```

From the repository root. `pyproject.toml` sets:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", "tests"]
```

That is why tests can do `from fakes import ...` and
`from batfish_mcp import ...` without installing the package.

## Layout

| File | Focus |
|---|---|
| `tests/fakes.py` | Shared doubles: `FakeBatfish`, `FakeAnswer`, `FakeQuestion`, `FakeRoutes`, `fake_session`, `trace`, `ROUTES` |
| `tests/test_queries.py` | `queries.run_query`, differential argument order, fork normalization, session injection |
| `tests/test_assertions.py` | Registry/pybatfish signature sync, YAML validation, runner statuses, answer-based routes logic |
| `tests/test_server.py` | Tool behavior end to end: discovery, search, snapshots, run_question, errors, assertions, fork, topology, and one in-memory MCP wire round-trip |

## The fakes

### `FakeBatfish`

A stand-in for `pybatfish.client.session.Session`:

```python
bf = FakeBatfish(snapshots=("snapA", "snapB"), answers={...})
session.set_session(bf)
...
session.clear_session()
```

- `networks`: `{"NETWORK": ["snapA", "snapB"]}`
- `network`: current network
- `q`: dynamic question namespace; any attribute returns a `_Question`
- `answers`: `{fun: FakeAnswer | Exception}`; default table when absent
- `missing_questions`: funs that raise `AttributeError` (simulates a question the
  installed pybatfish does not have)
- `calls`: list of tuples recording everything, e.g.
  `("set_network", "NETWORK")`, `("init_snapshot", name, overwrite)`,
  `("fork_snapshot", kwargs)`, `("answer", fun, options, kwargs)`
- `reject_uploads`, `fail_delete`: flags for error paths
- Helpers: `calls_named(name)`, `answers_for(fun)`

It validates names with pybatfish's `validate_name`, so name-sanitization bugs
surface in tests.

### `FakeAnswer`

Stands in for a pybatfish `Answer`:

```python
FakeAnswer(pd.DataFrame({"Node": ["r1"]}))   # .frame() returns it
FakeAnswer(empty=True)                       # falsy, simulates an empty answer
FakeAnswer(pd.DataFrame(...), extra={"status": "SUCCESS"})
```

- `frame()` can raise if constructed with an exception.
- `__getitem__` reads `extra` (used by `_serialize_answer` for `answer["status"]`).
- `__bool__` is `not empty`.

### `FakeQuestion` / `FakeRoutes`

- `FakeQuestion(rows=0, fail=False, log=[])` records the snapshot passed to
  `answer()` and returns a DataFrame with `rows` rows.
- `FakeRoutes(calls)` filters the module-level `ROUTES` frame by node/VRF like the
  real `routes` question, recording calls.

### `fake_session(log, calls)`

A smaller `SimpleNamespace` session for assertion-runner tests. It provides
`list_snapshots`, `network`, a few `q` questions, and an `asserts` namespace with
recorder functions that can be made to fail based on params.

### `trace(...)`

Builds a one-hop trace dict:

```python
trace(disposition="DENIED_IN", node="fw", detail={"k": "v"})
```

Used to test trace compaction.

## Test patterns

### Injecting a fake session

```python
@pytest.fixture
def fake():
    bf = FakeBatfish()
    set_session(bf)
    yield bf
    clear_session()
```

Always `clear_session()` in teardown; `session` is process-global.

### Proving discovery is offline

```python
def test_list_questions_without_a_server():
    clear_session()          # any Batfish access would now create a real session
    questions = server.batfish_list_questions()
    assert len(questions) == 60
```

### Testing a response shape

```python
def test_run_question_serializes_rows(fake):
    payload = server.batfish_run_question(
        "nodeProperties", snapshot="snapA"
    )
    assert payload["rows"] == [{"Node": "r1"}, {"Node": "r2"}]
    assert payload["truncated"] is False
```

### Testing differential order

```python
bf.answers["bgpEdges"] = FakeAnswer(pd.DataFrame({"x": [1]}))
server.batfish_run_question("bgpEdges", snapshot="b", reference_snapshot="a")
assert bf.answers_for("bgpEdges")[-1] == {
    "snapshot": "b", "reference_snapshot": "a"
}
```

### Testing validation before execution

Assert on the payload `code`, and assert that no answer was requested:

```python
payload = server.batfish_run_question(
    "bidirectionalReachability", options={"headers": {}}, snapshot="snapA"
)
assert payload["code"] == "BIDIRECTIONAL_REQUIRES_DST"
assert bf.answers_for("bidirectionalReachability") == []
```

### Testing the MCP wire

`test_tools_round_trip_over_the_mcp_wire` uses `fastmcp.Client` in memory:

```python
async with Client(server.mcp) as client:
    names = {t.name for t in await client.list_tools()}
    response = await client.call_tool("batfish_run_question", {...})
    payload = json.loads(response.content[0].text)
```

This is the only test that exercises FastMCP serialization; add tools there too.

## What is covered

- Tool names and schemas (wire test).
- Catalog/metadata completeness and specific metadata values.
- Plain-language search ranking and explainability.
- Snapshot list/upload/delete/fork, including collision naming.
- `run_question` serialization, differential argument mapping, trace modes, row
  caps, option validation, error classification, and `debug`.
- Assertion registry vs installed pybatfish signatures.
- YAML validation (many invalid cases) and all-or-nothing behavior.
- Assertion runner statuses: PASS/FAIL/ERROR, warnings-as-error, answer fetch
  failure, route assertions, headers conversion.
- Topology builders for L3 and routing.

## Adding tests

- **New tool or response field:** add to `tests/test_server.py`; if it is a new
  tool, update the expected tool-name set in the wire test.
- **New question metadata behavior:** add to `test_server.py` near the other
  metadata tests.
- **New assertion type:** `test_spec_matches_installed_pybatfish` picks it up
  automatically for `source: pybatfish`; add explicit runner tests for logic.
- **New pure helper:** a small focused test is enough; prefer a fake over a real
  session.
- **New error pattern:** assert the code and that the raw log only appears with
  `debug=True`.

## Pitfalls

- The `session` singleton is global; a test that forgets `clear_session()` can
  leak a fake into later tests.
- `server` caches catalog/metadata/search with `lru_cache`. Tests do not mutate
  those files, so they do not clear the caches. If you add a test that rewrites
  `question_metadata.json`, call the relevant `cache_clear()`.
- `FakeBatfish` has no `asserts` namespace; use `fake_session` for assertion
  runner tests, or extend the fake.
- Assertions are compared against the **installed** pybatfish. A dependency
  upgrade that changes an `assert_*` signature will fail the suite; that is
  intentional.
- `tests/fakes.py::page_path` is a leftover from the removed Streamlit `views/`
  directory and is unused.
