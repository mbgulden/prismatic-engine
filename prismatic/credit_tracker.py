"""
prismatic/credit_tracker.py — AI Ultra Credit Tracker

Tracks Google AI Ultra credit allocation (25K/month), parses generated media
artifacts from Gemini Omni / Veo 3.1 calls, decrements the ledger, and triggers
exhaustion alerts and auto-throttling.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Defaults and Thresholds ──────────────────────────────────
DEFAULT_DB_PATH = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

MONTHLY_ALLOCATION_LIMIT = 25000  # Google AI Ultra credits per month
VELOCITY_WINDOW_HOURS = 1         # Lookback window for burn velocity calculation

# Cost map for media generation (based on docs/provider-playbook-google-antigravity.md)
MEDIA_COST_MAP = {
    "omni-flash-4s": 15,
    "omni-flash-6s": 20,
    "omni-flash-8s": 25,
    "omni-flash-10s": 30,
    "veo-fast": 10,
    "veo-fast-any": 10,
    "veo-quality-8s": 100,
    "veo-quality-10s": 120,
    "veo-quality-any": 100,
}

class AIUltraCreditTracker:
    """Manages credit balance tracking and velocity alerting for Google AI Ultra.

    Saves state in the centralized SQLite database event_router.db.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Create necessary tables for media tracking if they do not exist."""
        db_dir = os.path.dirname(self._db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS telemetry_media_artifacts (
                    filepath        TEXT PRIMARY KEY,
                    file_hash       TEXT,
                    media_type      TEXT NOT NULL,
                    engine          TEXT NOT NULL,
                    duration        REAL DEFAULT 0.0,
                    credits_spent   INTEGER DEFAULT 0,
                    detected_at     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_media_detected_at
                    ON telemetry_media_artifacts(detected_at);

                CREATE TABLE IF NOT EXISTS telemetry_credit_ledger (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL,
                    agent           TEXT NOT NULL,
                    provider        TEXT NOT NULL,
                    model           TEXT,
                    credits_spent   INTEGER NOT NULL,
                    operation       TEXT,
                    recorded_at     TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_credit_ledger_run
                    ON telemetry_credit_ledger(run_id);
            """)
            conn.commit()
        finally:
            conn.close()

    def calculate_monthly_spent(self) -> int:
        """Calculate the total credits spent in the current calendar month for google-antigravity."""
        now = datetime.now(timezone.utc)
        # Get start of the current month in ISO format
        start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()

        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """SELECT SUM(credits_spent) FROM telemetry_credit_ledger
                   WHERE provider = 'google-antigravity' AND recorded_at >= ?""",
                (start_of_month,),
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        finally:
            conn.close()

    def get_remaining_credits(self) -> int:
        """Get the remaining monthly Google AI Ultra credits."""
        spent = self.calculate_monthly_spent()
        return max(0, MONTHLY_ALLOCATION_LIMIT - spent)

    def calculate_burn_velocity(self, lookback_hours: float = 1.0) -> float:
        """Calculate credit burn rate (credits per hour) over the specified lookback window."""
        now = datetime.now(timezone.utc).timestamp()
        cutoff_time = now - (lookback_hours * 3600)
        cutoff_str = datetime.fromtimestamp(cutoff_time, tz=timezone.utc).isoformat()

        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                """SELECT SUM(credits_spent) FROM telemetry_credit_ledger
                   WHERE provider = 'google-antigravity' AND recorded_at >= ?""",
                (cutoff_str,),
            )
            row = cursor.fetchone()
            total_spent = row[0] if row and row[0] is not None else 0
            return total_spent / lookback_hours
        finally:
            conn.close()

    def parse_media_artifacts(self, scan_dir: str, run_id_prefix: str = "media") -> List[Dict[str, Any]]:
        """Scan a directory for new media artifacts from Omni/Veo 3.1 calls.

        If new artifacts are found:
        1. Calculate credit cost.
        2. Insert record into telemetry_media_artifacts to prevent double counting.
        3. Insert credit entry into telemetry_credit_ledger.
        """
        if not os.path.exists(scan_dir):
            return []

        new_artifacts = []
        path_obj = Path(scan_dir)

        # We look for common media file patterns
        media_extensions = {".mp4", ".png", ".jpg", ".wav", ".mp3"}
        
        # Connect to DB to check already processed files
        conn = sqlite3.connect(self._db_path)
        try:
            for file_path in path_obj.rglob("*"):
                if file_path.suffix.lower() not in media_extensions:
                    continue

                abs_path = str(file_path.resolve())

                # Check if already processed
                cursor = conn.execute(
                    "SELECT 1 FROM telemetry_media_artifacts WHERE filepath = ?",
                    (abs_path,),
                )
                if cursor.fetchone():
                    continue

                # Read or estimate file metadata
                metadata = self._resolve_metadata(file_path)
                
                # Double check if we can parse a matching JSON metadata file
                json_meta_path = file_path.with_suffix(".json")
                if json_meta_path.exists():
                    try:
                        with open(json_meta_path, "r") as f:
                            json_data = json.load(f)
                            if "engine" in json_data:
                                metadata["engine"] = json_data["engine"]
                            if "duration" in json_data:
                                metadata["duration"] = float(json_data["duration"])
                            if "media_type" in json_data:
                                metadata["media_type"] = json_data["media_type"]
                            if "credits_spent" in json_data:
                                metadata["credits_spent"] = int(json_data["credits_spent"])
                    except Exception:
                        pass  # Fallback to estimated metadata

                # Determine cost if not explicitly provided
                if metadata["credits_spent"] == 0:
                    metadata["credits_spent"] = self._estimate_credit_cost(
                        metadata["media_type"], metadata["engine"], metadata["duration"]
                    )

                # Compute file hash to ensure uniqueness
                file_hash = self._compute_file_hash(file_path)

                # Save artifact to database
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """INSERT OR REPLACE INTO telemetry_media_artifacts
                       (filepath, file_hash, media_type, engine, duration, credits_spent, detected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        abs_path,
                        file_hash,
                        metadata["media_type"],
                        metadata["engine"],
                        metadata["duration"],
                        metadata["credits_spent"],
                        now_str,
                    ),
                )

                # Record expenditure in the credit ledger
                run_id = f"{run_id_prefix}-{hashlib.md5(abs_path.encode()).hexdigest()[:8]}"
                conn.execute(
                    """INSERT INTO telemetry_credit_ledger
                       (run_id, agent, provider, model, credits_spent, operation, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        "agent:agy",
                        "google-antigravity",
                        metadata["engine"],
                        metadata["credits_spent"],
                        f"media_generation_{metadata['media_type']}",
                        now_str,
                    ),
                )
                
                metadata["filepath"] = abs_path
                new_artifacts.append(metadata)

            conn.commit()
        finally:
            conn.close()

        return new_artifacts

    def evaluate_exhaustion_warning(self, lookback_hours: float = 1.0) -> Optional[Dict[str, Any]]:
        """Evaluate if the current burn velocity predicts exhaustion within 24 hours.

        Returns a dictionary with alert details if triggered, or None.
        """
        remaining = self.get_remaining_credits()
        velocity = self.calculate_burn_velocity(lookback_hours=lookback_hours)

        if velocity <= 0.0:
            return None

        hours_to_exhaustion = remaining / velocity
        
        if hours_to_exhaustion < 24.0:
            alert_details = {
                "triggered": True,
                "remaining_credits": remaining,
                "burn_velocity": velocity,
                "hours_to_exhaustion": hours_to_exhaustion,
                "severity": "CRITICAL" if hours_to_exhaustion < 6.0 else "WARNING",
                "message": (
                    f"Google AI Ultra Credit Exhaustion Warning: remaining credits ({remaining}) "
                    f"will be exhausted in {hours_to_exhaustion:.2f} hours based on current burn "
                    f"velocity of {velocity:.2f} credits/hour."
                ),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            # Log to overview dashboard file
            self._log_to_dashboard(alert_details)
            return alert_details

        return None

    def post_linear_comment(self, alert: Dict[str, Any], issue_id: str, api_key: str | None = None) -> bool:
        """Post alert comment to Linear. 

        Since we do not have API access by default, this prints to stdout/stderr 
        and acts as a mock/safeguard, but implements the actual mutation.
        """
        body = (
            f"## 🚨 credit exhaustion alert: google-antigravity\n\n"
            f"**{alert['message']}**\n\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| Severity | **{alert['severity']}** |\n"
            f"| Remaining Credits | {alert['remaining_credits']} / {MONTHLY_ALLOCATION_LIMIT} |\n"
            f"| Burn Velocity | {alert['burn_velocity']:.2f} credits/hr |\n"
            f"| Est. Exhaustion | **In {alert['hours_to_exhaustion']:.2f} hours** |\n"
        )
        
        print(f"\n[Linear Alert Mock] Post to {issue_id}:\n{body}\n")

        if not api_key:
            return False

        # GRO-2053: gate the Linear API call through LinearBudget so the
        # dispatcher (or any consumer) can't silently burn the 2500/hr budget.
        # Pattern from GRO-2034 fix in agent_dispatcher.py::_linear_gql.
        try:
            from prismatic.linear.budget import linear_budget
            if not linear_budget.check_and_consume("prismatic.credit_tracker"):
                print(
                    "[credit_tracker] Linear API budget exceeded — skipping comment post",
                    file=sys.stderr,
                )
                return False
        except ImportError:
            # LinearBudget not importable (e.g., tests, partial install) — log
            # warning and proceed so we don't break unrelated code paths.
            print(
                "[credit_tracker] WARNING: LinearBudget not importable — proceeding without budget gate",
                file=sys.stderr,
            )

        payload = json.dumps({
            "query": (
                "mutation { commentCreate(input: "
                f'{{ issueId: "{issue_id}", body: "{body}" }}'
                ") { success } }"
            ),
        })
        try:
            result = subprocess.run([
                "curl", "-s", "-X", "POST",
                "https://api.linear.app/graphql",
                "-H", f"Authorization: {api_key}",
                "-H", "Content-Type: application/json",
                "-d", payload,
            ], capture_output=True, text=True, timeout=15)
            resp = json.loads(result.stdout)
            ok = resp.get("data", {}).get("commentCreate", {}).get("success")
            return bool(ok)
        except Exception as e:
            print(f"[credit_tracker] Failed to post to Linear API: {e}", file=sys.stderr)
            return False

    # ── Helpers ──────────────────────────────────────────────

    def _resolve_metadata(self, file_path: Path) -> Dict[str, Any]:
        """Infer basic media attributes from file path."""
        ext = file_path.suffix.lower()
        if ext == ".mp4":
            return {"media_type": "video", "engine": "veo-fast", "duration": 5.0, "credits_spent": 0}
        elif ext in {".png", ".jpg"}:
            return {"media_type": "image", "engine": "omni-flash-4s", "duration": 4.0, "credits_spent": 0}
        elif ext in {".wav", ".mp3"}:
            return {"media_type": "audio", "engine": "omni-audio", "duration": 5.0, "credits_spent": 0}
        else:
            return {"media_type": "unknown", "engine": "unknown", "duration": 0.0, "credits_spent": 0}

    def _estimate_credit_cost(self, media_type: str, engine: str, duration: float) -> int:
        """Map engine and duration to the credit cost structure."""
        # Find exact key
        if engine in MEDIA_COST_MAP:
            return MEDIA_COST_MAP[engine]

        # Duration-based mapping
        if media_type == "video":
            if "quality" in engine:
                if duration > 8.0:
                    return MEDIA_COST_MAP["veo-quality-10s"]
                return MEDIA_COST_MAP["veo-quality-8s"]
            return MEDIA_COST_MAP["veo-fast-any"]
        
        elif media_type == "image":
            # Omni Flash ranges
            if duration <= 4.0:
                return MEDIA_COST_MAP["omni-flash-4s"]
            elif duration <= 6.0:
                return MEDIA_COST_MAP["omni-flash-6s"]
            elif duration <= 8.0:
                return MEDIA_COST_MAP["omni-flash-8s"]
            else:
                return MEDIA_COST_MAP["omni-flash-10s"]

        return 5  # default fallback

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute MD5 hash of the file."""
        hasher = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return file_path.name  # fallback to name

    def _log_to_dashboard(self, alert: Dict[str, Any]) -> None:
        """Append the alert to the overview file and print log."""
        state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
        overview_txt = os.path.join(state_dir, "overview.txt")
        os.makedirs(state_dir, exist_ok=True)
        
        log_line = f"[{alert['timestamp']}] [{alert['severity']} ALERT] {alert['message']}\n"
        try:
            with open(overview_txt, "a") as f:
                f.write(log_line)
        except Exception:
            pass
        
        print(f"\n[telemetry:alert] {alert['message']}\n")
