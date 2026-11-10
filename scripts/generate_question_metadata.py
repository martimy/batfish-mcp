#!/usr/bin/env python3
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

"""
Generate ``src/batfish_mcp/question_metadata.json``.

The catalog (``catalog.py``) owns the curated category/name/order structure.
This script enriches each catalog question with details that Batfish already
publishes, so the MCP server can answer discovery questions without guessing:

- one-line and long descriptions, tags, and differential support
  (from the live ``bf.q.<fun>.template``)
- input descriptions, accurate types, defaults, allowed values, and structured
  field specs (from each variable in the template)
- output columns (from the "Return Value" tables in the Batfish docs notebooks)
- curated plain-language synonyms for search/discovery

The generated file is checked in; regenerating it requires a running Batfish.

Usage::

    .mcp/bin/python3 scripts/generate_question_metadata.py \
        --host 127.0.0.1

The docs output-columns JSON can be refreshed with
``scripts/extract_output_columns.py`` (see that script for details).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from batfish_mcp import catalog  # noqa: E402


# Plain-language terms an LLM or human might use when they do not know the
# Batfish question name. Curated for the confusing groups called out in the
# assessment (traceroute/reachability, filters, RIBs, sessions, FHRP, ECMP),
# plus short aliases for the rest of the catalog.
SYNONYMS = {
    # Snapshot input
    "initIssues": [
        "snapshot initialization issues",
        "device conversion errors",
        "snapshot health",
    ],
    "fileParseStatus": [
        "file parse status",
        "which config files parsed",
        "parse failures",
    ],
    "parseWarning": [
        "parse warnings",
        "configuration warnings",
        "unparsed lines",
    ],
    # Configuration properties
    "nodeProperties": [
        "device configuration",
        "show configuration",
        "node/device properties",
    ],
    "interfaceProperties": ["interface status", "interfaces", "show interfaces"],
    "bgpProcessConfiguration": [
        "BGP process configuration",
        "router bgp",
        "BGP settings",
    ],
    "bgpPeerConfiguration": [
        "BGP neighbor configuration",
        "BGP peers",
        "neighbor config",
    ],
    "hsrpProperties": [
        "HSRP groups",
        "standby gateway",
        "redundant gateway",
        "first-hop redundancy",
    ],
    "ospfProcessConfiguration": [
        "OSPF process configuration",
        "router ospf",
        "OSPF settings",
    ],
    "ospfInterfaceConfiguration": [
        "OSPF interface configuration",
        "OSPF interface settings",
    ],
    "ospfAreaConfiguration": ["OSPF areas", "area configuration"],
    "mlagProperties": ["MLAG", "multi-chassis LAG", "vPC", "switch pairing"],
    "ipOwners": [
        "IP ownership",
        "duplicate IP",
        "IP conflict",
        "who owns an IP",
    ],
    "namedStructures": [
        "named structures",
        "named ACLs/route-maps",
        "object groups",
    ],
    "definedStructures": ["defined structures", "where is an ACL defined"],
    "referencedStructures": ["referenced structures", "where is an ACL used"],
    "undefinedReferences": [
        "undefined references",
        "missing ACL/route-map references",
        "dangling references",
    ],
    "unusedStructures": [
        "unused structures",
        "unreferenced ACLs",
        "dead configuration",
    ],
    "switchedVlanProperties": [
        "VLAN membership",
        "switchport VLANs",
        "show vlan",
    ],
    "vrrpProperties": [
        "VRRP groups",
        "virtual router",
        "redundant gateway",
        "first-hop redundancy",
    ],
    "a10VirtualServerConfiguration": [
        "A10 load balancer",
        "A10 virtual server",
        "VIP configuration",
    ],
    "f5BigipVipConfiguration": [
        "F5 load balancer",
        "F5 VIP",
        "BIG-IP virtual server",
    ],
    # Topology
    "userProvidedLayer1Edges": [
        "layer 1 topology",
        "physical links",
        "L1 edges",
    ],
    "layer3Edges": [
        "layer 3 topology",
        "L3 links",
        "network topology",
        "neighbors",
    ],
    # Routing protocol sessions and policies
    "bgpSessionCompatibility": [
        "BGP compatibility",
        "why is BGP down",
        "BGP misconfiguration",
        "BGP session mismatch",
    ],
    "bgpSessionStatus": [
        "BGP neighbor status",
        "BGP session up/down",
        "BGP established",
        "show ip bgp summary",
    ],
    "bgpEdges": ["BGP topology", "BGP adjacencies", "BGP neighbors"],
    "ospfSessionCompatibility": [
        "OSPF compatibility",
        "why is OSPF adjacency down",
        "OSPF mismatch",
    ],
    "ospfEdges": ["OSPF topology", "OSPF adjacencies", "OSPF neighbors"],
    "testRoutePolicies": [
        "test routing policy",
        "route-map test",
        "what does this policy do to a route",
    ],
    "searchRoutePolicies": [
        "search routing policies",
        "find route-map behavior",
        "route policy constraints",
    ],
    # Routing tables
    "routes": ["routing table", "RIB", "show ip route", "all routes"],
    "bgpRib": ["BGP table", "BGP routes", "BGP RIB", "show ip bgp"],
    "evpnRib": ["EVPN table", "EVPN routes", "EVPN RIB"],
    "lpmRoutes": [
        "longest prefix match",
        "which route wins",
        "route lookup",
        "LPM",
    ],
    # Packet forwarding
    "traceroute": [
        "trace path",
        "path trace",
        "hop-by-hop path",
        "where does this packet go",
        "one-way traceroute",
    ],
    "bidirectionalTraceroute": [
        "two-way traceroute",
        "round-trip path",
        "forward and return path",
    ],
    "reachability": [
        "can A reach B",
        "connectivity test",
        "successful flows",
        "flow search",
        "which flows succeed",
    ],
    "bidirectionalReachability": [
        "two-way reachability",
        "round-trip connectivity",
        "can A and B reach each other",
    ],
    "detectLoops": [
        "forwarding loop",
        "routing loop",
        "traffic going in circles",
        "loop detection",
    ],
    "subnetMultipathConsistency": [
        "host-subnet ECMP",
        "subnet multipath",
        "inconsistent forwarding to hosts",
    ],
    "loopbackMultipathConsistency": [
        "loopback ECMP",
        "router loopback multipath",
        "inconsistent forwarding to loopbacks",
    ],
    # Filters
    "filterLineReachability": [
        "shadowed ACL lines",
        "unreachable firewall rules",
        "redundant ACL lines",
        "dead filter lines",
    ],
    "searchFilters": [
        "find ACL match",
        "which flows hit a rule",
        "search filters",
        "filter flow search",
    ],
    "testFilters": [
        "is traffic allowed",
        "ACL permit/deny test",
        "does a filter permit this flow",
        "test ACL",
    ],
    "findMatchingFilterLines": [
        "which ACL line matches",
        "matching filter lines",
        "trace flow to rule",
    ],
    "snmpCommunityClients": [
        "SNMP community",
        "SNMP client access",
        "SNMP allowed clients",
    ],
    # IPSec
    "ipsecSessionStatus": [
        "IPSec tunnel status",
        "VPN status",
        "IPSec session up/down",
    ],
    "ipsecEdges": ["IPSec topology", "VPN tunnels", "IPSec peers"],
    # VXLAN and EVPN
    "vxlanVniProperties": ["VXLAN VNI", "VXLAN properties", "VNI mapping"],
    "vxlanEdges": ["VXLAN topology", "VXLAN tunnels", "VTEP peers"],
    "evpnL3VniProperties": ["EVPN L3 VNI", "EVPN VNI properties"],
    # Resolving specifiers
    "resolveLocationSpecifier": [
        "resolve location specifier",
        "why does this location match",
    ],
    "resolveFilterSpecifier": [
        "resolve filter specifier",
        "which filters match this pattern",
    ],
    "resolveNodeSpecifier": [
        "resolve node specifier",
        "which nodes match this regex",
    ],
    "resolveInterfaceSpecifier": [
        "resolve interface specifier",
        "which interfaces match",
    ],
    "resolveIpsOfLocationSpecifier": [
        "resolve IPs from locations",
        "IPs at a location",
    ],
    "resolveIpSpecifier": [
        "resolve IP specifier",
        "which IPs match this prefix",
    ],
    # Differential
    "compareFilters": [
        "compare ACLs",
        "filter diff between snapshots",
        "ACL changes",
        "differential filter comparison",
    ],
    "differentialReachability": [
        "reachability diff",
        "connectivity change",
        "flows affected by a change",
        "differential reachability",
    ],
}


# Batfish question templates expose enum values with a ``values`` list for only
# a few questions. The remaining enum-like inputs are *specifiers* whose values
# are documented in Batfish's questions/Parameters.md. These curated values are
# used only when the live template has no ``values`` of its own.
#
# Source: https://github.com/batfish/batfish/blob/master/questions/Parameters.md
CURATED_ALLOWED_VALUES = {
    ("bgpSessionStatus", "status"): [
        {
            "value": "NOT_COMPATIBLE",
            "description": "The BGP session is not compatibly configured.",
        },
        {
            "value": "NOT_ESTABLISHED",
            "description": (
                "The session is compatibly configured but was not established."
            ),
        },
        {
            "value": "ESTABLISHED",
            "description": "The BGP session is established.",
        },
    ],
    ("bgpSessionStatus", "type"): [
        {"value": "IBGP", "description": "Internal BGP session."},
        {
            "value": "EBGP_SINGLEHOP",
            "description": "Single-hop external BGP session.",
        },
        {
            "value": "EBGP_MULTIHOP",
            "description": "Multihop external BGP session.",
        },
        {
            "value": "EBGP_UNNUMBERED",
            "description": "Unnumbered external BGP session.",
        },
        {
            "value": "IBGP_UNNUMBERED",
            "description": "Unnumbered internal BGP session.",
        },
        {"value": "UNSET", "description": "Session type is not set."},
    ],
    ("bgpSessionCompatibility", "status"): [
        {
            "value": "LOCAL_IP_UNKNOWN_STATICALLY",
            "description": (
                "Local IP for an iBGP or multihop eBGP session is not "
                "configured."
            ),
        },
        {
            "value": "NO_LOCAL_IP",
            "description": (
                "Local IP for a single-hop eBGP session is not configured."
            ),
        },
        {
            "value": "NO_LOCAL_AS",
            "description": "Local AS for the session is not configured.",
        },
        {
            "value": "NO_REMOTE_IP",
            "description": (
                "Remote IP for a point-to-point peer is not configured."
            ),
        },
        {
            "value": "NO_REMOTE_PREFIX",
            "description": (
                "Remote prefix for a dynamic peer is not configured."
            ),
        },
        {
            "value": "NO_REMOTE_AS",
            "description": "Remote AS for the session is not configured.",
        },
        {
            "value": "INVALID_LOCAL_IP",
            "description": (
                "Configured local IP does not belong to any active interface."
            ),
        },
        {
            "value": "UNKNOWN_REMOTE",
            "description": (
                "Configured remote IP is not present in the network snapshot."
            ),
        },
        {
            "value": "HALF_OPEN",
            "description": (
                "No compatible match found for a point-to-point peer."
            ),
        },
        {
            "value": "MULTIPLE_REMOTES",
            "description": (
                "Multiple compatible matches found for a point-to-point peer."
            ),
        },
        {
            "value": "UNIQUE_MATCH",
            "description": (
                "Exactly one match found for a point-to-point peer."
            ),
        },
        {
            "value": "DYNAMIC_MATCH",
            "description": (
                "At least one compatible match found for a dynamic peer."
            ),
        },
        {
            "value": "NO_MATCH_FOUND",
            "description": "No compatible match found for a dynamic peer.",
        },
    ],
    ("bgpSessionCompatibility", "type"): [
        {"value": "IBGP", "description": "Internal BGP session."},
        {
            "value": "EBGP_SINGLEHOP",
            "description": "Single-hop external BGP session.",
        },
        {
            "value": "EBGP_MULTIHOP",
            "description": "Multihop external BGP session.",
        },
        {
            "value": "EBGP_UNNUMBERED",
            "description": "Unnumbered external BGP session.",
        },
        {
            "value": "IBGP_UNNUMBERED",
            "description": "Unnumbered internal BGP session.",
        },
        {"value": "UNSET", "description": "Session type is not set."},
    ],
    ("ospfSessionCompatibility", "statuses"): [
        {"value": "AREA_INVALID", "description": "The OSPF area is invalid."},
        {
            "value": "AREA_MISMATCH",
            "description": "The area does not match the neighbor.",
        },
        {
            "value": "AREA_TYPE_MISMATCH",
            "description": "The area type does not match.",
        },
        {
            "value": "DEAD_INTERVAL_MISMATCH",
            "description": "Dead intervals do not match.",
        },
        {
            "value": "DUPLICATE_ROUTER_ID",
            "description": "Duplicate router ID.",
        },
        {
            "value": "ESTABLISHED",
            "description": "The OSPF session is established.",
        },
        {
            "value": "HELLO_INTERVAL_MISMATCH",
            "description": "Hello intervals do not match.",
        },
        {
            "value": "MTU_MISMATCH",
            "description": "Interface MTUs do not match.",
        },
        {
            "value": "NETWORK_TYPE_MISMATCH",
            "description": "Network types do not match.",
        },
        {"value": "NO_SESSION", "description": "No OSPF session."},
        {
            "value": "PASSIVE_MISMATCH",
            "description": "Passive settings do not match.",
        },
        {
            "value": "PROCESS_INVALID",
            "description": "The OSPF process is invalid.",
        },
        {
            "value": "UNKNOWN_COMPATIBILITY_ISSUE",
            "description": "An unknown compatibility issue exists.",
        },
    ],
    ("ipsecSessionStatus", "status"): [
        {
            "value": "IPSEC_SESSION_ESTABLISHED",
            "description": "The IPSec session is established.",
        },
        {"value": "IKE_PHASE1_FAILED", "description": "IKE phase 1 failed."},
        {
            "value": "IKE_PHASE1_KEY_MISMATCH",
            "description": "IKE phase 1 key mismatch.",
        },
        {
            "value": "IPSEC_PHASE2_FAILED",
            "description": "IPSec phase 2 failed.",
        },
        {
            "value": "MISSING_END_POINT",
            "description": "An IPSec endpoint is missing.",
        },
    ],
    ("bgpRib", "status"): [
        {
            "value": "BEST",
            "description": (
                "The unique best route (or equivalent for ECMP) for an NLRI."
            ),
        },
        {
            "value": "BACKUP",
            "description": (
                "A route inferior to all best routes for the same NLRI."
            ),
        },
    ],
    ("routes", "protocols"): [
        {"value": "ALL", "description": "All protocols."},
        {"value": "IGP", "description": "All interior gateway protocols."},
        {"value": "OSPF", "description": "All OSPF routes."},
        {
            "value": "OSPF-INT",
            "description": "OSPF intra-area and inter-area routes.",
        },
        {"value": "OSPF-INTRA", "description": "OSPF intra-area routes."},
        {"value": "OSPF-INTER", "description": "OSPF inter-area routes."},
        {"value": "OSPF-EXT", "description": "OSPF external routes."},
        {
            "value": "OSPF-EXT1",
            "description": "OSPF external type 1 routes.",
        },
        {
            "value": "OSPF-EXT2",
            "description": "OSPF external type 2 routes.",
        },
        {"value": "ISIS", "description": "All IS-IS routes."},
        {"value": "ISIS-L1", "description": "IS-IS level 1 routes."},
        {"value": "ISIS-L2", "description": "IS-IS level 2 routes."},
        {"value": "EIGRP", "description": "All EIGRP routes."},
        {"value": "EIGRP-INT", "description": "EIGRP internal routes."},
        {"value": "EIGRP-EXT", "description": "EIGRP external routes."},
        {"value": "RIP", "description": "RIP routes."},
        {"value": "BGP", "description": "All BGP routes."},
        {"value": "EBGP", "description": "External BGP routes."},
        {"value": "IBGP", "description": "Internal BGP routes."},
        {"value": "AGGREGATE", "description": "Aggregate routes."},
        {"value": "STATIC", "description": "Static routes."},
        {"value": "LOCAL", "description": "Local routes."},
        {"value": "CONNECTED", "description": "Connected routes."},
    ],
    ("reachability", "actions"): [
        {
            "value": "success",
            "description": "Flows that were successfully delivered.",
        },
        {
            "value": "failure",
            "description": "Flows that were dropped somewhere in the network.",
        },
        {
            "value": "accepted",
            "description": "Flows accepted by a device in the snapshot.",
        },
        {
            "value": "delivered_to_subnet",
            "description": "Flows delivered to a host subnet.",
        },
        {
            "value": "exits_network",
            "description": (
                "Flows forwarded to a device outside the snapshot."
            ),
        },
        {
            "value": "denied_in",
            "description": "Flows denied by an input filter.",
        },
        {
            "value": "denied_out",
            "description": "Flows denied by an output filter.",
        },
        {
            "value": "no_route",
            "description": "Flows dropped because no route exists.",
        },
        {
            "value": "null_routed",
            "description": "Flows dropped by a null route.",
        },
        {
            "value": "neighbor_unreachable",
            "description": "Flows whose next hop could not be reached.",
        },
        {
            "value": "loop",
            "description": "Flows that encountered a forwarding loop.",
        },
        {
            "value": "insufficient_info",
            "description": (
                "Batfish does not have enough information to decide."
            ),
        },
    ],
    ("differentialReachability", "actions"): [
        {
            "value": "success",
            "description": "Flows that were successfully delivered.",
        },
        {
            "value": "failure",
            "description": "Flows that were dropped somewhere in the network.",
        },
        {
            "value": "accepted",
            "description": "Flows accepted by a device in the snapshot.",
        },
        {
            "value": "delivered_to_subnet",
            "description": "Flows delivered to a host subnet.",
        },
        {
            "value": "exits_network",
            "description": (
                "Flows forwarded to a device outside the snapshot."
            ),
        },
        {
            "value": "denied_in",
            "description": "Flows denied by an input filter.",
        },
        {
            "value": "denied_out",
            "description": "Flows denied by an output filter.",
        },
        {
            "value": "no_route",
            "description": "Flows dropped because no route exists.",
        },
        {
            "value": "null_routed",
            "description": "Flows dropped by a null route.",
        },
        {
            "value": "neighbor_unreachable",
            "description": "Flows whose next hop could not be reached.",
        },
        {
            "value": "loop",
            "description": "Flows that encountered a forwarding loop.",
        },
        {
            "value": "insufficient_info",
            "description": (
                "Batfish does not have enough information to decide."
            ),
        },
    ],
    ("searchFilters", "action"): [
        {
            "value": "permit",
            "description": "Return lines that permit the flow.",
        },
        {
            "value": "deny",
            "description": "Return lines that deny the flow.",
        },
        {
            "value": "matchLine <line number>",
            "description": "Match a specific filter line by number.",
        },
    ],
    ("searchRoutePolicies", "action"): [
        {
            "value": "permit",
            "description": "Find announcements the policy permits.",
        },
        {
            "value": "deny",
            "description": "Find announcements the policy denies.",
        },
    ],
    ("searchRoutePolicies", "pathOption"): [
        {
            "value": "per_path",
            "description": "Analyze each execution path separately.",
        },
        {
            "value": "non_overlap",
            "description": (
                "Analyze each path but greedily produce a different prefix "
                "for each advertisement."
            ),
        },
        {
            "value": "single",
            "description": (
                "Produce a single advertisement along one path (default)."
            ),
        },
    ],
}


def _catalog_questions() -> dict[str, dict]:
    return {
        q["fun"]: q
        for category in catalog.read_questions()["Batfish"]
        for q in category["questions"]
    }


def _allowed_values(values: list[Any]) -> list[Any]:
    """Normalize template values to strings or ``{value, description}``."""
    normalized: list[Any] = []
    for item in values:
        if isinstance(item, str):
            normalized.append(item)
            continue
        value = item.get("name", item.get("value"))
        description = (item.get("description") or "").strip()
        if description:
            normalized.append({"value": value, "description": description})
        else:
            normalized.append(value)
    return normalized


def _input_metadata(fun: str, name: str, spec: dict) -> dict:
    entry: dict[str, Any] = {
        "type": spec.get("type", "str"),
        "required": not spec.get("optional", False) and "value" not in spec,
    }
    if spec.get("displayName"):
        entry["display_name"] = spec["displayName"]
    if spec.get("description"):
        entry["description"] = spec["description"].strip()

    # A template ``value`` is a default, so the input is not required.
    if "value" in spec:
        entry["default"] = spec["value"]
    if spec.get("values"):
        entry["allowed_values"] = _allowed_values(spec["values"])
    elif (fun, name) in CURATED_ALLOWED_VALUES:
        entry["allowed_values"] = CURATED_ALLOWED_VALUES[(fun, name)]
    if spec.get("fields"):
        entry["field_specs"] = spec["fields"]
    if "minLength" in spec:
        entry["min_length"] = spec["minLength"]
    return entry


def _question_metadata(fun: str, bf) -> dict:
    template = getattr(bf.q, fun).template
    instance = template.get("instance", {})
    variables = instance.get("variables", {})
    order = instance.get("orderedVariableNames") or sorted(variables)

    inputs: dict[str, dict] = {}
    for name in order:
        if name in variables:
            inputs[name] = _input_metadata(fun, name, variables[name])
    # Keep any variable the template forgot to list rather than silently drop it.
    for name in sorted(set(variables) - set(inputs)):
        inputs[name] = _input_metadata(fun, name, variables[name])

    return {
        "description": (instance.get("description") or "").strip(),
        "long_description": (instance.get("longDescription") or "").strip(),
        "tags": list(instance.get("tags") or []),
        "synonyms": list(SYNONYMS.get(fun, [])),
        "differential": bool(template.get("differential", False)),
        "inputs": inputs,
    }


def _coverage(questions: dict[str, dict]) -> dict:
    missing_columns = [
        fun for fun, q in questions.items() if not q["output_columns"]
    ]
    allowed = {
        fun
        for fun, q in questions.items()
        if any("allowed_values" in i for i in q["inputs"].values())
    }
    inputs = [i for q in questions.values() for i in q["inputs"].values()]
    return {
        "questions": len(questions),
        "questions_with_output_columns": len(questions) - len(missing_columns),
        "questions_missing_output_columns": missing_columns,
        "questions_with_allowed_values": sorted(allowed),
        "inputs_with_descriptions": sum("description" in i for i in inputs),
        "inputs_with_defaults": sum("default" in i for i in inputs),
        "inputs_with_allowed_values": sum("allowed_values" in i for i in inputs),
    }


def _catalog_mismatches(questions: dict[str, dict]) -> dict:
    """Report catalog inputs that no longer match the live templates."""
    required_mismatches = []
    input_mismatches = []
    for fun, q in questions.items():
        catalog_inputs = {i["name"]: i for i in q["catalog_input"]}
        template_inputs = q["inputs"]
        catalog_only = sorted(set(catalog_inputs) - set(template_inputs))
        template_only = sorted(set(template_inputs) - set(catalog_inputs))
        if catalog_only or template_only:
            input_mismatches.append(
                {
                    "fun": fun,
                    "catalog_only": catalog_only,
                    "template_only": template_only,
                }
            )
        for name, meta in template_inputs.items():
            if name not in catalog_inputs:
                continue
            catalog_required = not catalog_inputs[name].get("optional", True)
            if catalog_required != meta["required"]:
                required_mismatches.append(
                    {
                        "fun": fun,
                        "input": name,
                        "catalog_required": catalog_required,
                        "template_required": meta["required"],
                    }
                )
    return {
        "catalog_input_mismatches": input_mismatches,
        "catalog_required_mismatches": required_mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Batfish coordinator host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--columns-json",
        default=str(ROOT / "scripts" / "output_columns.json"),
        help="Output columns parsed from the Batfish docs notebooks",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "src" / "batfish_mcp" / "question_metadata.json"),
        help="Where to write the generated metadata",
    )
    args = parser.parse_args()

    from pybatfish.client.session import Session

    catalog_questions = _catalog_questions()
    columns_path = Path(args.columns_json)
    if columns_path.exists():
        output_columns = json.loads(columns_path.read_text(encoding="utf-8"))
    else:
        output_columns = {}
        print(f"warning: {columns_path} not found; output columns omitted")

    bf = Session(host=args.host)
    available = {q["name"] for q in bf.q.list()}
    missing = sorted(set(catalog_questions) - available)
    if missing:
        raise SystemExit(f"questions missing from the live Batfish: {missing}")

    questions: dict[str, dict] = {}
    for fun, q in catalog_questions.items():
        questions[fun] = {
            **_question_metadata(fun, bf),
            "output_columns": output_columns.get(fun, []),
            "catalog_input": q.get("input") or [],
        }

    coverage = {**_coverage(questions), **_catalog_mismatches(questions)}
    for q in questions.values():
        del q["catalog_input"]

    data = {
        "schema_version": 1,
        "source": {
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "batfish_host": args.host,
            "pybatfish_version": importlib_metadata.version("pybatfish"),
            "question_templates": "bf.q.<fun>.template from the live coordinator",
            "allowed_values": (
                "template 'values' when present; otherwise curated from "
                "batfish/questions/Parameters.md enum specifiers"
            ),
            "output_columns": (
                "Batfish docs notebooks: 'Return Value' tables"
            ),
        },
        "coverage": coverage,
        "questions": questions,
    }

    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"wrote {out_path} ({len(questions)} questions)")
    print(json.dumps(coverage, indent=2))


if __name__ == "__main__":
    main()
