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
Human-readable hints describing Batfish parameter types, used as the
``description`` text for MCP tool parameters (the LLM reads these to
build e.g. ``HeaderConstraints`` dicts).
"""

TYPE_HINTS = {
    "HeaderConstraints": {
        "hint": (
            "Dictionary / JSON object of packet header constraints. "
            "srcIps/dstIps accept IPs, prefixes, node names, interfaces, or "
            "named IP spaces. srcPorts/dstPorts accept string port specs such "
            "as '443' or '80,443' (numeric JSON values are invalid). If "
            "ipProtocols is omitted, Batfish tests ICMP."
        ),
        "placeholder": "{'dstIps': 'server_2', 'srcIps': 'server_1'}",
        "example": (
            "{'dstIps': '8.8.8.8', 'ipProtocols': ['TCP'], "
            "'dstPorts': '443'}"
        ),
        "fields": "srcIps, dstIps, srcPorts, dstPorts, ipProtocols, applications, icmpCodes, icmpTypes, tcpFlags",
        "field_types": {
            "srcIps": "ipSpaceSpec (IP, prefix, node name, interface, or named IP space)",
            "dstIps": "ipSpaceSpec (IP, prefix, node name, interface, or named IP space)",
            "srcPorts": "string port spec, e.g. '443' or '80,443'",
            "dstPorts": "string port spec, e.g. '443' or '80,443'",
            "ipProtocols": "list of protocol names, e.g. ['TCP', 'ICMP']",
        },
    },
    "PathConstraints": {
        "hint": (
            "Dictionary / JSON object of path routing constraints. Locations "
            "are node names, interfaces, or location specifiers."
        ),
        "placeholder": "{'startLocation': 'server_1', 'endLocation': 'server_2'}",
        "example": "{'startLocation': 'as1border1', 'endLocation': 'as2border1'}",
        "fields": "startLocation, endLocation, transitLocations, forbiddenLocations",
    },
    "int": {
        "hint": "Integer value.",
        "placeholder": "1",
        "example": "10",
    },
    "str": {
        "hint": "String value.",
        "placeholder": "value",
        "example": "value",
    },
    "IpSpaceSpec": {
        "hint": (
            "Batfish IP space specifier: an IP, prefix, node name, interface, "
            "or named IP space."
        ),
        "placeholder": "server_2 or 10.0.0.0/24",
        "example": "server_2",
    },
    "InterfaceSpec": {
        "hint": "Batfish interface specifier: interface name, node[interface], or regex.",
        "placeholder": "r1[Gi0/0] or Gi0/0",
        "example": "r1[Gi0/0]",
    },
    "BgpRoute": {
        "hint": "Dictionary or list of dictionaries defining BGP route attributes.",
        "placeholder": "[{'network': '10.0.0.0/24', 'originatorIp': '1.1.1.1', 'originType': 'egp', 'protocol': 'bgp'}]",
        "example": "[{'network': '10.0.0.0/24', 'originatorIp': '1.1.1.1', 'originType': 'egp', 'protocol': 'bgp'}]",
        "fields": "network, originatorIp, originType, protocol, asPath, communities, localPreference, metric",
    },
    "BgpSessionProperties": {
        "hint": "Dictionary defining BGP session properties.",
        "placeholder": "{'localAs': 65001, 'remoteAs': 65002, 'localIp': '1.1.1.1', 'remoteIp': '1.1.1.2'}",
        "example": "{'localAs': 65001, 'remoteAs': 65002, 'localIp': '1.1.1.1', 'remoteIp': '1.1.1.2'}",
        "fields": "localAs, remoteAs, localIp, remoteIp",
    },
    "NodeSpec": {
        "hint": "Batfish Node specifier pattern (node name, regex '/pattern/', or comma-separated).",
        "placeholder": "as1.* or node1,node2",
        "example": "as1.*",
    },
    "IpSpec": {
        "hint": "Batfish IP specifier (IP address or CIDR subnet prefix).",
        "placeholder": "10.0.0.1 or 10.0.0.0/24",
        "example": "10.0.0.0/24",
    },
    "RoutingPolicySpec": {
        "hint": "Routing policy name or regex pattern.",
        "placeholder": "policy_name or /regex/",
        "example": "my_policy",
    },
    "LocationSpec": {
        "hint": "Batfish location specifier: where the flow starts (see the Batfish documentation).",
        "placeholder": "node1",
        "example": "as1border1",
    },
    "FilterSpec": {
        "hint": "Batfish filter specifier: filter (ACL) name, /regex/, or comma-separated names.",
        "placeholder": "acl_name or /regex/",
        "example": "acl_in",
    },
    "RouteMatch": {
        "hint": (
            "Dictionary of columns of the Routes answer that the route must "
            "match. Only the keys given are compared, and values must match "
            "exactly."
        ),
        "placeholder": "{'Network': '10.0.0.0/24', 'Protocol': 'bgp'}",
        "example": "{'Network': '10.0.0.0/24', 'Protocol': 'bgp'}",
        "fields": "Network, Protocol, Next_Hop_IP, Next_Hop_Interface, Admin_Distance, Metric, Tag",
    },
    "bool": {
        "hint": "Boolean flag: True or False.",
        "placeholder": "True / False",
        "example": "True",
    },
}

