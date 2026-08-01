# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from prismatic.core.grader import VisualVerificationResult, parse_viewports


class TestVisualVerifyCli(unittest.TestCase):
    def _fake_result(self, output_dir: str) -> VisualVerificationResult:
        return VisualVerificationResult(
            url="https://example.com",
            output_dir=output_dir,
            screenshots=[
                {
                    "name": "desktop",
                    "path": str(Path(output_dir) / "desktop.png"),
                    "width": 1200,
                    "height": 800,
                }
            ],
            grade={
                "status": "PASS",
                "score": 100,
                "viewportReports": {"desktop": {"pass": True, "defects": []}},
                "overallVerdict": "Looks good.",
            },
            metadata={"model": "gemini-3.1-flash", "delay_ms": 500},
        )

    def test_parse_viewports_accepts_aliases_and_custom_sizes(self) -> None:
        viewports = parse_viewports("desktop,hero:1440x900,375x667")
        self.assertEqual(
            viewports[0], {"name": "desktop", "width": 1200, "height": 800}
        )
        self.assertEqual(viewports[1], {"name": "hero", "width": 1440, "height": 900})
        self.assertEqual(viewports[2], {"name": "custom", "width": 375, "height": 667})

    def test_prismatic_visual_verify_accepts_positional_url_and_writes_report(
        self,
    ) -> None:
        from prismatic import cli

        with tempfile.TemporaryDirectory(prefix="prismatic-visual-cli-") as tmp:
            out = StringIO()
            with patch(
                "prismatic.cli.visual_verify.run_visual_verification",
                return_value=self._fake_result(tmp),
            ) as fake_run, redirect_stdout(out):
                rc = cli.run(
                    [
                        "visual-verify",
                        "https://example.com",
                        "--output-dir",
                        tmp,
                        "--grade",
                    ]
                )

            self.assertEqual(rc, 0)
            fake_run.assert_called_once()
            self.assertEqual(fake_run.call_args.kwargs["url"], "https://example.com")
            self.assertTrue(fake_run.call_args.kwargs["grade"])
            report_path = Path(tmp) / "visual-report.json"
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["grade"]["status"], "PASS")
            printed = json.loads(out.getvalue())
            self.assertTrue(printed["ok"])
            self.assertEqual(printed["report"], str(report_path))

    def test_prismatic_engine_visual_verify_delegates_to_same_cli(self) -> None:
        from prismatic import dispatcher

        with tempfile.TemporaryDirectory(prefix="prismatic-engine-visual-cli-") as tmp:
            with patch(
                "prismatic.cli.visual_verify.run_visual_verification",
                return_value=self._fake_result(tmp),
            ) as fake_run, patch(
                "sys.argv",
                [
                    "prismatic-engine",
                    "visual-verify",
                    "https://example.com",
                    "--output-dir",
                    tmp,
                ],
            ), redirect_stdout(StringIO()), self.assertRaises(SystemExit) as cm:
                dispatcher.main()

            self.assertEqual(cm.exception.code, 0)
            fake_run.assert_called_once()
            self.assertEqual(fake_run.call_args.kwargs["url"], "https://example.com")


if __name__ == "__main__":
    unittest.main()
