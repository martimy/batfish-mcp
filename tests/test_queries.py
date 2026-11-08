"""Tests for batfish_mcp/queries.py with a fake Batfish session (injected
via session.set_session()). Note: run_query raises on error instead of
returning None, so MCP tools can report failures to the LLM.
"""

from types import SimpleNamespace

import pandas as pd
import pytest

from batfish_mcp import queries as Q
from batfish_mcp.session import clear_session, get_session, set_session


class Recorder:
    """Callable question: records how it was called and what answer() got."""

    def __init__(self, frame=None, boom=None):
        self.calls, self.answers = [], []
        self.frame, self.boom = frame, boom

    def __call__(self, **kw):
        self.calls.append(kw)
        return self

    def answer(self, **kw):
        self.answers.append(kw)
        if self.boom:
            raise self.boom
        return SimpleNamespace(frame=lambda: self.frame, kw=kw)


@pytest.fixture
def bf():
    session = SimpleNamespace(
        q=SimpleNamespace(
            nodeProperties=Recorder(pd.DataFrame({"Node": ["r1", "r2"]})),
            interfaceProperties=Recorder(pd.DataFrame({"Interface": ["r1[Gi0]"]})),
            bgpEdges=Recorder(),
            broken=Recorder(boom=RuntimeError("server error")),
        ),
        set_snapshot=lambda name: f"set:{name}",
        forks=[],
    )
    session.fork_snapshot = lambda **kw: session.forks.append(kw)
    set_session(session)
    yield session
    clear_session()


# ------------------------------------------------------------------ session
def test_session_set_get_clear_round_trip(bf):
    assert get_session() is bf
    clear_session()
    # after clear, set a different fake and it is returned as-is
    other = SimpleNamespace()
    set_session(other)
    assert get_session() is other


# ---------------------------------------------------------------- helpers
def test_node_and_interface_properties_return_one_column(bf):
    assert Q.get_node_properties().tolist() == ["r1", "r2"]
    assert Q.get_interface_properties().tolist() == ["r1[Gi0]"]


def test_set_snapshot_passes_through(bf):
    assert Q.set_snapshot("snap1") == "set:snap1"


def test_fork_snapshot_names_the_fork_and_overwrites(bf):
    assert Q.fork_snapshot("snap1", failed_nodes=["r1"]) == "snap1_Fail"
    assert bf.forks == [
        {
            "base_name": "snap1",
            "name": "snap1_Fail",
            "deactivate_nodes": ["r1"],
            "deactivate_interfaces": None,
            "overwrite": True,
        }
    ]


def test_fork_snapshot_custom_name_and_no_overwrite(bf):
    assert Q.fork_snapshot("snap1", name="my_fork", overwrite=False) == "my_fork"
    assert bf.forks[-1]["overwrite"] is False


# ---------------------------------------------------------------- run_query
def test_run_query_without_options(bf):
    answer = Q.run_query({"fun": "nodeProperties"})
    assert answer.kw == {}
    assert bf.q.nodeProperties.calls == [{}]


def test_run_query_passes_options_to_the_question(bf):
    Q.run_query({"fun": "bgpEdges", "options": {"nodes": "r1"}})
    assert bf.q.bgpEdges.calls == [{"nodes": "r1"}]
    assert bf.q.bgpEdges.answers == [{}]


def test_run_query_with_single_snapshot_passes_it_to_answer(bf):
    """The MCP server passes the snapshot explicitly (no session state)."""
    Q.run_query({"fun": "bgpEdges"}, snapshot="changed")
    assert bf.q.bgpEdges.answers == [{"snapshot": "changed"}]
    Q.run_query({"fun": "bgpEdges", "options": {"nodes": "r1"}}, snapshot="s")
    assert bf.q.bgpEdges.calls[-1] == {"nodes": "r1"}
    assert bf.q.bgpEdges.answers[-1] == {"snapshot": "s"}


def test_run_query_with_two_snapshots_compares_second_to_first(bf):
    """snapshots = (reference, current): current is the one being analysed."""
    Q.run_query({"fun": "bgpEdges"}, snapshots=("base", "changed"))
    assert bf.q.bgpEdges.answers == [
        {"snapshot": "changed", "reference_snapshot": "base"}
    ]
    Q.run_query({"fun": "bgpEdges", "options": {"nodes": "r1"}}, ("a", "b"))
    assert bf.q.bgpEdges.calls[-1] == {"nodes": "r1"}
    assert bf.q.bgpEdges.answers[-1] == {"snapshot": "b", "reference_snapshot": "a"}


def test_run_query_raises_when_the_question_fails(bf):
    with pytest.raises(RuntimeError, match="server error"):
        Q.run_query({"fun": "broken"})


def test_run_query_raises_for_an_unknown_question(bf):
    with pytest.raises(AttributeError):
        Q.run_query({"fun": "doesNotExist"})
