# HOW-TO: Batfish MCP in OpenCode — a mini tutorial

This tutorial is based on Batfish example network `abc_net.zip`.

## What you will have at the end

You will be able to:

1. List the 60 Batfish questions the MCP can answer.
2. Upload `abc_net.zip` as snapshot `abc_net` on network `NETWORK`.
3. Describe the 9-node topology and 2 BGP routings.
4. Answer: "Can the servers reach the internet?"

## How many Batfish questions can I answer?

**You:** "how many Batfish questions can you answer?"

**Assistant calls (via `execute`):**

```js
return await tools["batfish-q-mcp"].batfish_list_questions({});
```

**Answer: 60**

Breakdown from the catalog:

* Snapshot Input: 3 (`initIssues`, `fileParseStatus`, `parseWarning`)
* Configuration Properties: 19
* Topology: 2 (`userProvidedLayer1Edges`, `layer3Edges`)
* Routing Protocol Sessions and Policies: 7
* Routing and Forwarding Tables: 4
* Packet Forwarding: 7 (`traceroute`, `reachability`, ...)
* Access-lists and firewall rules: 5
* IPSec Tunnels: 2
* VXLAN and EVPN: 3
* Resolving Specifiers: 6
* Differential Questions: 2

> Tip: call with `category` to filter, e.g. `{ category: "Packet Forwarding" }`.

## How do I find a question without knowing its name?

**You:** "who is the standby gateway?"

**Assistant calls (via `execute`):**

```js
return await tools["batfish-q-mcp"].batfish_find_questions({
  query: "standby gateway"
});
```

This returns ranked candidates such as `hsrpProperties` and `vrrpProperties`,
with `score` and `matched_on`. Use `batfish_get_question({ fun })` to inspect
the candidates before running one.

## How do I provide the snapshot?

**You:** "how do you want me to provide the snapshot?"

**Best practice:** drop the zip in the workspace and tell the assistant the filename.

```bash
ls -lh /home/user/batmcp-test/abc_net.zip
python3 -c "import zipfile; print('\n'.join(zipfile.ZipFile('abc_net.zip').namelist()))"
# abc_net/
# abc_net/batfish/isp_config.json, layer1_topology.json
# abc_net/configs/FW.cfg, R1.cfg, R2.cfg, R3.cfg
# abc_net/hosts/server_1.json, server_2.json, server_3.json
```

Alternatives:

* **Already on server:** give `snapshot` + `network` (`NETWORK`), no upload.
* **Bare configs:** put them in a folder; assistant can zip + upload.
* **Paste base64:** only for tiny snapshots (error-prone).


## Upload `abc_net.zip`

**You:** "you have snapshot in file abc_net.zip"

**Assistant does:**

```bash
base64 -w 0 abc_net.zip  # -> 5732 chars, must end with exact padding
```

Then via `execute`:

```js
return await tools["batfish-q-mcp"].batfish_upload_snapshot({
  name: "abc_net.zip",
  zip_base64: "<base64-of-zip>",
  network: "NETWORK",
  overwrite: true
});
```

**Gotcha:** `zip_base64 is not valid base64: Incorrect padding` means the paste was trimmed. Re-encode without line breaks and keep trailing `=`/`==` (`validate=True` on server).

**Direct upload alternative (shell, no paste):**

```bash
PYTHONPATH=src python3 - <<'PY'
import os
from pybatfish.client.session import Session
bf = Session(host=os.getenv("BATFISH_SERVER","127.0.0.1"))
bf.set_network("NETWORK")
bf.init_snapshot("abc_net.zip", name="abc_net", overwrite=True)
print(bf.list_snapshots())  # ['abc_net', 'abc_net_Fail', ...]
PY
```

**Verify:**

```js
await tools["batfish-q-mcp"].batfish_list_snapshots({ network: "NETWORK" });
// ["abc_net","abc_net_Fail_1","abc_net_Fail","baseline"]

await tools["batfish-q-mcp"].batfish_run_question({
  fun: "initIssues", snapshot: "abc_net", network: "NETWORK"
});
// { fun:"initIssues", status:"SUCCESS", rows:[] }  // no parse errors
```

> `batfish_list_snapshots` without `network` → `Network must be set`.

---

## Describe the topology

**You:** "describe the topology"

**Calls:**

```js
await tools["batfish-q-mcp"].batfish_get_topology({
  kind: "layer3", snapshot: "abc_net", network: "NETWORK"
});
await tools["batfish-q-mcp"].batfish_get_topology({
  kind: "routing", snapshot: "abc_net", network: "NETWORK"
});
await tools["batfish-q-mcp"].batfish_run_question({
  fun: "nodeProperties", snapshot: "abc_net", network: "NETWORK"
});
```

**Result:**

* **9 nodes, 9 L3 edges:** `fw, internet, isp_65002, r1, r2, r3, server_1, server_2, server_3`
* **Routing adjacencies:** only 2 BGP edges — `internet ↔ isp_65002 ↔ fw`

```
internet (To-isp_65002)
  <---> isp_65002 (To-Internet / To-fw-Gi0/0)
  <---> fw (Gi0/0:1.1.1.1/30)
         Gi0/1:10.10.10.2/30 -> r1 (Gi0/0:10.10.10.1/30)
         Gi0/2:192.168.1.1/24 -> server_1 (192.168.1.2/24)

r1 (Gi0/1:10.10.10.5/30) <---> r2 (Gi0/2:10.10.10.6/30)
r1 (Gi0/2:10.10.10.9/30) <---> r3 (Gi0/1:10.10.10.10/30)
r2 (Gi0/1:10.10.10.13/30) <---> r3 (Gi0/2:10.10.10.14/30)

r2 (Gi0/0:192.168.2.1/24) <---> server_2 (192.168.2.2/24)
r3 (Gi0/0:192.168.3.1/24) <---> server_3 (192.168.3.2/24)
```

Interior is a full-mesh triangle `r1–r2–r3` on OSPF. BGP only at the edge.


## Can the servers reach the internet?

**You:** "can the servers reach the internet?"

**Step 1 — find IPs:**

```js
await tools["batfish-q-mcp"].batfish_run_question({
  fun: "interfaceProperties", snapshot: "abc_net", network: "NETWORK"
});
// server_1 192.168.1.2/24, server_2 192.168.2.2/24, server_3 192.168.3.2/24
// internet out 240.254.254.1/30, fw Gi0/0 1.1.1.1/30, isp 1.1.1.2/30
```

**Step 2 — test reachability (traceroute to `internet` IP `240.254.254.1`):**

```js
for (const s of ["server_1","server_2","server_3"]) {
  await tools["batfish-q-mcp"].batfish_run_question({
    fun: "traceroute", snapshot: "abc_net", network: "NETWORK",
    options: { startLocation: s, headers: { dstIps: "240.254.254.1" } }
  });
}
```

**Step 3 — dig into the firewall:**

```js
await tools["batfish-q-mcp"].batfish_run_question({
  fun: "searchFilters", snapshot: "abc_net", network: "NETWORK",
  options: { nodes: "fw", filters: "INSIDE-INBOUND", headers: { dstIps: "8.8.8.8" } }
});
// permit ip 192.168.0.0 0.0.0.255 any  -> only /24 of server_1? No: /24 is 192.168.0.0
// DMZ-INBOUND permits 192.168.1.0/24
```

**Findings:**

| Server | Result | Why |
|---|---|---|
| `server_1` 192.168.1.2 → 8.8.8.8 | ✅ `EXITS_NETWORK` | `DMZ-INBOUND` permits 192.168.1.0/24, `fw` NAT `192.168.1.2→1.1.1.1`, ISP `Block outgoing reserved` permits 8.8.8.8, reaches `internet out` |
| `server_1` → 240.254.254.1 | ❌ `DENIED_OUT` at `isp_65002` | `240.0.0.0/4` is reserved; ISP egress `Block outgoing traffic using reserved addresses` drops it |
| `server_2` 192.168.2.2 | ❌ `DENIED_IN` at `fw` | `r2→r1→fw` routing OK, but `INSIDE-INBOUND` only `permit 192.168.0.0/24`, ends `deny ip any any` |
| `server_3` 192.168.3.2 | ❌ `DENIED_IN` at `fw` | Same as `server_2` |

**Answer: Only `server_1` can reach the internet (public IP). `server_2`/`server_3` are blocked at `fw INSIDE-INBOUND`.**

> To re-test with a public IP, use `dstIps: "8.8.8.8"` instead of `240.254.254.1`. Both `traceroute` and `bidirectionalTraceroute` work this way.

## Header and option gotchas

- **Node names work in `srcIps` / `dstIps`.** A node name resolves to that
  node's addresses, so `{ srcIps: "server_1", dstIps: "server_2" }` is valid.
- **Ports are strings.** Use `dstPorts: "443"` or `dstPorts: "80,443"`; a
  numeric value such as `dstPorts: 443` is rejected.
- **`maxTraces` is an integer**, not a string.
- **The default protocol is ICMP.** If a header has no `ipProtocols`, Batfish
  tests ICMP. For a service test, be explicit:
  `headers: { dstIps: "8.8.8.8", ipProtocols: ["TCP"], dstPorts: "443" }`.
  This applies to `assert_flows_succeed` / `assert_flows_fail` too.
- **`bidirectionalReachability` needs `dstIps`.** Without it Batfish fails
  while setting up reverse-flow transformations; the server returns a
  structured `BIDIRECTIONAL_REQUIRES_DST` error instead.
- **`reachability` returns examples**, not every reachable destination. Use
  `traceroute` for one destination or query per pair to build a matrix.
- **Question failures are structured.** Batfish errors come back as
  `{ status: "error", code, message }`; pass `debug: true` to see the raw log.


## Quick reference cheat-sheet

```js
// topology
await tools["batfish-q-mcp"].batfish_get_topology({kind:"layer3", snapshot:"abc_net", network:"NETWORK"});
await tools["batfish-q-mcp"].batfish_get_topology({kind:"routing", snapshot:"abc_net", network:"NETWORK"});

// reachability
await tools["batfish-q-mcp"].batfish_run_question({
  fun:"traceroute", snapshot:"abc_net", network:"NETWORK",
  options:{startLocation:"server_1", headers:{dstIps:"8.8.8.8"}}
});

// assertions
await tools["batfish-q-mcp"].batfish_run_assertion({
  name:"assert_no_forwarding_loops", snapshot:"abc_net", network:"NETWORK"
});
await tools["batfish-q-mcp"].batfish_run_assertion_suite({
  suite_yaml:"version: 1\n...", snapshot:"abc_net", network:"NETWORK"
});

// failure injection + diff
await tools["batfish-q-mcp"].batfish_fork_snapshot({
  base_name:"abc_net", failed_nodes:["r1"], network:"NETWORK"
});
await tools["batfish-q-mcp"].batfish_run_question({
  fun:"differentialReachability",
  options:{pathConstraints:{startLocation:"r2", endLocation:"r3"}, headers:{dstIps:"192.168.3.1"}},
  snapshot:"abc_net_Fail", reference_snapshot:"abc_net", network:"NETWORK"
});
```

See `examples/assertions.yaml` for a runnable suite.
