"""Tests for auth_loader — standardized credential lookup.

Coverage:
  - active profile resolution (HERMES_PROFILE, HERMES_HOME, default)
  - profile_env_path normalizes absolute vs relative profile strings
  - get_secret explicit path
  - get_secret env var path
  - get_secret profile-env path
  - get_secret gcloud-adc path (google_adc spec)
  - get_secret project-env path (shared hd-platform/.env)
  - get_secret returns AuthResult with correct shape
  - register_secret writes to profile .env, preserves existing keys
  - register_secret sets file mode 0600
  - AuthResult.export_to_env() populates os.environ
  - AuthResult.to_dict() is JSON-serializable
  - redaction never includes raw value
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


from prismatic.shipped_plugins.pwp.capabilities.provision_site import auth_loader


# --- Active profile resolution -------------------------------------------


def test_resolve_active_profile_hermes_profile_bare(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_PROFILE", "ned")
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert auth_loader._resolve_active_profile() == "ned"


def test_resolve_active_profile_hermes_profile_absolute(monkeypatch) -> None:
    monkeypatch.setenv(
        "HERMES_PROFILE",
        str(Path("~/.hermes/profiles/orchestrator").expanduser()),
    )
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert auth_loader._resolve_active_profile() == "orchestrator"


def test_resolve_active_profile_hermes_home_fallback(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.setenv(
        "HERMES_HOME",
        str(Path("~/.hermes/profiles/ned").expanduser()),
    )
    assert auth_loader._resolve_active_profile() == "ned"


def test_resolve_active_profile_defaults_to_ned(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert auth_loader._resolve_active_profile() == "ned"


# --- profile_env_path ----------------------------------------------------


def test_profile_env_path_handles_relative() -> None:
    p = auth_loader._profile_env_path("ned")
    assert p == auth_loader.PROFILES_DIR / "ned" / ".env"


def test_profile_env_path_handles_absolute() -> None:
    abs_p = str(Path("~/.hermes/profiles/orchestrator").expanduser())
    p = auth_loader._profile_env_path(abs_p)
    assert p == Path(abs_p) / ".env"


# --- AuthResult ----------------------------------------------------------


def test_auth_result_to_dict_is_serializable() -> None:
    r = auth_loader.AuthResult(
        value="sk_test_xxx",
        source="env",
        env_var="STRIPE_SECRET_KEY",
        hint="ok",
        redaction=auth_loader._redact("sk_test_xxx"),
    )
    d = r.to_dict()
    assert d["found"] is True
    assert d["source"] == "env"
    # Make sure it round-trips through JSON
    json.dumps(d)


def test_auth_result_found_property() -> None:
    r1 = auth_loader.AuthResult(
        value="x", source="env", env_var="X", hint="", redaction=""
    )
    r2 = auth_loader.AuthResult(
        value=None, source="none", env_var="X", hint="", redaction=""
    )
    assert r1.found is True
    assert r2.found is False


def test_auth_result_export_to_env(monkeypatch) -> None:
    for k in ("STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    r = auth_loader.AuthResult(
        value="sk_test_export",
        source="explicit",
        env_var="STRIPE_SECRET_KEY",
        hint="ok",
        redaction=auth_loader._redact("sk_test_export"),
        env={"STRIPE_SECRET_KEY": "sk_test_export"},
    )
    r.export_to_env()
    assert os.environ.get("STRIPE_SECRET_KEY") == "sk_test_export"
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)


# --- Redaction ------------------------------------------------------------


def test_redact_truncates_value() -> None:
    r = auth_loader._redact("sk_live_abc123def456ghi789")
    # Should NOT include any part of the value past the prefix
    assert "abc123" not in r
    assert r.startswith("sk_live")


def test_redact_handles_empty() -> None:
    assert auth_loader._redact("") == "<empty>"


# --- get_secret: explicit -------------------------------------------------


def test_get_secret_explicit_overrides_everything(monkeypatch, tmp_path: Path) -> None:
    """An explicit value bypasses all other sources."""
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "ignored_due_to_explicit")
    r = auth_loader.get_secret("stripe_secret_key", explicit="rk_live_explicit")
    assert r.found is True
    assert r.source == "explicit"
    assert r.value == "rk_live_explicit"


# --- get_secret: env var path --------------------------------------------


def test_get_secret_finds_env_var(monkeypatch) -> None:
    for k in ("STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "rk_live_env")
    r = auth_loader.get_secret("stripe_secret_key")
    assert r.found is True
    assert r.source == "env"
    assert r.env_var == "STRIPE_RESTRICTED_KEY"
    assert r.value == "rk_live_env"
    monkeypatch.delenv("STRIPE_RESTRICTED_KEY", raising=False)


def test_get_secret_env_precedence(monkeypatch) -> None:
    """When multiple env vars are set, the first in spec.env_vars wins."""
    for k in ("STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_second")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_third")
    r = auth_loader.get_secret("stripe_secret_key")
    # STRIPE_RESTRICTED_KEY not set; STRIPE_API_KEY wins over STRIPE_SECRET_KEY
    assert r.env_var == "STRIPE_API_KEY"
    assert r.value == "sk_live_second"


# --- get_secret: profile-env path ----------------------------------------


def test_get_secret_finds_profile_env(tmp_path: Path, monkeypatch) -> None:
    """A profile .env with the right key is found."""
    # Create an isolated profile env
    profile_dir = tmp_path / "fake-profile"
    profile_dir.mkdir()
    (profile_dir / ".env").write_text("CLOUDFLARE_PAGES_API_TOKEN=test_cf_token\n")

    # Patch the resolver to use this fake profile
    monkeypatch.setattr(auth_loader, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(auth_loader, "ACTIVE_PROFILE", "fake-profile")

    for k in ("CLOUDFLARE_PAGES_API_TOKEN", "CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)

    r = auth_loader.get_secret("cloudflare_token")
    assert r.found is True
    assert r.source == "profile-env"
    assert r.value == "test_cf_token"


# --- get_secret: gcloud-adc path (google_adc) ---------------------------


def test_get_secret_finds_gcloud_adc(tmp_path: Path, monkeypatch) -> None:
    """gcloud ADC JSON is discovered under the active profile."""
    adc = {
        "type": "authorized_user",
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "GOCSPX-test",
        "refresh_token": "1//test_refresh",
    }
    # Create the ADC in the fake profile's gcloud dir
    profile_dir = tmp_path / "test-profile"
    (profile_dir / "home" / ".config" / "gcloud").mkdir(parents=True)
    (
        profile_dir
        / "home"
        / ".config"
        / "gcloud"
        / "application_default_credentials.json"
    ).write_text(json.dumps(adc))

    monkeypatch.setattr(auth_loader, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(auth_loader, "ACTIVE_PROFILE", "test-profile")
    for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SA_JSON"):
        monkeypatch.delenv(k, raising=False)

    r = auth_loader.get_secret("google_adc")
    assert r.found is True
    assert r.source == "gcloud-adc"
    # The value should be the full JSON
    assert json.loads(r.value)["refresh_token"] == "1//test_refresh"


# --- get_secret: project-env path (shared hd-platform) ------------------


def test_get_secret_finds_project_env(tmp_path: Path, monkeypatch) -> None:
    """A shared project .env (like <WORK_DIR>/hd-platform/.env) is found."""
    # Mock WORK_DIR
    monkeypatch.setattr(auth_loader, "WORK_DIR", tmp_path)
    (tmp_path / "hd-platform").mkdir()
    (tmp_path / "hd-platform" / ".env").write_text(
        "STRIPE_SECRET_KEY=sk_test_proj_env\n"
    )
    for k in ("STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)

    r = auth_loader.get_secret("stripe_secret_key")
    assert r.found is True
    assert r.source == "project-env"
    assert r.value == "sk_test_proj_env"


# --- get_secret: not found -----------------------------------------------


def test_get_secret_returns_helpful_hint_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """When nothing is found, the hint must include a working register command."""
    monkeypatch.setattr(auth_loader, "WORK_DIR", tmp_path)
    monkeypatch.setattr(auth_loader, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(auth_loader, "ACTIVE_PROFILE", "empty-profile")
    for k in ("STRIPE_RESTRICTED_KEY", "STRIPE_API_KEY", "STRIPE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)

    r = auth_loader.get_secret("stripe_secret_key")
    assert r.found is False
    assert r.source == "none"
    assert "auth register" in r.hint
    assert "stripe_secret_key" in r.hint


# --- register_secret -----------------------------------------------------


def test_register_secret_creates_new_file(tmp_path: Path, monkeypatch) -> None:
    """register_secret creates a new .env file if it doesn't exist."""
    monkeypatch.setattr(auth_loader, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(auth_loader, "ACTIVE_PROFILE", "new-profile")

    path = auth_loader.register_secret("stripe_secret_key", value="sk_test_registered")
    assert path.exists()
    content = path.read_text()
    assert "STRIPE_RESTRICTED_KEY=sk_test_registered" in content


def test_register_secret_updates_existing_keys(tmp_path: Path, monkeypatch) -> None:
    """register_secret preserves other keys when updating one."""
    monkeypatch.setattr(auth_loader, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(auth_loader, "ACTIVE_PROFILE", "existing-profile")

    profile_dir = tmp_path / "existing-profile"
    profile_dir.mkdir()
    env_path = profile_dir / ".env"
    env_path.write_text("OTHER_KEY=other_value\nSTRIPE_RESTRICTED_KEY=old\n")

    path = auth_loader.register_secret("stripe_secret_key", value="sk_test_new")
    content = path.read_text()
    assert "OTHER_KEY=other_value" in content
    assert "STRIPE_RESTRICTED_KEY=sk_test_new" in content
    assert "old" not in content


def test_register_secret_sets_file_mode_0600(tmp_path: Path, monkeypatch) -> None:
    """register_secret restricts the .env file to owner read/write only."""
    monkeypatch.setattr(auth_loader, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(auth_loader, "ACTIVE_PROFILE", "secure-profile")

    path = auth_loader.register_secret("vercel_token", value="v_test_xxx")
    mode = path.stat().st_mode
    # 0o600 = owner rw, group 0, other 0 (POSIX platforms)
    if os.name != "nt":
        assert stat.S_IMODE(mode) == 0o600


# --- list_known ----------------------------------------------------------


def test_list_known_includes_stripe() -> None:
    specs = auth_loader.list_known()
    names = {s["name"] for s in specs}
    assert "stripe_secret_key" in names
    assert "google_adc" in names
    assert "vercel_token" in names
    assert "linear_api_key" in names
    assert "cloudflare_token" in names


# --- Layout B (HERMES_HOME points at a profile root) --------------------


def test_resolve_profiles_dir_layout_a_hermes_root() -> None:
    """Layout A: HERMES_HOME = ~/.hermes → PROFILES_DIR = ~/.hermes/profiles."""
    from pathlib import Path

    profiles_dir, profile = auth_loader._resolve_profiles_dir(
        Path("/home/test/.hermes")
    )
    assert profiles_dir == Path("/home/test/.hermes/profiles")
    assert profile is None


def test_resolve_profiles_dir_layout_b_profile_root() -> None:
    """Layout B: HERMES_HOME = ~/.hermes/profiles/<name> →
    PROFILES_DIR = ~/.hermes/profiles (parent IS the profiles dir)."""
    from pathlib import Path

    profiles_dir, profile = auth_loader._resolve_profiles_dir(
        Path("/home/test/.hermes/profiles/ned")
    )
    assert profiles_dir == Path("/home/test/.hermes/profiles")
    assert profile == "ned"


def test_resolve_profiles_dir_no_false_positive() -> None:
    """A path that contains '/profiles/' mid-string but not at the end
    must NOT be misclassified as Layout B."""
    from pathlib import Path

    # '/profiles/' in the middle, no profile name at end
    profiles_dir, profile = auth_loader._resolve_profiles_dir(
        Path("/srv/profiles/data")
    )
    assert profiles_dir == Path("/srv/profiles/data/profiles")
    assert profile is None


# --- AuthResult.__repr__ / __str__ redaction ----------------------------


def test_auth_result_repr_does_not_leak_value(monkeypatch) -> None:
    """__repr__ and __str__ must never include the raw secret value,
    even though the dataclass field is named `value`."""
    monkeypatch.setenv("GITHUB_TOKEN", "test_dummy_token_val_xxxxxxxxxxxxxxxxxxxxxxxxxx")
    r = auth_loader.get_secret("github_token")
    secret = "test_dummy_token_val_xxxxxxxxxxxxxxxxxxxxxxxxxx"
    # repr/str are safe
    assert secret not in repr(r)
    assert secret not in str(r)
    # .to_dict() also safe (the secret-bearing field is excluded)
    assert secret not in str(r.to_dict())
    # but .value (explicit access) still has it
    assert r.value == secret
    # also confirm `value` is in __repr__ field list but never inlined
    repr_str = repr(r)
    assert "ghp_super" not in repr_str
    assert "redaction=" in repr_str
    assert r.redaction in repr_str


def test_auth_result_repr_does_not_leak_value_in_dataclass_repr() -> None:
    """Make sure even direct dataclass repr (bypassing our __repr__) is
    safe to log via to_dict(), which is the canonical export path."""
    from dataclasses import asdict

    r = auth_loader.AuthResult(
        value="ghp_supersecret",
        source="env",
        env_var="GITHUB_TOKEN",
        hint="...",
        redaction="ghp_supe...len=16",
    )
    d = asdict(r)
    # Confirm field shape (so reviewers see what's in the dict)
    assert "value" in d
    assert d["value"] == "ghp_supersecret"  # .value contains the secret
    # But the safe-export method must NOT include it
    safe = r.to_dict()
    assert "value" not in safe or safe.get("value") != "ghp_supersecret"
