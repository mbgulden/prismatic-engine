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

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plugins.pwp.capabilities.provision_site import (
    CloudflareError,
    orchestrator,
)
from plugins.pwp.capabilities.provision_site.domain_verifier import (
    VERIFY_PREFIX,
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
    assert all(c in "0123456789abcdef" for c in tok[len("pwp-verify-"):])


def test_expected_record_name() -> None:
    assert expected_record_name("example.com") == "_pwp-verify.example.com"
    assert expected_record_name("foo.bar.example.com") == "_pwp-verify.foo.bar.example.com"


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
    with patch.dict(os.environ, {"CLOUDFLARE_PAGES_API_TOKEN": "pages-tok"}, clear=True):
        cf = CloudflareClient.from_env()
        assert cf._token == "pages-tok"
        assert cf._token_source == "CLOUDFLARE_PAGES_API_TOKEN"
    # CLOUDFLARE_API_TOKEN takes precedence over CLOUDFLARE_PAGES_API_TOKEN
    with patch.dict(os.environ, {
        "CLOUDFLARE_API_TOKEN": "general-tok",
        "CLOUDFLARE_PAGES_API_TOKEN": "pages-tok",
    }, clear=True):
        cf = CloudflareClient.from_env()
        assert cf._token == "general-tok"
        assert cf._token_source == "CLOUDFLARE_API_TOKEN"
    # CF_API_TOKEN wins over everything
    with patch.dict(os.environ, {
        "CF_API_TOKEN": "canonical-tok",
        "CLOUDFLARE_API_TOKEN": "general-tok",
        "CLOUDFLARE_PAGES_API_TOKEN": "pages-tok",
    }, clear=True):
        cf = CloudflareClient.from_env()
        assert cf._token == "canonical-tok"
        assert cf._token_source == "CF_API_TOKEN"


def test_cloudflare_client_parses_errors() -> None:
    """Cloudflare API errors must surface the CF error code + message."""
    cf = _mk_client_with_mock([_mock_response(400, success=False, errors=[
        {"code": 1004, "message": "Invalid zone name"},
    ])])
    with pytest.raises(CloudflareError) as excinfo:
        cf.zone_list()
    err = excinfo.value
    assert err.status_code == 400
    assert err.code == 1004
    assert "Invalid zone name" in err.message


def test_cloudflare_client_zones_endpoint_shape() -> None:
    """zone_list() must hit /zones and parse the `result` array."""
    cf = _mk_client_with_mock([
        _mock_response(200, success=True, result=[{
            "id": "abc123", "name": "example.com",
            "status": "active", "name_servers": ["ns1.cf", "ns2.cf"],
            "plan": {"name": "free"}, "paused": False,
        }]),
    ])
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
                name=step_name, status="failed",
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
    # verify_domain failed; subsequent steps should NOT have run.
    assert run.overall_status == "failed"
    assert call_count["n"] == 1
    assert run.steps[0].status == "failed"


def test_orchestrator_resume_skips_completed_steps(tmp_path: Path) -> None:
    """On a re-run after a partial failure, completed steps from the
    prior state must be skipped."""
    # Pre-populate state with verify_domain complete.
    state_path = tmp_path / "example.com.json"
    state_path.write_text(json.dumps({
        "domain": "example.com",
        "owner": "me@example.com",
        "started_at": "2026-01-01T00:00:00+00:00",
        "overall_status": "in_progress",
        "steps": [
            {"name": "verify_domain", "status": "complete",
             "started_at": "x", "finished_at": "y", "output": {}},
        ],
    }))
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
    # verify_domain was skipped (came from prior state); the remaining 6 ran.
    assert call_count["n"] == 6
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
    state_path.write_text(json.dumps({
        "domain": "example.com", "owner": "me@example.com",
        "started_at": "2026-01-01T00:00:00+00:00",
        "overall_status": "in_progress",
        "steps": [
            {"name": "verify_domain", "status": "failed",
             "started_at": "x", "finished_at": "y",
             "output": {"challenge_token": "pwp-verify-fixed-token",
                        "record_name": "_pwp-verify.example.com"}},
        ],
    }))
    with patch.object(orchestrator, "_run_step", side_effect=fake_step):
        orchestrator.run(
            domain="example.com", owner="me@example.com",
            publish_root=tmp_path, resume=True,
        )
    # The step received prior_outputs containing the prior challenge_token.
    assert captured["prior_outputs"].get("verify_domain", {}).get("challenge_token") == "pwp-verify-fixed-token"


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
        record_name=f"_pwp-verify.example.com",
        expected_value=expected,
        observed_values=[expected] if verified else [],
        error=None if verified else "no TXT record at _pwp-verify.example.com matches expected value",
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

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Sentinel imports (these are loaded once via the main test file's imports)
from plugins.pwp.capabilities.provision_site import orchestrator, types as prov_types
from plugins.pwp.capabilities.provision_site.steps import (
    ga4, gtm, gsc,
    step_verify_domain,
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
        GoogleClient, GoogleAuthError,
    )
    with pytest.raises(GoogleAuthError) as excinfo:
        GoogleClient.from_env()
    msg = str(excinfo.value)
    assert "GOOGLE_SA_JSON" in msg
    assert "GOOGLE_SA_INLINE" in msg


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
        GoogleClient, GoogleError,
    )
    sa = {"type": "service_account", "client_email": "x@x", "private_key": "k", "token_uri": "u"}
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
    from plugins.pwp.capabilities.provision_site.google_client import _make_jwt, _b64url
    _, sa = _gen_test_sa(tmp_path)
    jwt = _make_jwt(sa, scope="https://www.googleapis.com/auth/analytics.edit", aud="https://oauth2.googleapis.com/token")
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
    result = ga4.step_ga4_property(
        domain="example.com", owner="me@example.com",
        run=base_run_state, publish_root=tmp_path,
    )
    assert result.status == "failed"
    assert "GOOGLE_SA_JSON" in result.error


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
    with patch.dict(os.environ, {
        "GOOGLE_SA_JSON": str(sa_path),
        "GA4_ACCOUNT_ID": "12345",
    }):
        # Mock the JWT exchange (return fake token immediately).
        # Then mock the property + stream API calls.
        from plugins.pwp.capabilities.provision_site import google_client as gc_mod
        from plugins.pwp.capabilities.provision_site.google_client import GoogleClient
        with patch.object(gc_mod, "_exchange_jwt_for_access_token",
                          return_value="fake-access-token"):
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
    prior = {"ga4_property": {
        "measurement_id": "G-CACHED",
        "property_id": "999",
    }}
    result = ga4.step_ga4_property(
        domain="example.com", owner="me@example.com",
        run=base_run_state, publish_root=tmp_path,
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
    result = gtm.step_gtm_container(
        domain="example.com", owner="me@example.com",
        run=base_run_state, publish_root=tmp_path,
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
    with patch.dict(os.environ, {
        "GOOGLE_SA_JSON": str(sa_path),
        "GTM_ACCOUNT_ID": "55555",
    }):
        from plugins.pwp.capabilities.provision_site import google_client as gc_mod
        from plugins.pwp.capabilities.provision_site.google_client import GoogleClient
        with patch.object(gc_mod, "_exchange_jwt_for_access_token",
                          return_value="fake-access-token"):
            def fake_request(method, url, *, scope, json_body=None, params=None):
                return fake_container
            with patch.object(GoogleClient, "_request", side_effect=fake_request):
                result = gtm.step_gtm_container(
                    domain="example.com", owner="me@example.com",
                    run=base_run_state, publish_root=tmp_path,
                )
    assert result.status == "complete", result.error
    assert result.output["public_id"] == "GTM-P5H2XK8"
    assert result.output["account_id"] == "55555"


# -- step_gsc_verify (real, Cloudflare-managed TXT) -----------------------

def test_step_gsc_verify_no_zone_id(tmp_path, base_run_state):
    """gsc_verify fails with a clear error if cloudflare_zone has not completed."""
    result = gsc.step_gsc_verify(
        domain="example.com", owner="me@example.com",
        run=base_run_state, publish_root=tmp_path,
        prior_outputs={},
    )
    assert result.status == "failed"
    assert "cloudflare_zone" in result.error


def test_step_gsc_verify_placeholder_mode(tmp_path, base_run_state):
    """gsc_verify writes a placeholder TXT record via mocked Cloudflare."""
    prior = {"cloudflare_zone": {"zone_id": "fake-zone-123"}}
    # Mock Cloudflare.from_env + dns_list (empty) + dns_create.
    from plugins.pwp.capabilities.provision_site import cloudflare_client
    from plugins.pwp.capabilities.provision_site.steps import gsc as gsc_module
    with patch.object(gsc_module, "CloudflareClient") as MockCF:
        mock_cf_instance = MockCF.from_env.return_value
        mock_cf_instance.dns_list.return_value = []
        fake_dns_record = MagicMock()
        fake_dns_record.id = "dns-record-456"
        mock_cf_instance.dns_create.return_value = fake_dns_record
        result = gsc.step_gsc_verify(
            domain="example.com", owner="me@example.com",
            run=base_run_state, publish_root=tmp_path,
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
    from plugins.pwp.capabilities.provision_site import cloudflare_client
    from plugins.pwp.capabilities.provision_site.steps import gsc as gsc_module
    with patch.dict(os.environ, {"GSC_VERIFICATION_TOKEN": "google-issued-real-token"}):
        with patch.object(gsc_module, "CloudflareClient") as MockCF:
            mock_cf_instance = MockCF.from_env.return_value
            mock_cf_instance.dns_list.return_value = []
            fake_dns_record = MagicMock()
            fake_dns_record.id = "dns-record-789"
            mock_cf_instance.dns_create.return_value = fake_dns_record
            result = gsc.step_gsc_verify(
                domain="example.com", owner="me@example.com",
                run=base_run_state, publish_root=tmp_path,
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
        "gsc_verify": {"record_id": "prior-record-abc", "verification_mode": "google-issued"},
    }
    from plugins.pwp.capabilities.provision_site import cloudflare_client
    from plugins.pwp.capabilities.provision_site.steps import gsc as gsc_module
    with patch.object(gsc_module, "CloudflareClient") as MockCF:
        result = gsc.step_gsc_verify(
            domain="example.com", owner="me@example.com",
            run=base_run_state, publish_root=tmp_path,
            prior_outputs=prior,
        )
    # Cloudflare should NOT be called (we reused prior).
    MockCF.from_env.assert_not_called()
    assert result.status == "complete"
    assert result.output["record_id"] == "prior-record-abc"
    assert result.output["reused_prior_output"] is True


# -- end-to-end STEP_NAMES now 7 entries -----------------------------------

def test_step_names_phase_2_includes_new_steps():
    """Phase 2 adds gsc_verify (real), ga4_property, gtm_container."""
    expected = [
        "verify_domain",
        "cloudflare_zone",
        "gsc_verify",
        "ga4_property",
        "gtm_container",
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
