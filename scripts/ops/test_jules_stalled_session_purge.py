from __future__ import annotations

import json
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jules_stalled_session_purge as purge


def test_parse_jules_sessions_accepts_truncated_awaiting_status() -> None:
    output = """
           ID                                    Description                                    Repo                Last active                Status
 772936427268296840      GRO-3454: [AUDIT-JULES] PR Integration & Branch Verification  mbgulden/agentic-swar…  14h30m56s ago           Awaiting User F
 15755719818385697302    GRO-3452: audit completed                                  mbgulden/agentic-swar…  13h39m13s ago           Completed
"""

    sessions = purge.parse_jules_sessions(output)

    assert [s.session_id for s in sessions] == [
        "772936427268296840",
        "15755719818385697302",
    ]
    assert sessions[0].status == "Awaiting User Feedback"
    assert sessions[1].status == "Completed"


def test_decide_requires_tracked_json_and_threshold() -> None:
    now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
    sessions = [
        purge.JulesSession(
            "111111111111111",
            "111111111111111 Awaiting User F",
            "Awaiting User Feedback",
        ),
        purge.JulesSession(
            "222222222222222",
            "222222222222222 Awaiting User F",
            "Awaiting User Feedback",
        ),
        purge.JulesSession("333333333333333", "333333333333333 Completed", "Completed"),
    ]
    tracked = {
        "111111111111111": purge.TrackedSession(
            "111111111111111", now - timedelta(hours=25), "/tmp/state.json", "timestamp"
        ),
        "333333333333333": purge.TrackedSession(
            "333333333333333", now - timedelta(hours=99), "/tmp/state.json", "timestamp"
        ),
    }

    decisions = purge.decide(sessions, tracked, 24, now)

    assert [(d.session.session_id, d.action, d.reason) for d in decisions] == [
        ("111111111111111", "purge", "awaiting_feedback_older_than_threshold"),
        ("222222222222222", "skip", "no_tracked_state_json"),
        ("333333333333333", "skip", "status_not_awaiting_user_feedback"),
    ]


def test_load_tracked_sessions_from_manifest_shape(tmp_path: Path) -> None:
    state = tmp_path / "session-manifest.json"
    state.write_text(
        json.dumps(
            {
                "units": [
                    {
                        "created_at": "2026-07-06T00:00:00Z",
                        "sessions": [
                            {
                                "system": "jules",
                                "id": "444444444444444",
                                "url": "https://jules.google.com/session/444444444444444",
                            }
                        ],
                    }
                ]
            }
        )
    )

    tracked = purge.load_tracked_sessions([str(state)])

    assert tracked["444444444444444"].launched_at == datetime(
        2026, 7, 6, tzinfo=timezone.utc
    )
    assert tracked["444444444444444"].source_path == str(state)


def test_execute_mode_deletes_only_candidates(tmp_path: Path) -> None:
    list_output = tmp_path / "list.txt"
    list_output.write_text(
        "111111111111111 old task repo 2 days ago Awaiting User F\n"
        "222222222222222 fresh task repo 2h ago Awaiting User F\n"
        "333333333333333 done task repo 2 days ago Completed\n"
    )
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            [
                {"session_id": "111111111111111", "created_at": "2026-07-06T00:00:00Z"},
                {
                    "session_id": "222222222222222",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                {"session_id": "333333333333333", "created_at": "2026-07-06T00:00:00Z"},
            ]
        )
    )
    log = tmp_path / "delete.log"
    fake = tmp_path / "jules"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "if sys.argv[1:3] == ['remote', '--help']:\n"
        "    print('Available Commands:\\n  delete      Delete remote session')\n"
        "    raise SystemExit(0)\n"
        f"pathlib.Path({str(log)!r}).write_text(' '.join(sys.argv[1:]))\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(purge.__file__)),
            "--execute",
            "--jules-bin",
            str(fake),
            "--list-output-file",
            str(list_output),
            "--state-glob",
            str(state),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text() == "remote delete --session 111111111111111"
    assert "purge_candidates=1 deleted=1 errors=0" in result.stdout


def test_execute_mode_reports_missing_delete_command(tmp_path: Path) -> None:
    list_output = tmp_path / "list.txt"
    list_output.write_text("111111111111111 old task repo 2 days ago Awaiting User F\n")
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            [{"session_id": "111111111111111", "created_at": "2026-07-06T00:00:00Z"}]
        )
    )
    fake = tmp_path / "jules"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:3] == ['remote', '--help']:\n"
        "    print('Available Commands:\\n  list        List remote sessions')\n"
        "    raise SystemExit(0)\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(purge.__file__)),
            "--execute",
            "--jules-bin",
            str(fake),
            "--list-output-file",
            str(list_output),
            "--state-glob",
            str(state),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["purge_candidates"] == 1
    assert payload["deleted"] == []
    assert "not available" in payload["errors"][0]["stderr"]
