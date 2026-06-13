# Active Oahu: Mirror Repo vs active-oahu-static Split

The Active Oahu Tours static site involves TWO separate git repos that coexist on this host:

| Repo | Path | Purpose | Deploys? |
|------|------|---------|----------|
| `active-oahu-tours-mirror` | `~/work/active-oahu-tours-mirror` | CF Pages deployment repo | ✅ Deploys to activeoahutours.com |
| `active-oahu-static` | `~/work/active-oahu-static` | Template/source repo for page generation | ❌ Not deployed directly |

## The Split

The two repos have **diverged** — they contain different versions of templates and CSS:

- **Mirror repo** (`active-oahu-tours-mirror/site/`): Contains the GENERATED HTML pages (244+ .html files) and externally-linked assets (CSS, JS, images). This is what CF Pages serves.
- **Static repo** (`active-oahu-static/site/`): Contains the TEMPLATES (`_templates/head.html`, `body_top.html`, `body_bottom.html`) and source CSS that generation scripts read from.

## Generation Scripts

The Python generation scripts (`generate_pages.py`, `generate_rental_pages.py`, etc.) in the mirror repo use a hardcoded path:

```python
SITE = os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "/work/active-oahu-static/site"
```

They read templates from `active-oahu-static`, NOT from the mirror repo. This means:

- **Editing mirror repo templates** (`active-oahu-tours-mirror/site/_templates/`) does NOT affect page generation
- **Editing static repo templates** (`active-oahu-static/site/_templates/`) DOES affect generation — but the static repo is a DIFFERENT git repo with its own history

## CSS Divergence

The two repos have different CSS files:

- **Mirror CSS** (`active-oahu-tours-mirror/site/wp-content/themes/activeoahu/css/style.css`): The currently deployed version. 13 lines (1 massive minified line + mobile nav rules).
- **Static CSS** (`active-oahu-static/site/wp-content/themes/activeoahu/css/style.css`): Contains the GRO-751 Brand Design System rewrite (318 lines, structured CSS with variables). Was committed 2026-06-06 17:59 UTC but never synced to the mirror.

The static repo's last commit (`1a16b4b`) is a separate history from the mirror repo's commits.

## When This Matters

1. **Editing CSS**: Edit the mirror repo's CSS (`active-oahu-tours-mirror/site/wp-content/.../style.css`) — that's what deploys. Do NOT edit the static repo's CSS unless you also plan to regenerate all pages.
2. **Editing templates**: Edit the mirror repo's templates for quick deploys. Edit the static repo's templates if you plan to regenerate pages (but be aware they're different repos with different git histories).
3. **Regenerating pages**: The scripts read from `active-oahu-static`. Either update the scripts to point to the mirror repo, or sync templates between the two repos before running generation.
4. **CSS that looks fixed in one but broken in the other**: The GRO-751 CSS in `active-oahu-static` is a complete rewrite. It fixes mobile nav but overhauls desktop styling too. Don't blindly copy it to the mirror — extract only the rules needed.

## Quick Detection

```bash
# Are the CSS files different?
diff active-oahu-tours-mirror/site/wp-content/themes/activeoahu/css/style.css \
     active-oahu-static/site/wp-content/themes/activeoahu/css/style.css

# Where do generation scripts read from?
grep 'SITE =' ~/work/active-oahu-tours-mirror/generate_*.py
```

## See Also

- `force-push-rollback-and-deploy-trigger.md` — rollback pattern for bad deploys
- `cf-external-asset-cache-staleness.md` — CDN cache diagnosis
