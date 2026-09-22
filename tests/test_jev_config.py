"""Tests for JevConfig: env parsing, validation, frozen dataclass."""

import dataclasses

import pytest

from prismatic.jev import DecisionError, JevConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "SWARMJEV_TIMEOUT_S",
        "SWARMJEV_MAX_ATTEMPTS",
        "SWARMJEV_BACKOFF_BASE_S",
        "SWARMJEV_BACKOFF_CAP_S",
        "SWARMJEV_BREAKER_THRESHOLD",
        "SWARMJEV_BREAKER_RESET_S",
        "SWARMJEV_MAX_CONCURRENT",
        "SWARMJEV_MAX_REPAIR_ATTEMPTS",
        "SWARMJEV_MEMOIZE",
        "SWARMJEV_TRACE_PATH",
        "SWARMJEV_INCLUDE_META",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_safe():
    cfg = JevConfig.from_env()
    assert cfg.timeout_s == 10.0
    assert cfg.max_attempts == 3
    assert cfg.backoff_base_s == 0.2
    assert cfg.backoff_cap_s == 5.0
    assert cfg.breaker_failure_threshold == 5
    assert cfg.breaker_reset_timeout_s == 30.0
    assert cfg.max_concurrent == 20
    assert cfg.max_repair_attempts == 1
    assert cfg.memoize is False
    assert cfg.trace_path is None
    assert cfg.include_meta is True


def test_config_is_frozen():
    cfg = JevConfig.from_env()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.timeout_s = 1.0  # type: ignore[misc]


def test_env_overrides_parse(monkeypatch):
    monkeypatch.setenv("SWARMJEV_TIMEOUT_S", "5.5")
    monkeypatch.setenv("SWARMJEV_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("SWARMJEV_MEMOIZE", "true")
    monkeypatch.setenv("SWARMJEV_TRACE_PATH", "/tmp/jev.jsonl")
    monkeypatch.setenv("SWARMJEV_INCLUDE_META", "0")
    cfg = JevConfig.from_env()
    assert cfg.timeout_s == 5.5
    assert cfg.max_attempts == 2
    assert cfg.memoize is True
    assert cfg.trace_path == "/tmp/jev.jsonl"
    assert cfg.include_meta is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "On"])
def test_truthy_variants(monkeypatch, value):
    monkeypatch.setenv("SWARMJEV_MEMOIZE", value)
    assert JevConfig.from_env().memoize is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_falsy_variants(monkeypatch, value):
    monkeypatch.setenv("SWARMJEV_MEMOIZE", value)
    assert JevConfig.from_env().memoize is False


@pytest.mark.parametrize(
    "env_name,env_value",
    [
        ("SWARMJEV_TIMEOUT_S", "not-a-number"),
        ("SWARMJEV_TIMEOUT_S", "0.05"),  # below minimum 0.1
        ("SWARMJEV_MAX_ATTEMPTS", "zero"),
        ("SWARMJEV_MAX_ATTEMPTS", "0"),  # below minimum 1
        ("SWARMJEV_MAX_ATTEMPTS", "11"),  # above maximum 10
        ("SWARMJEV_BACKOFF_BASE_S", "-1"),
        ("SWARMJEV_BACKOFF_CAP_S", "0"),
        ("SWARMJEV_BREAKER_THRESHOLD", "0"),
        ("SWARMJEV_BREAKER_THRESHOLD", "101"),
        ("SWARMJEV_BREAKER_RESET_S", "0.5"),
        ("SWARMJEV_MAX_CONCURRENT", "0"),
        ("SWARMJEV_MAX_REPAIR_ATTEMPTS", "3"),  # above maximum 2
        ("SWARMJEV_MAX_REPAIR_ATTEMPTS", "-1"),
    ],
)
def test_invalid_env_fails_closed(monkeypatch, env_name, env_value):
    monkeypatch.setenv(env_name, env_value)
    with pytest.raises(DecisionError):
        JevConfig.from_env()


def test_repair_attempts_zero_disables(monkeypatch):
    monkeypatch.setenv("SWARMJEV_MAX_REPAIR_ATTEMPTS", "0")
    assert JevConfig.from_env().max_repair_attempts == 0


def test_direct_construction_ignores_env(monkeypatch):
    monkeypatch.setenv("SWARMJEV_TIMEOUT_S", "1.0")
    cfg = JevConfig(timeout_s=7.0)
    assert cfg.timeout_s == 7.0
