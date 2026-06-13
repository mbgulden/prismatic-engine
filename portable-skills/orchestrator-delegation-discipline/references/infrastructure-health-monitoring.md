# Infrastructure Health Monitoring Pattern

Silent-when-healthy cron scripts that monitor system resources and alert only on threshold breach. Built June 2026 for AGY resource + Proxmox cluster monitoring.

## Pattern

1. **Script** runs via cron (no_agent: true)
2. **Silent when healthy** — no stdout = no message delivered
3. **Alerts via Telegram bot** when thresholds breach
4. **Token from .env file** — reads bot token at runtime, no env var dependency

## Implementation

### AGY Resource Monitor

```python
#!/usr/bin/env python3
"""Silent unless AGY instances exceed thresholds."""
# Check: ps aux | grep agy-bin | wc -l
# Check: uptime → load average
# Check: free -m → RAM %
# Thresholds: 12+ AGY → CRITICAL, 10+ AGY → HIGH
#             load > 15 → CRITICAL, load > 10 → HIGH
#             AGY CPU > 80% → CRITICAL, AGY CPU > 60% → HIGH
#             RAM > 90% → CRITICAL
```

### Proxmox Cluster Monitor

```python
#!/usr/bin/env python3
"""SSH into cluster nodes, check load/RAM/VM count."""
# Nodes: pve1-pve6 via Tailscale IPs
# Check: ssh root@<ip> 'uptime; free -h; qm list'
# Thresholds: load > 8 CRITICAL, > 5 HIGH
#             RAM > 90% CRITICAL, > 80% HIGH
```

### Telegram Delivery (Autobot)

```python
def send_telegram(text):
    token = read_from_env_file("/home/ubuntu/work/next-step-bot/.env")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": "8190664947", "text": text, "parse_mode": "Markdown"})
    urllib.request.urlopen(urllib.request.Request(url, data=payload.encode(), 
        headers={"Content-Type": "application/json"}), timeout=10)
```

## Cron Setup

```bash
# AGY Resource Monitor — every 5 min, silent when healthy
cronjob create no_agent=true schedule="every 5m" \
  script="agy_resource_monitor.py" \
  deliver="local"  # script handles delivery via Telegram API

# Proxmox Cluster Monitor — every 10 min
cronjob create no_agent=true schedule="every 10m" \
  script="proxmox_cluster_monitor.py" \
  deliver="local"
```

## Files

- `/home/ubuntu/work/agentic-swarm-ops/ops/agy_resource_monitor.py`
- `/home/ubuntu/work/agentic-swarm-ops/ops/proxmox_cluster_monitor.py`
- Symlinked to `/home/ubuntu/.hermes/scripts/` for cron access

## Proxmox Access

Cluster nodes accessible via Tailscale SSH:
- pve1: 100.114.18.91 (377 GiB RAM, 2 VMs)
- pve2: 100.119.225.27 (62 GiB, 2 VMs) — watch RAM (currently 84%)
- pve3: 100.115.231.48 (62 GiB, 1 VM)
- pve6: 100.90.63.4 (188 GiB, 3 VMs including webtop-hermes)
- pve4, pve5: offline (last seen 45+ days)

SSH command pattern:
```bash
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@100.114.18.91 'uptime; free -h; qm list'
```

## Pitfalls

- **Tailscale SSH requires browser auth** — if connection fails with "visit https://login.tailscale.com/...", user must authorize in browser
- **sshpass not installed** — use key-based or Tailscale SSH instead
- **Script must be symlinked** — cron requires scripts in `~/.hermes/scripts/`, not absolute paths. Use `ln -sf <source> ~/.hermes/scripts/<name>.py`
- **Token from .env** — cron jobs don't inherit environment variables. Read the .env file directly in the script, don't rely on `os.environ`
