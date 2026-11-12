# 01 — High-level overview

## What this project is

`batfish-q-mcp` is a Python **MCP (Model Context Protocol) server** that exposes
[Batfish](https://batfish.org) network analysis as tools an LLM can call. Batfish
loads a *snapshot* of network configuration files and answers questions about
routing, ACLs, reachability, topology, and protocol sessions. This project wraps
that engine in a small, curated, LLM-friendly API.

It is a headless offshoot of the Bat-Q Streamlit app. The GUI was removed; the
question catalog, assertion registry/runner, answer serialization, and snapshot
tooling were kept and re-exposed over MCP.

- Package: `src/batfish_mcp/`
- Entry point: `batfish_mcp.server:main` (console script `batfish-q-mcp`)
- Transport: stdio JSON-RPC via FastMCP 4.x
- Python: `>=3.10`
- Batfish: external process, reached over the network (default `127.0.0.1`)

## System context

```
┌──────────────────────┐        stdio JSON-RPC         ┌─────────────────────────┐
│  MCP client / LLM    │  <──────────────────────────> │  batfish-q-mcp          │
│ (Claude, Cursor,     │   tools/list, tools/call      │  (this repository)      │
│  OpenCode, ...)      │                               │                         │
└──────────────────────┘                               │  server.py              │
                                                       │   ├─ discovery (local)  │
                                                       │   ├─ assertions         │
                                                       │   ├─ snapshots          │
                                                       │   └─ topology           │
                                                       └───────────┬─────────────┘
                                                                   │ pybatfish
                                                                   │ HTTP (9996/9997)
                                                                   ▼
                                                       ┌─────────────────────────┐
                                                       │  Batfish coordinator    │
                                                       │  (Docker allinone)      │
                                                       │  snapshots + analysis   │
                                                       └─────────────────────────┘
```

Important: the server itself is **stateless on disk**. It does not store
snapshots; Batfish does. The server holds one in-process pybatfish `Session`
(which caches the current network) for the lifetime of the process.

## What it can do

1. **Discover questions** without a Batfish server:
   - list the 60-question curated catalog,
   - search it from plain language,
   - fetch full input/output metadata for one question.
2. **Manage snapshots:** list, upload (base64 zip), delete, fork with failures.
3. **Run any catalog question**, including differential questions that compare a
   snapshot against a reference snapshot.
4. **Run assertions** (16 curated types), either one at a time or as a YAML
   suite.
5. **Return topology** as JSON nodes/edges for L3 links or BGP adjacencies.

## Core vocabulary

| Term | Meaning in this codebase |
|---|---|
| **Network** | A Batfish namespace that contains snapshots. Passed as `network`, e.g. `"NETWORK"`. The session remembers the current network. |
| **Snapshot** | A loaded set of configs (a zip) inside a network. Identified by name, e.g. `"abc_net"`. |
| **Reference snapshot** | The baseline in a differential question. `snapshot` is the one under test. |
| **Question / `fun`** | A Batfish analysis function, e.g. `traceroute`, `bgpSessionStatus`, `layer3Edges`. The pybatfish attribute name is the `fun`. |
| **Options** | Keyword arguments for a question, passed as a JSON object (`options`). |
| **Answer** | A pybatfish `Answer` object. Its `frame()` is a pandas `DataFrame`; this server turns that into JSON rows. |
| **Differential question** | A question run with both `snapshot` and `reference_snapshot`. Result columns are prefixed `Snapshot_*` and `Reference_*`. |
| **Catalog** | The curated list of 60 questions in `catalog.py`: categories, order, names, input names. |
| **Metadata** | Generated per-question details in `question_metadata.json`: descriptions, tags, synonyms, typed output columns, defaults, allowed values, field specs. |
| **Assertion** | A pass/fail/error check, either a pybatfish `assert_*` function or a small local implementation. |
| **Suite** | A YAML document containing a list of assertions, validated all-or-nothing. |
| **Fork** | A copy of a snapshot, optionally with nodes/interfaces deactivated, used for failure testing. |

## The two-layer question model

This is the single most important design decision to understand:

```
catalog.py                  question_metadata.json
(curated, hand-edited)      (generated, checked in)
  categories                  description / long_description
  order                       tags / synonyms
  fun                         inputs: types, defaults, allowed values, field specs
  display name                output_columns: name, type, description
  input names
        \                         /
         \                       /
          └────► server.py ◄────┘
                 merges at runtime (lru_cache)
                 exposes list/find/get/run
```

- The **catalog** owns structure and is safe to edit by hand.
- The **metadata** owns prose and types and is generated from a live Batfish plus
  the Batfish docs notebooks. Regenerate it with `scripts/`.
- If they drift, `batfish_get_question` surfaces template-only inputs, and tests
  (`test_static_metadata_covers_the_catalog_and_has_no_gaps`) fail.

Discovery tools read only these files, so they work with **no Batfish server**.

## End-to-end request walkthrough

### A. Upload a snapshot

```
LLM calls batfish_upload_snapshot(name="abc_net.zip", zip_base64="UEsDB...", network="NETWORK")
  server.py
    1. base64.b64decode(validate=True)                  -> bytes or ValueError
    2. acquire the global tool lock
    3. get_session(); set_network("NETWORK") if given
    4. safe_snapshot_name("abc_net.zip")                -> "abc_net"
    5. if not overwrite: pick "abc_net", "abc_net_1", ...
    6. write bytes to a NamedTemporaryFile(.zip)
    7. bf.init_snapshot(path, name=..., overwrite=...)
    8. delete the temp file; return {"name": "abc_net"}
```

### B. Run a question

```
LLM calls batfish_run_question(fun="traceroute", options={...}, snapshot="abc_net")
  server.py
    1. acquire the global tool lock
    2. get_session(); set_network(...) if given
    3. if reference_snapshot: require snapshot; snapshots=(reference, snapshot)
    4. _ensure_snapshots(): list snapshots, fail early with available names
    5. validate trace_detail, max_rows
    6. _validate_question_options(): ports are strings, maxTraces is int,
       bidirectionalReachability has dstIps
    7. queries.run_query(...)
         -> getattr(bf.q, fun)(**options).answer(snapshot=..., reference_snapshot=...)
    8. on exception -> errors.exception_payload() -> {status:"error", code, message}
    9. _serialize_answer():
         status = answer["status"]
         frame = answer.frame()
         drop all-empty columns            (presenter.format_result_lite)
         compact trace columns             (presenter.compact_traces)
         convert to JSON records
         apply max_rows cap
         return {fun, snapshot, reference_snapshot, status, rows,
                 removed_columns, total_rows, returned_rows, truncated}
```

### C. Run an assertion

```
LLM calls batfish_run_assertion(name="assert_flows_succeed", params={...}, snapshot="abc_net")
  server.py
    1. acquire lock, get_session, set_network, _ensure_snapshots
    2. assertion_io.validate_assertion(item, known_funs)
         -> validation problems? return {status:"error", code:"VALIDATION_ERROR", errors:[...]}
    3. assertions.run_assertion_item(item, snapshot)
         a. look up the spec in assertion_specs.py
         b. if answer-based: fetch the question's answer (or the routes table)
            - fetch failure => ERROR (nothing was checked)
         c. run the pybatfish/local assertion with soft=False
         d. BatfishAssertException => FAIL
            BatfishAssertWarning  => ERROR ("not evaluated")
            any other exception   => ERROR
    4. server._result_to_dict(): short message + structured answer_rows
       or a capped details string
```

## Design principles and invariants

1. **Headless core, thin MCP layer.** Tool bodies do orchestration and
   serialization; reusable logic lives in the other modules.
2. **Discovery must work offline.** `list/find/get` never touch Batfish.
3. **Everything returned to the LLM is JSON-safe.** No pandas objects, NaN,
   tuples, sets, or Java-style reprs leak through.
4. **Fail early with actionable errors.** Snapshot existence is checked before
   queueing work; Batfish exceptions are mapped to stable codes.
5. **One shared Batfish session, serialized.** A pybatfish `Session` carries
   mutable network state; all tool bodies take a process-wide `RLock`.
6. **Validation is all-or-nothing.** An invalid assertion suite runs nothing.
7. **The LLM is told how to use the API.** `_INSTRUCTIONS`, tool docstrings,
   `TYPE_HINTS`, and `question_metadata.json` are part of the product, not just
   documentation.

## What this project is not

- It is **not** a Batfish replacement or a config parser; Batfish does the work.
- It is **not** a multi-tenant service: one process = one session = one current
  network at a time.
- It does **not** authenticate or encrypt the MCP transport; that is the client's
  job.
- It is **not** a GUI (the Bat-Q GUI was removed).
- Large snapshots should be loaded into Batfish directly; base64 upload through
  an LLM is only practical for small lab snapshots.

## Where to go next

- How the pieces fit together: [02-architecture.md](02-architecture.md)
- Every module and function: [03-modules.md](03-modules.md)
- Tool contracts: [04-tools.md](04-tools.md)
