import unittest
from unittest.mock import patch, MagicMock
import json
import io
import contextlib

from prismatic.cli.linear_budget import run

class TestLinearBudgetCommand(unittest.TestCase):
    """Test suite for the linear budget CLI."""

    @patch("prismatic.linear.budget.linear_budget.get_current_utilization")
    @patch("prismatic.cli.linear_budget.os.path.exists")
    @patch("prismatic.cli.linear_budget.open")
    def test_status_command(self, mock_open, mock_exists, mock_get_util):
        # Mock budget utilization
        mock_get_util.return_value = {
            "current_tokens": 2400.0,
            "retry_after_estimate": 150,
            "hourly_rate_limit": 2500,
            "consumed_last_hour": 100,
            "utilization_percentage": 4.0
        }
        
        # Mock call counts file existence and read
        mock_exists.return_value = True
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = '{"cron.comment_trigger_monitor": 5, "cron.kai_callback_monitor": 2}'
        mock_open.return_value = mock_file
        
        # Capture stdout
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = run(["status"])
            
        self.assertEqual(exit_code, 0)
        output = json.loads(buf.getvalue())
        self.assertEqual(output["remaining"], 2400.0)
        self.assertEqual(output["reset_in_seconds"], 150)
        self.assertEqual(output["top_offenders"]["cron.comment_trigger_monitor"], 5)
        self.assertEqual(output["top_offenders"]["cron.kai_callback_monitor"], 2)

if __name__ == "__main__":
    unittest.main()
