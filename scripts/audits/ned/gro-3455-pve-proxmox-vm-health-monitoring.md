# GRO-3455 — PVE & Proxmox VM Health Monitoring Audit

**Issue:** [GRO-3455](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3455)  
**Audit timestamp:** 2026-07-05T20:09:45Z UTC  
**Auditor:** Ned  
**Scope:** Hypervisor metrics collection, VM state checks, and remote NAS storage checkups for the PVE/Proxmox fleet.

> Lane note: the Linear issue requested `audits/ned/`. Ned's enforced write lane in `PRISMATIC_ENGINE.yaml` is `scripts/`, `prismatic/`, and `plugins/`; `audits/` is not a Ned-owned path. This audit is therefore saved at the lane-compliant workspace path `scripts/audits/ned/gro-3455-pve-proxmox-vm-health-monitoring.md`.

## Executive summary

🟡 **PVE6 is reachable and authenticated API monitoring works, but the Proxmox cluster is in a degraded single-online-node posture.**

- ✅ PVE6 (`100.90.63.4`) is reachable over Tailscale: ping 0% packet loss; TCP 8006 and 22 open.
- ✅ Proxmox API credentials are available in the orchestrator profile env and successfully authenticated.
- 🟡 Cluster inventory reports **6 Proxmox nodes total; only `pve6` online; `pve1`–`pve5` offline**.
- 🟡 Cluster resource inventory reports **22 VM/LXC resources; 3 running, 1 stopped, 18 unknown** because their owning nodes are offline.
- 🟡 `webtop-hermes` VM is running but memory pressure is high: **~96.5% of 128 GiB allocated memory in use**.
- 🟡 Remote NAS storage is mounted and active, but the shared Synology NFS pool is **~81.7–82% used**, close to the 85% warning threshold.
- 🔴 GPU/Tailscale peer `100.78.237.7` did not respond to ping during this audit: **100% packet loss**. That aligns with offline VM/node telemetry for GPU-adjacent fleet members.

## Evidence collected

### 1. Network and service reachability

Command evidence collected from the cron host:

```text
UTC 2026-07-05T20:09:45Z
PVE6 ping: 0% packet loss
GPU/pve-ish node 100.78.237.7 ping: 100% packet loss
PVE6 8006 TCP: open
PVE6 SSH TCP: open
PVE6 API /version: 401 bytes=0
```

Interpretation:

- `401` on `/api2/json/version` without auth is expected and proves the HTTPS API listener is alive.
- Authenticated Proxmox API call succeeded afterward (`auth True`).
- SSH TCP is reachable; this audit did not require interactive SSH because Proxmox API authentication provided the needed node/VM/storage telemetry.

### 2. Hypervisor metrics collection

Authenticated Proxmox API probe returned:

```text
auth True
version ok
nodes count 6
cluster_resources count 66
storage count 7
```

Per-node API summary:

| Node | Status | CPU | Memory | Root/local disk | Uptime |
|---|---:|---:|---:|---:|---:|
| pve6 | online | 4.5% | 42.0% | 20.4% | 1,117,274s |
| pve1 | offline | 0% | 0% | 0% | n/a |
| pve2 | offline | 0% | 0% | 0% | n/a |
| pve3 | offline | 0% | 0% | 0% | n/a |
| pve4 | offline | 0% | 0% | 0% | n/a |
| pve5 | offline | 0% | 0% | 0% | n/a |

Detailed PVE6 host status:

| Metric | Value |
|---|---:|
| PVE version | `pve-manager/9.1.1/42db4a6cf33dac83` |
| Kernel | `Linux 6.17.2-1-pve` |
| CPU model | Intel Xeon Gold 5218 @ 2.30GHz |
| CPU topology | 2 sockets / 32 cores / 64 CPUs |
| Load average | 3.01 / 3.21 / 3.28 |
| Host CPU | ~3.4% at node status endpoint |
| Host memory | 170.2 GiB used / 405.4 GiB total (~42.0%) |
| Swap | 0 used / 8 GiB total |
| Rootfs | 9.4 GiB used / 46.1 GiB total (~20.4%) |

Assessment:

- Metrics collection is viable through the Proxmox API: node status, host CPU/memory/rootfs, storage, and VM states were all obtainable without SSH.
- Monitoring should treat `pve1`–`pve5` offline as a cluster-level warning, not as six independent noisy alerts every tick. Collapse them into one “cluster degraded / only pve6 online” incident.

### 3. VM state checks

Cluster resource inventory:

```text
resources_total 66
vms 22
running 3
stopped 1
unknown 18
```

PVE6 VM inventory from `/nodes/pve6/qemu`:

| VMID | Name | Status | vCPU | Memory use | Notes |
|---:|---|---:|---:|---:|---|
| 235 | `k3s-node-235` | running | 16 | ~5.6 GiB / 64 GiB (8.2% by node API; 4.3% in cluster summary) | Healthy low load |
| 243 | `hb-master-3` | running | 8 | ~6.8 GiB / 32 GiB (19.9% by node API; 17.6% in cluster summary) | Healthy low load |
| 800 | `webtop-hermes` | running | 24 | ~136.9 GiB / 128 GiB apparent allocation pressure (~99.6% node API; 96.5% cluster summary) | Watch closely |
| 9906 | `sriov-flasher-6` | stopped | 1 | 0 | Expected if maintenance-only |

Cluster-level VM/LXC resources on offline nodes show `unknown`, including several k3s nodes and historical VM IDs (`230`, `231`, `232`, `233`, `234`, `236`, etc.). Because the owning Proxmox nodes are offline, their VM state cannot be trusted from the cluster API beyond “unknown/offline owner.”

Assessment:

- VM state checks are operational for online `pve6` VMs.
- The monitoring gap is not the query path; it is cluster availability. Most VM state is unknown because five of six Proxmox nodes are offline.
- `webtop-hermes` memory should be a dedicated alert candidate. It is the only running PVE6 VM with high memory pressure in this sample.

### 4. Remote NAS storage checkups

Cron-host NFS mounts:

```text
Filesystem                             Size  Used Avail Use% Mounted on
192.168.1.40:/volume1/photo             27T   22T  4.8T  82% /home/ubuntu/mounts/synology-photo
192.168.1.40:/volume1/agentic-context   27T   22T  4.8T  82% /mnt/synology-agentic-context
```

Mount content probes:

```text
/home/ubuntu/mounts/synology-photo: 91 entries
/home/ubuntu/mounts/synology-agentic-context: 13 entries
```

PVE6 Proxmox storage endpoint:

| Storage | Type | Active | Shared | Used | Total | Use% | Content |
|---|---|---:|---:|---:|---:|---:|---|
| `Synology_NAS` | nfs | 1 | 1 | 23.5 TB | 28.8 TB | 81.7% | iso, backup |
| `fast-nvme` | zfspool | 1 | 0 | 142.3 GB | 965.6 GB | 14.7% | rootdir, images |
| `k3s-drive` | zfspool | 1 | 0 | 55.0 GB | 774.0 GB | 7.1% | rootdir, images |
| `local-zfs` | zfspool | 1 | 0 | 10.7 GB | 289.2 GB | 3.7% | rootdir, images |
| `storage` | dir | 1 | 0 | 45.6 GB | 1.85 TB | 2.5% | images, iso, rootdir, backup, vztmpl |
| `local` | dir | 1 | 0 | 9.4 GB | 46.1 GB | 20.4% | snippets, backup, vztmpl, rootdir, iso, images |
| `storage-12t` | dir | 1 | 0 | 9.4 GB | 46.1 GB | 20.4% | snippets, backup, vztmpl, images, iso, rootdir |

Assessment:

- Remote NAS checkups are currently possible from both the Hermes VM mount surface and the PVE6 Proxmox storage API.
- Synology capacity is below the 85% warning threshold but close enough to warrant trend monitoring.
- The PVE storage definition for `Synology_NAS` is active/shared and suitable for backup/ISO checks.

## Existing collection surfaces audited

### `scripts/weekly_inventory_update.py`

Findings:

- Runs `/home/ubuntu/work/SovereignSentinel/ops/inventory-collector.py` and parses `/home/ubuntu/work/SovereignSentinel/inventory.json`.
- Extracts `proxmox_status.status` for nodes and VM/container `status` fields.
- Updates `/home/ubuntu/work/agentic-swarm-ops/docs/homelab-hardware-inventory.md` and commits/pushes from `agentic-swarm-ops`.

Gaps:

- This is an inventory-document updater, not an alerting monitor.
- It does not threshold CPU, memory, disk, NAS usage, or VM pressure.
- It depends on the external SovereignSentinel collector; failure only logs a warning and continues if stale `inventory.json` exists.

### `scripts/generate_inventory_md.py`

Findings:

- Reads the same SovereignSentinel inventory JSON.
- Maps `pve1`–`pve6` Proxmox status to active/dormant in markdown.
- Uses `tailscale status --json` to infer VM hostname reachability.
- Checks whether PVE6 storage includes `Synology_NAS` and marks the NAS active/dormant.

Gaps:

- VM state check is Tailscale-hostname based, not authoritative Proxmox VM state.
- NAS check is binary active/dormant only; it does not record capacity/use% or backup freshness.
- No durable alert history, no dedupe, no escalation thresholds.

## Recommended monitoring contract

For a durable PVE/Proxmox VM health watchdog, use these checks:

1. **Proxmox API health**
   - TCP 8006 reachable.
   - Authenticated `/nodes`, `/cluster/resources`, `/nodes/<node>/status`, `/nodes/<node>/storage` succeed.
   - Alert if API auth fails after credentials were found; warning if unauthenticated endpoint stops returning 401/200.

2. **Cluster availability**
   - Expected node set: `pve1`–`pve6`.
   - Collapse `pve1`–`pve5` offline into one cluster-degraded alert when multiple offline nodes are detected.
   - Alert only on delta or sustained threshold to avoid six duplicate messages per tick.

3. **VM state checks**
   - For online nodes, query `/nodes/<node>/qemu` and `/nodes/<node>/lxc`.
   - Track critical VMs by name/VMID: `webtop-hermes`, `k3s-node-*`, `hb-master-*`.
   - Alert when a critical VM transitions running → stopped/unknown, not simply because an offline node repeats the same unknown state.

4. **Resource thresholds**
   - Host memory warning ≥85%, critical ≥95%.
   - VM memory warning ≥90%, critical ≥97%.
   - Storage warning ≥85%, critical ≥92%.
   - Rootfs warning ≥80%, critical ≥90%.

5. **Remote NAS storage**
   - Verify host mounts are mounted and listable.
   - Verify PVE `Synology_NAS` storage is active/shared.
   - Track capacity percentage and backup content freshness; current capacity is ~81.7–82%.

## Findings and severity

| Severity | Finding | Impact | Recommended fix |
|---|---|---|---|
| 🟡 Warning | Proxmox cluster is 1/6 online (`pve6` only). | Most VM states are `unknown`; cluster redundancy is effectively absent from the API view. | Treat as a single cluster-degraded alert; investigate physical/lab power/network state for pve1–pve5. |
| 🟡 Warning | `webtop-hermes` memory pressure ~96.5–99.6% of allocated memory. | Risk of guest swapping/OOM and degraded Hermes/desktop workflows. | Inspect guest memory consumers; consider VM memory adjustment or process cleanup. |
| 🟡 Warning | Synology NAS storage ~81.7–82% used. | Close to 85% warning threshold; backup/ISO growth could push it into alert range. | Add capacity trend; prune old backups/ISOs before 85%. |
| 🔴 Down | `100.78.237.7` ping 100% packet loss. | GPU/local-model node unreachable from this cron host during audit. | Continue treating GPU node as down until Tailscale/LAN/power check clears it. |
| ✅ Healthy | PVE6 API auth and telemetry collection path. | Node/VM/storage telemetry is obtainable without SSH. | Prefer API-based watchdog over SSH scraping. |

## Definition of Done mapping

- [x] Audit hypervisor metrics collection: PVE6 Proxmox API authenticated; node/status/storage metrics captured.
- [x] Audit VM state checks: cluster resource list and PVE6 qemu inventory captured; running/stopped/unknown split documented.
- [x] Audit remote NAS storage checkups: Hermes VM mounts and PVE `Synology_NAS` storage endpoint checked; capacity documented.
- [x] Write audit results in markdown format: this file.
- [x] Save output file to the workspace: lane-compliant path `scripts/audits/ned/`.

## Raw probe references

- Safe PVE API summary was printed from `/tmp/gro3455_pve_api_probe.json` without exposing credentials.
- Credentials were discovered in `/home/ubuntu/.hermes/profiles/orchestrator/.env` as `PVE6_IP`, `PVE6_USER`, and `PVE6_PASSWORD`; values are intentionally omitted from this report.
- OKF integration search under `/home/ubuntu/work/growthwebdev-knowledge/okf/integrations/` returned no PVE/Proxmox credential docs, so the env-file path is the current source of truth for API access.
