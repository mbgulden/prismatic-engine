# Local agent status checks

Use this reference when the user asks whether Hermes-managed local workers are currently available or what state they are in.

## Practical status order
1. Check the Hermes profile registry first.
   - Prefer `hermes profile list` or equivalent profile metadata for availability and model mapping.
   - Treat the profile state as the control plane for Hermes-managed local workers.

2. Check active background sessions only after the profile registry.
   - Use the process/session list to distinguish:
     - an explicitly stopped profile,
     - an active worker session,
     - or no background session at all.

3. Report the state in plain terms.
   - Example: `running`, `stopped`, `planned`, or `no session`.
   - If multiple local workers exist, name each one separately.

## Good reporting pattern
- State each worker explicitly.
- If a worker is stopped, say so directly.
- If no background process exists, distinguish that from a stopped profile.
- Avoid inferring health from a missing terminal window alone.

## Verification mindset
- Use the registry as the first source of truth for Hermes-managed workers.
- Use process/session state as the second source of truth for live execution.
- If the two disagree, treat it as an orchestration issue to resolve rather than a fact to guess around.
