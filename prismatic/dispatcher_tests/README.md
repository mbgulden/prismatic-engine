# Dispatcher routing checks

This directory holds focused dispatcher regression checks that stay inside the `prismatic/` lane.

## Jules host-path pre-screen

Jules runs inside a repository-only sandbox. Dispatcher issues labelled for Jules that explicitly require host-level access should be rerouted before launch instead of failing inside the sandbox.

Host-level markers include examples such as:

- `/home/ubuntu/...`
- `/etc/...`
- `/var/...`
- `/opt/...`
- `~/.hermes`
- `~/.config`
- `systemd`
- `crontab`

Expected behavior:

1. `detect_host_level_patterns(issue)` returns the host markers found in the title/description.
2. `reroute_jules_host_path_issue(issue, matches)` removes `agent:jules` / `agent::jules`, preserves unrelated labels, adds `agent:ned`, and comments with the detected markers.
3. `dispatch_once(...)` does not launch Jules for a host-path issue; it increments `host_path_rerouted` and marks the issue processed for the cycle.
