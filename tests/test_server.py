"""Tests for the MCP tool surface (batfish_mcp/server.py) using fakes.

No Batfish server is needed: a FakeBatfish is injected via
session.set_session(), and the tools are called through the FastMCP
registration (in-process) plus one in-memory MCP client round-trip.
"""

import asyncio
import base64
import io
import json
import zipfile

import pandas as pd
import pytest

from fakes import FakeAnswer, FakeBatfish, fake_session
from batfish_mcp import server
from batfish_mcp.session import clear_session, set_session


def _zip_bytes(names=("abc_net/configs/R1.cfg",)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, "hostname R1")
    return buf.getvalue()


@pytest.fixture
def fake():
    bf = FakeBatfish()
    set_session(bf)
    yield bf
    clear_session()


@pytest.fixture
def topo_fake():
    l3 = pd.DataFrame(
        [
            {
                "Interface": {"hostname": "r1", "interface": "Gi0/0"},
                "Remote_Interface": {"hostname": "r2", "interface": "Gi0/0"},
            }
        ]
    )
    bf = FakeBatfish(answers={"layer3Edges": FakeAnswer(l3)})
    set_session(bf)
    yield bf
    clear_session()


# ------------------------------------------------------------ questions catalog
def test_list_questions_without_a_server():
    clear_session()  # prove nothing touches Batfish
    questions = server.batfish_list_questions()
    assert len(questions) == 60
    assert questions[0]["fun"] == "initIssues"
    reachability = next(q for q in questions if q["fun"] == "reachability")
    inputs = {i["name"]: i for i in reachability["inputs"]}
    assert inputs["headers"]["type"] == "HeaderConstraints"
    assert "packet header constraints" in inputs["headers"]["hint"]

    topology = server.batfish_list_questions(category="Topology")
    assert topology and all(q["category"] == "Topology" for q in topology)
    assert server.batfish_list_questions(category="No such category") == []


# ------------------------------------------------------------------ snapshots
def test_list_and_upload_snapshot(fake):
    assert server.batfish_list_snapshots() == ["snapA", "snapB"]
    server.batfish_list_snapshots(network="OTHER")
    assert ("set_network", "OTHER") in fake.calls

    blob = base64.b64encode(_zip_bytes()).decode()
    result = server.batfish_upload_snapshot(name="abc net.zip", zip_base64=blob)
    assert result == {"name": "abc_net"}  # safe_snapshot_name applied
    assert "abc_net" in fake.list_snapshots()
    # second upload of the same name is unique-ified, Bat-Q style
    again = server.batfish_upload_snapshot(name="abc net.zip", zip_base64=blob)
    assert again == {"name": "abc_net_1"}

    with pytest.raises(ValueError, match="not valid base64"):
        server.batfish_upload_snapshot(name="x", zip_base64="!!! nope !!!")


# ---------------------------------------------------------------- run_question
def test_run_question_serializes_rows(fake):
    result = server.batfish_run_question(fun="nodeProperties", snapshot="snapA")
    assert result["fun"] == "nodeProperties"
    assert result["snapshot"] == "snapA"
    assert result["status"] is None  # FakeAnswer has no "status"
    assert result["rows"] == [{"Node": "r1"}, {"Node": "r2"}]
    assert result["removed_columns"] == []


def test_run_question_differential_and_validation(fake):
    server.batfish_run_question(
        fun="nodeProperties", snapshot="now", reference_snapshot="before"
    )
    assert fake.calls_named("answer")[-1][3] == {
        "snapshot": "now",
        "reference_snapshot": "before",
    }
    with pytest.raises(ValueError, match="reference_snapshot requires snapshot"):
        server.batfish_run_question(fun="nodeProperties", reference_snapshot="before")


def test_run_question_serializes_tuples_and_nan():
    df = pd.DataFrame({"A": [1, None], "B": [None, None]})
    set_session(
        FakeBatfish(answers={"nodeProperties": FakeAnswer(df)})
    )
    try:
        result = server.batfish_run_question(fun="nodeProperties")
        assert result["rows"] == [{"A": 1.0}, {"A": None}]
        assert result["removed_columns"] == ["B"]  # all-empty column dropped
    finally:
        clear_session()


# ------------------------------------------------------------------ assertions
def test_assertion_tools_on_fake_session():
    log, calls = [], []
    set_session(fake_session(log, calls))
    try:
        info = server.batfish_list_assertions()
        assert len(info["specs"]) == 16
        assert "batq_min_results" in info["answer"]
        flows = info["specs"]["assert_flows_succeed"]
        assert flows["needs_question"] is False
        assert flows["params"][1]["hint"]  # HeaderConstraints hint present

        result = server.batfish_run_assertion(
            name="assert_no_forwarding_loops", snapshot="snapA"
        )
        assert result["status"] == "fail"
        assert result["snapshot"] == "snapA"

        answer_based = server.batfish_run_assertion(
            name="batq_min_results",
            question={"fun": "bgpEdges"},
            params={"num": 2},
            snapshot="snapA",
        )
        assert answer_based["status"] == "pass"
        assert answer_based["fun"] == "bgpEdges"
        assert answer_based["answer_rows"]  # bgpEdges -> 3 fake rows
    finally:
        clear_session()


def test_run_assertion_validation_errors_run_nothing():
    set_session(fake_session([], []))
    try:
        result = server.batfish_run_assertion(
            name="assert_has_route", params={"node": "r1"}, snapshot="snapA"
        )
        assert result["status"] == "error"
        assert result["errors"]
        assert "type" not in result or result["type"] == "assert_has_route"
    finally:
        clear_session()


def test_suite_all_or_nothing_and_summary():
    set_session(fake_session([], []))
    try:
        good = (
            "version: 1\n"
            "assertions:\n"
            "  - type: assert_no_undefined_references\n"
            "  - type: assert_no_forwarding_loops\n"
        )
        result = server.batfish_run_assertion_suite(suite_yaml=good, snapshot="snapA")
        assert result["errors"] == []
        assert result["summary"] == {"passed": 1, "failed": 1, "error": 0}

        bad = "assertions:\n  - type: assert_nope\n"
        result = server.batfish_run_assertion_suite(suite_yaml=bad, snapshot="snapA")
        assert result["results"] == []
        assert result["errors"]
        assert result["summary"] == {"passed": 0, "failed": 0, "error": 0}
    finally:
        clear_session()


def test_suite_with_the_real_example_file():
    from fakes import ROOT

    set_session(fake_session([], []))
    try:
        text = (ROOT / "examples" / "assertions.yaml").read_text()
        result = server.batfish_run_assertion_suite(suite_yaml=text, snapshot="snapA")
        assert result["errors"] == []
        assert len(result["results"]) == 9
        assert result["summary"]["passed"] == 3  # fakes implement 3 as PASS
    finally:
        clear_session()


# ----------------------------------------------------------- fork + topology
def test_fork_snapshot_unique_names(fake):
    assert server.batfish_fork_snapshot(base_name="snapA") == {
        "name": "snapA_Fail",
        "base_name": "snapA",
    }
    again = server.batfish_fork_snapshot(base_name="snapA")
    assert again["name"] == "snapA_Fail_1"
    calls = fake.calls_named("fork_snapshot")
    assert calls[-1][1]["deactivate_nodes"] is None


def test_fork_snapshot_custom_and_failures(fake):
    result = server.batfish_fork_snapshot(
        base_name="snapA", name="my snap", failed_nodes=["r1"], network="OTHER"
    )
    assert result["name"] == "my_snap"
    assert ("set_network", "OTHER") in fake.calls
    call = fake.calls_named("fork_snapshot")[-1][1]
    assert call["deactivate_nodes"] == ["r1"]


def test_topology_layer3_and_routing(topo_fake):
    graph = server.batfish_get_topology(kind="layer3", snapshot="snapA")
    assert graph["nodes"] == ["r1", "r2"]
    assert graph["edges"] == [
        {
            "source": "r1",
            "target": "r2",
            "title": "r1 (Gi0/0) <---> r2 (Gi0/0)",
        }
    ]
    with pytest.raises(ValueError, match="layer3"):
        server.batfish_get_topology(kind="bogus")


# ------------------------------------------------------------------ wire test
def test_tools_round_trip_over_the_mcp_wire(fake):
    from fastmcp import Client

    async def run():
        async with Client(server.mcp) as client:
            names = [t.name for t in await client.list_tools()]
            assert "batfish_run_question" in names
            response = await client.call_tool(
                "batfish_run_question", {"fun": "nodeProperties", "snapshot": "snapA"}
            )
            payload = json.loads(response.content[0].text)
            assert payload["rows"] == [{"Node": "r1"}, {"Node": "r2"}]

    asyncio.run(run())
