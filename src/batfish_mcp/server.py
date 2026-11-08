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

Tool surface (derived from Bat-Q's GUI):

- ``batfish_list_questions``   — the curated question catalog (catalog.py)
- ``batfish_list_snapshots``   — snapshots of the current network
- ``batfish_upload_snapshot``  — upload a snapshot zip (base64-encoded)
- ``batfish_run_question``     — run any catalog question, incl. differential
"""

import base64
import binascii
import json
import os
import tempfile
import threading
from typing import Any, Optional

from fastmcp import FastMCP

from . import catalog, presenter, queries
from .hints import TYPE_HINTS
from .session import get_session
from .utils import safe_snapshot_name

mcp = FastMCP("batfish-mcp")

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


@mcp.tool()
def batfish_list_questions(category: Optional[str] = None) -> list[dict]:
    """List Batfish questions from the curated catalog (Bat-Q's bfqs.py).

    Returns one dict per question: ``{category, fun, name, type, inputs}``,
    where each input is ``{name, type, hint}`` (``hint`` comes from
    TYPE_HINTS and describes complex types like HeaderConstraints).
    Pass a category name to filter; omit it for the full catalog.
    """
    questions = []
    for cat in catalog.read_questions()["Batfish"]:
        if category is not None and cat["category"] != category:
            continue
        for q in cat["questions"]:
            inputs = []
            for inp in q.get("input") or []:
                in_type = inp.get("type", "str")
                entry = {"name": inp["name"], "type": in_type}
                hints = TYPE_HINTS.get(in_type)
                if hints:
                    entry["hint"] = hints["hint"]
                inputs.append(entry)
            entry = {
                "category": cat["category"],
                "fun": q["fun"],
                "name": q["name"],
                "inputs": inputs,
            }
            if q.get("type"):
                entry["type"] = q["type"]
            questions.append(entry)
    return questions


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
    (``base``, ``base_1``, ...), matching Bat-Q. If ``network`` is given,
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

    Returns ``{fun, snapshot, reference_snapshot, status, rows,
    removed_columns}`` where ``rows`` is a list of row dicts.
    """
    with _lock:
        if network is not None:
            get_session().set_network(network)

        snapshots = None
        if reference_snapshot is not None:
            if snapshot is None:
                raise ValueError(
                    "reference_snapshot requires snapshot (the snapshot "
                    "being compared against the reference)"
                )
            snapshots = (reference_snapshot, snapshot)

        answer = queries.run_query(
            {"fun": fun, "options": options or {}}, snapshots=snapshots
        )
        return _serialize_answer(answer, fun, snapshot, reference_snapshot)


def main() -> None:
    """Entry point for the MCP server (stdio transport by default)."""
    mcp.run()


if __name__ == "__main__":
    main()
