# 05 — Question catalog and metadata

The question layer is split into two files on purpose:

| File | Edited by | Contains |
|---|---|---|
| `src/batfish_mcp/catalog.py` | humans | categories, display order, `fun`, display names, input names, differential-only markers |
| `src/batfish_mcp/question_metadata.json` | generator (checked in) | descriptions, long descriptions, tags, synonyms, typed inputs, output columns |

Discovery must work **without a Batfish server**, so both files are checked in and
loaded locally. The generator exists because the prose/types come from Batfish
itself and would be tedious and error-prone to maintain by hand.

## `catalog.py`

The catalog is a JSON document embedded in a Python raw string, parsed by
`read_questions()`:

```json
{
  "Batfish": [
    {
      "category": "Packet Forwarding",
      "description": "This set of questions ...",
      "questions": [
        {
          "fun": "traceroute",
          "name": "Traceroute",
          "input": [
            {"name": "startLocation", "type": "LocationSpec"},
            {"name": "headers", "type": "HeaderConstraints"},
            {"name": "maxTraces", "type": "int"},
            {"name": "ignoreFilters", "type": "bool"}
          ]
        }
      ]
    }
  ]
}
```

Rules:

- `input[].optional` defaults to `true`. Setting it to `false` makes the input
  required in the tool output.
- `type` on an input is optional; when present it wins over the template type.
- `type: "differential"` on a question marks it differential-only. The two are
  `compareFilters` and `differentialReachability`.
- Category order and question order are the display order. Tests assert the first
  question is `initIssues`.
- The catalog has exactly 60 questions.

## `question_metadata.json`

Top-level shape:

```json
{
  "schema_version": 1,
  "source": {
    "generated_at": "2026-11-10T16:44:02Z",
    "batfish_host": "192.168.2.4",
    "pybatfish_version": "2026.9.17.3748",
    "question_templates": "bf.q.<fun>.template from the live coordinator",
    "allowed_values": "...",
    "output_columns": "..."
  },
  "coverage": {
    "questions": 60,
    "questions_with_output_columns": 60,
    "questions_missing_output_columns": [],
    "questions_with_allowed_values": ["bgpRib", "..."],
    "inputs_with_descriptions": 172,
    "inputs_with_defaults": 33,
    "inputs_with_allowed_values": 20,
    "catalog_input_mismatches": [],
    "catalog_required_mismatches": []
  },
  "questions": {
    "<fun>": {
      "description": "...",
      "long_description": "...",
      "tags": ["..."],
      "synonyms": ["..."],
      "differential": false,
      "inputs": {
        "<name>": {
          "type": "headerConstraint",
          "required": true,
          "display_name": "Headers",
          "description": "Packet header constraints.",
          "default": "...",
          "allowed_values": ["..."],
          "field_specs": {
            "dstIps": {"optional": false, "type": "ipSpaceSpec"}
          },
          "min_length": 1
        }
      },
      "output_columns": [
        {"name": "Flow", "type": "Flow", "description": "The flow"}
      ]
    }
  }
}
```

Notes:

- Input `type` values here are **Java/template names** (`integer`,
  `headerConstraint`, `nodeSpec`, ...). `server._TEMPLATE_TYPE_ALIASES` maps them to
  public names (`int`, `HeaderConstraints`, `NodeSpec`, ...). Unknown types become
  `str` rather than leaking implementation names.
- `required` in metadata is authoritative; the catalog's `optional` flag is the
  fallback.
- `allowed_values` can be a list of strings or a list of
  `{"value": "...", "description": "..."}` objects.
- `differential` exists in the file but the server currently hardcodes
  `supports_differential=True` instead of reading it. See
  [08-development.md](08-development.md#known-issues).

## Generation pipeline

```
Batfish docs notebooks                 Live Batfish coordinator
(readthedocs)                          (bf.q.<fun>.template)
      │                                        │
      ▼                                        ▼
scripts/extract_output_columns.py      scripts/generate_question_metadata.py
      │  parses "Return Value" tables        │  merges:
      │  + overrides BASE/DELTA labels       │   - catalog structure
      ▼                                      │   - template inputs/types/defaults
scripts/output_columns.json  ──────────────► │   - curated synonyms
                                             │   - allowed values
                                             ▼
                              src/batfish_mcp/question_metadata.json
                                             │
                                             ▼
                                      server.py (lru_cache)
```

### `scripts/extract_output_columns.py`

- Downloads the Batfish docs notebook sources listed in `PAGES` from
  `https://batfish.readthedocs.io/en/latest/_sources/notebooks/`.
- Parses each question's markdown "Return Value" table.
- Applies `COLUMN_DESCRIPTION_OVERRIDES` so `differentialReachability` columns say
  "snapshot argument" / "reference_snapshot" instead of BASE/DELTA.
- Writes `scripts/output_columns.json` keyed by `fun`.
- Prints a warning for catalog questions with no columns.

Requires network access; no Batfish needed.

### `scripts/generate_question_metadata.py`

```bash
.mcp/bin/python3 scripts/generate_question_metadata.py --host 127.0.0.1
```

Options: `--host` (default `127.0.0.1`), `--columns-json` (default
`scripts/output_columns.json`), `--out` (default
`src/batfish_mcp/question_metadata.json`).

Behavior:

1. Reads catalog questions.
2. Connects to Batfish, lists `bf.q.list()`, and **exits with an error** if any
   catalog question is missing from the live server.
3. For each question, reads `bf.q.<fun>.template` for descriptions, input
   types/defaults, and template-provided allowed values.
4. Fills in allowed values from `CURATED_ALLOWED_VALUES` (sourced from Batfish's
   `questions/Parameters.md`) when the template has no `values`.
5. Adds curated `SYNONYMS` (plain-language search terms).
6. Merges output columns from `output_columns.json`.
7. Computes `coverage` (missing columns, catalog/template input mismatches,
   required-flag mismatches) and writes the file.

Requires a live Batfish with all 60 questions.

### Full regeneration

```bash
# 1. refresh docs-derived output columns (network access)
.mcp/bin/python3 scripts/extract_output_columns.py

# 2. refresh everything else (live Batfish required)
.mcp/bin/python3 scripts/generate_question_metadata.py --host 127.0.0.1

# 3. prove nothing drifted
.mcp/bin/python3 -m pytest -q
```

The generated file records the coordinator host, pybatfish version, and coverage
gaps, so a reviewer can see how it was produced.

## How `server.py` merges the two layers

### List (`batfish_list_questions`)

For each catalog question:

1. look up metadata by `fun`,
2. for each catalog input, emit `_summary_input`:
   `{name, type, required}` plus TYPE_HINTS,
3. emit `output_columns` as names only,
4. add `supports_differential` and `differential_only`.

`_input_type` prefers the catalog's explicit `type`, then the template type.
`_required` prefers metadata `required`, then the catalog's `optional` flag.

### Get (`batfish_get_question`)

Uses `_detail_input`, which adds `display_name`, `description`, `default`,
`allowed_values`, `field_specs`, `min_length`, and TYPE_HINTS. It then iterates
metadata inputs not present in the catalog and appends them (drift guard).

### Search (`batfish_find_questions`)

Builds a pre-tokenized index once:

- `fun`, `name`, `synonyms`, `tags`, `description`, `category` (+ description),
  `long_description`,
- `inputs`: input names, display names, descriptions, and allowed values,
- `outputs`: output column names.

Scoring and weights are documented in [04-tools.md](04-tools.md#batfish_find_questions).

## Invariants enforced by tests

`tests/test_server.py::test_static_metadata_covers_the_catalog_and_has_no_gaps`:

- metadata question keys == catalog `fun` set,
- every question has a non-empty `description`, `tags`, `synonyms`, and
  `output_columns`,
- every output column has exactly `{name, type, description}`, all non-empty,
- metadata input names == catalog input names,
- metadata `required` == `not catalog_input.optional`.

Other tests assert specific metadata for `traceroute`, `reachability`, `routes`,
`testRoutePolicies`, `bgpSessionStatus`, `searchFilters`, and the differential
column labels. If you regenerate metadata, expect these to be the first failures
if the live Batfish or docs changed.

## How to add or change a question

1. Edit `catalog.py`:
   - add/rename the question inside the right category, or add a category,
   - list every input with the correct name and (optionally) public type,
   - mark differential-only questions with `"type": "differential"`.
2. Regenerate metadata against a Batfish that knows the question.
3. If the question is new to the docs, add output columns to
   `scripts/output_columns.json` (or extend `extract_output_columns.py`).
4. If the input uses a new complex type, add it to `_TEMPLATE_TYPE_ALIASES` and
   `hints.TYPE_HINTS`.
5. Add or update tests:
   - catalog/metadata coverage,
   - search if synonyms matter,
   - a `batfish_run_question` test if the response shape is special.
6. Update the count and category breakdown in `README.md` / `HOW-TO.md` if the
   total changed.

**Do not** hand-edit generated fields without also updating the generator; the
next regeneration will overwrite them.
