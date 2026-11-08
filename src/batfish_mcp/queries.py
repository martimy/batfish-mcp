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

from .session import get_session


def get_node_properties():
    bf = get_session()
    return bf.q.nodeProperties().answer().frame()["Node"]


def get_interface_properties():
    bf = get_session()
    return bf.q.interfaceProperties().answer().frame()["Interface"]


def set_snapshot(active_snapshot):
    bf = get_session()
    return bf.set_snapshot(active_snapshot)


def fork_snapshot(
    active_snapshot,
    failed_nodes=None,
    failed_interfaces=None,
    name=None,
    overwrite=True,
):
    """Forks a snapshot (optionally failing nodes/interfaces).

    ``name`` is the fork's name, defaulting to ``<base>_Fail`` (Bat-Q's
    convention); returns the name actually used.
    """
    bf = get_session()
    final_name = name if name is not None else active_snapshot + "_Fail"
    bf.fork_snapshot(
        base_name=active_snapshot,
        name=final_name,
        deactivate_nodes=failed_nodes,
        deactivate_interfaces=failed_interfaces,
        overwrite=overwrite,
    )
    return final_name


def run_query(question, snapshots=None, snapshot=None):
    """
    Runs a Batfish question and returns its answer.

    ``question`` is ``{"fun": <pybatfish question name>,
    "options": {<kwargs>}}``. ``snapshots`` is either ``None`` or a
    ``(reference_snapshot, snapshot)`` pair for differential questions.
    ``snapshot`` runs the question on that snapshot explicitly (the MCP
    server passes every snapshot this way, so no session state is needed).

    Errors (unknown question, bad options, connection failure, ...) are
    raised, not swallowed, so the caller can report them to the LLM
    instead of silently returning ``None``.
    """
    bf = get_session()
    fun = getattr(bf.q, question["fun"])
    qargs = question.get("options")

    if snapshots:
        if qargs:
            return fun(**qargs).answer(
                snapshot=snapshots[1], reference_snapshot=snapshots[0]
            )
        return fun().answer(
            snapshot=snapshots[1], reference_snapshot=snapshots[0]
        )

    if snapshot is not None:
        if qargs:
            return fun(**qargs).answer(snapshot=snapshot)
        return fun().answer(snapshot=snapshot)

    if qargs:
        return fun(**qargs).answer()
    return fun().answer()
