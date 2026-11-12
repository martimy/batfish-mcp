# -*- coding: utf-8 -*-
"""
Copyright 2026 Maen Artimy

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
from pybatfish.exception import BatfishException

from fakes import FakeAnswer, FakeBatfish, fake_session, trace
from batfish_mcp import server
from batfish_mcp.hints import TYPE_HINTS
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


def test_list_questions_exposes_p0_metadata():
    clear_session()
    questions = server.batfish_list_questions()
    by_fun = {q["fun"]: q for q in questions}

    reachability = by_fun["reachability"]
    assert reachability["category_description"]
    assert reachability["supports_differential"] is True
    assert reachability["differential_only"] is False
    headers = next(i for i in reachability["inputs"] if i["name"] == "headers")
    assert headers["required"] is False
    assert headers["fields"] and headers["placeholder"] and headers["example"]

    traceroute = {i["name"]: i for i in by_fun["traceroute"]["inputs"]}
    assert traceroute["startLocation"]["required"] is True
    assert traceroute["headers"]["required"] is True
    assert traceroute["maxTraces"]["required"] is False

    compare = by_fun["compareFilters"]
    assert compare["supports_differential"] is True
    assert compare["differential_only"] is True
    assert compare["type"] == "differential"

    aggregate = next(
        i for i in by_fun["parseWarning"]["inputs"]
        if i["name"] == "aggregateDuplicates"
    )
    assert aggregate["type"] == "bool"
    assert aggregate["placeholder"] == "True / False"

    matching = next(
        i for i in by_fun["findMatchingFilterLines"]["inputs"]
        if i["name"] == "headers"
    )
    assert matching["type"] == "HeaderConstraints"
    assert matching["fields"]

    filtered = server.batfish_list_questions(category="Packet Forwarding")
    assert len({q["category_description"] for q in filtered}) == 1
    assert all(
        q["category_description"] == reachability["category_description"]
        for q in filtered
    )


def test_server_instructions_guide_question_selection():
    instructions = server.mcp.instructions
    assert "batfish_list_questions" in instructions
    assert "batfish_get_question" in instructions
    assert "required: true" in instructions
    assert "reference_snapshot" in instructions


# ------------------------------------------------------- per-question metadata
def test_list_questions_includes_p1_discovery_metadata():
    clear_session()
    questions = server.batfish_list_questions()
    by_fun = {q["fun"]: q for q in questions}

    assert all(q["description"] for q in questions)
    assert all(isinstance(q["tags"], list) for q in questions)
    assert all(isinstance(q["synonyms"], list) for q in questions)

    traceroute = by_fun["traceroute"]
    assert traceroute["output_columns"] == ["Flow", "Traces", "TraceCount"]
    assert "trace path" in traceroute["synonyms"]

    reachability = by_fun["reachability"]
    assert "connectivity test" in reachability["synonyms"]
    assert "routing table" in by_fun["routes"]["synonyms"]

    # The compact list exposes names only, not per-column objects.
    assert all(
        isinstance(name, str)
        for q in questions
        for name in q["output_columns"]
    )


def test_get_question_returns_full_metadata():
    clear_session()
    traceroute = server.batfish_get_question("traceroute")

    assert traceroute["description"].startswith("Traces the path")
    assert "virtual traceroute" in traceroute["long_description"]
    assert traceroute["tags"] == ["dataplane", "traceroute"]
    assert traceroute["metadata_complete"] is True
    assert traceroute["output_columns"] == [
        {"name": "Flow", "type": "Flow", "description": "The flow"},
        {
            "name": "Traces",
            "type": "Set of Trace",
            "description": "The traces for this flow",
        },
        {
            "name": "TraceCount",
            "type": "int",
            "description": "The total number traces for this flow",
        },
    ]

    inputs = {i["name"]: i for i in traceroute["inputs"]}
    assert inputs["startLocation"]["required"] is True
    assert inputs["startLocation"]["description"]
    assert inputs["headers"]["field_specs"]["dstIps"]["optional"] is False
    assert inputs["headers"]["fields"]  # TYPE_HINTS still present


def test_differential_reachability_labels_match_arguments():
    clear_session()
    question = server.batfish_get_question("differentialReachability")
    columns = {c["name"]: c["description"] for c in question["output_columns"]}

    assert "snapshot argument" in columns["Snapshot_Traces"]
    assert "snapshot argument" in columns["Snapshot_TraceCount"]
    assert "reference_snapshot" in columns["Reference_Traces"]
    assert "reference_snapshot" in columns["Reference_TraceCount"]
    assert all("BASE" not in text and "DELTA" not in text for text in columns.values())


def test_get_question_exposes_defaults_and_allowed_values():
    clear_session()
    routes = server.batfish_get_question("routes")
    inputs = {i["name"]: i for i in routes["inputs"]}
    assert inputs["rib"]["allowed_values"] == ["main", "bgp", "evpn"]
    assert inputs["prefixMatchType"]["default"] == "EXACT"
    assert inputs["prefixMatchType"]["allowed_values"] == [
        "EXACT",
        "LONGEST_PREFIX_MATCH",
        "LONGER_PREFIXES",
        "SHORTER_PREFIXES",
    ]

    policies = server.batfish_get_question("testRoutePolicies")
    direction = next(
        i for i in policies["inputs"] if i["name"] == "direction"
    )
    assert direction["allowed_values"] == [
        {
            "value": "in",
            "description": "The route is inbound to the device",
        },
        {
            "value": "out",
            "description": "The route is outbound from the device",
        },
    ]

    # Enum-like specifiers without template ``values`` come from Batfish's
    # documented parameter grammar.
    bgp_status = next(
        i
        for i in server.batfish_get_question("bgpSessionStatus")["inputs"]
        if i["name"] == "status"
    )
    assert [v["value"] for v in bgp_status["allowed_values"]] == [
        "NOT_COMPATIBLE",
        "NOT_ESTABLISHED",
        "ESTABLISHED",
    ]
    assert all(v["description"] for v in bgp_status["allowed_values"])

    search_action = next(
        i
        for i in server.batfish_get_question("searchFilters")["inputs"]
        if i["name"] == "action"
    )
    assert [v["value"] for v in search_action["allowed_values"]] == [
        "permit",
        "deny",
        "matchLine <line number>",
    ]

    reachability = server.batfish_get_question("reachability")
    actions = next(
        i for i in reachability["inputs"] if i["name"] == "actions"
    )
    assert actions["default"] == "success"
    assert "failure" in [v["value"] for v in actions["allowed_values"]]


def test_get_question_uses_corrected_catalog_inputs():
    clear_session()
    by_fun = {
        q["fun"]: {i["name"]: i for i in q["inputs"]}
        for q in (
            server.batfish_get_question(fun)
            for fun in (
                "ipOwners",
                "searchRoutePolicies",
                "filterLineReachability",
                "resolveFilterSpecifier",
                "testFilters",
            )
        )
    }

    assert "ips" in by_fun["ipOwners"]
    assert "pathOption" in by_fun["searchRoutePolicies"]
    assert "nodes" in by_fun["filterLineReachability"]
    assert "filters" in by_fun["resolveFilterSpecifier"]
    assert "filter" not in by_fun["resolveFilterSpecifier"]
    assert by_fun["testFilters"]["headers"]["required"] is True


def test_get_question_unknown_name_lists_known_questions():
    clear_session()
    with pytest.raises(ValueError, match="unknown question 'nope'"):
        server.batfish_get_question("nope")


def test_static_metadata_covers_the_catalog_and_has_no_gaps():
    clear_session()
    metadata = server._question_metadata()
    catalog_funs = {
        q["fun"]
        for c in server.catalog.read_questions()["Batfish"]
        for q in c["questions"]
    }

    assert set(metadata) == catalog_funs
    for fun, meta in metadata.items():
        assert meta["description"], fun
        assert meta["tags"], fun
        assert meta["synonyms"], fun
        assert meta["output_columns"], fun
        for column in meta["output_columns"]:
            assert set(column) == {"name", "type", "description"}
            assert column["name"], fun
            assert column["type"], fun
            assert column["description"], fun

        question = next(
            q
            for c in server.catalog.read_questions()["Batfish"]
            for q in c["questions"]
            if q["fun"] == fun
        )
        catalog_inputs = {i["name"] for i in question.get("input") or []}
        assert catalog_inputs == set(meta["inputs"]), fun
        for name, inp in meta["inputs"].items():
            catalog_input = next(
                i for i in question["input"] if i["name"] == name
            )
            assert inp["required"] == (
                not catalog_input.get("optional", True)
            ), (fun, name)



# ------------------------------------------------------------ question search
def _search_funs(query, limit=5):
    result = server.batfish_find_questions(query, limit=limit)
    return [entry["fun"] for entry in result["results"]]


def test_find_questions_maps_plain_language_to_catalog_entries():
    clear_session()

    standby = _search_funs("standby gateway")
    assert {"hsrpProperties", "vrrpProperties"} <= set(standby[:2])

    assert _search_funs("traffic going in circles")[0] == "detectLoops"
    assert _search_funs("routing table")[0] == "routes"
    assert _search_funs("duplicate IP address")[0] == "ipOwners"
    assert (
        _search_funs("which firewall rule is blocking this connection")[0]
        == "testFilters"
    )
    assert (
        _search_funs("are any firewall rules impossible to hit")[0]
        == "filterLineReachability"
    )

    sessions = _search_funs("are the routers exchanging routes")
    assert {"bgpSessionStatus", "ospfSessionCompatibility"} <= set(
        sessions[:2]
    )


def test_find_questions_returns_explainable_compact_results():
    clear_session()
    result = server.batfish_find_questions("BGP session not established")

    assert result["query"] == "BGP session not established"
    assert result["count"] == len(result["results"])
    assert result["total_matches"] >= result["count"]

    top = result["results"][0]
    assert top["fun"] == "bgpSessionStatus"
    assert set(top) == {
        "fun",
        "name",
        "category",
        "description",
        "tags",
        "synonyms",
        "supports_differential",
        "differential_only",
        "score",
        "matched_on",
    }
    assert top["score"] > 0
    assert top["matched_on"]
    assert top["supports_differential"] is True
    assert top["differential_only"] is False


def test_find_questions_limit_and_validation():
    clear_session()
    limited = server.batfish_find_questions("filter", limit=2)
    assert len(limited["results"]) == 2

    with pytest.raises(ValueError, match="query must not be empty"):
        server.batfish_find_questions("   ")
    with pytest.raises(ValueError, match="limit must be at least 1"):
        server.batfish_find_questions("filter", limit=0)


def test_find_questions_unknown_query_returns_empty_results():
    clear_session()
    result = server.batfish_find_questions("zzzz_unfindable_zzzz")
    assert result == {
        "query": "zzzz_unfindable_zzzz",
        "count": 0,
        "total_matches": 0,
        "results": [],
    }


# ------------------------------------------------------------------ snapshots
def test_list_and_upload_snapshot(fake):
    assert server.batfish_list_snapshots() == ["snapA", "snapB"]
    server.batfish_list_snapshots(network="OTHER")
    assert ("set_network", "OTHER") in fake.calls

    blob = base64.b64encode(_zip_bytes()).decode()
    result = server.batfish_upload_snapshot(name="abc net.zip", zip_base64=blob)
    assert result == {"name": "abc_net"}  # safe_snapshot_name applied
    assert "abc_net" in fake.list_snapshots()
    # second upload of the same name is unique-ified
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
        fun="nodeProperties", snapshot="snapB", reference_snapshot="snapA"
    )
    assert fake.calls_named("answer")[-1][3] == {
        "snapshot": "snapB",
        "reference_snapshot": "snapA",
    }
    with pytest.raises(ValueError, match="reference_snapshot requires snapshot"):
        server.batfish_run_question(fun="nodeProperties", reference_snapshot="snapA")


def test_differential_reachability_maps_trace_columns(fake):
    result = server.batfish_run_question(
        fun="differentialReachability",
        snapshot="snapB",
        reference_snapshot="snapA",
    )
    assert result["trace_columns"] == {
        "Snapshot_Traces": "snapB",
        "Reference_Traces": "snapA",
    }

    single = server.batfish_run_question(fun="differentialReachability", snapshot="snapB")
    assert "trace_columns" not in single


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


def test_trace_detail_summarizes_and_none_drops_trace_columns():
    df = pd.DataFrame(
        {
            "Flow": ["f1"],
            "Traces": [[trace("DENIED_IN", "fw", {"filter": "INSIDE-INBOUND"})]],
            "TraceCount": [1],
        }
    )
    set_session(FakeBatfish(answers={"traceroute": FakeAnswer(df)}))
    try:
        summary = server.batfish_run_question(fun="traceroute", snapshot="snapA")
        assert summary["rows"] == [
            {
                "Flow": "f1",
                "Traces": [
                    {
                        "disposition": "DENIED_IN",
                        "path": ["fw"],
                        "hop_count": 1,
                    }
                ],
                "TraceCount": 1,
            }
        ]
        assert summary["total_rows"] == 1
        assert summary["returned_rows"] == 1
        assert summary["truncated"] is False

        full = server.batfish_run_question(
            fun="traceroute", snapshot="snapA", trace_detail="full"
        )
        assert full["rows"][0]["Traces"][0]["hops"][0]["node"]["name"] == "fw"

        none = server.batfish_run_question(
            fun="traceroute", snapshot="snapA", trace_detail="none"
        )
        assert none["rows"] == [{"Flow": "f1", "TraceCount": 1}]
        assert none["removed_columns"] == ["Traces"]
    finally:
        clear_session()


def test_run_question_caps_rows_and_reports_truncation():
    df = pd.DataFrame({"A": list(range(5))})
    set_session(FakeBatfish(answers={"nodeProperties": FakeAnswer(df)}))
    try:
        result = server.batfish_run_question(
            fun="nodeProperties", snapshot="snapA", max_rows=2
        )
        assert [row["A"] for row in result["rows"]] == [0, 1]
        assert result["total_rows"] == 5
        assert result["returned_rows"] == 2
        assert result["truncated"] is True

        unlimited = server.batfish_run_question(
            fun="nodeProperties", snapshot="snapA", max_rows=0
        )
        assert len(unlimited["rows"]) == 5
        assert unlimited["truncated"] is False
    finally:
        clear_session()


def test_run_question_validates_trace_detail_and_max_rows(fake):
    bad_trace = server.batfish_run_question(
        fun="nodeProperties", snapshot="snapA", trace_detail="everything"
    )
    assert bad_trace["status"] == "error"
    assert bad_trace["code"] == "INVALID_ARGUMENT"
    assert "trace_detail" in bad_trace["message"]

    bad_rows = server.batfish_run_question(
        fun="nodeProperties", snapshot="snapA", max_rows=-1
    )
    assert bad_rows["status"] == "error"
    assert bad_rows["code"] == "INVALID_ARGUMENT"
    assert "max_rows" in bad_rows["message"]
    assert fake.calls_named("answer") == []


def test_bidirectional_reachability_requires_dst_ips(fake):
    result = server.batfish_run_question(
        fun="bidirectionalReachability",
        options={
            "pathConstraints": {
                "startLocation": "server_1",
                "endLocation": "server_2",
            },
            "headers": {},
        },
        snapshot="snapA",
    )
    assert result["status"] == "error"
    assert result["code"] == "BIDIRECTIONAL_REQUIRES_DST"
    assert "dstIps" in result["message"]
    assert result["rows"] == []
    assert fake.calls_named("answer") == []  # rejected before queueing

    ok = server.batfish_run_question(
        fun="bidirectionalReachability",
        options={
            "pathConstraints": {
                "startLocation": "server_1",
                "endLocation": "server_2",
            },
            "headers": {"srcIps": "server_1", "dstIps": "server_2"},
        },
        snapshot="snapA",
    )
    assert ok["status"] is None
    assert fake.answers_for("bidirectionalReachability")[-1][0] == {
        "pathConstraints": {
            "startLocation": "server_1",
            "endLocation": "server_2",
        },
        "headers": {"srcIps": "server_1", "dstIps": "server_2"},
    }


def test_max_traces_is_an_integer_in_the_catalog():
    clear_session()
    questions = server.batfish_list_questions()
    max_traces = [
        (question["fun"], inp)
        for question in questions
        for inp in question["inputs"]
        if inp["name"] == "maxTraces"
    ]
    assert len(max_traces) == 7
    assert all(inp["type"] == "int" for _, inp in max_traces)

    for fun, _ in max_traces:
        inputs = {
            i["name"]: i
            for i in server.batfish_get_question(fun)["inputs"]
        }
        assert inputs["maxTraces"]["type"] == "int", fun
        assert inputs["maxTraces"]["hint"] == "Integer value."


def test_max_traces_accepts_an_int_and_rejects_a_string(fake):
    ok = server.batfish_run_question(
        fun="reachability",
        options={"maxTraces": 1},
        snapshot="snapA",
    )
    assert ok["status"] is None
    assert fake.answers_for("reachability")[-1][0] == {"maxTraces": 1}

    bad = server.batfish_run_question(
        fun="reachability",
        options={"maxTraces": "1"},
        snapshot="snapA",
    )
    assert bad["status"] == "error"
    assert bad["code"] == "INVALID_ARGUMENT"
    assert "must be an integer" in bad["message"]
    assert len(fake.answers_for("reachability")) == 1  # nothing queued


def test_header_hints_document_node_names_and_string_ports():
    headers = TYPE_HINTS["HeaderConstraints"]
    assert "node names" in headers["hint"]
    assert "server_2" in headers["placeholder"]
    assert "server_1" in headers["placeholder"]
    assert "'443'" in headers["example"]
    assert "string port spec" in headers["field_types"]["dstPorts"]
    assert "ICMP" in headers["hint"]
    assert "representative flows" in headers["hint"]
    assert "network or broadcast" in headers["hint"]
    assert "explicit host IPs" in headers["hint"]

    paths = TYPE_HINTS["PathConstraints"]
    assert "node names" in paths["hint"]
    assert "representative flows" in paths["hint"]
    assert "network-address" in paths["hint"]


def test_instructions_warn_about_representative_flows():
    instructions = server.mcp.instructions
    assert "representative flows" in instructions
    assert "network or broadcast" in instructions
    assert "explicit host IPs" in instructions


def test_port_fields_accept_strings_and_reject_numbers(fake):
    ok = server.batfish_run_question(
        fun="traceroute",
        options={
            "startLocation": "server_1",
            "headers": {"dstIps": "8.8.8.8", "dstPorts": "443"},
        },
        snapshot="snapA",
    )
    assert ok["status"] is None
    assert fake.answers_for("traceroute")[-1][0]["headers"]["dstPorts"] == "443"

    bad = server.batfish_run_question(
        fun="traceroute",
        options={
            "startLocation": "server_1",
            "headers": {"dstIps": "8.8.8.8", "dstPorts": 443},
        },
        snapshot="snapA",
    )
    assert bad["status"] == "error"
    assert bad["code"] == "INVALID_PORT_SPEC"
    assert "string port spec" in bad["message"]
    assert len(fake.answers_for("traceroute")) == 1  # nothing queued


def test_batfish_errors_become_structured_and_debug_keeps_the_log(fake):
    log = (
        "Work terminated abnormally\n"
        "work_item: ...\n"
        "Caused by: java.io.InvalidClassException: "
        "org.batfish.representation.cisco.CiscoConfiguration; "
        "local class incompatible: stream classdesc serialVersionUID = 1, "
        "local class serialVersionUID = 2"
    )
    fake.answers["initIssues"] = BatfishException(log)

    result = server.batfish_run_question(fun="initIssues", snapshot="snapA")
    assert result["status"] == "error"
    assert result["code"] == "SNAPSHOT_INCOMPATIBLE"
    assert "re-upload" in result["message"]
    assert "InvalidClassException" not in result["message"]
    assert "debug" not in result

    debug = server.batfish_run_question(
        fun="initIssues", snapshot="snapA", debug=True
    )
    assert debug["code"] == "SNAPSHOT_INCOMPATIBLE"
    assert debug["debug"]["exception"] == "BatfishException"
    assert "InvalidClassException" in debug["debug"]["log"]

    assertion = server.batfish_run_assertion(
        name="assert_zero_results",
        question={"fun": "initIssues"},
        snapshot="snapA",
        debug=True,
    )
    assert assertion["status"] == "error"
    assert assertion["code"] == "SNAPSHOT_INCOMPATIBLE"
    assert "InvalidClassException" in assertion["debug"]["raw_message"]


def test_assertion_descriptions_document_the_icmp_default():
    clear_session()
    specs = server.batfish_list_assertions()["specs"]
    for name in (
        "assert_flows_succeed",
        "assert_flows_fail",
        "assert_filter_permits",
        "assert_filter_denies",
    ):
        assert "ICMP" in specs[name]["description"], name
        assert "dstPorts" in specs[name]["description"], name


def test_delete_snapshot_removes_the_snapshot(fake):
    fake.networks["NETWORK"] = ["abc_net_1", "abc_net"]
    assert server.batfish_delete_snapshot(name="abc_net_1") == {
        "name": "abc_net_1"
    }
    assert fake.list_snapshots() == ["abc_net"]
    assert fake.calls_named("delete_snapshot") == [
        ("delete_snapshot", "abc_net_1")
    ]


def test_missing_snapshot_is_reported_before_running(fake):
    with pytest.raises(ValueError) as exc:
        server.batfish_run_question(fun="nodeProperties", snapshot="abc_net")
    message = str(exc.value)
    assert "snapshot 'abc_net' not found in network 'NETWORK'" in message
    assert "available: ['snapA', 'snapB']" in message
    assert fake.calls_named("answer") == []  # nothing was sent to Batfish

    with pytest.raises(ValueError) as exc:
        server.batfish_run_question(
            fun="nodeProperties", snapshot="snapA", reference_snapshot="abc_net"
        )
    assert "snapshot 'abc_net' not found" in str(exc.value)


def test_missing_snapshot_reports_the_network(fake):
    with pytest.raises(ValueError) as exc:
        server.batfish_run_question(
            fun="nodeProperties", snapshot="snapA", network="LAN"
        )
    message = str(exc.value)
    assert "snapshot 'snapA' not found in network 'LAN'" in message
    assert "available: []" in message
    assert ("set_network", "LAN") in fake.calls


def test_snapshot_precheck_applies_to_all_snapshot_tools(fake):
    calls = [
        lambda: server.batfish_get_topology(kind="layer3", snapshot="nope"),
        lambda: server.batfish_run_assertion(
            name="assert_no_forwarding_loops", snapshot="nope"
        ),
        lambda: server.batfish_run_assertion_suite(
            suite_yaml="version: 1\nassertions: []", snapshot="nope"
        ),
        lambda: server.batfish_fork_snapshot(base_name="nope"),
        lambda: server.batfish_delete_snapshot(name="nope"),
    ]
    for call in calls:
        with pytest.raises(ValueError) as exc:
            call()
        assert "snapshot 'nope' not found in network 'NETWORK'" in str(exc.value)


# ------------------------------------------------------------------ assertions
def test_assertion_tools_on_fake_session():
    log, calls = [], []
    set_session(fake_session(log, calls))
    try:
        info = server.batfish_list_assertions()
        assert len(info["specs"]) == 16
        assert "assert_min_results" in info["answer"]
        flows = info["specs"]["assert_flows_succeed"]
        assert flows["needs_question"] is False
        assert flows["params"][1]["hint"]  # HeaderConstraints hint present

        result = server.batfish_run_assertion(
            name="assert_no_forwarding_loops", snapshot="snapA"
        )
        assert result["status"] == "fail"
        assert result["snapshot"] == "snapA"

        answer_based = server.batfish_run_assertion(
            name="assert_min_results",
            question={"fun": "bgpEdges"},
            params={"num": 2},
            snapshot="snapA",
        )
        assert answer_based["status"] == "pass"
        assert answer_based["fun"] == "bgpEdges"
        assert answer_based["answer_rows"]  # bgpEdges -> 3 fake rows
    finally:
        clear_session()


def test_assertion_failure_is_short_and_structured():
    from pybatfish.exception import BatfishAssertException

    log, calls = [], []
    session = fake_session(log, calls)

    def fail(**kwargs):
        assert kwargs["df_format"] == "records"
        raise BatfishAssertException(
            "Found undefined reference(s), when none were expected\n"
            "[{'Node': 'r1', 'Structure_Type': 'acl', 'Structure_Name': 'X'}]"
        )

    session.asserts.assert_no_undefined_references = fail
    set_session(session)
    try:
        result = server.batfish_run_assertion(
            name="assert_no_undefined_references", snapshot="snapA"
        )
        assert result["status"] == "fail"
        assert result["message"] == (
            "Found undefined reference(s), when none were expected"
        )
        assert result["answer_rows"] == [
            {"Node": "r1", "Structure_Type": "acl", "Structure_Name": "X"}
        ]
        assert "details" not in result
    finally:
        clear_session()


def test_assertion_failure_keeps_unparseable_details_but_caps_them():
    from pybatfish.exception import BatfishAssertException

    log, calls = [], []
    session = fake_session(log, calls)

    def fail(**kwargs):
        raise BatfishAssertException(
            "Found duplicate router-id(s), when none were expected\n"
            "BGP: [{'Node': 'r1'}]"
        )

    session.asserts.assert_no_duplicate_router_ids = fail
    set_session(session)
    try:
        result = server.batfish_run_assertion(
            name="assert_no_duplicate_router_ids", snapshot="snapA"
        )
        assert result["message"] == (
            "Found duplicate router-id(s), when none were expected"
        )
        assert result["details"] == "BGP: [{'Node': 'r1'}]"
        assert "answer_rows" not in result
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
    fake.networks["OTHER"] = ["snapA"]
    result = server.batfish_fork_snapshot(
        base_name="snapA", name="my snap", failed_nodes=["r1"], network="OTHER"
    )
    assert result["name"] == "my_snap"
    assert ("set_network", "OTHER") in fake.calls
    call = fake.calls_named("fork_snapshot")[-1][1]
    assert call["deactivate_nodes"] == ["r1"]


def test_fork_snapshot_normalizes_failed_interfaces(fake):
    from pybatfish.datamodel.primitives import Interface

    result = server.batfish_fork_snapshot(
        base_name="snapA",
        failed_interfaces=[
            "r1[Gi0/0]",
            {"hostname": "r2", "interface": "Gi0/1"},
        ],
    )
    assert result["name"] == "snapA_Fail"
    call = fake.calls_named("fork_snapshot")[-1][1]
    sent = call["deactivate_interfaces"]
    assert all(isinstance(item, Interface) for item in sent)
    assert [str(item) for item in sent] == ["r1[Gi0/0]", "r2[Gi0/1]"]


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
            names = {t.name for t in await client.list_tools()}
            assert names == {
                "batfish_find_questions",
                "batfish_list_questions",
                "batfish_get_question",
                "batfish_list_snapshots",
                "batfish_delete_snapshot",
                "batfish_upload_snapshot",
                "batfish_run_question",
                "batfish_list_assertions",
                "batfish_run_assertion",
                "batfish_run_assertion_suite",
                "batfish_fork_snapshot",
                "batfish_get_topology",
            }
            response = await client.call_tool(
                "batfish_run_question", {"fun": "nodeProperties", "snapshot": "snapA"}
            )
            payload = json.loads(response.content[0].text)
            assert payload["rows"] == [{"Node": "r1"}, {"Node": "r2"}]

    asyncio.run(run())
