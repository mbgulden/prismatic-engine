# Michael Gulden Reference Chart

**Birth data:** Dec 10, 1989 17:07 PST in Simi Valley, CA (34.2694, -118.7815)
**UTC:** Dec 11, 1989 01:07

## Golden Reference (verified against Swiss Ephemeris)

| Field | Value |
|---|---|
| Type | Projector |
| Strategy | Wait for the Invitation |
| Authority | Splenic |
| Signature | Success |
| Not-Self Theme | Bitterness |
| Profile | 3/5 |
| Definition | Split |
| Incarnation Cross | Right Angle Cross of Rulership 4 |

## Defined Centers
G, Heart/Ego, Spleen, Throat

## Defined Channels
- 1-8: Inspiration (Individual) — G → Throat
- 26-44: Surrender (Tribal) — Heart/Ego → Spleen

## All Active Gates (20)
1, 6, 7, 8, 10, 13, 14, 22, 26, 29, 30, 38, 44, 45, 47, 48, 50, 52, 58, 60

## Personality Planets
| Planet | Gate | Line | Longitude |
|---|---|---|---|
| Sun | 26 | 2 | 258.6296° |
| Earth | 45 | 2 | 78.6296° |
| Moon | 23 | 3 | 51.1805° |
| South Node | 7 | 6 | 138.3880° |

## Design Planets
| Planet | Gate | Line | Longitude |
|---|---|---|---|
| Sun | 47 | 4 | 170.6296° |
| Earth | 22 | 4 | 350.6296° |
| Moon | 49 | 5 | 322.6423° |
| South Node | 29 | 2 | 145.7059° |

## Verification command
```python
import sys
sys.path.insert(0, os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "/work/OpenHumanDesignMCP/hd-mcp-server/src")
from datetime import datetime
from cosmic_calculator import calculate_natal_chart
from ephemeris_engine import init_ephemeris

init_ephemeris()
birth_dt = datetime(1989, 12, 11, 1, 7)  # UTC
chart = calculate_natal_chart(
    name='Michael', birth_dt=birth_dt,
    lat=34.2694, lon=-118.7815, timezone='America/Los_Angeles'
)
assert chart['profile'] == '3/5', f"Profile wrong: {chart['profile']}"
assert chart['hd_type'] == 'Projector'
assert chart['authority'] == 'Splenic'
assert 'G' in chart['defined_centers'] and 'Spleen' in chart['defined_centers']
print("PASS")
```
