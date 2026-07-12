# Linear Webhook HMAC Verification Fix (GRO-3170)

## Problem
In `prismatic/gateway/server.py`, the Linear webhook endpoints (`/api/gateway/linear` and `/webhooks/linear` alias) only verified the `linear-signature` header if it was provided by the client. If the signature header was omitted or if no secrets were configured, verification was bypassed, allowing anyone to post unauthenticated/fake events to the event bus.

## Fix
1. **Always Verify Signature when Secrets exist**: Enforced signature checks whenever `secrets` are configured via environment variables (such as `LINEAR_WEBHOOK_SIGNING_SECRET`, `PRISMATIC_LINEAR_WEBHOOK_SECRET`, etc.).
2. **Reject Invalid/Missing Signatures**: If the signature header is missing or does not match any of the configured secrets, the server now immediately rejects the request with `401 Unauthorized` and returns `{"status": "auth-failed"}`.
3. **Graceful Dev Mode**: If no secrets are configured in the environment, the server logs a warning and proceeds without signature verification to support local development.

### Code Changes
Modified the validation block in `prismatic/gateway/server.py` to always extract secrets first:
```python
    secrets = get_linear_secrets()
    if secrets:
        expected = None
        for secret in secrets:
            candidate = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if _hmac.compare_digest(candidate, signature):
                expected = candidate
                break
        if expected is None:
            _webhook_counters["linear_auth_failed"] += 1
            await _publish_webhook_auth_failed("linear")
            from fastapi.responses import JSONResponse

            return JSONResponse({"status": "auth-failed"}, status_code=401)
    else:
        logger.warning("Linear webhook skipped signature check: no secrets configured")
```

---

## Testing & Verification
A new test suite was created in `tests/test_linear_webhook.py` to cover all scenarios:
1. **Missing signature** (`test_linear_webhook_no_signature`) -> returns `401`
2. **Invalid signature** (`test_linear_webhook_invalid_signature`) -> returns `401`
3. **Valid signature** (`test_linear_webhook_valid_signature`) -> returns `200`
4. **Valid signature on alias path** (`test_linear_webhook_alias_valid_signature`) -> returns `200`

### Test Output
All tests in `tests/test_linear_webhook.py` ran and passed:
```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.0, pluggy-1.6.0
rootdir: /home/ubuntu/work/prismatic-engine
configfile: pyproject.toml
plugins: anyio-4.13.0
collecting ... collecting 0 items                                                             collected 4 items                                                              

tests/test_linear_webhook.py ....                                        [100%]
======================== 4 passed, 5 warnings in 0.82s =========================
```
Also verified that other existing gateway tests (`prismatic/gateway/test_merge_status.py` and `prismatic/tests/test_gateway_recovery_controls.py`) passed successfully.
