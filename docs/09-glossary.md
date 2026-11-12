# 09 — Glossary

Terms used in the code, docs, and tool responses.

## MCP and transport

| Term | Meaning |
|---|---|
| **MCP** | Model Context Protocol: a JSON-RPC protocol that lets an LLM client discover and call tools. |
| **FastMCP** | The Python framework used here (`fastmcp>=4,<5`) to define tools and run the stdio server. |
| **Tool** | A function exposed to the LLM, named `batfish_*` in this project. |
| **stdio transport** | The server reads/writes JSON-RPC on stdin/stdout. It must not print logs to stdout. |
| **Instructions** | The `_INSTRUCTIONS` text attached to the FastMCP instance; guides the LLM's workflow. |
| **`TYPE_HINTS`** | Human/LLM-readable descriptions of complex parameter types, merged into tool input metadata. |

## Batfish

| Term | Meaning |
|---|---|
| **Batfish** | External network-analysis engine that parses configs and answers questions. |
| **Coordinator** | The Batfish service (default ports 9996/9997) that stores snapshots and runs analyses. |
| **pybatfish** | Python client library for Batfish (`pybatfish.client.session.Session`). |
| **Network** | A namespace of snapshots inside Batfish. Set with `set_network`. |
| **Snapshot** | A loaded set of configuration files (usually a zip) inside a network. |
| **Reference snapshot** | The baseline in a differential question. |
| **Differential question** | A question run with both `snapshot` and `reference_snapshot`; columns are `Snapshot_*` and `Reference_*`. |
| **Fork** | A copy of a snapshot, optionally with nodes/interfaces deactivated. |
| **Deactivate** | Mark a node or interface as failed/down in a forked snapshot. |
| **Question** | A Batfish analysis, identified by its pybatfish function name (`fun`), e.g. `traceroute`. |
| **Answer** | The pybatfish result object; `.frame()` returns a pandas `DataFrame`. |
| **Frame** | A pandas `DataFrame` with one row per result and named columns. |
| **RIB** | Routing Information Base (the routing table). |
| **FIB** | Forwarding Information Base (used for forwarding decisions). |
| **LPM** | Longest-prefix match. |
| **VRF** | Virtual Routing and Forwarding instance. |
| **ACL / filter** | Access control list / packet filter; Batfish calls these filters. |
| **Specifier** | A Batfish mini-language for matching IPs, nodes, interfaces, filters, etc. |
| **`ipSpaceSpec`** | Specifier for an IP space: IP, prefix, node name, interface, or named IP space. |
| **`nodeSpec`** | Specifier for nodes: name, regex `/pattern/`, or comma-separated names. |
| **`headerConstraints`** | Dict describing packet headers (`srcIps`, `dstIps`, ports, protocols, ...). |
| **`pathConstraints`** | Dict describing a path (`startLocation`, `endLocation`, transit/forbidden locations). |
| **Trace** | The hop-by-hop path Batfish computes for a flow, with dispositions and steps. |
| **Disposition** | The outcome of a trace, e.g. `ACCEPTED`, `DENIED_IN`, `DENIED_OUT`, `NO_ROUTE`. |
| **HSRP / VRRP** | First-hop redundancy protocols (standby gateways). |
| **BGP / OSPF** | Routing protocols Batfish can model and check. |

## This project

| Term | Meaning |
|---|---|
| **Catalog** | `catalog.py`: curated categories, order, question names, and input names. |
| **Metadata** | `question_metadata.json`: generated descriptions, tags, synonyms, typed inputs, output columns. |
| **`fun`** | The pybatfish question function name; the primary question identifier in tools. |
| **`options`** | Keyword arguments passed to a question, as a JSON object. |
| **Assertion** | A pass/fail/error check, from the 16-entry registry. |
| **Suite** | A YAML list of assertions, validated all-or-nothing. |
| **`scope`** | Reserved assertion field (`active`/`alternate`/`both`/`failed`); validated but not enforced by the runner. |
| **Session-bound assertion** | An assertion called through `Session.asserts` with a snapshot. |
| **Answer-based assertion** | An assertion called with an answer/DataFrame (e.g. `assert_zero_results`). |
| **`answer_from`** | For answer-based assertions: whether the answer comes from a chosen question or the `routes` question. |
| **PASS / FAIL / ERROR** | Assertion outcomes: satisfied, assertion raised, or could not be evaluated. |
| **Structured error** | A normal tool result with `status: "error"`, `code`, and `message` (not an exception). |
| **Error code** | Stable identifier such as `SNAPSHOT_INCOMPATIBLE` or `INVALID_PORT_SPEC`. |
| **Trace compaction** | Replacing full traces with `{disposition, path, hop_count}` (`trace_detail="summary"`). |
| **Row cap** | `max_rows` limit on returned rows (`0` = unlimited). |
| **`removed_columns`** | Columns dropped because they were entirely empty or because traces were compacted away. |
| **`total_rows` / `returned_rows` / `truncated`** | Truncation reporting for question answers. |
| **`answer_rows`** | Structured failing rows attached to an assertion result. |
| **`details`** | Capped text fallback when failing rows cannot be structured. |
| **`safe_snapshot_name`** | Sanitizer for Batfish snapshot names. |
| **`_ensure_snapshots`** | Pre-flight existence check that turns typos into clear errors. |
| **`_lock`** | Process-wide `RLock` that serializes Batfish-backed tool calls. |
