from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
_loaded_prismatic = sys.modules.get("prismatic")
if _loaded_prismatic is not None:
    module_file = getattr(_loaded_prismatic, "__file__", "") or ""
    if not module_file.startswith(str(_REPO_ROOT)):
        for name in list(sys.modules):
            if name == "prismatic" or name.startswith("prismatic."):
                sys.modules.pop(name, None)

from fastapi.testclient import TestClient

from prismatic.gateway.server import app
from prismatic.native_crons import (
    CRON_STATE_ACTIVE,
    CRON_STATE_DEACTIVATED,
    CRON_STATE_DELETED,
    CRON_STATE_PAUSED,
    NativeCronStore,
    export_system_crontab_lines,
    list_native_crons,
    mutate_native_cron,
)


def test_native_cron_seed_contains_portable_seo_jobs(tmp_path: Path) -> None:
    store = NativeCronStore(tmp_path / "native_crons.json")
    crons = list_native_crons(include_deleted=True, store=store)
    ids = {cron["id"] for cron in crons}

    assert "seo.ubersuggest-token-refresh" in ids
    assert "seo.aot-weekly-rankings" in ids
    assert "seo.aot-competitor-velocity" in ids
    assert "seo.aot-full-sweep" in ids
    refresh = next(cron for cron in crons if cron["id"] == "seo.ubersuggest-token-refresh")
    assert refresh["portable"] is True
    assert refresh["queue_state"] == "queued"
    assert "scripts/pwp" in refresh["display_command"]


def test_pause_resume_stays_in_queue_and_deactivate_moves_out(tmp_path: Path) -> None:
    store = NativeCronStore(tmp_path / "native_crons.json")

    paused = mutate_native_cron("seo.aot-weekly-rankings", "pause", store=store)["cron"]
    assert paused["state"] == CRON_STATE_PAUSED
    assert paused["queue_state"] == "queued"
    assert paused["enabled"] is False

    resumed = mutate_native_cron("seo.aot-weekly-rankings", "resume", store=store)["cron"]
    assert resumed["state"] == CRON_STATE_ACTIVE
    assert resumed["queue_state"] == "queued"
    assert resumed["enabled"] is True

    deactivated = mutate_native_cron("seo.aot-weekly-rankings", "deactivate", store=store)["cron"]
    assert deactivated["state"] == CRON_STATE_DEACTIVATED
    assert deactivated["queue_state"] == "out_of_queue"
    assert deactivated["enabled"] is False

    activated = mutate_native_cron("seo.aot-weekly-rankings", "activate", store=store)["cron"]
    assert activated["state"] == CRON_STATE_ACTIVE
    assert activated["queue_state"] == "queued"


def test_delete_marks_deleted_and_default_list_hides_it(tmp_path: Path) -> None:
    store = NativeCronStore(tmp_path / "native_crons.json")
    deleted = mutate_native_cron("seo.aot-competitor-velocity", "delete", store=store)["cron"]

    assert deleted["state"] == CRON_STATE_DELETED
    assert deleted["queue_state"] == "out_of_queue"
    assert "deleted_at" in deleted and deleted["deleted_at"]
    assert "seo.aot-competitor-velocity" not in {c["id"] for c in list_native_crons(store=store)}
    assert "seo.aot-competitor-velocity" in {c["id"] for c in list_native_crons(include_deleted=True, store=store)}


def test_export_system_crontab_omits_paused_deactivated_deleted_and_manual(tmp_path: Path) -> None:
    store = NativeCronStore(tmp_path / "native_crons.json")
    mutate_native_cron("seo.aot-weekly-rankings", "pause", store=store)
    mutate_native_cron("seo.aot-competitor-velocity", "deactivate", store=store)
    lines = export_system_crontab_lines(store=store)

    joined = "\n".join(lines)
    assert "seo_full_sweep" not in joined
    assert "aot_kpi_tracker" not in joined
    assert "competitor_velocity" not in joined
    assert "scripts/pwp" in joined
    assert lines[0].startswith("0 3 * * *")


def test_gateway_native_cron_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRISMATIC_NATIVE_CRON_STORE", str(tmp_path / "native_crons.json"))
    client = TestClient(app)

    response = client.get("/native-crons")
    assert response.status_code == 200
    crons = response.json()
    assert any(cron["id"] == "seo.ubersuggest-token-refresh" for cron in crons)

    pause = client.post("/native-crons/seo.aot-weekly-rankings/action", json={"action": "pause"})
    assert pause.status_code == 200
    assert pause.json()["cron"]["state"] == CRON_STATE_PAUSED

    deactivate = client.post("/native-crons/seo.aot-weekly-rankings/action", json={"action": "deactivate"})
    assert deactivate.status_code == 200
    assert deactivate.json()["cron"]["queue_state"] == "out_of_queue"

    bad = client.post("/native-crons/seo.aot-weekly-rankings/action", json={"action": "explode"})
    assert bad.status_code == 400

    missing = client.post("/native-crons/nope/action", json={"action": "pause"})
    assert missing.status_code == 404


def test_native_cron_store_persists_json(tmp_path: Path) -> None:
    path = tmp_path / "native_crons.json"
    store = NativeCronStore(path)
    mutate_native_cron("seo.aot-weekly-rankings", "pause", store=store)

    raw = json.loads(path.read_text())
    assert raw["version"] == 1
    stored = next(cron for cron in raw["crons"] if cron["id"] == "seo.aot-weekly-rankings")
    assert stored["state"] == CRON_STATE_PAUSED
    assert stored["queue_state"] == "queued"
