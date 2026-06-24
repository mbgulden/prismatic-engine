import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add bin/ directory to sys.path so we can import it
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../bin")))

from prismatic_rewrite_paths import rewrite_paths_in_text, publish_path, upload_to_telegram

class TestPrismaticRewritePaths(unittest.TestCase):
    def setUp(self):
        # Create a dummy file that exists so path checking passes
        self.dummy_file = "/tmp/test_dummy_report.md"
        with open(self.dummy_file, "w") as f:
            f.write("dummy content")

    def tearDown(self):
        if os.path.exists(self.dummy_file):
            os.remove(self.dummy_file)

    @patch("subprocess.run")
    def test_publish_path_success(self, mock_run):
        # Mock subprocess.run to simulate successful prismatic-publish
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://files.growthwebdev.com/raw/published/test_dummy_report.md\n"
        mock_run.return_value = mock_result

        url, err = publish_path(self.dummy_file)
        self.assertEqual(url, "https://files.growthwebdev.com/raw/published/test_dummy_report.md")
        self.assertIsNone(err)

    @patch("urllib.request.urlopen")
    def test_upload_to_telegram_success(self, mock_urlopen):
        # Mock Telegram API response
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch("prismatic_rewrite_paths.TELEGRAM_BOT_TOKEN", "mock_token"), \
             patch("prismatic_rewrite_paths.TELEGRAM_HOME_CHANNEL", "mock_channel"):
            success, err = upload_to_telegram(self.dummy_file)
            self.assertTrue(success, msg=f"Telegram upload failed: {err}")
            self.assertIsNone(err)

    @patch("subprocess.run")
    @patch("prismatic_rewrite_paths.upload_to_telegram")
    def test_publish_path_fail_telegram_success(self, mock_tg, mock_run):
        # Simulate prismatic-publish failing, but Telegram upload succeeding
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Permission denied"
        mock_run.return_value = mock_result

        mock_tg.return_value = (True, None)

        url, err = publish_path(self.dummy_file)
        self.assertIsNone(url)
        self.assertEqual(err, "failed to publish (Permission denied), uploaded to Telegram")

    @patch("subprocess.run")
    @patch("prismatic_rewrite_paths.upload_to_telegram")
    def test_publish_path_all_fail(self, mock_tg, mock_run):
        # Simulate both failing
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Permission denied"
        mock_run.return_value = mock_result

        mock_tg.return_value = (False, "Network error")

        url, err = publish_path(self.dummy_file)
        self.assertIsNone(url)
        self.assertEqual(err, "failed to publish (Permission denied) and Telegram upload failed (Network error)")

    @patch("prismatic_rewrite_paths.publish_path")
    def test_rewrite_paths_in_text(self, mock_publish):
        mock_publish.return_value = ("https://files.growthwebdev.com/raw/published/test_dummy_report.md", None)

        input_text = f"Fred created a report at {self.dummy_file} for Michael."
        expected = f"Fred created a report at [test_dummy_report.md](https://files.growthwebdev.com/raw/published/test_dummy_report.md) for Michael."
        
        output = rewrite_paths_in_text(input_text)
        self.assertEqual(output, expected)

    @patch("prismatic_rewrite_paths.publish_path")
    def test_rewrite_paths_in_text_fail(self, mock_publish):
        mock_publish.return_value = (None, "failed to publish (Permission denied), uploaded to Telegram")

        input_text = f"Fred created a report at {self.dummy_file} for Michael."
        expected = f"Fred created a report at [test_dummy_report.md (failed to publish (Permission denied), uploaded to Telegram)] for Michael."

        output = rewrite_paths_in_text(input_text)
        self.assertEqual(output, expected)

    @patch("prismatic_rewrite_paths.publish_path")
    def test_fred_prepare_reply_success(self, mock_publish):
        mock_publish.return_value = ("https://hermes.growthwebdev.com/artifacts/raw/published/test_dummy_report.md", None)
        
        from prismatic_rewrite_paths import fred_prepare_reply
        result = fred_prepare_reply(f"Please find the report at {self.dummy_file}")
        
        self.assertIn("[test_dummy_report.md](https://hermes.growthwebdev.com/artifacts/raw/published/test_dummy_report.md)", result["text"])
        self.assertEqual(len(result["uploads"]), 0)

    @patch("prismatic_rewrite_paths.publish_path")
    def test_fred_prepare_reply_fallback(self, mock_publish):
        mock_publish.return_value = (None, "failed to publish (Permission denied), uploaded to Telegram")
        
        from prismatic_rewrite_paths import fred_prepare_reply
        result = fred_prepare_reply(f"Please find the report at {self.dummy_file}")
        
        self.assertIn("[test_dummy_report.md (failed to publish (Permission denied), uploaded to Telegram)]", result["text"])
        self.assertEqual(len(result["uploads"]), 1)
        self.assertEqual(result["uploads"][0]["path"], self.dummy_file)
        self.assertTrue(result["uploads"][0]["success"])
        self.assertEqual(result["uploads"][0]["error"], "failed to publish (Permission denied), uploaded to Telegram")

    @patch("prismatic_rewrite_paths.publish_path")
    def test_fred_prepare_reply_double_failure(self, mock_publish):
        mock_publish.return_value = (None, "failed to publish (Permission denied) and Telegram upload failed (Network error)")
        
        from prismatic_rewrite_paths import fred_prepare_reply
        result = fred_prepare_reply(f"Please find the report at {self.dummy_file}")
        
        self.assertIn("[test_dummy_report.md (failed to publish (Permission denied) and Telegram upload failed (Network error))]", result["text"])
        self.assertEqual(len(result["uploads"]), 1)
        self.assertEqual(result["uploads"][0]["path"], self.dummy_file)
        self.assertFalse(result["uploads"][0]["success"])
        self.assertEqual(result["uploads"][0]["error"], "failed to publish (Permission denied) and Telegram upload failed (Network error)")

