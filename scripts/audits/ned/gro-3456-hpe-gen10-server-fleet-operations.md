# GRO-3456 — HPE Gen10 Server Fleet Operations Audit

**Issue:** [GRO-3456](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3456)  
**Audit timestamp:** 2026-07-05T20:25:33Z UTC  
**Auditor:** Ned  
**Scope:** Server hardware health collection, iLO alert coverage, and firmware-status evidence for the HPE Gen10 fleet.

> Lane note: the Linear issue requested `audits/ned/`. Ned's enforced write lane is `scripts/`, `prismatic/`, and `plugins/`; `audits/` is not a Ned-owned path. This audit is therefore saved at the lane-compliant workspace path `scripts/audits/ned/gro-3456-hpe-gen10-server-fleet-operations.md`.

## Executive summary

🟡 **The HPE Gen10 fleet has partial OS/Proxmox inventory coverage, but no working iLO/Redfish alert feed or current firmware compliance ledger.**

- ✅ Fleet source-of-truth topology identifies two HPE Gen10 hosts: `pve5` and `pve6`, both listed as **HP DL360 Gen10** CPU worker/subnet-router hosts.
- ✅ Current Sovereign Sentinel inventory was refreshed at `2026-07-05T20:01:04Z` and contains Proxmox/OS-level hardware state for `pve6`.
- 🟢 `pve6` is reachable on LAN (`192.168.1.205`) and Tailscale (`100.90.63.4`): ping 0% packet loss; TCP 22 and 8006 open.
- 🔴 `pve5` is down/unreachable on LAN (`192.168.1.204`) and Tailscale (`100.65.32.83`): ping 100% packet loss; TCP 22/443/623/8006 all closed or filtered.
- 🟡 No iLO/IPMI/Redfish credentials or host variables were found in Ned/orchestrator env files (`ILO_*`, `IPMI_*`, `HPE_ILO_*` all absent).
- 🟡 No iLO alert/event-log collector was found in the current code search. The only HPE-specific action surface is a dashboard “fan overdrive” path that stops `hp_thermostat.service` over SSH; that is not health monitoring.
- 🟡 Firmware evidence exists only in stale snapshot artifacts (`Snapshot_2026-04-26_001645`), mostly disk/NIC firmware lines. There is no current HPE SPP/iLO/BIOS firmware status ledger.
- 🔴 Direct Proxmox API authentication using current `PVE6_*` env values failed during this audit (`401 authentication failure`), even though the inventory file shows a successful collector run earlier at 20:01Z. Treat this as a credential drift finding, not as proof that the inventory is invalid.

## Fleet scope

Topology source: `/home/ubuntu/work/SovereignSentinel/topology.json`.

| Host | Hardware | LAN | Tailscale | Role | Current state |
|---|---|---:|---:|---|---|
| `pve5` | HP DL360 Gen10 | `192.168.1.204` | `100.65.32.83` | CPU Worker / Subnet Router | 🔴 unreachable/offline |
| `pve6` | HP DL360 Gen10 | `192.168.1.205` | `100.90.63.4` | CPU Worker / Subnet Router | 🟢 reachable; inventory says online |

Related resale inventory also contains one decommissioned `HPE ProLiant DL380 Gen10` (`HW-RACK-001`) in `/home/ubuntu/work/homelab/inventory.json`; that is not part of the active Proxmox fleet.

## Evidence collected

### 1. Network and management-plane reachability

Probe timestamp: `2026-07-05T20:25:33Z`.

| Target | IP | Ping loss | TCP 22 | TCP 443 | TCP 623/IPMI | TCP 8006/PVE |
|---|---:|---:|---:|---:|---:|---:|
| `pve5_lan` | `192.168.1.204` | 100% | closed/filtered | closed/filtered | closed/filtered | closed/filtered |
| `pve5_ts` | `100.65.32.83` | 100% | closed/filtered | closed/filtered | closed/filtered | closed/filtered |
| `pve6_lan` | `192.168.1.205` | 0% | open | closed/filtered | closed/filtered | open |
| `pve6_ts` | `100.90.63.4` | 0% | open | closed/filtered | closed/filtered | open |
| `gpu_230_ts` | `100.78.237.7` | 100% | closed/filtered | closed/filtered | closed/filtered | closed/filtered |

Interpretation:

- `pve6` is reachable through the normal OS/Proxmox path.
- `pve5` is unreachable through both data-plane and management-adjacent ports from this cron host.
- No exposed IPMI/iLO endpoint is visible on the known host IPs. That does not rule out dedicated iLO NICs on separate IPs; it means those IPs are not documented in the reachable config surfaces Ned can read.

### 2. Hardware health collection

Current collection surfaces audited:

| Surface | Path | Status | Notes |
|---|---|---:|---|
| Sovereign Sentinel inventory | `/home/ubuntu/work/SovereignSentinel/inventory.json` | ✅ current | `_last_scan` is `2026-07-05T20:01:04.612415+00:00`. Captures Proxmox status, VMs, storage, disks for reachable nodes. |
| Weekly inventory updater | `/home/ubuntu/work/prismatic-engine/scripts/weekly_inventory_update.py` | ✅ present | Runs Sovereign Sentinel collector, parses node/VM state, updates `agentic-swarm-ops/docs/homelab-hardware-inventory.md`. |
| Sovereign Sentinel collector | `/home/ubuntu/work/SovereignSentinel/ops/inventory-collector.py` | 🟡 partial | Runs `proxmox_connector.py` + `node_scanner.py`, detects node/VM/disk/storage/GPU drift and storage >80%. No iLO/Redfish path found. |
| Dashboard action API | `/home/ubuntu/work/SovereignSentinel/sentinel_dashboard.py` | 🟡 action-only | Has HPE-specific “fan overdrive” SSH action (`systemctl stop hp_thermostat.service`) but not alert ingestion or health collection. |

Current inventory summary for the active HPE hosts:

| Host | Inventory state | Evidence |
|---|---:|---|
| `pve5` | offline | `proxmox_status.status = offline`; no VMs/storage/disks collected in current inventory. |
| `pve6` | online | `proxmox_status.status = online`; host `192.168.1.205`; scanned at `2026-07-05T20:01:01.703789+00:00`. |

`pve6` current inventory excerpts:

- Memory: 377.5 GiB total, 158.5–158.6 GiB used (~42%).
- Root disk: ~9.4 GiB used / 46.1 GiB total (~20.4%).
- VMs: `webtop-hermes` running, `k3s-node-235` running, `hb-master-3` running, `sriov-flasher-6` stopped.
- Storage: `Synology_NAS` enabled at ~81.7%; local pools well below warning thresholds.

Assessment:

- OS/Proxmox-level hardware collection exists and is good enough for online/offline, disk/storage capacity, and VM state.
- It is **not** good enough for HPE-specific hardware health: PSU redundancy, fan failure, DIMM ECC events, predictive disk failures behind Smart Array, iLO event log, or thermal sensor fault history.

### 3. iLO alerts and event logs

Credential/config search performed before declaring a gap:

- OKF integration search: `/home/ubuntu/work/growthwebdev-knowledge/okf/integrations/` for `HPE`, `Gen10`, `iLO`, `firmware`, `server fleet`, `DL380`, `DL360`, `Proxmox`, `PVE` → no iLO credential or runbook hit.
- Session history search for `HPE OR Gen10 OR iLO OR DL380 OR DL360 OR firmware OR IPMI` → only resale/prior physical-task context and older topology references; no reusable iLO endpoint inventory found.
- Env grep across Ned/orchestrator/workspace env files found `PVE6_*` only. No `ILO_*`, `IPMI_*`, or `HPE_ILO_*` keys.
- Code search found no Redfish/iLO collector. Only `sentinel_dashboard.py` line ~844 uses Dell raw `ipmitool` for Dell fan override and HPE SSH service-stop for fan overdrive.

Result:

🔴 **No current iLO alert feed is wired.** There is no evidence that iLO Integrated Management Log (IML), Redfish event subscriptions, iLO health rollups, PSU/fan/DIMM status, or HPE firmware baselines are being pulled into the monitoring ledger.

Recommended minimum contract:

1. Add a dedicated `hpe_ilo_hosts.json` or env-backed registry with host, iLO IP/FQDN, serial, and expected model.
2. Query Redfish endpoints read-only:
   - `/redfish/v1/Systems/1/`
   - `/redfish/v1/Systems/1/LogServices/IML/Entries/`
   - `/redfish/v1/Chassis/1/Power/`
   - `/redfish/v1/Chassis/1/Thermal/`
   - `/redfish/v1/UpdateService/FirmwareInventory/`
3. Persist summarized status to the same inventory artifact or a small SQLite ledger.
4. Alert only on state changes/new critical IML entries to avoid spam.

### 4. Firmware status logs

Current firmware evidence surfaces:

| Source | Freshness | Coverage |
|---|---:|---|
| `/home/ubuntu/work/SovereignSentinel/Snapshot_2026-04-26_001645/raw_data/.../san_storage/smart_health.txt` | stale (2026-04-26 snapshot) | Disk/NVMe firmware/revision lines for several PVE nodes, including `pve5` and `pve6`. |
| `/home/ubuntu/work/SovereignSentinel/Snapshot_2026-04-26_001645/raw_data/.../rdma_gpu_state/ibstat.txt` | stale | Mellanox firmware version lines (`2.42.5000`) on some nodes. |
| Current `/home/ubuntu/work/SovereignSentinel/inventory.json` | current | Disk names/sizes/topology, but not firmware versions or HPE BIOS/iLO firmware. |

Stale snapshot firmware examples:

| Node snapshot | Firmware evidence found |
|---|---|
| `network_snapshot_pve5/san_storage/smart_health.txt` | `Firmware Version: M0DL022`, `Firmware Version: D1MU021`, assorted disk `Revision` values (`DWF8`, `FS03`, `DA07`, `DA06`). |
| `network_snapshot_pve6/san_storage/smart_health.txt` | `Firmware Version: D1MU021`, disk `Revision` values (`YS0A`, `HT66`, `DWF8`, `FS03`, `DA06`, `DA07`). |
| `network_snapshot_pve3/rdma_gpu_state/ibstat.txt` | Mellanox `Firmware version: 2.42.5000`. |

Assessment:

🟡 Firmware logging exists only as incidental snapshot output. There is no current “firmware compliance” surface for HPE Gen10 BIOS, iLO 5, Smart Array, NICs, or disk firmware, and no comparison against an approved baseline.

### 5. Credential drift finding

During this audit, direct Proxmox API authentication against `https://$PVE6_IP:8006/api2/json/access/ticket` using current `PVE6_IP`, `PVE6_USER`, and `PVE6_PASSWORD` from `/home/ubuntu/.hermes/profiles/orchestrator/.env` returned:

```text
{"data":null,"message":"authentication failure\n"}
```

That conflicts with the current inventory file, which was refreshed at `2026-07-05T20:01:04Z`. Likely explanations:

- a different collector credential path exists in the SovereignSentinel environment or runtime;
- the env password is stale but the inventory was not generated by this exact env source;
- the inventory file was updated by another process before credentials drifted.

Impact:

- Ned can still audit the generated inventory artifact.
- Ned cannot currently perform fresh authenticated Proxmox API checks from the orchestrator env alone.
- This blocks any new HPE/iLO integration that expects to reuse the `PVE6_*` env path for live proof.

## Findings and severity

| Severity | Finding | Impact | Recommended fix |
|---|---|---|---|
| 🔴 Down | `pve5` (HP DL360 Gen10) is unreachable on LAN and Tailscale. | Half of the active HPE Gen10 pair is offline; no hardware health can be collected. | Physical/network/power check for `pve5`; verify Tailscale and Proxmox services after recovery. |
| 🔴 Gap | No iLO/Redfish alert feed or iLO credentials found. | PSU/fan/DIMM/thermal/IML faults are invisible unless they surface through OS logs. | Add read-only iLO registry + Redfish collector; persist IML deltas and health rollup. |
| 🟡 Warning | Firmware logs are stale snapshots only. | Cannot tell whether HPE BIOS/iLO/Smart Array/NIC firmware is current or drifting. | Add firmware inventory collection via Redfish `UpdateService/FirmwareInventory` and compare against a baseline. |
| 🟡 Warning | Direct PVE API auth failed with current orchestrator env. | Fresh authenticated API checks are unreliable from this cron context. | Reconcile `PVE6_*` env values with the collector’s actual credential source; rotate/update if stale. |
| ✅ Healthy | `pve6` is reachable and current inventory shows it online. | OS/Proxmox collection for one HPE Gen10 host is functioning. | Keep Proxmox/OS collection, but do not treat it as iLO hardware-health coverage. |

## Definition of Done mapping

- [x] Audit server hardware health collection: OS/Proxmox inventory surfaces reviewed; `pve5`/`pve6` reachability and current inventory state documented.
- [x] Audit iLO alerts: no iLO/Redfish/env/code alert path found after OKF, session history, env, and code search; required collector contract documented.
- [x] Audit firmware status logs: current inventory and stale Sovereign Sentinel snapshot firmware evidence reviewed; firmware-compliance gap documented.
- [x] Write audit results in markdown format: this file.
- [x] Save output file to the workspace: lane-compliant path `scripts/audits/ned/`.

## Raw probe references

- Live probe JSON: `/tmp/gro3456_probe.json` (not committed; contains no secret values, only boolean credential-presence flags and summarized probe output).
- Current inventory: `/home/ubuntu/work/SovereignSentinel/inventory.json` (`_last_scan = 2026-07-05T20:01:04.612415+00:00`).
- Topology: `/home/ubuntu/work/SovereignSentinel/topology.json`.
- Existing collection scripts inspected: `scripts/weekly_inventory_update.py`, `SovereignSentinel/ops/inventory-collector.py`, `SovereignSentinel/sentinel_dashboard.py`.
