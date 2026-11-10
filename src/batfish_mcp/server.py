# -*- coding: utf-8 -*-
"""
Copyright 2023-2026 Maen Artimy

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

"""
MCP server exposing Batfish as tools (FastMCP, stdio transport).

All tools share the singleton Batfish session from ``.session``
(``BATFISH_SERVER`` env var, default ``127.0.0.1``). Tool bodies are
serialized with a re-entrant lock because a shared pybatfish ``Session``
carries mutable state (the current network) and is not safe for
concurrent use.

Tool surface:

- ``batfish_list_questions``       — the curated question catalog (catalog.py)
- ``batfish_get_question``         — full metadata for one question
- ``batfish_list_snapshots``       — snapshots of the current network
- ``batfish_delete_snapshot``      — delete a snapshot
- ``batfish_upload_snapshot``      — upload a snapshot zip (base64-encoded)
- ``batfish_run_question``         — run any catalog question, incl. differential
- ``batfish_list_assertions``      — assertion registry with param descriptions
- ``batfish_run_assertion``        — validate + run one assertion on a snapshot
- ``batfish_run_assertion_suite``  — validate + run a YAML assertion suite
- ``batfish_fork_snapshot``        — fork a snapshot, optionally failing nodes
- ``batfish_get_topology``         — L3 / routing topology as a node/edge graph
"""

import base64
import binascii
import functools
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP

from . import (
    assertion_io,
    assertion_specs,
    assertions,
    catalog,
    presenter,
    queries,
    topology,
)
from .hints import TYPE_HINTS
from .session import get_session
from .utils import safe_snapshot_name

_INSTRUCTIONS = """\
This server exposes a curated catalog of 60 Batfish questions plus snapshot,
assertion, topology, and failure-testing tools. Workflow: call
batfish_list_questions (optionally filtered by category) to choose a question,
call batfish_get_question for its full description, output columns, and input
details, then batfish_run_question with its `fun` and an `options` dict.

Each catalog entry includes a one-line description, tags, synonyms, output
column names, a category description, required/optional inputs, and structured
type metadata (`fields`, `placeholder`, `example`) for complex values such as
HeaderConstraints. Inputs marked `required: true` must be supplied; other
inputs may be omitted. batfish_get_question adds long descriptions, typed
output columns, input descriptions, defaults, allowed values, and field specs.

batfish_list_questions works without a running Batfish; the metadata is checked
in and generated from Batfish templates plus the official docs.

Question selection guide:
- Snapshot health: initIssues, fileParseStatus, parseWarning.
- Configuration: nodeProperties, interfaceProperties, *Configuration,
  *Properties, namedStructures, definedStructures, unusedStructures.
- Topology: layer3Edges, userProvidedLayer1Edges; or batfish_get_topology.
- Routing: bgpSessionStatus, bgpSessionCompatibility, ospfSessionCompatibility,
  routes, bgpRib, lpmRoutes, testRoutePolicies, searchRoutePolicies.
- Forwarding: traceroute (one source), reachability (successful flows),
  bidirectionalReachability, detectLoops, *MultipathConsistency.
- Filters: testFilters (is traffic permitted?), searchFilters (find matches),
  findMatchingFilterLines, filterLineReachability (unreachable lines).
- Tunnels/VXLAN: ipsec*, vxlan*, evpn*.
- Specifier debugging: resolve*.
- Snapshot differences: any question with `supports_differential: true` can run
  with both `snapshot` and `reference_snapshot`; compareFilters and
  differentialReachability are differential-only.

Call batfish_list_snapshots before running questions, and pass `network` on
the first call if the server has no network set.
"""

mcp = FastMCP("batfish-q-mcp", instructions=_INSTRUCTIONS)

# Serializes tool calls: the singleton Session is shared state.
_lock = threading.RLock()


def _serialize_answer(answer: Any, fun: str, snapshot: Optional[str],
                      reference_snapshot: Optional[str]) -> dict:
    """Turns a pybatfish Answer into a JSON-safe dict for the LLM."""
    try:
        status = answer["status"] if answer is not None else None
    except Exception:
        status = None

    base = {
        "fun": fun,
        "snapshot": snapshot,
        "reference_snapshot": reference_snapshot,
        "status": status,
        "rows": [],
    }
    if answer is None or not answer:
        return base

    try:
        frame = answer.frame()
    except Exception:
        return {**base, "rows": getattr(answer, "rows", [])}

    frame, removed = presenter.format_result_lite(frame)
    try:
        rows = json.loads(frame.to_json(orient="records", date_format="iso"))
    except (TypeError, ValueError):
        # Nested dict/list cells pandas cannot JSON-encode natively.
        rows = json.loads(
            json.dumps(frame.to_dict(orient="records"), default=str)
        )
    return {**base, "rows": rows, "removed_columns": sorted(removed)}


def _ensure_snapshots(bf, *snapshots: Optional[str]) -> None:
    """Raise a clear error before Batfish fails on an unknown snapshot.

    Passing a nonexistent snapshot to a question otherwise surfaces as an
    opaque 500 ("Failed to queue work: No value present") with a Java stack
    trace. Listing snapshots first lets the caller see exactly what was
    requested and what is available.
    """
    requested = [s for s in snapshots if s is not None]
    if not requested:
        return

    available = bf.list_snapshots()
    missing = [s for s in requested if s not in available]
    if not missing:
        return

    network = getattr(bf, "network", None)
    subject = "snapshot" if len(missing) == 1 else "snapshots"
    names = ", ".join(repr(s) for s in missing)
    raise ValueError(
        f"{subject} {names} not found in network {network!r}; "
        f"available: {available}"
    )


_METADATA_PATH = Path(__file__).with_name("question_metadata.json")


@functools.lru_cache(maxsize=1)
def _question_metadata() -> dict[str, dict]:
    """Static per-question metadata generated from Batfish templates/docs.

    The file is checked in so question discovery (including output columns)
    works without a running Batfish. Regenerate it with
    ``scripts/generate_question_metadata.py``.
    """
    try:
        data = json.loads(_METADATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data.get("questions", {})


@functools.lru_cache(maxsize=1)
def _catalog_index() -> dict[str, tuple[dict, dict]]:
    """Map question ``fun`` to its ``(category, question)`` catalog entries."""
    index = {}
    for category in catalog.read_questions()["Batfish"]:
        for question in category["questions"]:
            index[question["fun"]] = (category, question)
    return index


def _required(inp: dict, meta_input: dict) -> bool:
    """Catalog required flag, preferring the live-template metadata."""
    if "required" in meta_input:
        return meta_input["required"]
    return not inp.get("optional", True)


def _detail_input(inp: dict, meta_input: dict) -> dict:
    """One catalog input plus its template metadata and TYPE_HINTS."""
    in_type = inp.get("type", "str")
    entry = {
        "name": inp["name"],
        "type": in_type,
        "required": _required(inp, meta_input),
    }
    if meta_input.get("display_name"):
        entry["display_name"] = meta_input["display_name"]
    if meta_input.get("description"):
        entry["description"] = meta_input["description"]
    if "default" in meta_input:
        entry["default"] = meta_input["default"]
    if "allowed_values" in meta_input:
        entry["allowed_values"] = meta_input["allowed_values"]
    if meta_input.get("field_specs"):
        entry["field_specs"] = meta_input["field_specs"]
    if "min_length" in meta_input:
        entry["min_length"] = meta_input["min_length"]
    entry.update(TYPE_HINTS.get(in_type, {}))
    return entry


def _summary_input(inp: dict, meta_input: dict) -> dict:
    """Compact input for the list tool: no descriptions or value domains."""
    in_type = inp.get("type", "str")
    entry = {
        "name": inp["name"],
        "type": in_type,
        "required": _required(inp, meta_input),
    }
    entry.update(TYPE_HINTS.get(in_type, {}))
    return entry


@mcp.tool()
def batfish_list_questions(category: Optional[str] = None) -> list[dict]:
    """List Batfish questions from the curated catalog.

    Returns one dict per question:
    ``{category, category_description, fun, name, description, tags,
    synonyms, supports_differential, differential_only, output_columns,
    inputs, type?}``.

    - ``description`` is a one-line summary; ``tags`` are Batfish's tags;
      ``synonyms`` are plain-language search terms.
    - ``output_columns`` lists only column names; call
      ``batfish_get_question`` for their types and descriptions.
    - Each input is ``{name, type, required, hint?, fields?, placeholder?,
      example?}``. ``required`` is true when the input must be supplied
      (not optional and has no default).
    - ``supports_differential`` is true when the question accepts a
      ``reference_snapshot``. ``differential_only`` marks the two questions
      (``compareFilters`` and ``differentialReachability``) that can only be
      run differentially; ``type`` is retained on those entries.

    Pass a category name to filter; omit it for the full catalog. This tool
    uses checked-in metadata and does not contact Batfish.
    """
    metadata = _question_metadata()
    questions = []
    for cat in catalog.read_questions()["Batfish"]:
        if category is not None and cat["category"] != category:
            continue
        for q in cat["questions"]:
            meta = metadata.get(q["fun"], {})
            meta_inputs = meta.get("inputs", {})
            inputs = [
                _summary_input(inp, meta_inputs.get(inp["name"], {}))
                for inp in q.get("input") or []
            ]
            differential_only = q.get("type") == "differential"
            # Batfish docs say most questions can run differentially and two
            # are differential-only. The catalog has no per-question exclusion
            # list, so every question is advertised as differential-capable.
            entry = {
                "category": cat["category"],
                "category_description": cat.get("description", ""),
                "fun": q["fun"],
                "name": q["name"],
                "description": meta.get("description", ""),
                "tags": meta.get("tags", []),
                "synonyms": meta.get("synonyms", []),
                "supports_differential": True,
                "differential_only": differential_only,
                "output_columns": [
                    column["name"]
                    for column in meta.get("output_columns", [])
                ],
                "inputs": inputs,
            }
            if q.get("type"):
                entry["type"] = q["type"]
            questions.append(entry)
    return questions


@mcp.tool()
def batfish_get_question(fun: str) -> dict:
    """Return full metadata for one question, including output columns.

    ``fun`` is a catalog question name (see ``batfish_list_questions``).

    Returns ``{category, category_description, fun, name, description,
    long_description, tags, synonyms, supports_differential,
    differential_only, output_columns, inputs, type?}``:

    - ``output_columns`` is a list of ``{name, type, description}`` objects
      describing the answer table.
    - each input includes its catalog ``type`` and ``required`` flag plus
      template details when available: ``description``, ``display_name``,
      ``default``, ``allowed_values`` (strings, or ``{value, description}``
      objects), ``field_specs`` for structured values, and ``min_length``.
    - ``TYPE_HINTS`` add ``hint``/``fields``/``placeholder``/``example`` for
      complex values such as HeaderConstraints.

    Raises ``ValueError`` for an unknown ``fun``, listing the known names.
    """
    index = _catalog_index()
    if fun not in index:
        known = ", ".join(sorted(index))
        raise ValueError(f"unknown question {fun!r}; known questions: {known}")

    cat, q = index[fun]
    meta = _question_metadata().get(fun, {})
    meta_inputs = meta.get("inputs", {})

    inputs = []
    seen = set()
    for inp in q.get("input") or []:
        seen.add(inp["name"])
        inputs.append(_detail_input(inp, meta_inputs.get(inp["name"], {})))
    # Surface template-only inputs if the catalog has drifted.
    for name, meta_input in meta_inputs.items():
        if name in seen:
            continue
        entry = {
            "name": name,
            "type": meta_input.get("type", "str"),
            "required": meta_input.get("required", False),
        }
        for key in (
            "display_name",
            "description",
            "default",
            "allowed_values",
            "field_specs",
            "min_length",
        ):
            if key in meta_input:
                entry[key] = meta_input[key]
        entry.update(TYPE_HINTS.get(entry["type"], {}))
        inputs.append(entry)

    differential_only = q.get("type") == "differential"
    result = {
        "category": cat["category"],
        "category_description": cat.get("description", ""),
        "fun": fun,
        "name": q["name"],
        "description": meta.get("description", ""),
        "long_description": meta.get("long_description", ""),
        "tags": meta.get("tags", []),
        "synonyms": meta.get("synonyms", []),
        "supports_differential": True,
        "differential_only": differential_only,
        "output_columns": meta.get("output_columns", []),
        "inputs": inputs,
        "metadata_complete": bool(meta),
    }
    if q.get("type"):
        result["type"] = q["type"]
    return result


@mcp.tool()
def batfish_list_snapshots(network: Optional[str] = None) -> list[str]:
    """List snapshots of a Batfish network.

    If ``network`` is given, the session is pointed at it first
    (``set_network``); otherwise the session's current network is used.
    """
    with _lock:
        bf = get_session()
        if network is not None:
            bf.set_network(network)
        return bf.list_snapshots()


@mcp.tool()
def batfish_delete_snapshot(
    name: str,
    network: Optional[str] = None,
) -> dict:
    """Delete a Batfish snapshot.

    - ``name``: the snapshot name to delete (sanitized with
      ``safe_snapshot_name``).
    - ``network``: the network the snapshot lives in; if given, the
      session is pointed at it first.

    Returns ``{name}`` — the name that was deleted.
    """
    with _lock:
        bf = get_session()
        if network is not None:
            bf.set_network(network)
        safe_name = safe_snapshot_name(name)
        _ensure_snapshots(bf, safe_name)
        bf.delete_snapshot(safe_name)
        return {"name": safe_name}


@mcp.tool()
def batfish_upload_snapshot(
    name: str,
    zip_base64: str,
    network: Optional[str] = None,
    overwrite: bool = False,
) -> dict:
    """Upload a Batfish snapshot zip, base64-encoded.

    The name is sanitized with ``safe_snapshot_name`` (Batfish allows only
    letters, digits, '-' and '_'). Unless ``overwrite`` is true, an
    existing snapshot of that name is kept and a unique name is chosen
    (``base``, ``base_1``, ...). If ``network`` is given,
    the session is pointed at it first. Returns the name actually used.
    """
    try:
        data = base64.b64decode(zip_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"zip_base64 is not valid base64: {e}") from e

    with _lock:
        bf = get_session()
        if network is not None:
            bf.set_network(network)

        safe_name = safe_snapshot_name(name)
        if not overwrite:
            existing = set(bf.list_snapshots())
            candidate, n = safe_name, 1
            while candidate in existing:
                candidate = f"{safe_name}_{n}"
                n += 1
            safe_name = candidate

        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        try:
            tmp.write(data)
            tmp.close()
            bf.init_snapshot(tmp.name, name=safe_name, overwrite=overwrite)
            return {"name": safe_name}
        finally:
            if not tmp.closed:
                tmp.close()
            os.unlink(tmp.name)


@mcp.tool()
def batfish_run_question(
    fun: str,
    options: Optional[dict] = None,
    snapshot: Optional[str] = None,
    reference_snapshot: Optional[str] = None,
    network: Optional[str] = None,
) -> dict:
    """Run a Batfish question and return its answer as JSON-safe rows.

    - ``fun`` is the pybatfish question function name (see
      ``batfish_list_questions``); ``options`` is its keyword-argument dict.
    - ``snapshot`` runs the question on that snapshot (the session's
      current snapshot otherwise).
    - ``reference_snapshot`` + ``snapshot`` runs a differential question
      comparing the two; both are required for differential questions.
    - ``network`` points the session at a network first, if given.
    - A named ``snapshot``/``reference_snapshot`` must exist in the network;
      otherwise a ``ValueError`` lists the available snapshots.

    Returns ``{fun, snapshot, reference_snapshot, status, rows,
    removed_columns}`` where ``rows`` is a list of row dicts.
    """
    with _lock:
        bf = get_session()
        if network is not None:
            bf.set_network(network)

        snapshots = None
        if reference_snapshot is not None:
            if snapshot is None:
                raise ValueError(
                    "reference_snapshot requires snapshot (the snapshot "
                    "being compared against the reference)"
                )
            snapshots = (reference_snapshot, snapshot)

        _ensure_snapshots(bf, snapshot, reference_snapshot)
        answer = queries.run_query(
            {"fun": fun, "options": options or {}},
            snapshots=snapshots,
            snapshot=snapshot,
        )
        return _serialize_answer(answer, fun, snapshot, reference_snapshot)


def _graph_to_dict(graph, kind: str, snapshot: Optional[str]) -> dict:
    """A networkx graph (topology.py) as a JSON-safe node/edge dict."""
    edges = []
    for source, target, attrs in graph.edges(data=True):
        edge = {"source": str(source), "target": str(target)}
        for key, value in attrs.items():
            edge[key] = str(value)
        edges.append(edge)
    return {
        "kind": kind,
        "snapshot": snapshot,
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "nodes": sorted(str(n) for n in graph.nodes()),
        "edges": edges,
    }


@functools.lru_cache(maxsize=1)
def _known_funs() -> frozenset[str]:
    """Question function names valid in an assertion's ``question`` block."""
    return frozenset(
        q["fun"]
        for c in catalog.read_questions()["Batfish"]
        for q in c["questions"]
    )


def _enriched_params(spec: dict) -> list[dict]:
    """Registry param specs, each with its TYPE_HINTS description."""
    out = []
    for p in spec.get("params") or []:
        entry = {
            "name": p["name"],
            "type": p.get("type", "str"),
            "optional": p.get("optional", True),
        }
        hints = TYPE_HINTS.get(p.get("type"))
        if hints:
            entry["hint"] = hints["hint"]
        out.append(entry)
    return out


def _answer_rows(answer: Any) -> list:
    """JSON-safe rows for an assertion's fetched answer (if any)."""
    if answer is None:
        return []
    frame = None
    if hasattr(answer, "frame"):
        try:
            frame = answer.frame()
        except Exception:
            frame = None
    if frame is None and hasattr(answer, "columns"):  # a DataFrame itself
        frame = answer
    if frame is not None:
        filtered, _ = presenter.format_result_lite(frame)
        try:
            return json.loads(
                filtered.to_json(orient="records", date_format="iso")
            )
        except (TypeError, ValueError):
            return json.loads(
                json.dumps(filtered.to_dict(orient="records"), default=str)
            )
    if isinstance(answer, list):
        return answer
    if isinstance(answer, dict):
        return [answer]
    return [{"value": str(answer)}]


def _result_to_dict(result: "assertions.AssertionResult", name: str) -> dict:
    """Flat JSON dict for an AssertionResult (raw answer rows included)."""
    out = {
        "type": name,
        "status": result.status,
        "message": result.message,
        "snapshot": result.snapshot or None,
        "ran_at": result.ran_at,
        "runtime": round(result.runtime, 3),
    }
    if result.fun:
        out["fun"] = result.fun
    rows = _answer_rows(result.answer)
    if rows:
        out["answer_rows"] = rows
    return out


@mcp.tool()
def batfish_list_assertions() -> dict:
    """List the assertions available (registry + live pybatfish names).

    Returns ``{snapshot, answer, specs}``:

    - ``snapshot`` / ``answer``: assertion names available in the installed
      pybatfish, grouped by how they are called (session-bound vs
      answer-based).
    - ``specs``: the curated registry ``{type: {label, description, group,
      phase, call, needs_question, params}}`` — each param is
      ``{name, type, optional, hint}``, where ``hint`` describes complex
      types (e.g. ``HeaderConstraints``) so you can build ``params``.
    """
    runtime = assertions.list_assertions()
    specs = {}
    for name, spec in assertion_specs.ASSERTION_SPECS.items():
        specs[name] = {
            "label": spec["label"],
            "description": spec["description"],
            "group": spec["group"],
            "phase": spec["phase"],
            "call": spec["call"],
            "needs_question": assertion_specs.needs_question(spec),
            "params": _enriched_params(spec),
        }
    return {
        "snapshot": runtime["snapshot"],
        "answer": runtime["answer"],
        "specs": specs,
    }


@mcp.tool()
def batfish_run_assertion(
    name: str,
    params: Optional[dict] = None,
    question: Optional[dict] = None,
    snapshot: Optional[str] = None,
    network: Optional[str] = None,
) -> dict:
    """Validate and run one assertion on a snapshot.

    - ``name``: assertion type (see ``batfish_list_assertions``).
    - ``params``: its parameters, e.g. ``{"headers": {"dstIps":
      "8.8.8.8"}}`` for ``assert_flows_succeed``.
    - ``question``: ``{"fun": ..., "options": {...}}`` — required for
      answer-based assertions (``assert_zero_results``, ``assert_num_results``,
      ``assert_min_results``); the question is run first and its answer checked.
    - ``snapshot`` / ``network``: run on that snapshot (and network first),
      otherwise the session's current ones.

    Returns ``{type, status (pass/fail/error), message, snapshot, ran_at,
    runtime, fun?, answer_rows?}``. Validation problems are returned as
    ``status: "error"`` with an ``errors`` list (nothing is run).
    """
    with _lock:
        bf = get_session()
        if network is not None:
            bf.set_network(network)
        _ensure_snapshots(bf, snapshot)

        item = {"type": name, "params": params or {}}
        if question:
            item["question"] = question

        errors = assertion_io.validate_assertion(item, _known_funs())
        if errors:
            return {
                "type": name,
                "status": assertions.ERROR,
                "message": "; ".join(errors),
                "errors": errors,
                "snapshot": snapshot,
            }
        return _result_to_dict(
            assertions.run_assertion_item(item, snapshot), name
        )


@mcp.tool()
def batfish_run_assertion_suite(
    suite_yaml: str,
    snapshot: Optional[str] = None,
    network: Optional[str] = None,
) -> dict:
    """Validate and run an assertion suite (YAML text) on a snapshot.

    ``suite_yaml`` uses the suite schema (see ``examples/assertions.yaml``)::

        version: 1
        assertions:
          - type: assert_flows_succeed
            params:
              startLocation: as1border1
              headers: {dstIps: 2.128.0.101}

    All-or-nothing: if the suite is invalid, nothing runs and the result is
    ``{results: [], errors: [...], summary: {...}}``. Otherwise results are
    ``{type, status, message, snapshot, ran_at, runtime, fun?,
    answer_rows?}`` per assertion, plus a ``summary`` of pass/fail/error counts.
    """
    with _lock:
        bf = get_session()
        if network is not None:
            bf.set_network(network)
        _ensure_snapshots(bf, snapshot)

        items, errors = assertion_io.load_assertions(suite_yaml, _known_funs())
        if errors:
            return {
                "results": [],
                "errors": errors,
                "summary": {"passed": 0, "failed": 0, "error": 0},
            }

        results = [
            _result_to_dict(
                assertions.run_assertion_item(item, snapshot), item["type"]
            )
            for item in items
        ]
        summary = {
            "passed": sum(1 for r in results if r["status"] == assertions.PASS),
            "failed": sum(1 for r in results if r["status"] == assertions.FAIL),
            "error": sum(1 for r in results if r["status"] == assertions.ERROR),
        }
        return {"results": results, "errors": [], "summary": summary}


@mcp.tool()
def batfish_fork_snapshot(
    base_name: str,
    name: Optional[str] = None,
    failed_nodes: Optional[list] = None,
    failed_interfaces: Optional[list] = None,
    overwrite: bool = False,
    network: Optional[str] = None,
) -> dict:
    """Fork a snapshot, optionally failing (deactivating) nodes/interfaces.

    - ``base_name``: the snapshot to fork.
    - ``name``: the fork's name (sanitized with ``safe_snapshot_name``);
      defaults to ``<base>_Fail``. Unless ``overwrite`` is true, an
      existing name is kept and a unique one is chosen
      (``name``, ``name_1``, ...).
    - ``failed_nodes`` / ``failed_interfaces``: names to deactivate in
      the fork (failure-testing flow: fork, then diff the
      fork against the base with a differential question).
    - ``network``: the network both snapshots live in, if given.

    Returns ``{name, base_name}`` — the name actually used.
    """
    with _lock:
        bf = get_session()
        if network is not None:
            bf.set_network(network)
        _ensure_snapshots(bf, base_name)

        final = safe_snapshot_name(name) if name is not None else safe_snapshot_name(base_name + "_Fail")
        if not overwrite:
            existing = set(bf.list_snapshots())
            candidate, n = final, 1
            while candidate in existing:
                candidate = f"{final}_{n}"
                n += 1
            final = candidate

        queries.fork_snapshot(
            base_name,
            failed_nodes=failed_nodes,
            failed_interfaces=failed_interfaces,
            name=final,
            overwrite=overwrite,
        )
        return {"name": final, "base_name": base_name}


@mcp.tool()
def batfish_get_topology(
    kind: str = "layer3",
    snapshot: Optional[str] = None,
    network: Optional[str] = None,
) -> dict:
    """Return the topology graph of a snapshot as nodes/edges.

    - ``kind="layer3"`` (default): L3 links from ``layer3Edges``
      (node — interface <-> interface — node, so edges carry a ``title``
      like ``"r1 (Gi0/0) <---> r2 (Gi0/0)"``).
    - ``kind="routing"``: routing adjacencies from ``bgpEdges``.

    Returns ``{kind, snapshot, node_count, edge_count, nodes, edges}``.
    """
    if kind not in ("layer3", "routing"):
        raise ValueError(f"kind must be 'layer3' or 'routing' (got {kind!r})")

    with _lock:
        bf = get_session()
        if network is not None:
            bf.set_network(network)
        _ensure_snapshots(bf, snapshot)
        if kind == "layer3":
            frame = queries.run_query(
                {"fun": "layer3Edges"}, snapshot=snapshot
            ).frame()
            graph = topology.get_topology(frame)
        else:
            frame = queries.run_query(
                {"fun": "bgpEdges"}, snapshot=snapshot
            ).frame()
            graph = topology.get_routing_topology(frame)
        return _graph_to_dict(graph, kind, snapshot)


def main() -> None:
    """Entry point for the MCP server (stdio transport by default)."""
    mcp.run()


if __name__ == "__main__":
    main()
