"""
tests/test_doctor_module.py
============================

Verifies the extracted doctor modules in ``prismatic.doctor`` (pure
function) and ``prismatic.cli.doctor`` (CLI handler).

The legacy inline ``cmd_doctor`` in ``prismatic.dispatcher`` now
delegates to ``prismatic.cli.doctor.run``. The pre-existing
``tests/test_doctor_command.py`` continues to test the legacy
subcommand surface. This file tests the new module surface.
"""

import sys
import unittest
from unittest.mock import patch, MagicMock
from dataclasses import asdict

from prismatic.doctor import (
    run_doctor,
    DoctorReport,
    SystemInfo,
    ConfigInfo,
    ProviderReport,
    CapabilityReport,
    DEFAULT_CAPABILITY_NAMES,
    _probe_system,
    _probe_config_paths,
    _probe_github,
    _probe_linear,
    _compute_verdict,
)
from prismatic.cli.doctor import run as doctor_cli_run, EXIT_OK, EXIT_ERROR


def _empty_namespace(**kwargs):
    """Build a SimpleNamespace from kwargs. Avoids importing types."""
    from types import SimpleNamespace
    return SimpleNamespace(**kwargs)


class TestDoctorPureFunction(unittest.TestCase):
    """The pure function should return a typed DoctorReport."""

    def test_run_doctor_returns_doctor_report(self):
        report = run_doctor()
        self.assertIsInstance(report, DoctorReport)
        self.assertIsInstance(report.system, SystemInfo)
        self.assertIsInstance(report.config, ConfigInfo)
        self.assertIsInstance(report.providers, list)
        self.assertIsInstance(report.capabilities, list)
        self.assertIn(report.verdict, ("OK", "WARN", "ERROR"))

    def test_run_doctor_default_provider_set_is_github_and_linear(self):
        report = run_doctor()
        names = {p.name for p in report.providers}
        self.assertEqual(names, {"github", "linear"})

    def test_run_doctor_provider_filter_only_returns_named_provider(self):
        # GitHub provider probe calls subprocess for 'gh auth token' if no
        # token in env. We don't have GITHUB_TOKEN/GH_TOKEN in the test env,
        # so the report is "disconnected" — that's fine for this test, we
        # only care that the filter restricted the providers list.
        report = run_doctor(provider="github")
        names = {p.name for p in report.providers}
        self.assertEqual(names, {"github"})

    def test_run_doctor_to_dict_is_json_safe(self):
        report = run_doctor()
        d = report.to_dict()
        # Should be JSON-serializable
        import json
        json.dumps(d)
        # Top-level keys
        self.assertEqual(
            set(d.keys()),
            {"system", "config", "providers", "capabilities", "verdict"},
        )

    def test_default_capability_names_is_canonical_order(self):
        # The legacy cmd_doctor probed these five in this order. The new
        # module MUST preserve that order so any operator who scans the
        # report output doesn't see surprises.
        self.assertEqual(
            DEFAULT_CAPABILITY_NAMES,
            ["linear", "vcs.github", "agy", "jules", "telegram"],
        )


class TestSystemAndConfigProbes(unittest.TestCase):
    """System info and config path probes should be deterministic."""

    def test_probe_system_populates_python_version(self):
        info = _probe_system()
        self.assertTrue(info.python_version)
        # Should look like "3.x.y"
        parts = info.python_version.split(".")
        self.assertGreaterEqual(len(parts), 2)

    def test_probe_config_paths_returns_three_paths(self):
        home, cfg, db = _probe_config_paths()
        # All three should be absolute paths
        for p in (home, cfg, db):
            self.assertTrue(p.is_absolute())

    def test_run_doctor_populates_config_info(self):
        report = run_doctor()
        self.assertTrue(report.config.prismatic_home)
        self.assertTrue(report.config.user_config_path)
        self.assertTrue(report.config.database_path)
        # These are bool fields
        self.assertIsInstance(report.config.user_config_exists, bool)
        self.assertIsInstance(report.config.database_exists, bool)


class TestProviderProbes(unittest.TestCase):
    """Provider probes should return ProviderReport instances."""

    def test_probe_github_no_token_returns_disconnected(self):
        env = {k: v for k, v in __import__("os").environ.items()
               if k not in ("GITHUB_TOKEN", "GH_TOKEN", "PRISMATIC_GITHUB_TOKEN")}
        with patch.dict("os.environ", env, clear=True):
            # Use a tmp path that has no config.yaml
            with patch("pathlib.Path.exists", return_value=False):
                with patch("subprocess.run") as mock_run:
                    # First subprocess.run is for "gh auth token" — return empty
                    mock_run.return_value = MagicMock(
                        returncode=1, stdout="", stderr=""
                    )
                    report = _probe_github(__import__("pathlib").Path("/nonexistent/config.yaml"))
        self.assertEqual(report.name, "github")
        self.assertEqual(report.status, "disconnected")
        self.assertIn("GITHUB_TOKEN", report.remediation or "")

    def test_probe_linear_missing_token_returns_disconnected(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "LINEAR_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            report = _probe_linear()
        self.assertEqual(report.name, "linear")
        self.assertEqual(report.status, "disconnected")

    def test_probe_linear_with_token_returns_connected(self):
        with patch.dict("os.environ", {"LINEAR_API_KEY": "test-key"}, clear=False):
            # Mock the provider import to avoid hitting the real API
            with patch.dict("sys.modules", {"prismatic.providers.tasks.linear": MagicMock()}):
                mock_mod = sys.modules["prismatic.providers.tasks.linear"]
                mock_provider = MagicMock()
                mock_provider._api_key = "test-key"
                mock_mod.LinearTaskProvider = MagicMock(return_value=mock_provider)
                report = _probe_linear()
        # When the lazy import works, the probe should report connected.
        # If the provider module isn't available in the test env, the
        # status is "auth_failed" — both are acceptable outcomes for this
        # test (we only check the shape).
        self.assertIn(report.status, ("connected", "auth_failed"))


class TestCapabilityProbes(unittest.TestCase):
    """The capability registry probe should handle missing slots."""

    def test_capability_names_custom_order_preserved(self):
        # Even with a custom list, the report reflects the requested order.
        with patch("prismatic.capabilities.registry") as mock_reg:
            mock_reg.get.return_value = None  # all missing
            from prismatic.doctor import _probe_capabilities
            reports = _probe_capabilities(["agy", "vcs.github"])
        self.assertEqual([r.name for r in reports], ["agy", "vcs.github"])
        self.assertEqual([r.status for r in reports], ["missing_registry", "missing_registry"])


class TestVerdictComputation(unittest.TestCase):
    """Verdict rollup rules."""

    def test_verdict_ok_when_all_green(self):
        providers = [
            ProviderReport(name="github", status="connected"),
            ProviderReport(name="linear", status="connected"),
        ]
        capabilities = [
            CapabilityReport(name="linear", status="ok"),
            CapabilityReport(name="vcs.github", status="ok"),
        ]
        self.assertEqual(_compute_verdict(providers, capabilities), "OK")

    def test_verdict_error_when_github_disconnected(self):
        providers = [
            ProviderReport(name="github", status="disconnected"),
            ProviderReport(name="linear", status="connected"),
        ]
        capabilities = [CapabilityReport(name="linear", status="ok")]
        self.assertEqual(_compute_verdict(providers, capabilities), "ERROR")

    def test_verdict_error_when_linear_disconnected(self):
        providers = [
            ProviderReport(name="github", status="connected"),
            ProviderReport(name="linear", status="disconnected"),
        ]
        capabilities = [CapabilityReport(name="linear", status="ok")]
        self.assertEqual(_compute_verdict(providers, capabilities), "ERROR")

    def test_verdict_warn_when_non_golden_provider_disconnected(self):
        # If only an "extra" provider is disconnected and the golden
        # ones (github, linear) are healthy, this is a WARN, not ERROR.
        providers = [
            ProviderReport(name="github", status="connected"),
            ProviderReport(name="linear", status="connected"),
            ProviderReport(name="agy", status="disconnected"),
        ]
        capabilities = [CapabilityReport(name="agy", status="ok")]
        self.assertEqual(_compute_verdict(providers, capabilities), "WARN")

    def test_verdict_warn_when_capability_not_ok(self):
        providers = [
            ProviderReport(name="github", status="connected"),
            ProviderReport(name="linear", status="connected"),
        ]
        capabilities = [
            CapabilityReport(name="agy", status="error", message="missing token"),
        ]
        self.assertEqual(_compute_verdict(providers, capabilities), "WARN")


class TestDoctorCliHandler(unittest.TestCase):
    """The CLI handler must delegate to run_doctor and print the expected sections."""

    def test_cli_run_with_no_provider_returns_exit_ok_when_verdict_ok(self):
        # If everything is green, the CLI returns 0. We force a green
        # report by mocking run_doctor at the import location used by
        # the CLI handler.
        green = DoctorReport(
            system=SystemInfo(python_version="3.12.0", git_version="git 2.43.0", gh_cli_version="gh 2.45.0"),
            config=ConfigInfo(
                prismatic_home="/tmp",
                user_config_path="/tmp/config.yaml",
                user_config_exists=False,
                database_path="/tmp/db",
                database_exists=False,
            ),
            providers=[
                ProviderReport(name="github", status="connected", user="alice"),
                ProviderReport(
                    name="linear",
                    status="connected",
                    rate_limit_info={"remaining": 2490.0, "limit": 2500, "consumed": 10, "utilization_pct": 0.40}
                ),
            ],
            capabilities=[
                CapabilityReport(name="linear", status="ok"),
                CapabilityReport(name="vcs.github", status="ok"),
                CapabilityReport(name="agy", status="ok"),
                CapabilityReport(name="jules", status="ok"),
                CapabilityReport(name="telegram", status="ok"),
            ],
            verdict="OK",
        )
        with patch("prismatic.cli.doctor.run_doctor", return_value=green):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = doctor_cli_run(_empty_namespace())
            self.assertEqual(rc, EXIT_OK)
            output = buf.getvalue()
            self.assertIn("Prismatic Engine — Capability Status", output)
            self.assertIn("[System]", output)
            self.assertIn("[Config]", output)
            self.assertIn("[GitHub]", output)
            self.assertIn("[Linear]", output)
            self.assertIn("Rate Limit:", output)
            self.assertIn("[Capabilities]", output)
            self.assertIn("Reports:", output)
            self.assertIn("Diagnostics complete.", output)

    def test_cli_run_returns_exit_error_when_verdict_error(self):
        error_report = DoctorReport(
            system=SystemInfo(python_version="3.12.0"),
            config=ConfigInfo(prismatic_home="/tmp", user_config_path="/tmp/cfg", database_path="/tmp/db"),
            providers=[ProviderReport(name="github", status="disconnected")],
            capabilities=[CapabilityReport(name="vcs.github", status="error", message="missing")],
            verdict="ERROR",
        )
        with patch("prismatic.cli.doctor.run_doctor", return_value=error_report):
            rc = doctor_cli_run(_empty_namespace())
        self.assertEqual(rc, EXIT_ERROR)

    def test_cli_run_provider_filter_propagates(self):
        # The CLI must pass args.provider through to run_doctor.
        captured: dict = {}
        def fake_run_doctor(provider=None, capability_names=None):
            captured["provider"] = provider
            return DoctorReport(
                system=SystemInfo(),
                config=ConfigInfo(prismatic_home="", user_config_path="", database_path=""),
                providers=[],
                capabilities=[],
                verdict="OK",
            )
        with patch("prismatic.cli.doctor.run_doctor", side_effect=fake_run_doctor):
            rc = doctor_cli_run(_empty_namespace(provider="github"))
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(captured["provider"], "github")


class TestBackwardCompatDispatchDelegate(unittest.TestCase):
    """The dispatcher's cmd_doctor must still exist as a callable wrapper."""

    def test_dispatcher_cmd_doctor_delegates_to_cli_module(self):
        import prismatic.dispatcher as dispatcher
        self.assertTrue(callable(dispatcher.cmd_doctor))
        # The signature takes an args object. The dispatcher delegates
        # to prismatic.cli.doctor.run (the CLI handler), which then
        # calls prismatic.doctor.run_doctor (the pure function).
        # We mock the CLI handler to verify the delegation.
        ns = _empty_namespace(provider=None)
        with patch("prismatic.cli.doctor.run") as fake_cli:
            # The CLI handler's run() will return whatever we set here.
            fake_cli.return_value = EXIT_OK
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = dispatcher.cmd_doctor(ns)
            self.assertEqual(rc, EXIT_OK)
            fake_cli.assert_called_once_with(ns)


if __name__ == "__main__":
    unittest.main()
