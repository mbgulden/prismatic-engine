"""
OllamaProvider — Query Ollama VRAM allocations, tags, and preload models.
========================================================================

Provides helper functions and an OllamaClient class to interact with Ollama's
API. Supports querying loaded models, preloading models to VRAM, unloading models,
and checking health.

Uses only the standard library (urllib.request, urllib.error) to remain
dependency-free.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any, Optional


class OllamaClient:
    """Client for querying and managing Ollama instances and their VRAM usage."""

    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 10.0):
        """Initialize OllamaClient.

        Args:
            base_url: The Ollama server base URL (default: http://localhost:11434).
            timeout: HTTP request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, method: str = "GET", data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Internal helper to make JSON requests to the Ollama API.

        Args:
            path: API path (e.g. '/api/ps').
            method: HTTP method (GET or POST).
            data: Optional dictionary to serialize to JSON for the request body.

        Returns:
            Parsed JSON dict, or None if error occurs.
        """
        url = f"{self.base_url}{path}"
        serialized_data = None
        headers = {}

        if data is not None:
            serialized_data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(
            url,
            data=serialized_data,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            print(f"[OllamaClient] HTTP Error {exc.code} for {method} {path}: {exc.reason}")
            try:
                err_body = exc.read().decode("utf-8")
                print(f"[OllamaClient] Error response body: {err_body}")
            except Exception:
                pass
            return None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f"[OllamaClient] Network/Connection error for {method} {path}: {exc}")
            return None
        except json.JSONDecodeError as exc:
            print(f"[OllamaClient] Failed to parse JSON response for {method} {path}: {exc}")
            return None

    def check_health(self) -> bool:
        """Check if the Ollama server is running and reachable.

        Returns:
            True if healthy, False otherwise.
        """
        url = self.base_url
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8").strip()
                    # Standard response is "Ollama is running"
                    return "Ollama is running" in body or body == ""
                return False
        except Exception as exc:
            print(f"[OllamaClient] Health check failed for {url}: {exc}")
            # Fallback check on api/tags
            tags = self.get_available_models()
            return tags is not None

    def get_available_models(self) -> list[dict[str, Any]] | None:
        """List all models downloaded/pulled on the Ollama host (GET /api/tags).

        Returns:
            A list of model dicts, or None if the request failed.
            Example dict schema:
            {
                "name": "qwen2.5:32b",
                "model": "qwen2.5:32b",
                "size": 19128000000,
                ...
            }
        """
        res = self._request("/api/tags", "GET")
        if res is not None and "models" in res:
            return res["models"]
        return None

    def get_active_models(self) -> list[dict[str, Any]] | None:
        """List all models currently loaded in memory/VRAM (GET /api/ps).

        Returns:
            A list of model dicts, or None if the request failed.
            Example dict schema:
            {
                "name": "qwen2.5:32b",
                "model": "qwen2.5:32b",
                "size": 19128000000,
                "size_vram": 19128000000,
                "expires_at": "2026-06-16T15:20:00Z",
                ...
            }
        """
        res = self._request("/api/ps", "GET")
        if res is not None and "models" in res:
            return res["models"]
        return None

    def preload_model(self, model_name: str, keep_alive: int | str = -1) -> bool:
        """Preload a model into memory (VRAM) and configure its keep-alive duration.

        Args:
            model_name: The name of the model to load (e.g. 'qwen2.5:32b').
            keep_alive: How long the model should stay loaded.
                -1 means load indefinitely.
                0 unloads the model immediately.
                Integer specifies seconds (e.g. 300).
                String specifies duration (e.g. '5m', '1h').

        Returns:
            True if preloading succeeded, False otherwise.
        """
        # Sending keep_alive via POST /api/generate preloads the model.
        # We specify stream: false and empty prompt to avoid executing generation.
        data = {
            "model": model_name,
            "prompt": "",
            "keep_alive": keep_alive,
            "stream": False,
        }
        res = self._request("/api/generate", "POST", data)
        return res is not None

    def unload_model(self, model_name: str) -> bool:
        """Unload a model from memory/VRAM immediately by setting keep_alive to 0.

        Args:
            model_name: The name of the model to unload.

        Returns:
            True if unloading succeeded, False otherwise.
        """
        return self.preload_model(model_name, keep_alive=0)

    def get_vram_metrics(self, total_vram_bytes: int | None = None) -> dict[str, Any]:
        """Retrieve metrics regarding loaded models and active VRAM usage.

        Args:
            total_vram_bytes: Optional total VRAM of the GPU in bytes to calculate utilization.

        Returns:
            A dictionary of metrics:
            {
                "active_models": [...],
                "total_vram_used_bytes": int,
                "total_model_size_bytes": int,
                "utilization_ratio": float or None,
                "available_vram_bytes_estimate": int or None,
                "status": "healthy" | "unreachable"
            }
        """
        active = self.get_active_models()
        if active is None:
            return {
                "active_models": [],
                "total_vram_used_bytes": 0,
                "total_model_size_bytes": 0,
                "utilization_ratio": None,
                "available_vram_bytes_estimate": None,
                "status": "unreachable",
            }

        total_vram_used = sum(m.get("size_vram", 0) for m in active)
        total_model_size = sum(m.get("size", 0) for m in active)

        utilization_ratio = None
        available_vram = None

        if total_vram_bytes is not None:
            utilization_ratio = float(total_vram_used) / total_vram_bytes
            available_vram = max(0, total_vram_bytes - total_vram_used)

        return {
            "active_models": active,
            "total_vram_used_bytes": total_vram_used,
            "total_model_size_bytes": total_model_size,
            "utilization_ratio": utilization_ratio,
            "available_vram_bytes_estimate": available_vram,
            "status": "healthy",
        }

    def make_routing_decision(
        self,
        model_name: str,
        required_vram_bytes: int = 0,
        total_vram_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Make a real-time routing decision for loading or calling a model.

        Args:
            model_name: Name of the model desired.
            required_vram_bytes: Estimated size/VRAM required by the model.
                If 0, we will try to look up the size of the model from /api/tags if available.
            total_vram_bytes: Optional total GPU VRAM capacity in bytes.

        Returns:
            A routing decision dict:
            {
                "route_allowed": bool,
                "reason": str,
                "is_warm": bool,
                "action_recommended": "call" | "preload_and_call" | "unload_others" | "reject"
            }
        """
        metrics = self.get_vram_metrics(total_vram_bytes)
        if metrics["status"] == "unreachable":
            return {
                "route_allowed": False,
                "reason": "Ollama server is unreachable",
                "is_warm": False,
                "action_recommended": "reject",
            }

        # Check if already loaded
        active_models = metrics["active_models"]
        target_model = None
        for m in active_models:
            if m.get("name") == model_name or m.get("model") == model_name:
                target_model = m
                break

        if target_model:
            # Check if fully loaded in VRAM (or mostly in VRAM)
            vram_loaded = target_model.get("size_vram", 0)
            total_size = target_model.get("size", 0)
            is_warm = vram_loaded > 0 and (vram_loaded >= 0.9 * total_size or total_size == 0)
            
            return {
                "route_allowed": True,
                "reason": f"Model '{model_name}' is already loaded in VRAM ({vram_loaded}/{total_size} bytes)",
                "is_warm": is_warm,
                "action_recommended": "call",
            }

        # Model not loaded. Check if it's available locally.
        available = self.get_available_models()
        is_available = False
        actual_model_size = 0
        if available:
            for m in available:
                if m.get("name") == model_name or m.get("model") == model_name:
                    is_available = True
                    actual_model_size = m.get("size", 0)
                    break

        if not is_available:
            return {
                "route_allowed": False,
                "reason": f"Model '{model_name}' is not downloaded/available in Ollama. Must be pulled first.",
                "is_warm": False,
                "action_recommended": "reject",
            }

        effective_required_vram = required_vram_bytes if required_vram_bytes > 0 else actual_model_size

        # If total_vram_bytes is not specified, we can't estimate availability boundary but we can allow routing (non-warm)
        if total_vram_bytes is None:
            return {
                "route_allowed": True,
                "reason": f"Model '{model_name}' is available. Preloading recommended (no VRAM capacity specified).",
                "is_warm": False,
                "action_recommended": "preload_and_call",
            }

        # Check VRAM space
        available_vram = metrics["available_vram_bytes_estimate"]
        if available_vram >= effective_required_vram:
            return {
                "route_allowed": True,
                "reason": f"Model '{model_name}' can be loaded. Available VRAM ({available_vram} bytes) >= Required VRAM ({effective_required_vram} bytes).",
                "is_warm": False,
                "action_recommended": "preload_and_call",
            }
        else:
            # Suggest unloading models
            return {
                "route_allowed": False,
                "reason": f"Insufficient VRAM to load '{model_name}'. Available VRAM ({available_vram} bytes) < Required VRAM ({effective_required_vram} bytes).",
                "is_warm": False,
                "action_recommended": "unload_others",
            }


# Standalone function API
def check_health(base_url: str = "http://localhost:11434", timeout: float = 10.0) -> bool:
    """Check if the Ollama server is running and reachable."""
    return OllamaClient(base_url, timeout).check_health()


def get_available_models(base_url: str = "http://localhost:11434", timeout: float = 10.0) -> list[dict[str, Any]] | None:
    """List all models downloaded/pulled on the Ollama host (GET /api/tags)."""
    return OllamaClient(base_url, timeout).get_available_models()


def get_active_models(base_url: str = "http://localhost:11434", timeout: float = 10.0) -> list[dict[str, Any]] | None:
    """List all models currently loaded in memory/VRAM (GET /api/ps)."""
    return OllamaClient(base_url, timeout).get_active_models()


def preload_model(model_name: str, keep_alive: int | str = -1, base_url: str = "http://localhost:11434", timeout: float = 60.0) -> bool:
    """Preload a model into memory (VRAM) and configure its keep-alive duration."""
    return OllamaClient(base_url, timeout).preload_model(model_name, keep_alive)


def unload_model(model_name: str, base_url: str = "http://localhost:11434", timeout: float = 10.0) -> bool:
    """Unload a model from memory/VRAM immediately."""
    return OllamaClient(base_url, timeout).unload_model(model_name)


def get_vram_metrics(base_url: str = "http://localhost:11434", total_vram_bytes: int | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Retrieve metrics regarding loaded models and active VRAM usage."""
    return OllamaClient(base_url, timeout).get_vram_metrics(total_vram_bytes)


def make_routing_decision(
    model_name: str,
    required_vram_bytes: int = 0,
    total_vram_bytes: int | None = None,
    base_url: str = "http://localhost:11434",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Make a real-time routing decision for loading or calling a model."""
    return OllamaClient(base_url, timeout).make_routing_decision(model_name, required_vram_bytes, total_vram_bytes)
