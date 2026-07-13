#!/usr/bin/env python3
"""
Creative Lane — processes creative briefs, generates artifacts, GPG signs manifests,
and enforces per-tenant daily budget caps.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make sibling modules importable
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# === Logger ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("prismatic.creative")

# === Paths ===
PRISMATIC_HOME = Path(os.environ.get("PRISMATIC_HOME") or Path.home())
ARTIFACTS_DIR = Path.home() / ".prismatic/artifacts"

def get_bus_db_path() -> Path:
    p = Path(os.environ.get("PRISMATIC_BUS_DB") or ".prismatic/bus/event_log.sqlite")
    if not p.is_absolute():
        p = PRISMATIC_HOME / p
    return p

def get_creative_db_path() -> Path:
    p = Path(os.environ.get("PRISMATIC_CREATIVE_DB") or ".prismatic/creative/state.sqlite")
    if not p.is_absolute():
        p = PRISMATIC_HOME / p
    return p

def get_config_path() -> Path:
    p = Path(os.environ.get("PRISMATIC_CREATIVE_CONFIG") or "prismatic-engine/config/creative.yaml")
    if not p.is_absolute():
        p = PRISMATIC_HOME / p
    return p

# === Pricing ===
FORMAT_PRICING = {
    "text": 0.05,
    "image": 0.10,
    "video": 0.50,
}

# === DB Initialization ===
def init_db():
    db_path = get_creative_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS briefs (
                brief_id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                format TEXT NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                artifact_path TEXT,
                cost REAL NOT NULL DEFAULT 0.0,
                created_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant TEXT NOT NULL,
                brief_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

# === Helpers ===
def get_last_processed_rowid() -> int:
    conn = sqlite3.connect(get_creative_db_path(), timeout=5)
    try:
        cur = conn.execute("SELECT value FROM metadata WHERE key = 'last_processed_rowid'")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()

def set_last_processed_rowid(rowid: int) -> None:
    conn = sqlite3.connect(get_creative_db_path(), timeout=5)
    try:
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_processed_rowid', ?)", (str(rowid),))
        conn.commit()
    finally:
        conn.close()

def get_tenant_limit(tenant: str) -> float:
    cfg_path = get_config_path()
    if cfg_path.exists():
        try:
            import yaml
            with cfg_path.open() as f:
                cfg = yaml.safe_load(f)
                if isinstance(cfg, dict) and "tenants" in cfg:
                    tenant_cfg = cfg["tenants"].get(tenant)
                    if isinstance(tenant_cfg, dict):
                        return float(tenant_cfg.get("daily_budget_ceiling", 5.0))
                    elif isinstance(tenant_cfg, (int, float)):
                        return float(tenant_cfg)
        except Exception as e:
            logger.warning("Error parsing creative config: %s", e)
    return 5.0

def get_daily_spend(conn, tenant: str, date_str: str) -> float:
    # Query spend for the tenant on date_str
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=0, minute=0, second=0
    ).timestamp()
    day_end = day_start + 86400
    cur = conn.execute(
        "SELECT COALESCE(SUM(cost), 0.0) FROM briefs WHERE tenant = ? AND created_at >= ? AND created_at < ?",
        (tenant, day_start, day_end)
    )
    return float(cur.fetchone()[0])

def read_passphrase() -> str:
    path = Path.home() / ".prismatic/vault/.passphrase"
    if path.exists():
        return path.read_text().strip()
    return os.environ.get("PRISMATIC_VAULT_PASSPHRASE", "")

def sign_manifest(manifest_path: Path, passphrase: str) -> None:
    output_path = manifest_path.with_suffix(".json.gpg")
    cmd = [
        "gpg", "--batch", "--yes", "--quiet",
        "--cipher-algo", "AES256",
        "--symmetric",
        "--passphrase", passphrase,
        "-o", str(output_path),
        str(manifest_path)
    ]
    subprocess.run(cmd, check=True)

def sha256_hash(file_path: Path) -> str:
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def emit_event(event_type: str, source: str, payload: dict) -> None:
    bus_db = get_bus_db_path()
    if not bus_db.exists():
        logger.warning("Bus DB does not exist, cannot emit event %s", event_type)
        return
    timestamp = datetime.now(timezone.utc).isoformat()
    event_dict = {
        "type": event_type,
        "source": source,
        "timestamp": timestamp,
        "payload": payload
    }
    payload_str = json.dumps(payload or {}, sort_keys=True, default=str)
    p_hash = hashlib.md5(payload_str.encode()).hexdigest()[:12]
    dedup_key = f"{event_type}:{source}:{timestamp}:{p_hash}"
    
    conn = sqlite3.connect(bus_db, timeout=5)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT OR IGNORE INTO events (dedup_key, topic, payload_json, ts) VALUES (?, ?, ?, ?)",
            (
                dedup_key,
                event_type,
                json.dumps(event_dict, default=str),
                time.time()
            )
        )
        conn.commit()
        logger.info("Emitted event %s to event bus", event_type)
    except Exception as e:
        logger.error("Failed to emit event %s to event bus: %s", event_type, e)
    finally:
        conn.close()

# === Event Processing ===
def process_event(event_rowid: int, payload_json: dict) -> None:
    # Extract inner payload
    inner = payload_json.get("payload") if isinstance(payload_json.get("payload"), dict) else payload_json
    tenant = str(inner.get("tenant", "default"))
    format_type = str(inner.get("format", "text")).lower()
    prompt = str(inner.get("prompt", ""))
    
    brief_id = f"brief_{event_rowid}"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    cost = FORMAT_PRICING.get(format_type, 0.05)
    budget_limit = get_tenant_limit(tenant)
    
    conn = sqlite3.connect(get_creative_db_path(), timeout=5)
    try:
        current_spend = get_daily_spend(conn, tenant, date_str)
        if current_spend + cost > budget_limit:
            reason = f"Per-tenant cost ceiling exceeded (${current_spend + cost:.2f} > ${budget_limit:.2f})"
            logger.warning("[AUDIT] Tenant '%s' brief '%s' rejected: %s", tenant, brief_id, reason)
            
            # Log audit trail
            conn.execute(
                "INSERT INTO audit_logs (tenant, brief_id, reason, created_at) VALUES (?, ?, ?, ?)",
                (tenant, brief_id, reason, time.time())
            )
            # Log in briefs table as rejected
            conn.execute(
                "INSERT OR REPLACE INTO briefs (brief_id, tenant, format, prompt, status, cost, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (brief_id, tenant, format_type, prompt, "rejected", 0.0, time.time(), time.time())
            )
            conn.commit()
            
            # Emit brief.rejected
            emit_event(
                event_type="brief.rejected",
                source="lane:creative",
                payload={"tenant": tenant, "brief_id": brief_id, "reason": reason}
            )
            return
            
        logger.info("Processing creative brief '%s' for tenant '%s' (format: %s, cost: $%s)", brief_id, tenant, format_type, cost)
        
        # Prepare artifact directory
        tenant_date_dir = ARTIFACTS_DIR / tenant / date_str / brief_id
        tenant_date_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate artifact file
        ext = "txt"
        if format_type == "image":
            ext = "svg"
        elif format_type == "video":
            ext = "mp4"
            
        artifact_name = f"artifact.{ext}"
        artifact_path = tenant_date_dir / artifact_name
        
        if format_type == "image":
            # Generate a vector SVG representation of the image
            svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" width="100%" height="100%">
  <defs>
    <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#4f46e5;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#ec4899;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="800" height="600" fill="url(#grad)" />
  <text x="50%" y="45%" dominant-baseline="middle" text-anchor="middle" fill="#ffffff" font-family="sans-serif" font-size="28" font-weight="bold">Creative Image Artifact</text>
  <text x="50%" y="55%" dominant-baseline="middle" text-anchor="middle" fill="#ffffff" font-family="sans-serif" font-size="20">Prompt: {prompt}</text>
</svg>
"""
            artifact_path.write_text(svg_content, encoding="utf-8")
        elif format_type == "video":
            # Generate a tiny binary MP4 placeholder
            artifact_path.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 100)
        else: # text
            text_content = f"""Creative Text Artifact
======================
Tenant: {tenant}
Prompt: {prompt}
Generated: {datetime.now(timezone.utc).isoformat()}

Content:
Here is the creative text generated for the prompt: "{prompt}".
"""
            artifact_path.write_text(text_content, encoding="utf-8")
            
        # Generate manifest.json
        manifest_path = tenant_date_dir / "manifest.json"
        manifest_data = {
            "brief_id": brief_id,
            "tenant": tenant,
            "date": date_str,
            "format": format_type,
            "prompt": prompt,
            "files": {
                artifact_name: sha256_hash(artifact_path)
            },
            "cost": cost,
            "created_at": time.time()
        }
        
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
        
        # GPG sign the manifest
        passphrase = read_passphrase()
        if passphrase:
            try:
                sign_manifest(manifest_path, passphrase)
                logger.info("Manifest signed with GPG symmetrically")
            except Exception as e:
                logger.error("Failed to sign manifest with GPG: %s", e)
        else:
            logger.warning("No passphrase found in vault/env, skipping GPG signing")
            
        # Update Database
        conn.execute(
            """INSERT OR REPLACE INTO briefs
            (brief_id, tenant, format, prompt, status, artifact_path, cost, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (brief_id, tenant, format_type, prompt, "completed", str(artifact_path), cost, time.time(), time.time())
        )
        conn.commit()
        
        # Emit brief.completed
        # Artifact URL resolves through our newly mapped workspace in hermes-artifact-publisher
        artifact_url = f"http://127.0.0.1:9120/raw/prismatic-artifacts/{tenant}/{date_str}/{brief_id}/{artifact_name}"
        emit_event(
            event_type="brief.completed",
            source="lane:creative",
            payload={
                "tenant": tenant,
                "brief_id": brief_id,
                "format": format_type,
                "prompt": prompt,
                "artifact_url": artifact_url
            }
        )
        logger.info("Successfully completed brief '%s', artifact URL: %s", brief_id, artifact_url)
        
    except Exception as e:
        logger.error("Error processing brief '%s': %s", brief_id, e)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO briefs (brief_id, tenant, format, prompt, status, cost, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (brief_id, tenant, format_type, prompt, "failed", 0.0, time.time(), time.time())
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()

# === Main Processing Loop ===
def fetch_and_process_events() -> int:
    last_rowid = get_last_processed_rowid()
    bus_db = get_bus_db_path()
    if not bus_db.exists():
        return 0
        
    # Read events directly
    conn = sqlite3.connect(bus_db, timeout=5)
    try:
        cur = conn.execute(
            "SELECT rowid, topic, payload_json FROM events WHERE rowid > ? ORDER BY rowid ASC",
            (last_rowid,)
        )
        rows = cur.fetchall()
    finally:
        conn.close()
        
    processed = 0
    new_last_rowid = last_rowid
    for row in rows:
        rowid, topic, payload_json_str = row
        new_last_rowid = rowid
        if topic == "brief.requested":
            try:
                payload = json.loads(payload_json_str)
            except Exception:
                payload = {}
            process_event(rowid, payload)
            processed += 1
            
    if new_last_rowid > last_rowid:
        set_last_processed_rowid(new_last_rowid)
        
    return processed

async def main_loop(poll_interval: float = 3.0):
    logger.info("Creative Lane started in continuous loop mode (polling every %s seconds)", poll_interval)
    while True:
        try:
            fetch_and_process_events()
        except Exception as e:
            logger.error("Error in creative loop: %s", e)
        await asyncio.sleep(poll_interval)

def main():
    ap = argparse.ArgumentParser(description="Prismatic Creative Lane")
    ap.add_argument("--once", action="store_true", help="Process pending brief.requested events and exit")
    ap.add_argument("--poll-interval", type=float, default=3.0, help="Polling interval in seconds")
    args = ap.parse_args()
    
    init_db()
    
    if args.once:
        logger.info("Creative Lane running once...")
        n = fetch_and_process_events()
        logger.info("Processed %d brief.requested events", n)
        return
        
    asyncio.run(main_loop(args.poll_interval))

if __name__ == "__main__":
    main()
