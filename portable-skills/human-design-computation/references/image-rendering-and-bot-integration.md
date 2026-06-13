# Image Rendering & Telegram Bot Integration

Session: 2026-05-27 — v0.4: Proper 9-center mandala + birth data auto-routing.

## image_generator.py Architecture

Located at `~/work/OpenHumanDesignMCP/hd-mcp-server/src/image_generator.py`.

### render_bodygraph(chart_data, output_path) -> str

Renders a proper 9-center Human Design bodygraph mandala on 1200×800 PNG.

**Center positions (v2 mandala lattice)** — center at x=600, y=400:
| Center | Shape | Position (x,y) |
|---|---|---|
| Head | triangle_down | (600, 80) |
| Ajna | triangle_up (inverted) | (600, 170) |
| Throat | square | (600, 270) |
| G-Center | diamond | (600, 380) |
| Heart/Ego | triangle_up_right | (750, 340) |
| Spleen | triangle_up_left | (350, 340) |
| Solar Plexus | triangle_right | (850, 440) |
| Sacral | square | (600, 510) |
| Root | square | (600, 620) |

**NOT the old layout** (single vertical column with arrows) — that was a "lazy developer shortcut" called out in review. The mandala lattice is the only correct layout.

**Colors**: Defined=#E8A838 (warm amber), Undefined=#F5F0E8 (parchment), Line=#4A3728
**Channel lines**: Bézier curves between connected centers (not straight lines)
**Lattice background**: Faint lines for all 26 potential channel pairs behind defined channels
**Sidebar**: Type, Strategy, Authority, Profile, Signature, Not-Self, defined channels list

### render_bodygraph_with_transits(chart_data, transit_data, output_path) -> str

Natal bodygraph + transit overlay:
- Transit-conditioned channels in blue
- Transit-conditioned centers get a blue dot indicator
- New transit gate badges in blue
- Sidebar shows transit datetime, conditioned centers/channels, interpretation hints

### render_cartography_map(lines_data, output_path, title="") -> str

1920×960 PNG world map from GeoJSON lines data.
- Dual backend: cartopy (PlateCarree) with fallback to matplotlib continent outlines
- Planet colors: Sun=#FFD700, Moon=#C0C0C0, Mercury=#FF8C00, Venus=#FF69B4, Mars=#FF0000, Jupiter=#FFA500, Saturn=#808080, Uranus=#00FFFF, Neptune=#0000FF, Pluto=#8B0000
- Angle line styles: ASC=solid, DSC=dashed, MC=dash-dot, IC=dotted

Packages in hermes-agent venv: `pillow`, `matplotlib`, `cartopy`

## Telegram Bot Integration

### /chart and /map commands

Registered BEFORE the catch-all MessageHandler:
```python
app.add_handler(CommandHandler("chart", chart_cmd))
app.add_handler(CommandHandler("map", map_cmd))
```

Both handlers must convert local birth time to UTC:
```python
from geo_resolver import local_to_utc
utc = local_to_utc(year, month, day, hour, location)
birth_dt = datetime(utc[0], utc[1], utc[2], int(utc[3]), int((utc[3] % 1) * 60))
chart = calculate_natal_chart(name=name, birth_dt=birth_dt, ...)
```

### Birth Data Auto-Routing

Jamie detects birth data in free text via the `birth_query` classifier type and auto-routes to chart rendering.

**Classifier addition:**
```
"birth_query": User is providing birth data (date + time + location).
  e.g. "12/10/1989 @17:07 Simi Valley CA"
```

**Extraction pipeline**: Regex → AI (DeepSeek) fallback

Regex handles: `MM/DD/YYYY`, `YYYY-MM-DD`, `MonthName DD YYYY` with `@HH:MM` or `HH:MM AM/PM` time notation.

**Month-name regex gotcha**: `rf'({month_names})[\s,]+(\d{{1,2}})[\s,]+(\d{{2,4}})'` creates a nested group — day is `.group(3)`, year is `.group(4)`, month is `.group(1).lower()[:3]`.

**Handler flow**: extract → `local_to_utc()` → `resolve_location()` → `calculate_natal_chart()` → `render_bodygraph()` → `reply_photo()`

## Common Failure Modes

### Profile mismatch (e.g., 2/4 instead of 3/5)
**Root cause**: `calculate_natal_chart()` docstring says "birth_dt: Birth datetime (UTC)". The function expects UTC — it does NOT convert from local. Passing local time (e.g. 17:07 PST) instead of UTC (01:07 next day) shifts the chart by timezone offset.
**Fix**: Convert local→UTC with `local_to_utc()` BEFORE calling `calculate_natal_chart()`. For Michael: Dec 10 1989 17:07 PST → Dec 11 1989 01:07 UTC.

### "Infinite greeting loop"
**Root cause**: Bot process is `inactive (dead)`. Check `systemctl status next-step-bot`.
**Fix**: `sudo systemctl start next-step-bot`
**Prevention**: Wrap message handler in try/except that sends error text to user.

### Chiron ephemeris missing
**Symptom**: `swisseph.Error: SwissEph file 'se02060s.se1' not found`
**Fix**: Wrap `get_planet_position()` in try/except with logger warning + continue.

### Python f-string nested dict access
`f"Channels: {', '.join(f\"{c['gates'][0]}\" for c in channels)}"` causes SyntaxError.
**Fix**: Extract to variable first: `text = ', '.join(...)` then interpolate the variable.

### systemd restart conflicts
"Conflict: terminated by other getUpdates request" during restart is normal. Old instance takes ~10s to shut down while new one starts.
