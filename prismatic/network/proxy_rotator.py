"""Proxy rotation for high-risk outbound domains.

Config can be loaded from `prismatic/config/proxies.yaml` or provided directly in
memory for tests. The YAML format is intentionally simple and dependency-light;
PyYAML is already a project dependency.
"""

from __future__ import annotations

import os
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from prismatic.network.rate_limiter import get_domain_policy, normalize_domain


@dataclass
class ProxyConfig:
    name: str
    url: str
    healthy: bool = True
    domains: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def matches(self, domain: str) -> bool:
        if not self.domains or "*" in self.domains:
            return True
        return any(domain == d or domain.endswith(f".{d}") for d in self.domains)


class ProxyRotator:
    """Round-robin healthy proxy selector with per-domain rules."""

    def __init__(
        self,
        proxies: list[ProxyConfig] | None = None,
        *,
        domain_rules: dict[str, str] | None = None,
        mode: str = "round_robin",
        rng: random.Random | None = None,
    ) -> None:
        self.proxies = proxies or []
        self.domain_rules = domain_rules or {}
        self.mode = mode
        self._rng = rng or random.Random()
        self._index = 0
        self._lock = threading.Lock()

    @classmethod
    def from_file(cls, path: str | Path) -> "ProxyRotator":
        data = yaml.safe_load(Path(path).read_text()) or {}
        proxies: list[ProxyConfig] = []
        for item in data.get("proxies", []) or []:
            if not item.get("url"):
                continue
            proxies.append(
                ProxyConfig(
                    name=item.get("name") or item["url"],
                    url=item["url"],
                    healthy=bool(item.get("healthy", True)),
                    domains=tuple(item.get("domains", ["*"])),
                    metadata={k: v for k, v in item.items() if k not in {"name", "url", "healthy", "domains"}},
                )
            )
        return cls(
            proxies,
            domain_rules=dict(data.get("domain_rules", {}) or {}),
            mode=str(data.get("rotation", "round_robin")),
        )

    @classmethod
    def from_env_or_file(cls, path: str | Path = "prismatic/config/proxies.yaml") -> "ProxyRotator":
        env_pool = os.environ.get("PRISMATIC_PROXY_POOL", "").strip()
        if env_pool:
            proxies = [
                ProxyConfig(name=f"env-{idx+1}", url=url.strip())
                for idx, url in enumerate(env_pool.split(","))
                if url.strip()
            ]
            return cls(proxies)
        config_path = Path(path)
        if config_path.exists():
            return cls.from_file(config_path)
        return cls([])

    def requires_proxy(self, url_or_domain: str) -> bool:
        domain = normalize_domain(url_or_domain)
        rule = self._matching_rule(domain)
        if rule == "always_proxy":
            return True
        if rule == "direct":
            return False
        return get_domain_policy(domain).requires_proxy

    def select(self, url_or_domain: str, *, require: bool | None = None) -> ProxyConfig | None:
        domain = normalize_domain(url_or_domain)
        if require is None:
            require = self.requires_proxy(domain)
        candidates = [p for p in self.proxies if p.healthy and p.matches(domain)]
        if not candidates:
            if require:
                raise RuntimeError(f"proxy required for {domain}, but no healthy proxy is configured")
            return None
        with self._lock:
            if self.mode == "random":
                return self._rng.choice(candidates)
            selected = candidates[self._index % len(candidates)]
            self._index += 1
            return selected

    def mark_unhealthy(self, name_or_url: str) -> None:
        for proxy in self.proxies:
            if proxy.name == name_or_url or proxy.url == name_or_url:
                proxy.healthy = False
                return
        raise KeyError(name_or_url)

    def mark_healthy(self, name_or_url: str) -> None:
        for proxy in self.proxies:
            if proxy.name == name_or_url or proxy.url == name_or_url:
                proxy.healthy = True
                return
        raise KeyError(name_or_url)

    def _matching_rule(self, domain: str) -> str | None:
        for suffix, rule in self.domain_rules.items():
            suffix = normalize_domain(suffix)
            if domain == suffix or domain.endswith(f".{suffix}"):
                return rule
        return None
