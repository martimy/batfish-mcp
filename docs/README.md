# Developer documentation

These docs are for people who need to **change** this project: add a tool, add a
question, fix a bug, or understand why a response looks the way it does. They
complement the user-facing [README](../README.md) and the
[HOW-TO](../HOW-TO.md) tutorial.

Everything here is written against the current source. If a change makes a
statement here false, update the doc in the same change.

## Recommended structure (and why)

The docs are numbered so that the first file answers "what is this and how does
a request flow?" before any file asks you to care about a module.

| # | Document | Answers |
|---|---|---|
| 01 | [Overview](01-overview.md) | What the server is, the system context, core vocabulary, and one end-to-end request. **Start here.** |
| 02 | [Architecture](02-architecture.md) | Layers, module dependencies, runtime flow, state/concurrency, serialization, and error handling. |
| 03 | [Module reference](03-modules.md) | What every file and public function is responsible for, plus per-module gotchas. |
| 04 | [Tool surface](04-tools.md) | The 12 MCP tools: signatures, response shapes, validation rules, and discovery workflow. |
| 05 | [Question catalog and metadata](05-question-catalog.md) | The two-layer question model, the metadata generation pipeline, and search. |
| 06 | [Assertions](06-assertions.md) | Registry, YAML schema, validation, runner semantics, and PASS/FAIL/ERROR rules. |
| 07 | [Testing](07-testing.md) | Fakes, fixtures, test layout, and how to add tests without a live Batfish. |
| 08 | [Development guide](08-development.md) | Setup, common change recipes, debugging, known issues, and dead code. |
| 09 | [Glossary](09-glossary.md) | Batfish/MCP terms used throughout the code and docs. |

### Suggested reading paths

- **New contributor:** 01 → 02 → 03, then skim 04–06.
- **Fixing a tool response or validation bug:** 01 → 04 → 02 (serialization/error
  sections) → 03 (`server.py`, `presenter.py`, `errors.py`).
- **Adding a question or improving discovery:** 01 → 05 → 04.
- **Adding or changing an assertion:** 01 → 06 → 07.
- **Working on tests only:** 07 → 03.

## Documentation conventions

- Paths are relative to the repository root.
- `module.function()` refers to a Python function; `ToolName` refers to an MCP
  tool exposed by `server.py`.
- "Catalog" means the curated question list in `catalog.py`; "metadata" means the
  generated per-question details in `question_metadata.json`.
- Response examples are abbreviated. The authoritative shapes are in
  [04-tools.md](04-tools.md) and the tool docstrings in `server.py`.

## Source-of-truth map

When docs and code disagree, the code wins; then fix the docs.

| Topic | Source of truth |
|---|---|
| Tool names, signatures, docstrings | `src/batfish_mcp/server.py` |
| Question categories/order/inputs | `src/batfish_mcp/catalog.py` |
| Question descriptions/output columns | `src/batfish_mcp/question_metadata.json` |
| Type hints shown to the LLM | `src/batfish_mcp/hints.py` |
| Assertion registry | `src/batfish_mcp/assertion_specs.py` |
| Assertion YAML rules | `src/batfish_mcp/assertion_io.py` |
| Assertion execution | `src/batfish_mcp/assertions.py` |
| Error codes/messages | `src/batfish_mcp/errors.py` |
| Test doubles | `tests/fakes.py` |
