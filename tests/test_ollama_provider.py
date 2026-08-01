import unittest
from unittest.mock import patch, MagicMock
import json
import io
from prismatic.providers.ollama import OllamaClient


class TestOllamaProvider(unittest.TestCase):
    """Test suite for Ollama VRAM preloader, health check, and routing decision helper."""

    def setUp(self):
        self.client = OllamaClient(base_url="http://mock-ollama:11434", timeout=2.0)

    @patch("urllib.request.urlopen")
    def test_check_health_success(self, mock_urlopen):
        # Setup mock response for GET /
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"Ollama is running"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        self.assertTrue(self.client.check_health())
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://mock-ollama:11434")
        self.assertEqual(req.get_method(), "GET")

    @patch("urllib.request.urlopen")
    def test_check_health_failure(self, mock_urlopen):
        # Setup mock network error using URLError
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        # Fallback will check get_available_models, which will also fail and return None
        # So check_health should return False
        self.assertFalse(self.client.check_health())

    @patch("urllib.request.urlopen")
    def test_get_available_models(self, mock_urlopen):
        # Mock response for GET /api/tags
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_response_data = {
            "models": [
                {
                    "name": "qwen2.5:32b",
                    "model": "qwen2.5:32b",
                    "size": 19128000000,
                    "details": {
                        "parameter_size": "32B",
                        "quantization_level": "Q4_K_M"
                    }
                },
                {
                    "name": "hermes3:70b",
                    "model": "hermes3:70b",
                    "size": 42000000000
                }
            ]
        }
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        models = self.client.get_available_models()
        self.assertIsNotNone(models)
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]["name"], "qwen2.5:32b")
        self.assertEqual(models[1]["size"], 42000000000)

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://mock-ollama:11434/api/tags")

    @patch("urllib.request.urlopen")
    def test_get_active_models(self, mock_urlopen):
        # Mock response for GET /api/ps
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_response_data = {
            "models": [
                {
                    "name": "qwen2.5:32b",
                    "model": "qwen2.5:32b",
                    "size": 19128000000,
                    "size_vram": 19128000000,
                    "expires_at": "2026-06-16T15:20:00Z"
                }
            ]
        }
        mock_resp.read.return_value = json.dumps(mock_response_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        active = self.client.get_active_models()
        self.assertIsNotNone(active)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["size_vram"], 19128000000)

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://mock-ollama:11434/api/ps")

    @patch("urllib.request.urlopen")
    def test_preload_model(self, mock_urlopen):
        # Mock response for POST /api/generate
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status": "success"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        success = self.client.preload_model("qwen2.5:32b", keep_alive="30m")
        self.assertTrue(success)

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://mock-ollama:11434/api/generate")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers["Content-type"], "application/json")
        
        # Verify JSON body sent
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "qwen2.5:32b")
        self.assertEqual(body["keep_alive"], "30m")
        self.assertEqual(body["prompt"], "")
        self.assertFalse(body["stream"])

    @patch("urllib.request.urlopen")
    def test_unload_model(self, mock_urlopen):
        # Mock response for POST /api/generate
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status": "success"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        success = self.client.unload_model("hermes3:70b")
        self.assertTrue(success)

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "hermes3:70b")
        self.assertEqual(body["keep_alive"], 0)

    @patch("prismatic.providers.ollama.OllamaClient.get_active_models")
    def test_get_vram_metrics(self, mock_get_active):
        # Setup mock loaded models
        mock_get_active.return_value = [
            {"name": "qwen2.5:32b", "size": 19000000000, "size_vram": 19000000000},
            {"name": "small-embed:latest", "size": 500000000, "size_vram": 300000000}
        ]

        # Scenario 1: No total_vram_bytes specified
        metrics = self.client.get_vram_metrics()
        self.assertEqual(metrics["total_vram_used_bytes"], 19300000000)
        self.assertEqual(metrics["total_model_size_bytes"], 19500000000)
        self.assertIsNone(metrics["utilization_ratio"])
        self.assertIsNone(metrics["available_vram_bytes_estimate"])
        self.assertEqual(metrics["status"], "healthy")

        # Scenario 2: With total_vram_bytes specified (e.g. 24GB = 25,769,803,776 bytes)
        total_vram = 24 * 1024 * 1024 * 1024
        metrics_with_total = self.client.get_vram_metrics(total_vram_bytes=total_vram)
        self.assertEqual(metrics_with_total["total_vram_used_bytes"], 19300000000)
        self.assertAlmostEqual(metrics_with_total["utilization_ratio"], 19300000000 / total_vram)
        self.assertEqual(metrics_with_total["available_vram_bytes_estimate"], total_vram - 19300000000)

    @patch("prismatic.providers.ollama.OllamaClient.get_available_models")
    @patch("prismatic.providers.ollama.OllamaClient.get_active_models")
    def test_make_routing_decision(self, mock_get_active, mock_get_available):
        # Mock responses
        # Active: Qwen is active and fully in VRAM
        mock_get_active.return_value = [
            {"name": "qwen2.5:32b", "size": 19000000000, "size_vram": 19000000000}
        ]
        # Available: Qwen, Hermes, and Llama are available
        mock_get_available.return_value = [
            {"name": "qwen2.5:32b", "size": 19000000000},
            {"name": "hermes3:70b", "size": 42000000000},
            {"name": "llama3:8b", "size": 4800000000}
        ]

        total_vram = 48 * 1024 * 1024 * 1024 # 48GB

        # Case 1: Desired model is already loaded (warm call)
        decision = self.client.make_routing_decision("qwen2.5:32b", total_vram_bytes=total_vram)
        self.assertTrue(decision["route_allowed"])
        self.assertTrue(decision["is_warm"])
        self.assertEqual(decision["action_recommended"], "call")

        # Case 2: Desired model not loaded, but available and enough VRAM is free
        # Used VRAM = 19GB, Available = 29GB. Llama3 requires ~4.8GB (from its actual size)
        decision2 = self.client.make_routing_decision("llama3:8b", total_vram_bytes=total_vram)
        self.assertTrue(decision2["route_allowed"])
        self.assertFalse(decision2["is_warm"])
        self.assertEqual(decision2["action_recommended"], "preload_and_call")

        # Case 3: Desired model not loaded, but available, and NOT enough VRAM is free
        # Hermes3 requires 42GB, but only 29GB is free.
        decision3 = self.client.make_routing_decision("hermes3:70b", total_vram_bytes=total_vram)
        self.assertFalse(decision3["route_allowed"])
        self.assertFalse(decision3["is_warm"])
        self.assertEqual(decision3["action_recommended"], "unload_others")

        # Case 4: Desired model is not downloaded at all
        decision4 = self.client.make_routing_decision("mistral:latest", total_vram_bytes=total_vram)
        self.assertFalse(decision4["route_allowed"])
        self.assertEqual(decision4["action_recommended"], "reject")


if __name__ == "__main__":
    unittest.main()
