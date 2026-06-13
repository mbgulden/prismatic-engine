# httpx 0.13.3 API Reference

The pipx-managed Hermes venv has httpx **0.13.3** (released 2020). Many modern
httpx APIs are unavailable. Use these patterns:

## Client

```python
import httpx

# Async client — pass timeout as float, not Timeout object
async with httpx.AsyncClient(timeout=30.0) as client:
    ...

# Sync client
with httpx.Client(timeout=10.0) as client:
    ...
```

## Available Exception Classes

```python
httpx.ConnectTimeout    # ✅ available
httpx.ReadTimeout       # ✅ available
httpx.PoolTimeout       # ✅ available
httpx.NetworkError      # ✅ available (base for connect/read errors)
httpx.ProtocolError     # ✅ available
httpx.HTTPError         # ✅ available (base for all HTTP errors)
httpx.DecodingError     # ✅ available
httpx.InvalidURL        # ✅ available
```

## NOT Available (modern httpx only)

```python
httpx.ConnectError      # ❌ NOT in 0.13.3 — use ConnectTimeout
httpx.RemoteProtocolError # ❌ NOT in 0.13.3 — use ProtocolError
httpx.Timeout(...)      # ❌ constructor differs — pass float instead
```

## Streaming (SSE pattern)

```python
async with client.stream("GET", url, timeout=None) as response:
    async for chunk in response.aiter_text():
        # chunk is a str — may be partial
        ...
```

All `aiter_*` methods ARE available: `aiter_text()`, `aiter_bytes()`, `aiter_lines()`, `aiter_raw()`.

## POST (Telegram sendMessage)

```python
async with httpx.AsyncClient(timeout=10.0) as client:
    resp = await client.post(url, json=payload)
    if resp.status_code == 200:
        ...
```
