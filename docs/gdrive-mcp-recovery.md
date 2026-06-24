# Google Drive MCP Connection Recovery

## Issue Description
The Google Drive MCP connection was failing repeatedly with:
```
WARNING tools.mcp_tool: Failed to connect to MCP server 'gdrive' (command=node): Connection closed
```
And in `mcp-stderr.log`:
```
Error: Cannot find module '/$PRISMATIC_HOME/work/local-gdrive-mcp/server.js'
```

The root cause was that `$PRISMATIC_HOME` in `/home/ubuntu/.hermes/profiles/orchestrator/config.yaml` was not expanded by the launcher process, resulting in the literal string `/$PRISMATIC_HOME/work/...` being passed as the argument to Node.

## Resolution
1. The path was updated to the absolute path `/home/ubuntu/work/local-gdrive-mcp/server.js` in `/home/ubuntu/.hermes/profiles/orchestrator/config.yaml`.
2. The user systemd service `hermes-orchestrator-gateway` was restarted to load the new config:
   ```bash
   systemctl --user restart hermes-orchestrator-gateway
   ```

## Verification
- Verified connection using the Hermes command line test tool:
  ```bash
  hermes mcp test gdrive
  ```
  Result:
  ```
  ✓ Connected (941ms)
  ✓ Tools discovered: 4
  ```
- Tested read-only tools like `drive_about` and `drive_search` successfully.

## June 2026 OAuth Token Expiration Recovery (GRO-1945)

### Issue Description
The Google Drive MCP connection failed to connect with the following error pattern in gateway logs:
```
unhandled errors in a TaskGroup -> Connection closed
```
This error effectively made the Google Drive integration down for the swarm/agent fleet.

### Root Cause
1. **OAuth Token Expiration:** The Google API credentials stored in `/home/ubuntu/.config/mcp-gdrive/.gdrive-server-credentials.json` had expired. Because no background/automatic refresh process was running to keep it alive using the refresh token, the credentials became invalid.
2. **Scope Mismatch:** When attempting to run initial re-auth using legacy scripts (`get_auth_url_with_docs.js`), Google returned `Error 400: invalid_scope`. The script requested explicit Google Docs and Sheets scopes (`documents` and `spreadsheets`) which were not allowed/verified in the GCP OAuth client project configuration.
3. **Redirect URI Mismatch:** The legacy script expected redirecting to `http://localhost:8085`, whereas the GCP client project allowlist only allowed `http://localhost` (no port).

### Resolution
1. **New Authentication Scripts:** Refactored/created updated authentication scripts inside `/home/ubuntu/work/local-gdrive-mcp/`:
   - `get_auth_url_fixed.js`: Requests only the verified scopes (`drive.readonly`, `drive.file`, `userinfo.email`, `userinfo.profile`, `openid`) and uses `prompt=consent` plus `access_type=offline` to force Google to return a `refresh_token`.
   - `exchange_gdrive_code_fixed.js`: Receives the OAuth code and exchanges it, caching the fresh token file containing both the `access_token` and `refresh_token`.
   - `auth_callback_fixed.js`: A lightweight localhost redirect handler that captures the code and automatically triggers token exchange.
2. **OAuth Flow Completion:** Executed the authorization flow. The user approved the OAuth access, generating a fresh token with `refresh_token` stored in `/home/ubuntu/.config/mcp-gdrive/.gdrive-server-credentials.json`.
3. **Automatic Refresh Verification:** Verified that the MCP server `/home/ubuntu/work/local-gdrive-mcp/server.js` now successfully auto-refreshes the access token using the cached `refresh_token` when required.

### Verification
- Ran the standalone MCP test client:
  ```bash
  node /home/ubuntu/work/local-gdrive-mcp/test_mcp_client.js
  ```
  Result: Successfully connected and performed Drive search and user query operations.
- Checked the MCP connection inside the agent environment using `drive_about` and `drive_search` tools:
  - `drive_about` returned: Michael Gulden (mbgulden@gmail.com)
  - `drive_search` for query "Sentinel" returned file lists successfully.

### Re-verification (June 24, 2026)
- Checked Google Drive MCP connection state and confirmed it is **operational**.
- Executed `test_auth.mjs` and verified that credentials are valid and access/refresh tokens are working correctly.
- Ran `test_mcp_client.js` standalone client and confirmed `drive_about` and `drive_search` function perfectly.
- Ran the Hermes test suite using `hermes mcp test gdrive` with successful connection and tool discovery.


