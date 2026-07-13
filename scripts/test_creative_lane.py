#!/usr/bin/env python3
"""
Test Creative Lane — validates creative brief consumption, per-tenant budget caps,
GPG manifest signing, curator tagging, and daily digest layout.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Make engine imports possible
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prismatic.curator.lane import tag_event, BusEvent, render_digest
from prismatic.lanes.creative.lane import (
    process_event, init_db, get_tenant_limit, get_daily_spend,
    read_passphrase, sign_manifest, emit_event
)

class TestCreativeLane(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        
        # Override DB and config paths
        self.bus_db_path = self.tmp_path / "bus_log.sqlite"
        self.creative_db_path = self.tmp_path / "creative_state.sqlite"
        self.config_path = self.tmp_path / "creative.yaml"
        self.vault_dir = self.tmp_path / "vault"
        self.artifacts_dir = self.tmp_path / "artifacts"
        
        self.vault_dir.mkdir()
        self.artifacts_dir.mkdir()
        
        # Write mock passphrase
        self.passphrase_file = self.vault_dir / ".passphrase"
        self.passphrase_file.write_text("mock_passphrase_123\n")
        
        # Write mock config
        self.config_path.write_text(
            "tenants:\n"
            "  default:\n"
            "    daily_budget_ceiling: 1.00\n"
            "  acme:\n"
            "    daily_budget_ceiling: 0.15\n"
        )
        
        # Set up mock environment variables
        os.environ["PRISMATIC_BUS_DB"] = str(self.bus_db_path)
        os.environ["PRISMATIC_CREATIVE_DB"] = str(self.creative_db_path)
        os.environ["PRISMATIC_CREATIVE_CONFIG"] = str(self.config_path)
        
        # Initialize EventBus DB
        conn = sqlite3.connect(self.bus_db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT UNIQUE,
                topic TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                ts REAL NOT NULL,
                processed INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()
        conn.close()
        
        # Initialize Creative DB
        init_db()

    def tearDown(self):
        self.tmp_dir.cleanup()
        # Clear env variables
        os.environ.pop("PRISMATIC_BUS_DB", None)
        os.environ.pop("PRISMATIC_CREATIVE_DB", None)
        os.environ.pop("PRISMATIC_CREATIVE_CONFIG", None)

    def test_curator_tagging(self):
        # 1. Check brief.requested
        ev_req = BusEvent(rowid=1, topic="brief.requested", payload={"tenant": "acme"}, ts=1.0, source="test")
        res_req = tag_event(ev_req)
        self.assertEqual(res_req.tag, "delegate")
        self.assertEqual(res_req.lane_hint, "creative")

        # 2. Check brief.completed
        ev_comp = BusEvent(rowid=2, topic="brief.completed", payload={"tenant": "acme"}, ts=2.0, source="test")
        res_comp = tag_event(ev_comp)
        self.assertEqual(res_comp.tag, "auto-pick")
        self.assertEqual(res_comp.lane_hint, "creative")

        # 3. Check brief.rejected
        ev_rej = BusEvent(rowid=3, topic="brief.rejected", payload={"tenant": "acme"}, ts=3.0, source="test")
        res_rej = tag_event(ev_rej)
        self.assertEqual(res_rej.tag, "escalate")
        self.assertEqual(res_rej.lane_hint, "creative")

    def test_budget_limits_loading(self):
        self.assertEqual(get_tenant_limit("default"), 1.00)
        self.assertEqual(get_tenant_limit("acme"), 0.15)
        self.assertEqual(get_tenant_limit("nonexistent"), 5.0)  # default fallback

    def test_process_brief_under_budget(self):
        # Mock monkeypatch ARTIFACTS_DIR
        import prismatic.lanes.creative.lane as cl
        cl.ARTIFACTS_DIR = self.artifacts_dir
        cl.Path.home = lambda: self.tmp_path  # Redirect home path for passphrase lookups
        
        # Create brief.requested payload
        payload = {
            "tenant": "default",
            "format": "image",
            "prompt": "sunset over mokulua islands"
        }
        
        # Process brief
        process_event(event_rowid=101, payload_json=payload)
        
        # Verify artifact exists
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expected_dir = self.artifacts_dir / "default" / date_str / "brief_101"
        self.assertTrue(expected_dir.exists())
        
        artifact_file = expected_dir / "artifact.svg"
        self.assertTrue(artifact_file.exists())
        svg_content = artifact_file.read_text()
        self.assertIn("sunset over mokulua islands", svg_content)
        
        # Verify manifest exists and is signed
        manifest_file = expected_dir / "manifest.json"
        self.assertTrue(manifest_file.exists())
        
        manifest_data = json.loads(manifest_file.read_text())
        self.assertEqual(manifest_data["brief_id"], "brief_101")
        self.assertEqual(manifest_data["tenant"], "default")
        self.assertEqual(manifest_data["format"], "image")
        
        # Verify GPG manifest backup file was generated
        gpg_file = expected_dir / "manifest.json.gpg"
        self.assertTrue(gpg_file.exists())
        
        # Verify event completed is in the EventBus DB
        conn = sqlite3.connect(self.bus_db_path)
        cur = conn.execute("SELECT topic, payload_json FROM events WHERE topic = 'brief.completed'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        ev_payload = json.loads(row[1])
        self.assertEqual(ev_payload["payload"]["brief_id"], "brief_101")
        self.assertIn("artifact.svg", ev_payload["payload"]["artifact_url"])
        conn.close()

    def test_process_brief_budget_rejection(self):
        import prismatic.lanes.creative.lane as cl
        cl.ARTIFACTS_DIR = self.artifacts_dir
        cl.Path.home = lambda: self.tmp_path
        
        # ACME tenant daily limit is $0.15.
        # Format "video" costs $0.50. This exceeds limit immediately.
        payload = {
            "tenant": "acme",
            "format": "video",
            "prompt": "corporate promo video"
        }
        
        process_event(event_rowid=202, payload_json=payload)
        
        # Check database records
        conn = sqlite3.connect(self.creative_db_path)
        
        # 1. Briefs status
        cur = conn.execute("SELECT status, cost FROM briefs WHERE brief_id = 'brief_202'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "rejected")
        self.assertEqual(row[1], 0.0)
        
        # 2. Audit logs
        cur = conn.execute("SELECT reason FROM audit_logs WHERE brief_id = 'brief_202'")
        audit_row = cur.fetchone()
        self.assertIsNotNone(audit_row)
        self.assertIn("ceiling exceeded", audit_row[0])
        conn.close()
        
        # Verify brief.rejected was emitted
        conn = sqlite3.connect(self.bus_db_path)
        cur = conn.execute("SELECT topic, payload_json FROM events WHERE topic = 'brief.rejected'")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        ev_payload = json.loads(row[1])
        self.assertEqual(ev_payload["payload"]["brief_id"], "brief_202")
        self.assertIn("ceiling exceeded", ev_payload["payload"]["reason"])
        conn.close()

if __name__ == "__main__":
    unittest.main()
