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
