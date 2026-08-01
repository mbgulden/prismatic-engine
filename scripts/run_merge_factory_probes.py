"""Custom behavior probe runner for GRO-4111 Merge Factory tests.

Runs target tests and writes verbose outputs to the log file.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Insert workspace root into python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.test_merge_factory import (
    test_token_auth_resolution,
    test_custom_env_tokens,
    test_policy_mutations,
    test_cohort_mutations,
    test_lease_global_cap_and_concurrency,
    test_lease_stale_takeover_and_expiry,
    test_old_backlog_exclusion_when_paused,
    test_judge_attestations_and_transitions,
    test_merge_locks_and_bindings,
    test_event_idempotency,
    test_no_config_fail_closed_auth,
    test_default_known_token_denial,
    test_runtime_key_rotation_and_removal,
    test_malformed_and_duplicate_config,
    test_stage_mismatch_and_invalid_stage,
    test_excessive_ttl,
    test_installed_package_api_import_and_runtime,
)


class MockMonkeyPatch:
    def __init__(self) -> None:
        self.env = os.environ.copy()

    def setenv(self, key: str, value: str) -> None:
        os.environ[key] = value

    def delenv(self, key: str, raising: bool = True) -> None:
        if key in os.environ:
            del os.environ[key]

    def restore(self) -> None:
        os.environ.clear()
        os.environ.update(self.env)


def run_all_probes(log_path: Path) -> bool:
    log_file = open(log_path, "w", encoding="utf-8")
    log_file.write("Merge Factory Behavior Probes log\n")
    log_file.write("=" * 80 + "\n\n")

    success = True
    from prismatic.core.merge_factory import MergeFactoryStore

    tests = [
        ("test_token_auth_resolution", lambda s: test_token_auth_resolution()),
        ("test_custom_env_tokens", lambda s: None),  # handled separately
        ("test_policy_mutations", lambda s: test_policy_mutations(s)),
        ("test_cohort_mutations", lambda s: test_cohort_mutations(s)),
        (
            "test_lease_global_cap_and_concurrency",
            lambda s: test_lease_global_cap_and_concurrency(s),
        ),
        (
            "test_lease_stale_takeover_and_expiry",
            lambda s: test_lease_stale_takeover_and_expiry(s),
        ),
        (
            "test_old_backlog_exclusion_when_paused",
            lambda s: test_old_backlog_exclusion_when_paused(s),
        ),
        (
            "test_judge_attestations_and_transitions",
            lambda s: test_judge_attestations_and_transitions(s),
        ),
        ("test_merge_locks_and_bindings", lambda s: test_merge_locks_and_bindings(s)),
        ("test_event_idempotency", lambda s: test_event_idempotency(s)),
        ("test_no_config_fail_closed_auth", lambda s: None),
        ("test_default_known_token_denial", lambda s: None),
        ("test_runtime_key_rotation_and_removal", lambda s: None),
        ("test_malformed_and_duplicate_config", lambda s: None),
        (
            "test_stage_mismatch_and_invalid_stage",
            lambda s: test_stage_mismatch_and_invalid_stage(s),
        ),
        ("test_excessive_ttl", lambda s: test_excessive_ttl(s)),
        ("test_installed_package_api_import_and_runtime", lambda s: None),
    ]

    for name, func in tests:
        # Create a fresh temporary DB for each test to ensure test isolation
        temp_dir = tempfile.mkdtemp()
        db_path = Path(temp_dir) / "test_db.sqlite3"
        store_obj = MergeFactoryStore(db_path=db_path)

        log_file.write(f"Running probe: {name} (DB: {db_path})...\n")
        try:
            if name == "test_custom_env_tokens":
                mp = MockMonkeyPatch()
                try:
                    test_custom_env_tokens(mp)
                finally:
                    mp.restore()
            elif name == "test_no_config_fail_closed_auth":
                mp = MockMonkeyPatch()
                try:
                    test_no_config_fail_closed_auth(mp)
                finally:
                    mp.restore()
            elif name == "test_default_known_token_denial":
                mp = MockMonkeyPatch()
                try:
                    test_default_known_token_denial(mp)
                finally:
                    mp.restore()
            elif name == "test_runtime_key_rotation_and_removal":
                mp = MockMonkeyPatch()
                try:
                    test_runtime_key_rotation_and_removal(mp)
                finally:
                    mp.restore()
            elif name == "test_malformed_and_duplicate_config":
                mp = MockMonkeyPatch()
                try:
                    test_malformed_and_duplicate_config(mp)
                finally:
                    mp.restore()
            elif name == "test_installed_package_api_import_and_runtime":
                mp = MockMonkeyPatch()
                try:
                    test_installed_package_api_import_and_runtime(mp, Path(temp_dir))
                finally:
                    mp.restore()
            else:
                func(store_obj)
            log_file.write("  Result: PASS\n\n")
        except BaseException as exc:
            import traceback

            log_file.write("  Result: FAIL\n")
            log_file.write(f"  Error: {exc}\n")
            log_file.write(traceback.format_exc() + "\n\n")
            success = False
        finally:
            # Cleanup temp DB
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    log_file.close()
    return success


if __name__ == "__main__":
    log_p = Path("/tmp/agy-GRO-4111-source-continuity-repair-verify.log")
    ok = run_all_probes(log_p)
    if ok:
        print("ALL PROBES PASSED")
        sys.exit(0)
    else:
        print(
            "PROBES FAILED - check log at /tmp/agy-GRO-4111-source-continuity-repair-verify.log"
        )
        sys.exit(1)
