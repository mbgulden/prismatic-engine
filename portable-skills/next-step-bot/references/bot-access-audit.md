# Bot Access Audit & Multi-Bot Security

## When to Run
- After deploying a new bot
- When someone new needs access to an existing bot
- When Michael asks "who has access to which bots?"
- As part of quarterly security review

## Audit Steps

### 1. Find all running bots
```bash
# Systemd services
systemctl list-units --all | grep -iE "bot|next-step|sage|kai|ella|sam"

# Hermes profiles (Kai-style bots)
hermes profile list 2>/dev/null
ps aux | grep "hermes.*gateway\|hermes.*profile" | grep -v grep

# Standalone bot processes
ps aux | grep "bot.py" | grep -v grep
```

### 2. Check ALLOWED_CHAT_IDS for each bot

**Standalone bots** (.env files):
```bash
for env in ~/work/next-step-*/.env ~/work/beyondsaas-bot/.env; do
  echo "--- $(dirname $env) ---"
  grep ALLOWED_CHAT "$env"
done
```

**Hermes profile bots** (config.yaml):
```bash
for profile in ~/.hermes/profiles/*/; do
  config="${profile}config.yaml"
  if grep -q "allowed_chats" "$config" 2>/dev/null; then
    echo "--- $(basename $profile) ---"
    grep allowed_chats "$config"
  fi
done
```

### 3. Verify access matrix

| Bot | Who Should Have Access |
|---|---|
| Jamie (next-step-bot) | Michael only |
| Sam (next-step-sam) | Michael only |
| beyondsaas-bot | Michael only |
| Becca/Sage (becca-sage) | Becca + Michael |
| Kai (Hermes profile) | Michael + Ella |

### 4. Fix mismatches

**For standalone bots:** Edit `.env`, update `ALLOWED_CHAT_IDS`, restart:
```bash
sudo systemctl restart <service-name>
```

**For Hermes profiles:** Edit `~/.hermes/profiles/<name>/config.yaml`, update `allowed_chats`, restart:
```bash
hermes --profile <name> gateway restart
```

## Pitfalls

- **Kai is a Hermes profile, not a standalone bot**: Uses `hermes --profile kai gateway run`, NOT `bot.py`. Access control is in `~/.hermes/profiles/kai/config.yaml` → `telegram.allowed_chats`, NOT in a `.env` file.
- **Becca's bot (`becca-sage`) is also standalone**: Despite being called "Sage", it runs as a systemd service from `~/work/next-step-becca/`. Don't confuse it with Hermes profiles.
- **Restart required after ALLOWED_CHAT_IDS changes**: The bot reads `ALLOWED_CHAT_IDS` at startup. Changes without restart have no effect.
