# HD Platform — Systemd Service Management

## Service Architecture (as of Jun 2026)

| Service | Port | Systemd Unit | Binary | Status |
|---------|------|-------------|--------|--------|
| API (FastAPI) | 8000 | `hde-api.service` | `.venv/bin/python3 -m uvicorn api.main:app` | ✅ Active |
| Payment | 8002 | `hde-payment.service` | `python3 payment/server.py` | ✅ Active |
| Reports | 8081 | `hde-reports.service` | `python3 reports/server.py` | ✅ Active |
| CF Tunnel | N/A | `cloudflared-hde.service` | cloudflared | ✅ Active |

## Creating a New Systemd Service (Python/FastAPI Pattern)

### 1. Verify the port is free
```bash
ss -tlnp | grep <PORT>
```
If an orphan process holds the port (listening but no response), kill it:
```bash
sudo kill <PID>
```

### 2. Locate the correct Python binary
The HD Platform uses a venv at `$PRISMATIC_HOME/work/hd-platform/.venv/`. Always use `.venv/bin/python3`, never `/usr/bin/python3`.

### 3. Determine WorkingDirectory for relative imports
If the app uses `from .routes import ...` (relative imports), the CWD must be the PARENT of the package:
- `api/main.py` with `from .routes import ...` → WorkingDirectory=`$PRISMATIC_HOME/work/hd-platform`, app path=`api.main:app`
- NOT: WorkingDirectory=`$PRISMATIC_HOME/work/hd-platform/api`, app path=`main:app` (this fails with `ImportError: attempted relative import with no known parent package`)

### 4. Set PYTHONPATH for cross-project dependencies
```ini
Environment=PYTHONPATH=$PRISMATIC_HOME/work/hd-platform:$PRISMATIC_HOME/work/OpenHumanDesignMCP/hd-mcp-server/src
```

### 5. Service unit template
```ini
[Unit]
Description=Human Design Engine — <Name>
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$PRISMATIC_HOME/work/hd-platform
Environment=PYTHONPATH=$PRISMATIC_HOME/work/hd-platform:$PRISMATIC_HOME/work/OpenHumanDesignMCP/hd-mcp-server/src
Environment=PORT=<PORT>
ExecStart=$PRISMATIC_HOME/work/hd-platform/.venv/bin/python3 -m uvicorn <module>:app --host 0.0.0.0 --port <PORT>
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 6. Deploy and verify
```bash
sudo cp /tmp/<unit>.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable <unit>.service
sudo systemctl start <unit>.service
sleep 3
systemctl status <unit>.service --no-pager -l
curl -s http://localhost:<PORT>/ping
```

### 7. If it fails
```bash
sudo journalctl -u <unit>.service --no-pager -n 60
```
Common failures:
- **`No module named uvicorn`** → Wrong python binary. Use `.venv/bin/python3`.
- **`ImportError: attempted relative import with no known parent package`** → WorkingDirectory must be the parent of the package. Use `api.main:app` from the project root, not `main:app` from the api/ directory.
- **DB/Redis connection warnings** → Non-fatal. The `lifespan` handler skips gracefully. Service still starts.

## Health Check Endpoints

| Service | Health URL | Expected Response |
|---------|-----------|-------------------|
| API | `GET /ping` | `{"status":"ok","version":"0.1.0","license":"AGPLv3"}` |
| API | `GET /docs` | Swagger UI HTML (200 OK) |
| Payment | `GET /health` | `HDE Payment Server` |
| Reports | `GET /` | `{"error": "Not found"}` (root, expected) |

## Port Health Verification (One-Liner)
```bash
ss -tlnp 2>/dev/null | grep -E ':(80|8000|8001|8002|8080|8081|8085)' | sort -t: -k2 -n
```
Expected: 80, 8000, 8002, 8080, 8081, 8085 all LISTEN. Port 8001 is DOWN (no listener — secondary API port, not yet configured).
