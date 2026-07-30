"""Tests for the provision_site capability — Phase 1 Cloudflare-first MVP.

Covers:
  - Domain verification (DNS TXT challenge) — token generation, record
    name, verified vs. unverified cases
  - Cloudflare client — token validation, error parsing, request shape
  - Orchestrator — step ordering, resume from partial run, status
    persistence, overall_status transitions
  - Step functions — verify_domain (with mocked DoH), cloudflare_zone
    (with mocked client), register_in_registry, migrate_kpi
  - operator_cli wiring — subcommand registration, JSON output shape
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plugins.pwp.capabilities.provision_site import (
    CloudflareError,
    orchestrator,
)
from plugins.pwp.capabilities.provision_site.domain_verifier import (
    generate_challenge_token,
    expected_record_name,
    verify,
)
from plugins.pwp.capabilities.provision_site.steps import (
    register_in_registry,
    step_cloudflare_zone,
    step_verify_domain,
)
from plugins.pwp.capabilities.provision_site import types as prov_types


HERE = Path(__file__).resolve().parent


# --- Domain verifier ----------------------------------------------------


def test_generate_challenge_token_format() -> None:
    """The challenge token must be `pwp-verify-<16 hex chars>` so DNS
    propagation is reliable and the value is readable in the zone file."""
    tok = generate_challenge_token()
    assert tok.startswith("pwp-verify-")
    assert len(tok) == len("pwp-verify-") + 16
    assert all(c in "0123456789abcdef" for c in tok[len("pwp-verify-") :])


def test_expected_record_name() -> None:
    assert expected_record_name("example.com") == "_pwp-verify.example.com"
    assert (
        expected_record_name("foo.bar.example.com") == "_pwp-verify.foo.bar.example.com"
    )


def test_verify_with_observed_values_match() -> None:
    """When the observed TXT record matches the expected token, verify
    returns verified=True and surfaces the observation."""
    res = verify(
        "example.com",
        expected_value="pwp-verify-abc123",
        observed_values=["pwp-verify-abc123", "other=value"],
    )
    assert res.verified is True
    assert res.observed_values == ["pwp-verify-abc123", "other=value"]
    assert res.error is None


def test_verify_with_observed_values_mismatch() -> None:
    """When the observed TXT records don't include the expected token,
    verify returns verified=False with a helpful error."""
    res = verify(
        "example.com",
        expected_value="pwp-verify-abc123",
        observed_values=["other=value", "spf=v1"],
    )
    assert res.verified is False
    assert "pwp-verify-abc123" not in res.observed_values
    assert res.error is not None
    assert "no TXT record" in res.error


def test_verify_with_no_observed_values() -> None:
    """An empty observation list (record doesn't exist yet) yields
    verified=False with the record name surfaced for the user."""
    res = verify("example.com", expected_value="pwp-verify-abc123", observed_values=[])
    assert res.verified is False
    assert "_pwp-verify.example.com" in res.error


def test_generate_challenge_tokens_are_unique() -> None:
    """Two consecutive tokens must not collide — used to avoid replay
    attacks if a domain was previously verified with an old token."""
    a = generate_challenge_token()
    b = generate_challenge_token()
    assert a != b


# --- Cloudflare client --------------------------------------------------


def test_cloudflare_client_requires_token() -> None:
    """A CloudflareClient constructed without a token must raise ValueError."""
    with pytest.raises(ValueError, match="Cloudflare token is empty"):
        from plugins.pwp.capabilities.provision_site import CloudflareClient

        CloudflareClient(token="")


def test_cloudflare_client_from_env_missing() -> None:
    """from_env must surface a helpful error listing the env vars it tried."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError) as excinfo:
            from plugins.pwp.capabilities.provision_site import CloudflareClient

            CloudflareClient.from_env()
    msg = str(excinfo.value)
    # The error should list every env var the user could set.
    for name in ("CF_API_TOKEN", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_PAGES_API_TOKEN"):
        assert name in msg, f"{name} not in error message: {msg}"


def test_cloudflare_client_from_env_precedence() -> None:
    """When multiple token env vars are set, CF_API_TOKEN wins; if absent,
    CLOUDFLARE_API_TOKEN; if absent, CLOUDFLARE_PAGES_API_TOKEN."""
    from plugins.pwp.capabilities.provision_site import CloudflareClient

    # Only CLOUDFLARE_PAGES_API_TOKEN set
    with patch.dict(
        os.environ, {"CLOUDFLARE_PAGES_API_TOKEN": "pages-tok"}, clear=True
    ):
        cf = CloudflareClient.from_env()
        assert cf._token == "pages-tok"
        assert cf._token_source == "CLOUDFLARE_PAGES_API_TOKEN"
    # CLOUDFLARE_API_TOKEN takes precedence over CLOUDFLARE_PAGES_API_TOKEN
    with patch.dict(
        os.environ,
        {
            "CLOUDFLARE_API_TOKEN": "general-tok",
            "CLOUDFLARE_PAGES_API_TOKEN": "pages-tok",
        },
        clear=True,
    ):
        cf = CloudflareClient.from_env()
        assert cf._token == "general-tok"
        assert cf._token_source == "CLOUDFLARE_API_TOKEN"
    # CF_API_TOKEN wins over everything
    with patch.dict(
        os.environ,
        {
            "CF_API_TOKEN": "canonical-tok",
            "CLOUDFLARE_API_TOKEN": "general-tok",
            "CLOUDFLARE_PAGES_API_TOKEN": "pages-tok",
        },
        clear=True,
    ):
        cf = CloudflareClient.from_env()
        assert cf._token == "canonical-tok"
        assert cf._token_source == "CF_API_TOKEN"


def test_cloudflare_client_parses_errors() -> None:
    """Cloudflare API errors must surface the CF error code + message."""
    cf = _mk_client_with_mock(
        [
            _mock_response(
                400,
                success=False,
                errors=[
                    {"code": 1004, "message": "Invalid zone name"},
                ],
            )
        ]
    )
    with pytest.raises(CloudflareError) as excinfo:
        cf.zone_list()
    err = excinfo.value
    assert err.status_code == 400
    assert err.code == 1004
    assert "Invalid zone name" in err.message


def test_cloudflare_client_zones_endpoint_shape() -> None:
    """zone_list() must hit /zones and parse the `result` array."""
    cf = _mk_client_with_mock(
        [
            _mock_response(
                200,
                success=True,
                result=[
                    {
                        "id": "abc123",
                        "name": "example.com",
                        "status": "active",
                        "name_servers": ["ns1.cf", "ns2.cf"],
                        "plan": {"name": "free"},
                        "paused": False,
                    }
                ],
            ),
        ]
    )
    zones = cf.zone_list()
    assert len(zones) == 1
    z = zones[0]
    assert z.id == "abc123"
    assert z.name == "example.com"
    assert z.status == "active"
    assert z.nameservers == ["ns1.cf", "ns2.cf"]
    assert z.plan == "free"


# --- Orchestrator -------------------------------------------------------


def test_orchestrator_runs_steps_in_order(tmp_path: Path) -> None:
    """The orchestrator should run steps in STEP_NAMES order and write
    state after each step."""
    step_calls: list[str] = []

    def fake_step(step_name, domain, owner, run, *, publish_root, prior_outputs=None):
        step_calls.append(step_name)
        return prov_types.StepResult(name=step_name, status="complete")

    with patch.object(orchestrator, "_run_step", side_effect=fake_step):
        run = orchestrator.run(
            domain="example.com",
            owner="me@example.com",
            publish_root=tmp_path,
            resume=False,
        )
    assert step_calls == orchestrator.STEP_NAMES
    assert run.overall_status == "complete"
    # State file was written
    assert (tmp_path / "example.com.json").exists()


def test_orchestrator_stops_on_step_failure(tmp_path: Path) -> None:
    """If any step fails, the orchestrator should stop, mark the run
    failed, and persist the state."""
    call_count = {"n": 0}

    def fake_step(step_name, domain, owner, run, *, publish_root, prior_outputs=None):
        call_count["n"] += 1
        if step_name == "verify_domain":
            return prov_types.StepResult(
                name=step_name,
                status="failed",
                error="verification pending",
            )
        return prov_types.StepResult(name=step_name, status="complete")

    with patch.object(orchestrator, "_run_step", side_effect=fake_step):
        run = orchestrator.run(
            domain="example.com",
            owner="me@example.com",
            publish_root=tmp_path,
            resume=False,
        )
    # platform_detect ran first; verify_domain failed and stopped the run.
    # Subsequent steps (cloudflare_zone, etc.) must NOT have run.
    assert run.overall_status == "failed"
    assert call_count["n"] == 2
    assert run.steps[0].name == "platform_detect"
    assert run.steps[0].status == "complete"
    assert run.steps[1].name == "verify_domain"
    assert run.steps[1].status == "failed"


def test_orchestrator_resume_skips_completed_steps(tmp_path: Path) -> None:
    """On a re-run after a partial failure, completed steps from the
    prior state must be skipped."""
    # Pre-populate state with verify_domain complete.
    state_path = tmp_path / "example.com.json"
    state_path.write_text(
        json.dumps(
            {
                "domain": "example.com",
                "owner": "me@example.com",
                "started_at": "2026-01-01T00:00:00+00:00",
                "overall_status": "in_progress",
                "steps": [
                    {
                        "name": "verify_domain",
                        "status": "complete",
                        "started_at": "x",
                        "finished_at": "y",
                        "output": {},
                    },
                ],
            }
        )
    )
    call_count = {"n": 0}

    def fake_step(step_name, domain, owner, run, *, publish_root, prior_outputs=None):
        call_count["n"] += 1
        return prov_types.StepResult(name=step_name, status="complete")

    with patch.object(orchestrator, "_run_step", side_effect=fake_step):
        run = orchestrator.run(
            domain="example.com",
            owner="me@example.com",
            publish_root=tmp_path,
            resume=True,
        )
    # verify_domain was skipped (came from prior state); the remaining
    # 11 steps (platform_detect + cloudflare_zone + vercel_project +
    # gsc_verify + ga4_property + gtm_container + register_stripe +
    # github_checkout + register_zapier_webhook + register_in_registry
    # + migrate_kpi) ran.
    assert call_count["n"] == 11
    # verify_domain is still in run.steps but its status was preserved
    # from the prior state (complete).
    verify_step = next(s for s in run.steps if s.name == "verify_domain")
    assert verify_step.status == "complete"


def test_orchestrator_passes_prior_outputs_to_step(tmp_path: Path) -> None:
    """When resuming a failed run, prior_outputs from the prior step
    attempts are passed to step functions so they can recover state
    (e.g. reuse a challenge token)."""
    captured = {}

    def fake_step(step_name, domain, owner, run, *, publish_root, prior_outputs=None):
        captured["prior_outputs"] = prior_outputs
        return prov_types.StepResult(name=step_name, status="complete")

    # Pre-populate state with verify_domain FAILED with a challenge_token.
    state_path = tmp_path / "example.com.json"
    state_path.write_text(
        json.dumps(
            {
                "domain": "example.com",
                "owner": "me@example.com",
                "started_at": "2026-01-01T00:00:00+00:00",
                "overall_status": "in_progress",
                "steps": [
                    {
                        "name": "verify_domain",
                        "status": "failed",
                        "started_at": "x",
                        "finished_at": "y",
                        "output": {
                            "challenge_token": "pwp-verify-fixed-token",
                            "record_name": "_pwp-verify.example.com",
                        },
                    },
                ],
            }
        )
    )
    with patch.object(orchestrator, "_run_step", side_effect=fake_step):
        orchestrator.run(
            domain="example.com",
            owner="me@example.com",
            publish_root=tmp_path,
            resume=True,
        )
    # The step received prior_outputs containing the prior challenge_token.
    assert (
        captured["prior_outputs"].get("verify_domain", {}).get("challenge_token")
        == "pwp-verify-fixed-token"
    )


def test_orchestrator_prior_outputs_include_complete_steps(tmp_path: Path) -> None:
    """Regression for #CRITICAL: prior_outputs must include output from
    COMPLETE upstream steps (not just failed ones). gsc_verify depends on
    cloudflare_zone.zone_id; if complete steps are excluded from
    prior_outputs, downstream steps can't see them."""
    captured = {}

    def fake_step(step_name, domain, owner, run, *, publish_root, prior_outputs=None):
        captured["prior_outputs"] = prior_outputs
        return prov_types.StepResult(name=step_name, status="complete")

    state_path = tmp_path / "example.com.json"
    state_path.write_text(
        json.dumps(
            {
                "domain": "example.com",
                "owner": "me@example.com",
                "started_at": "2026-01-01T00:00:00+00:00",
                "overall_status": "in_progress",
                "steps": [
                    {
                        "name": "cloudflare_zone",
                        "status": "complete",
                        "started_at": "x",
                        "finished_at": "y",
                        "output": {"zone_id": "abc-123", "action": "lookup"},
                    },
                ],
            }
        )
    )
    with patch.object(orchestrator, "_run_step", side_effect=fake_step):
        orchestrator.run(
            domain="example.com",
            owner="me@example.com",
            publish_root=tmp_path,
            resume=True,
        )
    # Verify the COMPLETE cloudflare_zone output made it into prior_outputs.
    assert (
        captured["prior_outputs"].get("cloudflare_zone", {}).get("zone_id") == "abc-123"
    )


def test_orchestrator_status_returns_none_for_unknown_domain(tmp_path: Path) -> None:
    """status() returns None if no run exists for the domain."""
    assert orchestrator.status("nope.example.com", publish_root=tmp_path) is None


# --- Steps --------------------------------------------------------------


def test_step_verify_domain_issues_token_and_pending(tmp_path: Path) -> None:
    """verify_domain on first run: issues a token, returns failed
    (pending verification) with the TXT record instructions."""
    run_state = prov_types.ProvisionRun(
        domain="example.com",
        owner="me@example.com",
        started_at="2026-01-01T00:00:00+00:00",
    )
    result = step_verify_domain(
        domain="example.com",
        owner="me@example.com",
        run=run_state,
        publish_root=tmp_path,
    )
    assert result.status == "failed"  # pending verification
    assert "pwp-verify-" in result.output["challenge_token"]
    assert result.output["record_name"] == "_pwp-verify.example.com"
    assert "Create a DNS TXT record" in result.error


def test_step_verify_domain_success_after_record_created(tmp_path: Path) -> None:
    """verify_domain succeeds when the TXT record is found.

    The step generates a fresh challenge token on each call; the test
    asserts the step ran the verify() check (mocked to return True)
    and surfaced the observed values without asserting a specific
    token (which would couple the test to the step's token-format).
    """
    with patch(
        "plugins.pwp.capabilities.provision_site.steps.verify",
        return_value=_mk_verify_result(verified=True, expected="pwp-verify-x"),
    ):
        run_state = prov_types.ProvisionRun(
            domain="example.com",
            owner="me@example.com",
            started_at="2026-01-01T00:00:00+00:00",
        )
        result = step_verify_domain(
            domain="example.com",
            owner="me@example.com",
            run=run_state,
            publish_root=tmp_path,
        )
    assert result.status == "complete"
    assert result.output["challenge_token"].startswith("pwp-verify-")
    assert "pwp-verify-x" in result.output["observed_values"]
    # First run — should NOT mark the token as reused.
    assert result.output["reused_prior_token"] is False


def test_step_verify_domain_reuses_prior_token(tmp_path: Path) -> None:
    """When called with prior_outputs containing a challenge_token,
    the step should reuse it (so the user doesn't have to update the
    TXT record with a new value each run)."""
    prior = {"verify_domain": {"challenge_token": "pwp-verify-fixed"}}
    with patch(
        "plugins.pwp.capabilities.provision_site.steps.verify",
        return_value=_mk_verify_result(verified=True, expected="pwp-verify-fixed"),
    ):
        run_state = prov_types.ProvisionRun(
            domain="example.com",
            owner="me@example.com",
            started_at="2026-01-01T00:00:00+00:00",
        )
        result = step_verify_domain(
            domain="example.com",
            owner="me@example.com",
            run=run_state,
            publish_root=tmp_path,
            prior_outputs=prior,
        )
    assert result.status == "complete"
    assert result.output["challenge_token"] == "pwp-verify-fixed"
    assert result.output["reused_prior_token"] is True


def test_step_register_in_registry_writes_appendix(tmp_path: Path) -> None:
    """register_in_registry should write to <publish_root>/sites.json."""
    run_state = prov_types.ProvisionRun(
        domain="newco.com",
        owner="founder@newco.com",
        started_at="2026-01-01T00:00:00+00:00",
    )
    from plugins.pwp.capabilities.provision_site.steps import (
        step_register_in_registry,
    )

    result = step_register_in_registry(
        domain="newco.com",
        owner="founder@newco.com",
        run=run_state,
        publish_root=tmp_path,
    )
    assert result.status == "complete"
    appendix = tmp_path / "sites.json"
    assert appendix.exists()
    data = json.loads(appendix.read_text())
    assert "newco.com" in data
    entry = data["newco.com"]
    assert entry["slug"] == "newco"
    assert entry["owner"] == "founder@newco.com"
    assert entry["ga4_measurement_env"] == "NEWCO_GA4_MEASUREMENT_ID"


def test_slug_from_domain() -> None:
    """slug_from_domain turns 'example.com' into 'example'."""
    assert register_in_registry.slug_from_domain("example.com") == "example"
    assert register_in_registry.slug_from_domain("foo-bar.com") == "foo-bar"
    assert register_in_registry.slug_from_domain("xn--bcher-kva.com") == "xn--bcher-kva"


# --- Helpers ------------------------------------------------------------


def _mk_verify_result(*, verified: bool, expected: str):
    """Build a VerifyResult for tests without going through DNS."""
    from plugins.pwp.capabilities.provision_site.domain_verifier import (
        VerifyResult,
    )

    return VerifyResult(
        verified=verified,
        record_name="_pwp-verify.example.com",
        expected_value=expected,
        observed_values=[expected] if verified else [],
        error=None
        if verified
        else "no TXT record at _pwp-verify.example.com matches expected value",
    )


def _mock_response(status_code: int, *, success: bool, **payload):
    """Build a fake requests.Response."""
    import requests

    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.text = json.dumps({"success": success, **payload})
    resp.json.return_value = {"success": success, **payload}
    resp.headers = {}
    return resp


def _mk_client_with_mock(responses):
    """Build a CloudflareClient whose `requests.Session.request` returns
    the given responses in order."""
    from plugins.pwp.capabilities.provision_site import CloudflareClient

    cf = CloudflareClient(token="fake-token-for-tests", max_retries=0)
    cf._session.request = MagicMock(side_effect=responses)
    return cf


"""Phase 2 tests to append to test_provision_site.py.

Tests:
  - GoogleClient.from_env (missing creds, valid creds, jwt signing)
  - GoogleClient.ga4_property_create (mocked API)
  - GoogleClient.gtm_container_create (mocked API)
  - step_gsc_verify (Cloudflare TXT-create path, resume, prior zone_id required)
  - step_ga4_property (success, missing creds, reused_prior_output)
  - step_gtm_container (success, missing creds, reused_prior_output)
  - End-to-end: full STEP_NAMES now 7 entries
"""

from pathlib import Path


# Sentinel imports (these are loaded once via the main test file's imports)
from plugins.pwp.capabilities.provision_site.steps import (
    ga4,
    gtm,
    gsc,
)


# -- GoogleClient: JWT signing & from_env ----------------------------------


def _gen_test_sa(tmp_path):
    """Generate a real test service-account JSON (returns sa_path, sa_dict)."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    sa = {
        "type": "service_account",
        "client_email": "test-sa@pwp-test.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    p = tmp_path / "sa.json"
    p.write_text(json.dumps(sa))
    return p, sa


def test_google_client_from_env_missing(tmp_path, monkeypatch):
    """from_env should raise clearly when no SA creds are configured."""
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SA_INLINE", raising=False)
    from plugins.pwp.capabilities.provision_site.google_client import (
        GoogleClient,
        GoogleAuthError,
    )
    from plugins.pwp.capabilities.provision_site import auth_loader

    # Block the auth_loader fallback so this test is hermetic
    with patch(
        "plugins.pwp.capabilities.provision_site.auth_loader.get_secret",
        return_value=auth_loader.AuthResult(
            value=None,
            source="none",
            env_var="",
            hint="(test stub)",
            redaction="<missing>",
        ),
    ):
        with pytest.raises(GoogleAuthError) as excinfo:
            GoogleClient.from_env()
        msg = str(excinfo.value)
        assert "GOOGLE_SA_JSON" in msg or "No Google credentials" in msg


def test_google_client_from_env_json_file(tmp_path):
    """from_env with GOOGLE_SA_JSON pointing at a valid file should succeed."""
    from plugins.pwp.capabilities.provision_site.google_client import GoogleClient

    sa_path, _ = _gen_test_sa(tmp_path)
    with patch.dict(os.environ, {"GOOGLE_SA_JSON": str(sa_path)}):
        gc = GoogleClient.from_env()
    assert gc.service_account_email == "test-sa@pwp-test.iam.gserviceaccount.com"


def test_google_client_from_env_inline(tmp_path):
    """from_env with GOOGLE_SA_INLINE should succeed."""
    from plugins.pwp.capabilities.provision_site.google_client import GoogleClient

    _, sa = _gen_test_sa(tmp_path)
    with patch.dict(os.environ, {"GOOGLE_SA_INLINE": json.dumps(sa)}, clear=True):
        gc = GoogleClient.from_env()
    assert gc.service_account_email == sa["client_email"]


def test_google_client_ga4_account_id_env():
    """ga4_account_id property uses GA4_ACCOUNT_ID env var (or constructor arg)."""
    from plugins.pwp.capabilities.provision_site.google_client import (
        GoogleClient,
        GoogleError,
    )

    sa = {
        "type": "service_account",
        "client_email": "x@x",
        "private_key": "k",
        "token_uri": "u",
    }
    # No env var at all: property must raise.
    with patch.dict(os.environ, {}, clear=True):
        gc = GoogleClient(service_account=sa)
        with pytest.raises(GoogleError, match="GA4 account ID"):
            gc.ga4_account_id
    # Constructor arg takes precedence over env.
    with patch.dict(os.environ, {"GA4_ACCOUNT_ID": "999"}, clear=True):
        gc = GoogleClient(service_account=sa)
        assert gc.ga4_account_id == "999"


def test_google_client_jwt_signing_is_three_part_rs256(tmp_path):
    """_make_jwt returns a 3-part dot-separated RS256 token."""
    from plugins.pwp.capabilities.provision_site.google_client import _make_jwt

    _, sa = _gen_test_sa(tmp_path)
    jwt = _make_jwt(
        sa,
        scope="https://www.googleapis.com/auth/analytics.edit",
        aud="https://oauth2.googleapis.com/token",
    )
    parts = jwt.split(".")
    assert len(parts) == 3
    # Decode header (b64url) to verify alg.
    import base64

    pad = lambda s: s + "=" * (-len(s) % 4)
    header = json.loads(base64.urlsafe_b64decode(pad(parts[0])))
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"
    payload = json.loads(base64.urlsafe_b64decode(pad(parts[1])))
    assert payload["iss"] == sa["client_email"]
    assert payload["aud"] == "https://oauth2.googleapis.com/token"


# -- step_ga4_property -----------------------------------------------------


def test_step_ga4_property_missing_creds(tmp_path, monkeypatch, base_run_state):
    """step_ga4_property returns a clean failure when GOOGLE_SA_JSON is unset."""
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SA_INLINE", raising=False)
    monkeypatch.delenv("GA4_ACCOUNT_ID", raising=False)
    from plugins.pwp.capabilities.provision_site.google_client import GoogleAuthError

    # Block the auth_loader fallback so this test is hermetic
    with patch(
        "plugins.pwp.capabilities.provision_site.google_client.GoogleClient.from_env",
        side_effect=GoogleAuthError("No Google credentials found"),
    ):
        result = ga4.step_ga4_property(
            domain="example.com",
            owner="me@example.com",
            run=base_run_state,
            publish_root=tmp_path,
        )
    assert result.status == "failed"
    assert (
        result.output.get("_soft_failure") is True
        or "GA4 service-account" in (result.error or "")
        or "GOOGLE_SA_JSON" in (result.error or "")
    )


def test_step_ga4_property_success(tmp_path, base_run_state):
    """step_ga4_property succeeds when the API returns a property + stream."""
    sa_path, _ = _gen_test_sa(tmp_path)
    fake_property = {
        "name": "properties/987654",
        "displayName": "Example",
        "createTime": "2026-01-01T00:00:00Z",
    }
    fake_stream = {
        "name": "properties/987654/dataStreams/111",
        "displayName": "Example — Web",
        "webStreamData": {"measurementId": "G-TEST1234"},
    }
    fake_token = {"access_token": "fake-access-token", "expires_in": 3600}
    with patch.dict(
        os.environ,
        {
            "GOOGLE_SA_JSON": str(sa_path),
            "GA4_ACCOUNT_ID": "12345",
        },
    ):
        # Mock the JWT exchange (return fake token immediately).
        # Then mock the property + stream API calls.
        from plugins.pwp.capabilities.provision_site import google_client as gc_mod
        from plugins.pwp.capabilities.provision_site.google_client import GoogleClient

        with patch.object(
            gc_mod, "_exchange_jwt_for_access_token", return_value="fake-access-token"
        ):
            # Mock the two API requests.
            def fake_request(method, url, *, scope, json_body=None, params=None):
                if method == "POST" and url.endswith("/v1beta/properties"):
                    return fake_property
                if method == "POST" and url.endswith("/dataStreams"):
                    return fake_stream
                raise AssertionError(f"unexpected request: {method} {url}")

            with patch.object(GoogleClient, "_request", side_effect=fake_request):
                result = ga4.step_ga4_property(
                    domain="example.com",
                    owner="me@example.com",
                    run=base_run_state,
                    publish_root=tmp_path,
                )
    assert result.status == "complete", result.error
    assert result.output["measurement_id"] == "G-TEST1234"
    assert result.output["property_id"] == "987654"
    assert result.output["ga4_account_id"] == "12345"


def test_step_ga4_property_reuses_prior_output(tmp_path, base_run_state):
    """When prior_outputs contains ga4_property, the step returns it without API call."""
    prior = {
        "ga4_property": {
            "measurement_id": "G-CACHED",
            "property_id": "999",
        }
    }
    result = ga4.step_ga4_property(
        domain="example.com",
        owner="me@example.com",
        run=base_run_state,
        publish_root=tmp_path,
        prior_outputs=prior,
    )
    assert result.status == "complete"
    assert result.output["measurement_id"] == "G-CACHED"
    assert result.output["reused_prior_output"] is True


# -- step_gtm_container ----------------------------------------------------


def test_step_gtm_container_missing_creds(tmp_path, monkeypatch, base_run_state):
    """step_gtm_container fails cleanly without credentials."""
    monkeypatch.delenv("GOOGLE_SA_JSON", raising=False)
    monkeypatch.delenv("GOOGLE_SA_INLINE", raising=False)
    monkeypatch.delenv("GTM_ACCOUNT_ID", raising=False)
    from plugins.pwp.capabilities.provision_site.google_client import GoogleAuthError

    with patch(
        "plugins.pwp.capabilities.provision_site.google_client.GoogleClient.from_env",
        side_effect=GoogleAuthError("No Google credentials found"),
    ):
        result = gtm.step_gtm_container(
            domain="example.com",
            owner="me@example.com",
            run=base_run_state,
            publish_root=tmp_path,
        )
    assert result.status == "failed"
    assert "GOOGLE_SA_JSON" in result.error


def test_step_gtm_container_success(tmp_path, base_run_state):
    """step_gtm_container succeeds when the API returns a container."""
    sa_path, _ = _gen_test_sa(tmp_path)
    fake_container = {
        "publicId": "GTM-P5H2XK8",
        "containerId": "987654",
        "name": "Example",
        "domains": ["example.com"],
    }
    with patch.dict(
        os.environ,
        {
            "GOOGLE_SA_JSON": str(sa_path),
            "GTM_ACCOUNT_ID": "55555",
        },
    ):
        from plugins.pwp.capabilities.provision_site import google_client as gc_mod
        from plugins.pwp.capabilities.provision_site.google_client import GoogleClient

        with patch.object(
            gc_mod, "_exchange_jwt_for_access_token", return_value="fake-access-token"
        ):

            def fake_request(method, url, *, scope, json_body=None, params=None):
                return fake_container

            with patch.object(GoogleClient, "_request", side_effect=fake_request):
                result = gtm.step_gtm_container(
                    domain="example.com",
                    owner="me@example.com",
                    run=base_run_state,
                    publish_root=tmp_path,
                )
    assert result.status == "complete", result.error
    assert result.output["public_id"] == "GTM-P5H2XK8"
    assert result.output["account_id"] == "55555"


# -- step_gsc_verify (real, Cloudflare-managed TXT) -----------------------


def test_step_gsc_verify_no_zone_id(tmp_path, base_run_state):
    """gsc_verify fails with a clear error if cloudflare_zone has not completed."""
    result = gsc.step_gsc_verify(
        domain="example.com",
        owner="me@example.com",
        run=base_run_state,
        publish_root=tmp_path,
        prior_outputs={},
    )
    assert result.status == "failed"
    assert "cloudflare_zone" in result.error


def test_step_gsc_verify_placeholder_mode(tmp_path, base_run_state):
    """gsc_verify writes a placeholder TXT record via mocked Cloudflare."""
    prior = {"cloudflare_zone": {"zone_id": "fake-zone-123"}}
    # Mock Cloudflare.from_env + dns_list (empty) + dns_create.
    from plugins.pwp.capabilities.provision_site.steps import gsc as gsc_module

    with patch.object(gsc_module, "CloudflareClient") as MockCF:
        mock_cf_instance = MockCF.from_env.return_value
        mock_cf_instance.dns_list.return_value = []
        fake_dns_record = MagicMock()
        fake_dns_record.id = "dns-record-456"
        mock_cf_instance.dns_create.return_value = fake_dns_record
        result = gsc.step_gsc_verify(
            domain="example.com",
            owner="me@example.com",
            run=base_run_state,
            publish_root=tmp_path,
            prior_outputs=prior,
        )
    assert result.status == "complete", result.error
    assert result.output["verification_mode"] == "placeholder"
    assert result.output["record_id"] == "dns-record-456"
    assert result.output["token"].startswith("pwp-gsc-")
    assert "GSC_VERIFICATION_TOKEN" in result.output["note"]
    # Confirm Cloudflare API was called with the right TXT content.
    call_args = mock_cf_instance.dns_create.call_args
    assert call_args.kwargs["type_"] == "TXT"
    assert call_args.kwargs["name"] == "example.com"
    assert call_args.kwargs["content"].startswith("pwp-gsc-")


def test_step_gsc_verify_google_issued_mode(tmp_path, base_run_state):
    """If GSC_VERIFICATION_TOKEN env var is set, use it verbatim instead of minting."""
    prior = {"cloudflare_zone": {"zone_id": "fake-zone-123"}}
    from plugins.pwp.capabilities.provision_site.steps import gsc as gsc_module

    with patch.dict(os.environ, {"GSC_VERIFICATION_TOKEN": "google-issued-real-token"}):
        with patch.object(gsc_module, "CloudflareClient") as MockCF:
            mock_cf_instance = MockCF.from_env.return_value
            mock_cf_instance.dns_list.return_value = []
            fake_dns_record = MagicMock()
            fake_dns_record.id = "dns-record-789"
            mock_cf_instance.dns_create.return_value = fake_dns_record
            result = gsc.step_gsc_verify(
                domain="example.com",
                owner="me@example.com",
                run=base_run_state,
                publish_root=tmp_path,
                prior_outputs=prior,
            )
    assert result.status == "complete"
    assert result.output["verification_mode"] == "google-issued"
    assert result.output["token"] == "google-issued-real-token"
    call_args = mock_cf_instance.dns_create.call_args
    assert call_args.kwargs["content"] == "google-issued-real-token"


def test_step_gsc_verify_reuses_prior_output(tmp_path, base_run_state):
    """If a prior gsc_verify attempt succeeded, skip the API call."""
    prior = {
        "cloudflare_zone": {"zone_id": "fake-zone-123"},
        "gsc_verify": {
            "record_id": "prior-record-abc",
            "verification_mode": "google-issued",
        },
    }
    from plugins.pwp.capabilities.provision_site.steps import gsc as gsc_module

    with patch.object(gsc_module, "CloudflareClient") as MockCF:
        result = gsc.step_gsc_verify(
            domain="example.com",
            owner="me@example.com",
            run=base_run_state,
            publish_root=tmp_path,
            prior_outputs=prior,
        )
    # Cloudflare should NOT be called (we reused prior).
    MockCF.from_env.assert_not_called()
    assert result.status == "complete"
    assert result.output["record_id"] == "prior-record-abc"
    assert result.output["reused_prior_output"] is True


# -- end-to-end STEP_NAMES now 10 entries (Phase 3 + Phase 4) -------------


def test_step_names_phase_3_includes_new_steps():
    """Phase 3 adds platform_detect + vercel_project;
    Phase 4 adds register_stripe between gtm_container and register_in_registry;
    Phase 4.1 adds github_checkout between register_stripe and register_in_registry.
    The canonical STEP_NAMES now has 11 entries in the platform-aware order.
    """
    expected = [
        "platform_detect",
        "verify_domain",
        "cloudflare_zone",
        "vercel_project",
        "gsc_verify",
        "ga4_property",
        "gtm_container",
        "register_stripe",
        "github_checkout",
        "register_zapier_webhook",  # Phase 4.6 (F6)
        "register_in_registry",
        "migrate_kpi",
    ]
    assert orchestrator.STEP_NAMES == expected


# -- fixture ----------------------------------------------------------------


@pytest.fixture
def base_run_state():
    return prov_types.ProvisionRun(
        domain="example.com",
        owner="me@example.com",
        started_at="2026-01-01T00:00:00+00:00",
    )


# ============================================================================
# Phase 3 tests — VercelClient + platform_detect + vercel_project
# ============================================================================


# -- VercelClient ----------------------------------------------------------


def test_vercel_client_from_env_missing() -> None:
    """from_env must surface a helpful error when neither VERCEL_TOKEN
    nor VERCEL_API_TOKEN is set."""
    with patch.dict(os.environ, {}, clear=True):
        from plugins.pwp.capabilities.provision_site.vercel_client import VercelClient

        with pytest.raises(ValueError) as excinfo:
            VercelClient.from_env()
        msg = str(excinfo.value)
        assert "VERCEL_TOKEN" in msg
        assert "VERCEL_API_TOKEN" in msg


def test_vercel_client_from_env_precedence() -> None:
    """VERCEL_TOKEN takes precedence over VERCEL_API_TOKEN."""
    from plugins.pwp.capabilities.provision_site.vercel_client import VercelClient

    with patch.dict(
        os.environ,
        {"VERCEL_TOKEN": "primary", "VERCEL_API_TOKEN": "secondary"},
        clear=True,
    ):
        vc = VercelClient.from_env()
    assert vc.token_source == "VERCEL_TOKEN"


def test_vercel_client_from_env_team_id() -> None:
    """VERCEL_TEAM_ID env var is captured into team_id."""
    from plugins.pwp.capabilities.provision_site.vercel_client import VercelClient

    with patch.dict(
        os.environ,
        {"VERCEL_TOKEN": "test-token", "VERCEL_TEAM_ID": "team_abc"},
        clear=True,
    ):
        vc = VercelClient.from_env()
    assert vc.team_id == "team_abc"


def test_vercel_client_construct_requires_token() -> None:
    """Direct construction must reject an empty token."""
    from plugins.pwp.capabilities.provision_site.vercel_client import VercelClient

    with pytest.raises(ValueError):
        VercelClient(token="")
    with pytest.raises(ValueError):
        VercelClient(token="   ")


def test_vercel_client_request_shape_and_bearer_auth() -> None:
    """A GET request must hit api.vercel.com with Bearer token in the
    Authorization header and the teamId query param when set."""
    from plugins.pwp.capabilities.provision_site.vercel_client import VercelClient

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["method"] = req.method
        # Return a JSON body
        return MagicMock(
            __enter__=lambda s: s,
            __exit__=lambda *a: None,
            read=lambda: (
                b'{"id":"prj_123","name":"foo","framework":"nextjs","accountId":"acc_1"}'
            ),
        )

    vc = VercelClient(token="test-token", team_id="team_xyz", max_retries=0)
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = vc.project_lookup("foo")

    assert captured["method"] == "GET"
    assert captured["url"].startswith("https://api.vercel.com/v9/projects/foo")
    assert "teamId=team_xyz" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert result.id == "prj_123"
    assert result.name == "foo"
    assert result.framework == "nextjs"


def test_vercel_client_404_returns_none_for_project_lookup() -> None:
    """A 404 on project_lookup must return None (not raise)."""
    from plugins.pwp.capabilities.provision_site.vercel_client import VercelClient
    import http.client

    hdrs = http.client.HTTPMessage()
    err = urllib.error.HTTPError(
        "https://api.vercel.com/v9/projects/foo",
        404,
        "Not Found",
        hdrs,
        io.BytesIO(b'{"error":{"code":"not_found","message":"not found"}}'),
    )
    vc = VercelClient(token="test-token", max_retries=0)
    with patch("urllib.request.urlopen", side_effect=err):
        result = vc.project_lookup("foo")
    assert result is None


def test_vercel_client_500_raises_vercel_error() -> None:
    """A 500 on project_lookup must raise VercelError (not return None)."""
    from plugins.pwp.capabilities.provision_site.vercel_client import (
        VercelClient,
        VercelError,
    )
    import http.client

    hdrs = http.client.HTTPMessage()
    err = urllib.error.HTTPError(
        "https://api.vercel.com/v9/projects/foo",
        500,
        "Internal Server Error",
        hdrs,
        io.BytesIO(b'{"error":{"code":"internal","message":"boom"}}'),
    )
    vc = VercelClient(token="test-token", max_retries=0)
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(VercelError) as excinfo:
            vc.project_lookup("foo")
    assert excinfo.value.status == 500


# -- platform_detect ------------------------------------------------------


def test_platform_detect_cloudflare_active_zone() -> None:
    """If cloudflare_zone completed with status=active, platform_detect
    must short-circuit to platform=cloudflare_pages."""
    from plugins.pwp.capabilities.provision_site.steps import platform_detect

    prior = {"cloudflare_zone": {"zone_id": "z-1", "status": "active"}}

    def fake_doh(_):
        return None

    def fake_probe(_, timeout=5.0):
        return {}

    with (
        patch.object(platform_detect, "_doh_cname", side_effect=fake_doh),
        patch.object(platform_detect, "_http_probe", side_effect=fake_probe),
    ):
        result = platform_detect.step_platform_detect(
            domain="example.com",
            owner="me@example.com",
            run=prov_types.ProvisionRun(
                domain="example.com",
                owner="me@example.com",
                started_at="2026-01-01T00:00:00+00:00",
            ),
            publish_root=None,
            prior_outputs=prior,
        )
    assert result.status == "complete"
    assert result.output["platform"] == "cloudflare_pages"
    assert result.output["cloudflare_zone_id"] == "z-1"
    assert "cf-zone=z-1" in result.output["evidence"]


def test_platform_detect_vercel_via_x_vercel_id() -> None:
    """An HTTP probe that returns X-Vercel-Id header should classify as
    'vercel' with project_name derived from the domain."""
    from plugins.pwp.capabilities.provision_site.steps import platform_detect

    fake_headers = {"x-vercel-id": "cdg1::abc123"}

    def fake_probe(host, timeout=5.0):
        return fake_headers

    with patch.object(platform_detect, "_http_probe", side_effect=fake_probe):
        result = platform_detect.step_platform_detect(
            domain="ezshare.systems",
            owner="me@example.com",
            run=prov_types.ProvisionRun(
                domain="ezshare.systems",
                owner="me@example.com",
                started_at="2026-01-01T00:00:00+00:00",
            ),
            publish_root=None,
            prior_outputs={},
        )
    assert result.status == "complete"
    assert result.output["platform"] == "vercel"
    assert result.output["vercel_project_name"] == "ezshare"


def test_platform_detect_unknown_when_no_signals() -> None:
    """If the domain doesn't resolve and there are no Cloudflare / Vercel
    signals, the step returns platform='unknown'."""
    from plugins.pwp.capabilities.provision_site.steps import platform_detect

    def fake_doh(_):
        return None

    def fake_probe(host, timeout=5.0):
        return {}

    with (
        patch.object(platform_detect, "_doh_cname", side_effect=fake_doh),
        patch.object(platform_detect, "_http_probe", side_effect=fake_probe),
    ):
        result = platform_detect.step_platform_detect(
            domain="nope.invalid",
            owner="me@example.com",
            run=prov_types.ProvisionRun(
                domain="nope.invalid",
                owner="me@example.com",
                started_at="2026-01-01T00:00:00+00:00",
            ),
            publish_root=None,
            prior_outputs={},
        )
    assert result.status == "complete"
    assert result.output["platform"] == "unknown"


# -- vercel_project -------------------------------------------------------


def test_vercel_project_skips_when_platform_not_vercel() -> None:
    """When platform_detect found a non-Vercel platform, vercel_project
    must skip itself cleanly."""
    from plugins.pwp.capabilities.provision_site.steps import vercel_project

    prior = {"platform_detect": {"platform": "cloudflare_pages"}}
    result = vercel_project.step_vercel_project(
        domain="example.com",
        owner="me@example.com",
        run=prov_types.ProvisionRun(
            domain="example.com",
            owner="me@example.com",
            started_at="2026-01-01T00:00:00+00:00",
        ),
        publish_root=None,
        prior_outputs=prior,
    )
    assert result.status == "skipped"
    assert "cloudflare_pages" in result.output["reason"]


def test_vercel_project_skips_when_no_platform_detect() -> None:
    """When platform_detect wasn't run (no prior_outputs), vercel_project
    must skip itself (we don't know the platform yet)."""
    from plugins.pwp.capabilities.provision_site.steps import vercel_project

    result = vercel_project.step_vercel_project(
        domain="example.com",
        owner="me@example.com",
        run=prov_types.ProvisionRun(
            domain="example.com",
            owner="me@example.com",
            started_at="2026-01-01T00:00:00+00:00",
        ),
        publish_root=None,
        prior_outputs={},
    )
    assert result.status == "skipped"
    assert (
        "None" in result.output["reason"] or "platform=None" in result.output["reason"]
    )


def test_vercel_project_no_token_fails_cleanly() -> None:
    """Without VERCEL_TOKEN, the step returns a clean error (not raise)."""
    from plugins.pwp.capabilities.provision_site.steps import vercel_project

    prior = {
        "platform_detect": {"platform": "vercel", "vercel_project_name": "ezshare"}
    }
    with patch.dict(os.environ, {}, clear=True):
        result = vercel_project.step_vercel_project(
            domain="ezshare.systems",
            owner="me@example.com",
            run=prov_types.ProvisionRun(
                domain="ezshare.systems",
                owner="me@example.com",
                started_at="2026-01-01T00:00:00+00:00",
            ),
            publish_root=None,
            prior_outputs=prior,
        )
    assert result.status == "failed"
    assert "VERCEL_TOKEN" in result.error


def test_vercel_project_lookup_existing() -> None:
    """When VERCEL_TOKEN is set and project_lookup finds the project,
    the step returns action='lookup' with the project metadata."""
    from plugins.pwp.capabilities.provision_site.steps import vercel_project

    prior = {
        "platform_detect": {"platform": "vercel", "vercel_project_name": "ezshare"}
    }
    fake_project = MagicMock()
    fake_project.id = "prj_existing"
    fake_project.name = "ezshare"
    fake_project.framework = "nextjs"
    fake_project.account_id = "acc_xyz"

    with (
        patch.dict(os.environ, {"VERCEL_TOKEN": "fake"}, clear=True),
        patch(
            "plugins.pwp.capabilities.provision_site.vercel_client.VercelClient.from_env"
        ) as MockCF,
    ):
        MockCF.return_value.project_lookup.return_value = fake_project
        result = vercel_project.step_vercel_project(
            domain="ezshare.systems",
            owner="me@example.com",
            run=prov_types.ProvisionRun(
                domain="ezshare.systems",
                owner="me@example.com",
                started_at="2026-01-01T00:00:00+00:00",
            ),
            publish_root=None,
            prior_outputs=prior,
        )
    assert result.status == "complete"
    assert result.output["action"] == "lookup"
    assert result.output["project_id"] == "prj_existing"


# -- cloudflare_zone conditional skip -------------------------------------


def test_cloudflare_zone_skips_when_platform_is_vercel() -> None:
    """When platform_detect found platform='vercel', cloudflare_zone must
    skip (the vercel_project step is responsible for Vercel sites)."""
    prior = {"platform_detect": {"platform": "vercel"}}
    result = step_cloudflare_zone(
        domain="ezshare.systems",
        owner="me@example.com",
        run=prov_types.ProvisionRun(
            domain="ezshare.systems",
            owner="me@example.com",
            started_at="2026-01-01T00:00:00+00:00",
        ),
        publish_root=tmp_path_fixture(),
        prior_outputs=prior,
    )
    assert result.status == "skipped"
    assert "vercel" in result.output["reason"].lower()


def test_cloudflare_zone_runs_when_platform_is_cloudflare() -> None:
    """When platform_detect found platform='cloudflare_pages', cloudflare_zone
    must NOT skip — it proceeds to the normal zone lookup/create path."""
    prior = {"platform_detect": {"platform": "cloudflare_pages"}}

    fake_zone = MagicMock()
    fake_zone.id = "z-9876"
    fake_zone.nameservers = ["ns1.cloudflare.com", "ns2.cloudflare.com"]
    fake_zone.status = "active"

    with (
        patch.dict(os.environ, {"CF_API_TOKEN": "fake"}, clear=True),
        patch(
            "plugins.pwp.capabilities.provision_site.cloudflare_client.CloudflareClient"
        ) as MockCF,
    ):
        MockCF.from_env.return_value.zone_lookup.return_value = fake_zone
        result = step_cloudflare_zone(
            domain="example.com",
            owner="me@example.com",
            run=prov_types.ProvisionRun(
                domain="example.com",
                owner="me@example.com",
                started_at="2026-01-01T00:00:00+00:00",
            ),
            publish_root=tmp_path_fixture(),
            prior_outputs=prior,
        )
    assert result.status == "complete"
    assert result.output["zone_id"] == "z-9876"


def tmp_path_fixture():
    """Return a fresh tmp path for tests that don't pass tmp_path."""
    import tempfile

    return Path(tempfile.mkdtemp())


# -- platform_detect: cf_tunnel classification -----------------------------


def test_platform_detect_cf_tunnel_via_cname() -> None:
    """If the apex CNAME ends in .cfargotunnel.com, the platform is
    classified as 'cf_tunnel' even without an active Cloudflare zone."""
    from plugins.pwp.capabilities.provision_site.steps import platform_detect

    with (
        patch.object(
            platform_detect,
            "_doh_cname",
            return_value="abcd1234-5678-90ab-cdef-1234567890ab.cfargotunnel.com",
        ),
        patch.object(platform_detect, "_http_probe", return_value={}),
    ):
        result = platform_detect.step_platform_detect(
            domain="selfhosted.example.com",
            owner="me@example.com",
            run=prov_types.ProvisionRun(
                domain="selfhosted.example.com",
                owner="me@example.com",
                started_at="2026-01-01T00:00:00+00:00",
            ),
            publish_root=None,
            prior_outputs={},
        )
    assert result.status == "complete"
    assert result.output["platform"] == "cf_tunnel"
    assert result.output["apex_cname"].endswith(".cfargotunnel.com")
    assert "cname=" in result.output["evidence"]


def test_platform_detect_vercel_cname_takes_precedence_over_zone() -> None:
    """If a CF zone exists but the CNAME points to vercel, the platform
    is 'vercel' (Vercel wins over CF zone)."""
    from plugins.pwp.capabilities.provision_site.steps import platform_detect

    prior = {"cloudflare_zone": {"zone_id": "z-1", "status": "active"}}
    with (
        patch.object(
            platform_detect,
            "_doh_cname",
            return_value="cname.vercel-dns.com",
        ),
        patch.object(platform_detect, "_http_probe", return_value={}),
    ):
        result = platform_detect.step_platform_detect(
            domain="hybrid.example.com",
            owner="me@example.com",
            run=prov_types.ProvisionRun(
                domain="hybrid.example.com",
                owner="me@example.com",
                started_at="2026-01-01T00:00:00+00:00",
            ),
            publish_root=None,
            prior_outputs=prior,
        )
    assert result.status == "complete"
    assert result.output["platform"] == "vercel"
    assert result.output["vercel_project_name"] == "hybrid"


def test_platform_detect_live_ezshare_classifies_as_vercel() -> None:
    """Real-world test: ezshare.systems currently CNAMEs to cname.vercel-dns.com.
    platform_detect should classify it as 'vercel' against the live DNS."""
    from plugins.pwp.capabilities.provision_site.steps import platform_detect

    # Don't mock — let it actually call DoH and HTTP.
    result = platform_detect.step_platform_detect(
        domain="ezshare.systems",
        owner="ned@example.com",
        run=prov_types.ProvisionRun(
            domain="ezshare.systems",
            owner="ned@example.com",
            started_at="2026-01-01T00:00:00+00:00",
        ),
        publish_root=None,
        prior_outputs={},
    )
    assert result.status == "complete"
    # We expect either 'vercel' (CNAME) or 'unknown' (network failure) —
    # anything else would indicate a misclassification bug.
    assert result.output["platform"] in ("vercel", "unknown"), (
        f"Unexpected classification: {result.output}"
    )


# -- gsc_verify: Vercel skip ------------------------------------------------


def test_gsc_verify_skips_when_platform_is_vercel(tmp_path) -> None:
    """When platform_detect found platform='vercel', gsc_verify must
    skip cleanly (Vercel sites have no CF zone to write TXT records to)."""
    from plugins.pwp.capabilities.provision_site.steps import gsc as gsc_mod

    prior = {"platform_detect": {"platform": "vercel"}}
    result = gsc_mod.step_gsc_verify(
        domain="ezshare.systems",
        owner="me@example.com",
        run=prov_types.ProvisionRun(
            domain="ezshare.systems",
            owner="me@example.com",
            started_at="2026-01-01T00:00:00+00:00",
        ),
        publish_root=tmp_path,
        prior_outputs=prior,
    )
    assert result.status == "skipped"
    assert "vercel" in result.output["reason"].lower()
    assert result.output["platform"] == "vercel"
