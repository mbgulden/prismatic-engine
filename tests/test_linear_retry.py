import io
import urllib.error
import urllib.request
import time
from unittest import mock
import pytest
from datetime import datetime, timezone

from prismatic.linear.retry import execute_linear_request


def make_http_error(code, reason="Error", headers=None):
    # Construct a Message headers object if dict is passed
    import http.client
    msg = http.client.HTTPMessage()
    if headers:
        for k, v in headers.items():
            msg.add_header(k, v)
    fp = io.BytesIO(b"error body")
    return urllib.error.HTTPError(
        url="https://api.linear.app/graphql",
        code=code,
        msg=reason,
        hdrs=msg,
        fp=fp
    )


def test_execute_linear_request_success():
    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = b'{"data": {"ok": true}}'

    with mock.patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        resp = execute_linear_request("https://api.linear.app/graphql", data=b"{}", headers={})
        body = resp.read()
        assert body == b'{"data": {"ok": true}}'
        mock_urlopen.assert_called_once()


def test_execute_linear_request_retry_then_success():
    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = b'{"data": {"ok": true}}'

    err_429 = make_http_error(429, "Too Many Requests")

    # Succeed on 4th attempt (3 retries)
    side_effects = [err_429, err_429, err_429, mock_resp]

    with mock.patch("urllib.request.urlopen", side_effect=side_effects) as mock_urlopen, \
         mock.patch("time.sleep") as mock_sleep:
        
        resp = execute_linear_request("https://api.linear.app/graphql", data=b"{}", headers={})
        assert resp.read() == b'{"data": {"ok": true}}'
        assert mock_urlopen.call_count == 4
        assert mock_sleep.call_count == 3


def test_execute_linear_request_retry_after_header():
    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = b'{"data": {"ok": true}}'

    # Retry-After in seconds
    headers = {"Retry-After": "4.5"}
    err_429 = make_http_error(429, "Too Many Requests", headers)
    side_effects = [err_429, mock_resp]

    with mock.patch("urllib.request.urlopen", side_effect=side_effects), \
         mock.patch("time.sleep") as mock_sleep:
        
        execute_linear_request("https://api.linear.app/graphql", data=b"{}", headers={})
        mock_sleep.assert_called_once_with(4.5)


def test_execute_linear_request_x_ratelimit_reset_header():
    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = b'{"data": {"ok": true}}'

    # Reset time in epoch milliseconds (e.g. current + 1.5s)
    target_time_ms = (time.time() + 1.5) * 1000
    headers = {"X-RateLimit-Requests-Reset": str(target_time_ms)}
    err_429 = make_http_error(429, "Too Many Requests", headers)
    side_effects = [err_429, mock_resp]

    with mock.patch("urllib.request.urlopen", side_effect=side_effects), \
         mock.patch("time.sleep") as mock_sleep:
        
        execute_linear_request("https://api.linear.app/graphql", data=b"{}", headers={})
        assert mock_sleep.call_count == 1
        # It should sleep approximately 1.5 seconds (assert between 0.5 and 2.5)
        sleep_arg = mock_sleep.call_args[0][0]
        assert 0.5 <= sleep_arg <= 2.5


def test_execute_linear_request_max_retries():
    err_429 = make_http_error(429, "Too Many Requests")
    side_effects = [err_429] * 12  # More than 11 attempts

    with mock.patch("urllib.request.urlopen", side_effect=side_effects), \
         mock.patch("time.sleep") as mock_sleep:
        
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            execute_linear_request("https://api.linear.app/graphql", data=b"{}", headers={})
        
        assert exc_info.value.code == 429
        # Max retries is 10, so 11 attempts total. Max sleeps is 10.
        assert mock_sleep.call_count == 10


def test_execute_linear_request_other_error():
    err_500 = make_http_error(500, "Internal Server Error")

    with mock.patch("urllib.request.urlopen", side_effect=err_500), \
         mock.patch("time.sleep") as mock_sleep:
        
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            execute_linear_request("https://api.linear.app/graphql", data=b"{}", headers={})
            
        assert exc_info.value.code == 500
        assert mock_sleep.call_count == 0
