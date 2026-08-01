"""
Prismatic Engine — Signal Providers
====================================

Package-level factory: load the right SignalProvider from config.
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

from .base import SignalProvider, SignalPayload, SignalAction
from .file import FileSignalProvider
from .http import HTTPSignalProvider
from .redis import RedisSignalProvider

__all__ = [
    "SignalProvider",
    "SignalPayload",
    "SignalAction",
    "FileSignalProvider",
    "HTTPSignalProvider",
    "RedisSignalProvider",
    "create_signal_provider",
    "create_fallback_chain",
]


def create_signal_provider(config: dict[str, Any]) -> SignalProvider:
    """Factory: instantiate the right provider from a config dict.

    Config shape:
        { "type": "file", "directory": "/tmp/prismatic" }
        { "type": "http", "endpoints": {"fred": "..."}, "shared_secret": "..." }
        { "type": "redis", "host": "redis.local", "port": 6379 }

    Raises ValueError for unknown provider types.
    """
    provider_type = config.get("type", "file")

    if provider_type == "file":
        return FileSignalProvider(
            directory=config.get("directory", "/tmp/prismatic"),
        )

    elif provider_type == "http":
        return HTTPSignalProvider(
            endpoints=config.get("endpoints", {}),
            shared_secret=config.get("shared_secret"),
            timeout=config.get("timeout", 10.0),
            max_retries=config.get("max_retries", 3),
        )

    elif provider_type == "redis":
        return RedisSignalProvider(
            host=config.get("host", "localhost"),
            port=config.get("port", 6379),
            db=config.get("db", 0),
            password=config.get("password"),
            prefix=config.get("prefix", "prismatic:signal"),
        )

    # Bolt-on providers can be registered here
    raise ValueError(f"Unknown signal provider type: {provider_type}")


class FallbackChain(SignalProvider):
    """Try providers in order until one succeeds.

    The chain stops at the first provider that returns True from send().
    If all fail, the dead_letter provider is tried last (e.g., Telegram
    to notify Michael that the swarm is unreachable).
    """

    def __init__(self, providers: list[SignalProvider], dead_letter: SignalProvider | None = None):
        self._providers = providers
        self._dead_letter = dead_letter

    def send(self, target: str, payload: SignalPayload) -> bool:
        for provider in self._providers:
            if provider.send(target, payload):
                return True
        # All primary providers failed — try dead letter
        if self._dead_letter:
            return self._dead_letter.send(target, payload)
        return False

    def poll(self, target: str, timeout: float = 0) -> SignalPayload | None:
        # Poll the first provider that supports polling
        for provider in self._providers:
            result = provider.poll(target, timeout=timeout)
            if result is not None:
                return result
        if self._dead_letter:
            return self._dead_letter.poll(target, timeout=timeout)
        return None

    def acknowledge(self, signal_id: str) -> bool:
        for provider in self._providers:
            if provider.acknowledge(signal_id):
                return True
        if self._dead_letter:
            return self._dead_letter.acknowledge(signal_id)
        return False

    def list_targets(self) -> list[str]:
        targets = set()
        for provider in self._providers:
            targets.update(provider.list_targets())
        if self._dead_letter:
            targets.update(self._dead_letter.list_targets())
        return sorted(targets)


def create_fallback_chain(configs: list[dict[str, Any]]) -> FallbackChain:
    """Build a fallback chain from a list of provider configs.

    Example config:
        [
            {"type": "file", "directory": "/tmp/prismatic"},
            {"type": "http", "endpoints": {"fred": "..."}},
        ]

    The last config in the list is treated as the dead_letter provider.
    """
    if not configs:
        raise ValueError("At least one provider config required")

    providers = [create_signal_provider(c) for c in configs[:-1]]
    dead_letter = create_signal_provider(configs[-1]) if len(configs) > 1 else None
    return FallbackChain(providers, dead_letter=dead_letter)
