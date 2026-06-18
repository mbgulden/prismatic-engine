"""
Tests for the prismatic-research CLI utility.
"""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from prismatic.cli.research import slugify, cmd_init, run


class TestPrismaticResearch(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("WASM Runtimes"), "wasm-runtimes")
        self.assertEqual(slugify("WASM: Wasmtime vs Wasmer!"), "wasm-wasmtime-vs-wasmer")
        self.assertEqual(slugify("  some-spaced  text  "), "some-spaced-text")

    def test_cmd_init_standard_bundle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "my-research"
            args = argparse.Namespace(
                topic="Test Topic",
                bundle="standard",
                use_case="Analyze test scenarios",
                out=str(out_path),
                audience="Developers",
                anchors="http://example.com,tests/test_prismatic_research.py",
                depth="deep",
                no_expansion=False,
            )

            rc = cmd_init(args)
            self.assertEqual(rc, 0)

            # Check core files exist
            brief_file = out_path / "AGY_RESEARCH_BRIEF.md"
            source_map = out_path / "SOURCE_MAP.md"
            evidence_ledger = out_path / "EVIDENCE_LEDGER.md"
            launch_commands = out_path / "AGY_LAUNCH_COMMANDS.md"
            reports_dir = out_path / "REPORTS"

            self.assertTrue(brief_file.is_file())
            self.assertTrue(source_map.is_file())
            self.assertTrue(evidence_ledger.is_file())
            self.assertTrue(launch_commands.is_file())
            self.assertTrue(reports_dir.is_dir())

            # Verify report files corresponding to 'standard' bundle
            self.assertTrue((reports_dir / "01-executive-synthesis.md").is_file())
            self.assertTrue((reports_dir / "02-trend-analysis.md").is_file())
            self.assertTrue((reports_dir / "03-recommendations.md").is_file())

            # Read brief content and assert details
            brief_content = brief_file.read_text()
            self.assertIn("Test Topic", brief_content)
            self.assertIn("Analyze test scenarios", brief_content)
            self.assertIn("Developers", brief_content)
            self.assertIn("standard", brief_content)
            self.assertIn("deep", brief_content)
            self.assertIn("Use the anchors first, then investigate beyond them", brief_content)
            self.assertIn("[http://example.com](http://example.com)", brief_content)

            # Check launch commands contains agy --print
            launch_content = launch_commands.read_text()
            self.assertIn("agy --print", launch_content)
            self.assertNotIn("file://", brief_content)
            self.assertNotIn("file://", launch_content)

    def test_cmd_init_no_expansion_and_slugification(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            import os
            old_cwd_dir = os.getcwd()
            try:
                os.chdir(tmp_dir)
                args = argparse.Namespace(
                    topic="Topic with spaces and chars!",
                    bundle="brief",
                    use_case=None,  # Should fallback to topic
                    out=None,       # Should trigger slugify
                    audience="Lead Architects",
                    anchors="",
                    depth="quick",
                    no_expansion=True,
                )

                rc = cmd_init(args)
                self.assertEqual(rc, 0)

                expected_dir = Path("research/topic-with-spaces-and-chars")
                self.assertTrue(expected_dir.is_dir())

                brief_file = expected_dir / "AGY_RESEARCH_BRIEF.md"
                brief_content = brief_file.read_text()
                self.assertIn("Strict anchor confinement", brief_content)
                self.assertIn("brief", brief_content)
                self.assertIn("quick", brief_content)

                reports_dir = expected_dir / "REPORTS"
                self.assertTrue((reports_dir / "01-executive-summary.md").is_file())
                self.assertTrue((reports_dir / "02-key-recommendations.md").is_file())
                self.assertFalse((reports_dir / "03-recommendations.md").is_file())

            finally:
                os.chdir(old_cwd_dir)

    def test_content_engine_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir) / "content-res"
            args = argparse.Namespace(
                topic="Web Design",
                bundle="content-engine",
                use_case="Web Dev Blog",
                out=str(out_path),
                audience="General Readers",
                anchors="",
                depth="standard",
                no_expansion=False,
            )
            rc = cmd_init(args)
            self.assertEqual(rc, 0)

            reports_dir = out_path / "REPORTS"
            # Verify one of the content engine report files
            audience_map = reports_dir / "01-audience-map.md"
            self.assertTrue(audience_map.is_file())

            content = audience_map.read_text()
            self.assertTrue(content.startswith("---"))
            self.assertIn("seo_keywords: []", content)
            self.assertIn('title: "Audience Map: Web Design"', content)


if __name__ == "__main__":
    unittest.main()
