---
name: daily-transit-briefing
description: Generate personalized daily transit briefings that are better than Co-Star. Creative quotes, transit-informed guidance, family snippets. For Sage (Becca) and Fred (Michael). Screenshot-worthy output.
category: human-design
triggers:
  - transit briefing
  - daily transit
  - Co-Star
  - Sage briefing
  - personalized HD daily
always-delegate: false
---

# Daily Transit Briefing — Better Than Co-Star

Co-Star knows your sun sign. We know your entire design, your spouse's design, your kids' designs, your current context, and what you've been experiencing. Use ALL of it.

## Birth Data Reference

From `~/work/next-step-bot/family.json`:

| Person | Design | Key |
|--------|--------|-----|
| Michael | 3/5 Splenic Projector, Fear Motivation, Cross of Rulership 4, channels 1-8 + 44-26, Split | Defined: G, Heart/Ego, Spleen, Throat |
| Becca | 6/2 Splenic Projector | Defined: 3 centers |
| Benjamin (9) | 5/1 MG Emotional | |
| William (7) | 3/6 Generator Sacral | |
| Victoria (5) | 4/1 Generator Sacral | |

## Transit Computation

Use the transit engine to get today's planetary positions:

```python
import sys; sys.path.insert(0, "/home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server/src")
from transit_engine import calculate_transit_positions
from ephemeris_engine import julday, init_ephemeris
from datetime import datetime
import pytz

init_ephemeris()
mt = pytz.timezone("America/Denver")
now = datetime.now(mt)
jd = julday(now.year, now.month, now.day, now.hour + now.minute/60.0)
transits = calculate_transit_positions(target_jd=jd)

# transits is dict: planet_name → {gate, line, color, tone, base, gate_name, longitude, sign, degree}
```

Then map to each person's design:
- Which of their defined channels are being activated by transit?
- Which of their open centers are being conditioned?
- Which gates in their chart are being hit?

## Output Format — Two Versions

### Version A: Sage → Becca (subject: Becca)

```
✨ [CREATIVE QUOTE — 1-2 sentences inspired by today's transits intersecting Becca's 6/2 Splenic Projector design, her role as mom/wife/HD practitioner, and what she's likely experiencing. Metaphorical, poetic, precise. Not generic horoscope language.]

━ TODAY FOR YOU ━
🌊 Favorable: [2-3 specific activities supported by today's transits]
⚠️ Watch for: [1-2 things to be aware of based on open centers being conditioned]
🌟 Supported: [What's cosmically backing her up]

━ NEXT STEP ━
→ Do this: [ONE specific action for Becca today. Not "be mindful" — an actual thing she can do or not do. Pulled from the transit data.]
→ For them: [ONE specific action involving Michael or the kids. Something she can actually execute.]

━ FAMILY SNIPPETS ━
💫 Michael: [1 sentence — what transit is hitting his chart, what it means for him today]
💫 Benjamin: [1 sentence]
💫 William: [1 sentence]  
💫 Victoria: [1 sentence]
```

### Version B: Fred → Michael (subject: Michael)

```
⚡ [CREATIVE QUOTE — 1-2 sentences inspired by today's transits intersecting Michael's 3/5 Splenic Projector design, his current ventures/context, Fear Motivation, Cross of Rulership 4. Should feel like insight, not prediction.]

━ TODAY FOR YOU ━
🌊 Favorable: [2-3 specific activities]
⚠️ Watch for: [1-2 things — especially spleen signals, invitation quality, energy management]
🌟 Supported: [What's cosmically backing her up]

━ NEXT STEP ━
→ Do this: [ONE specific action for Becca today. Not "be mindful" — an actual thing she can do or not do. Pulled from the transit data.]
→ For them: [ONE specific action involving Michael or the kids. Something she can actually execute.]

━ FAMILY SNIPPETS ━
💫 Becca: [1 sentence — her transit, what she's navigating]
💫 Benjamin: [1 sentence]
💫 William: [1 sentence]
💫 Victoria: [1 sentence]
```

## Quote Crafting Rules

The quote is the differentiator. Rules:
- Never use "you will" or "today is a day for" — Co-Star language
- Pull from the actual gate names and transit geometry — be specific
- Connect to their real life: Michael's ventures, Becca's practice, their parenting
- Use metaphor grounded in HD mechanics (channels as energies, gates as doors, centers as operating systems)
- If a channel gets fully lit by transit, that's the headline
- 1-2 sentences max. Screenshot-length.
- Examples of tone:
  - "The 1-8 channel hums under today's transit — your spleen already knows which venture to nurture. The question isn't what to build, but what to let someone else build for you."
  - "Gate 44 lights up your undefined Ajna today. Ideas will arrive that feel like yours. They're not. Wait 24 hours before committing to any of them."

## Activity Guidance Rules

- Activities must be SPECIFIC, not "practice self-care" or "be mindful"
- Connect to their actual life: "Take the kayak delivery route through Kahana" not "spend time in nature"
- Pull from transit-activated gates: if Gate 52 (Stillness) is active, suggest literal stillness
- If a splenic hit occurs, note it: "Your spleen will speak clearly around 2pm — listen for the instant no"

## Family Snippets Rules

- One sentence per person. No fluff.
- Connect transit to their design: "Benjamin's emotional wave gets a boost from today's Gate 39 transit — let him ride it out before asking about homework."
- Include ages implicitly through context (William is 7, Benjamin 9, Victoria 5)

## Cron Setup

Two separate crons:

**Sage → Becca**: `deliver: telegram:8570023972`, schedule: 7am MT (13:00 UTC)
**Fred → Michael**: `deliver: origin` (this chat), schedule: 7am MT (13:00 UTC)

Both load this skill, compute transits, and generate their respective version.

## Pitfalls

- Never use generic horoscope language — no "the stars align" or "universal energy"
- Don't mention gates/channels by number without their name/meaning
- If the transit engine fails, say so honestly — don't fake it
- Family snippets should feel like a parent noticing their kid, not an astrologer
- The quote must feel like it could ONLY be about that specific person today
- Keep output under 18 lines — shorter than a Co-Star screenshot
- **The NEXT STEP is the most important section.** The user's directive: "Becca and I need to know 'so what!?' Like, the next step. What do I do about that?" If the briefing only describes what's happening without giving a concrete action, it failed. Actions must be specific and executable: "Text Michael the one word: 'spleen'" or "Say 'I'll think about it' to the first request" — not "practice presence" or "trust yourself."
