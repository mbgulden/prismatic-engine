# Michael's HD Relationship Report Prompt Series

Comprehensive prompt templates for generating jargon-free, actionable Human Design reports for individuals and pairs. These prompts force the AI to use everyday language, concrete examples, and exact conversational scripts.

---

## Part 1: Individual Deep-Dive

```
Act as an authoritative expert in Human Design and AstroHD mechanics, focused on delivering visceral, grounded insights. Attached is the data for an individual who is entirely new to these symbolic frameworks. You must translate their complex chart mechanics into everyday language that reflects the specific season of life they are currently navigating.

Strip away the esoteric jargon and provide opinionated, practical examples that resonate with their actual, real-world experiences today. Your goal is to translate their complex chart mechanics into a comprehensive, deeply insightful, and highly practical report using everyday 'normal person' vocabulary. Absolutely no esoteric HD jargon (do not use terms like 'Sacral,' 'Not-Self,' 'Lines,' or 'Authority' without immediately translating them into plain English concepts).

Please provide a deep analysis that includes:

1. **Core Strengths, Weaknesses, and Hidden Talents**: Quantify these clearly. What are their natural gifts, and where are their inherent blind spots?

2. **Inner vs. Outer Self (The Phantom Costumes)**: Compare their Astrological Angles against their actual defined/undefined centers. How does the world see them (the expectation/costume) versus who their aligned self actually is (the reality)?

3. **Mathematical Correlations**: Evaluate the mathematical correlations and divergences between their Personality (Conscious) and Design (Unconscious) astrological angles. Are there any perfect mirrors, extreme concentrations, flipped axes, or unique synchronicities? Explain what this foundational math means for their conscious vs. unconscious life path in plain language.

4. **Situational Examples**: Provide highly specific, opinionated, and visceral real-world examples demonstrating these traits in action. Show me exactly how these tendencies play out in an everyday work environment, a stressful situation, and a social setting.
```

---

## Part 2: Relationship Series (2 Individuals)

Input both people's birth data before running.

### 2A: Foundational Dynamics & Summary

```
Act as a practical relationship and behavioral analyst utilizing Human Design and AstroHD frameworks. I have provided the chart data for [Person A] and [Person B], who are [insert relationship: e.g., father and son, husband and wife].

First, provide an observation and summary of their overarching dynamic. Do not give me vague horoscope theory; give me grounded reality. How do their energetic mechanics naturally interact? Highlight their major points of friction and their greatest points of synergy.

Provide 3 specific, multi-factor real-world scenarios that demonstrate how their dynamic plays out in daily life (e.g., planning a trip, handling an unexpected crisis, or relaxing at home).
```

### 2B: Person A's Perspective & Strategy

```
Now, I want you to step entirely into [Person A]'s perspective. Based on the mechanical intersections of their charts, give [Person A] a highly practical, opinionated guide on how to cooperate, communicate, and improve their relationship with [Person B].

Translate [Person B]'s complex traits into a 'user manual' for [Person A]. What are the unwritten rules for talking to [Person B]? What behaviors should [Person A] stop doing immediately because they will backfire?

Provide 3 clear, real-world examples with exact conversational scripts that [Person A] can use to smooth out friction and collaborate more effectively.
```

### 2C: Person B's Perspective & Strategy

```
Next, flip the perspective entirely to [Person B]. Based on the chart mechanics, give [Person B] a highly practical, opinionated guide on how to cooperate, communicate, and improve their relationship with [Person A].

What are [Person A]'s blind spots that [Person B] needs to have patience with? How should [Person B] structure their requests or boundaries so that [Person A] actually hears them?

Provide 3 clear, real-world examples with exact conversational scripts that [Person B] can use to navigate [Person A]'s specific tendencies.
```

### 2D: Deep Synthesis, Anomalies & Astrological Weather

```
Finally, provide a combined, highly detailed synthesis of their relationship. I want you to look deeper than the basic dynamic.

1. **Anomalies & Deep Connections**: Highlight any unique anomalies, rare mathematical connections, or intense AstroHD angle combinations between their two charts. Explain the 'why' behind these connections and how they manifest in their bond. Do not repeat basic information from previous prompts.

2. **Collaboration**: Give me a specific framework for how these two should tackle a large, stressful project together (e.g., renovating a house or starting a business). Who should do what, and why?

3. **The Astrological Weather**: Look at the upcoming astrological transits for the next 12 months. Based on the interaction of these two individuals with those transits, highlight 3 key dates or periods they should look forward to or prepare for. Explain how the 'weather' will make their tendencies lean a certain way during these times, and give actionable advice on how to navigate it together.
```

---

## Implementation Notes

- These prompts are designed to be answered by the user's AI model (DeepSeek, GPT-5.5, etc.) WITH the raw HD chart data provided as context.
- The agent should compute the charts first (via MCP), assemble the raw data (types, centers, channels, electromagnetic connections, transit data), then feed it alongside the prompt to the AI.
- Output format: markdown with clean headers, no raw data dumps, no jargon.
- Michael's preference: concrete > abstract, actionable > theoretical, warm > clinical.

---

## Client-Facing Adaptation (Non-HD Audience)

When the person receiving the report has **zero familiarity with Human Design**, the full prompt series output is too long and the vocabulary substitutions alone aren't enough. Apply these adaptations:

### 1. Add a "No Belief Required" Intro (Critical)

Without this, even vocabulary-substituted content feels esoteric. Open with 2-3 sentences that explicitly release the reader from needing to believe in anything:

> "This isn't astrology. It's not a personality test. Think of it like a user manual — the kind you wish came with every important person in your life. You don't need to believe in any of this. Just try the suggestions at the end and see if anything shifts."

### 2. Drop the Anchor Data Verification Table

The table of gate numbers, electromagnetic connections, and angle aspects is essential for verifying the math is right — but for a client PDF, it's the exact moment their eyes glaze over. Keep it for internal verification. Delete it from the client output.

### 3. Isolate ONE Core Tension

Instead of listing every friction point, pick the single biggest mechanical tension. For JT/Jonathan, it was the Generator-Projector energy gap. Name it once, explain it in plain English, and anchor everything else around it. If you give them 7 problems, they remember zero.

### 4. Match the Closing to Decision Type

The final question or invitation must match the recipient's decision-making style:

- **Generator / Manifesting Generator** (gut-response): End with a binary yes/no question they can answer instantly. "When was the last time you felt genuinely seen by someone — just seen, not fixed?"
- **Projector** (needs invitation): Extend a clean, recognizing invitation. "I noticed something about how you operate with [person]. If you're open to it, I'd love to hear what resonates."
- **Manifestor** (initiates): Ask what they want to start. "What's one thing you've been wanting to do differently with [person] that you haven't started yet?"
- **Reflector** (needs time): Ask them to sit with a question for a lunar cycle. "No need to answer now. Just notice over the next month: when do you feel most at ease around [person]?"

### 5. Keep It Under 6 Pages

Long reports overwhelm. The goal is to leave them wanting to try ONE experiment, not to explain their entire design. If a section doesn't point to a specific action, cut it.

### 6. Pandoc PDF Generation

```bash
sudo apt-get install -y pandoc wkhtmltopdf
pandoc input.md -o output.pdf --pdf-engine=wkhtmltopdf --metadata title="Document Title"
```

PDf output is ~36-56KB for reports of this size. Clean formatting, no dependencies beyond apt.

### 7. Telegram-Safe Formatting

When delivering reports via Telegram (as MEDIA: files or inline messages):

**Never use:**
- Pipe tables (`| col1 | col2 |`) — Telegram has no table syntax; renders as garbage characters
- Horizontal rules (`---` / `***`) — renders as raw dashes/asterisks
- Nested markdown in code blocks

**Use instead:**
- Bullet lists with `•` for key-value pairs
- Bold headers (`**Label:**`) for labeling
- Line breaks between sections for visual separation
- Emoji for visual landmarks (🌱 🎯 ⚠️)
