---
name: expert-interview-content-production
description: >
  Turn domain expertise into authoritative web content, social media, and
  structured data through recorded interviews. Covers the full pipeline:
  research-backed interview strategy, structured script writing (6 content
  types), post-interview swarm processing (transcription → extraction →
  multi-format output), human-in-the-loop team operations, and compliance
  patterns. Use when building content for any Michael-owned website
  (Active Oahu Tours, activeoahu.com, yourhawaiiguide.com) that needs
  first-hand local knowledge, personal stories, and authentic expertise
  that AI alone cannot produce.
triggers:
  - expert interview or interview content or content from Michael
  - E-E-A-T content or authoritative content or local expert content
  - interview script or ask Michael questions or record answers
  - authentic content or personalized content or expert knowledge
  - website content strategy or content pipeline
  - activeoahu or yourhawaiiguide or tour content
  - content for website or build content
  - audio answer or video response
always-delegate: false
---

# Expert Interview Content Production

## Why This Works

Google's E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) rewards
first-hand experience from a real local expert. AI-generated content cannot compete
with authentic stories, specific details, and personal expertise from someone who
has actually done the thing. Michael has years of Oahu kayak guiding, tour operations,
and local knowledge — this pipeline captures that into high-ranking content.

## The Pipeline

```
Research questions  →  Write interview script  →  Post to Linear (assigned to Michael)
      ↓
Michael records audio/video  →  Uploads MP3/MP4 to Linear task
      ↓
Swarm transcribes (Gemini/Whisper)  →  Extracts quotes, stories, facts
      ↓
Outputs: web pages (with schema) + social posts + video shorts + email content
```

One 20-30 minute recording fuels 5+ content outputs across multiple platforms.
That's the leverage play.

## When to Use

- Building a new website section that needs authentic local knowledge
- Creating comparison/review content (tour operators, beaches, activities)
- Writing safety guides, route descriptions, or "insider" content
- Generating FAQ content where Michael's experience is the differentiator
- Any page where "a real person who's done this" matters more than SEO keywords alone
- User prefers talking over writing (ADHD-friendly — record while driving, on the beach, wherever)
- Multiple sites need content on related topics (one interview can feed multiple sites)

Do NOT use for: programmatic SEO pages (gates, centers), API documentation, or content
that can be factually generated from structured data alone.

## Process

### 1. Research Phase (Before Any Question)

Before writing a single question, research what people actually search for:
- Google "people also ask" boxes for the topic
- Competitor content gaps (what are they NOT covering?)
- Reddit/forums for real visitor questions
- Identify question formats: comparison queries ("vs"), how-to queries, best-of queries, local-secret queries
- **Use Ubersuggest MCP for keyword research** before writing any questions. Connect via OAuth (see `references/ubersuggest-mcp-integration.md`), then pull: `domain_keywords` for competitors, `keyword_suggestions` for seed terms, `serp_analysis` to check who ranks for target keywords. This data IS the interview outline — you're asking Michael to answer the exact questions people type into Google.
- **Verify competitor claims with data** — especially claims about their physical presence, ownership, and capabilities. Don't assume a competitor is weak or absent from a geography without checking their site, Ubersuggest SERP data, and Google Maps. Michael knows his competitors better than you do. When he pushes back on an assumption, accept the correction and re-analyze from data.

### 2. Write the Interview Script

Each script targets ONE Linear issue. Structure:

```markdown
## 🎙️ Expert Interview — [Topic]

**Interviewer:** Hermes (Fred)
**Expert:** Michael Gulden — [relevant credentials]
**Use for:** [target domain] [specific page types], social content

### Instructions, Michael:
Record as audio or video. Answer naturally — like telling a friend.
We'll pull quotes, stories, and facts into multiple outputs.

### SECTION 1: [Theme] (5-7 questions)

### SECTION 2: [Theme] (5-7 questions)

### SECTION 3: [Theme] (5-7 questions)

---

**Delivery:** Upload MP3/MP4 to this task.

**After upload:** Swarm transcribes → extracts quotes → generates
web pages, social posts, schema markup.
```

#### Question Design Rules

1. **Open-ended.** Never yes/no. "Describe..." "Tell me about..." "What was it like when..."
2. **Story-driven.** Ask for memories, specific moments, funny incidents, close calls.
3. **Specific.** "What's the worst conditions you've ever paddled the Mokes in?" beats "Is it dangerous?"
4. **Structured for reuse.** Each section maps to a specific web page or content type.
5. **Natural speech.** Questions should sound like a conversation, not a survey.
6. **Start broad, narrow to specifics.** Mix: stories (emotional), facts (SEO keywords), tips (practical value).
7. **End sections with feeling questions.** "What still takes your breath away?"
8. **Aim for 15-25 questions per script, grouped into 3-5 sections.** Answerable in one sitting.

#### Section Themes (adapt per topic)

| Theme | Maps To | Example Question |
|-------|---------|-----------------|
| The experience itself | Tour/activity page | "Describe paddling out to the Mokes for the first time." |
| Practical knowledge | How-to/guide content | "What safety gear do YOU personally bring?" |
| Comparisons | Comparison/review pages | "Which operators do kayaking best and why?" |
| Local secrets | Insider/authority content | "What's something no guidebook ever mentions?" |
| Stories & personality | About page, social | "Tell me about your favorite tour group ever." |
| Safety/risk factors | Safety/authority pages | "What conditions make you cancel a tour?" |

### 3. Create the Linear Task

Each interview gets ONE Linear issue with:
- Project: the target website project
- Assignee: Michael Gulden
- Priority: P0 (blocks content creation) or P1
- The full interview script as the first comment
- Label: `type:content` (add if not present)

**Important:** The Linear task IS the handoff. Don't send interview scripts via chat — they get lost. Post them to the issue.

### 4. Post-Interview Processing

When Michael uploads a recording:

1. **Transcribe** — Use Gemini (preferred, handles Hawaiian names/terms) or Whisper fallback
2. **Extract** — Pull named entities (locations, operators, activities), direct quotes, key facts
3. **Map quotes to page sections** — this quote becomes an H2, that story becomes the intro
4. **Map to outputs**:
   - Each section → one web page or page section
   - Direct quotes → pull-quote blocks, social graphics
   - Stories → blog posts, about page content
   - Facts/claims → FAQ schema, how-to schema
5. **Generate schema markup** — FAQ, HowTo, Article, LocalBusiness, TouristAttraction as appropriate
6. **Generate social assets** — Quote cards (square + story format), short clips from video
7. **Pull video clips** — timestamps of the most shareable 30-60s moments

## Script Templates by Content Type

### Tour/Activity Page Interview
Focus: what the experience actually feels like, practical details, safety, what
makes it special. Questions like: "Describe paddling out for the first time. What
do you see? What surprises people? What's the biggest mistake beginners make?"

### Comparison/Guide Page Interview
Focus: honest pros and cons, who-it's-for, pricing context. Questions like: "Walk
me through every major operator. What does each do well? What do they do poorly?
Who's overpriced? Where do you get the most value?"

### Local Knowledge Interview
Focus: insider tips, hidden spots, local etiquette, seasonal changes. Questions
like: "The beaches tourists never find. Where do locals go? What drives locals
crazy about visitor behavior? What's the most beautiful moment you've experienced
here?"

### Safety/Authority Interview
Focus: real risks, preparation, what guides know that visitors don't. Questions
like: "What's the most dangerous spot and why? Walk me through exactly what to
do if you capsize. What conditions make you cancel a tour?"

### Brand/Origin Story Interview
Focus: why Michael does this, community context, personal connection. Questions
like: "Why this location? Tell me about starting the business. What's the best
compliment you've ever received? What does this place mean to you personally?"

### Comparison Page Interview ("Honest Comparison" Pattern)
Comparison pages rank well ("X vs Y" searches), build trust through honest
assessment, and naturally cross-sell services.

Question focus: genuine pros/cons of two options, specific to Michael's expertise.
"Walk me through the real differences between X and Y. Who should pick X? Who
should pick Y? What does X do better? What does Y do better? Be honest —
badmouthing neither helps anyone."

Include parking/access/logistics questions — these are often the deciding factor
and create natural cross-sell opportunities. Every comparison should have a clear
"who should choose which" section with specific audience recommendations.

## Multi-Site Content Strategy

One interview can feed multiple domains when they cover different angles of the
same expertise. Same stories, different framing:

```
Michael's Expertise (kayak guide on Oahu)
    ├── activeoahutours.com  ← tour experience, safety, booking context
    ├── yourhawaiiguide.com  ← comparisons, honest reviews, affiliate context
    └── activeoahu.com       ← community, events, retreats context
```

## Human-in-the-Loop Team Model

The interview pipeline works because humans do what AI cannot. The full content
operation runs as a 3-role team:

| Role | Who | Does |
|------|-----|------|
| **Orchestrator** | Hermes (AI) | Research, question generation, task assignment, schema markup, technical SEO, transcription management, batch planning, deployment |
| **Domain Expert** | Michael | Records audio interviews, provides local knowledge, final approval on content, business strategy |
| **Content Writer** | Ella (or future hires) | Writes blog posts/pages from transcribed insights, reviews AI-generated content, polishes raw transcriptions, catches AI mistakes, applies creative judgment |

### What Humans Add That AI Cannot

**Michael adds**: Voice (actual stories and personality), verification (knows routes/tides/distances), authority (Google E-E-A-T rewards firsthand expertise), business judgment (which tours need bookings, which competitors to target).

**The Content Writer adds**: Creative writing quality (pacing, hooks, voice), fresh eyes (if they're confused, customers will be too), taste and judgment ("this sounds corny," "this paragraph drags"), transcription polishing (raw speech-to-text needs a human touch to read well).

### The 4-Stage Pipeline with Human Touchpoints

```
STAGE 1 — Interview Recording (Michael's job, Hermes prepares)
  Hermes: Writes interview script → Posts to Linear assigned to Michael
  Michael: Records 15-25 min audio → Uploads MP3 to Linear task

STAGE 2 — Transcription & Extraction (Hermes handles)
  Swarm transcribes → Extracts quotes, facts, stories → Maps to web pages

STAGE 3 — Content Creation (Content Writer + Hermes)
  Hermes: Provides content templates with transcribed insights embedded
  Content Writer: Writes the actual pages — creative judgment makes it human
  Hermes: Generates schema markup (FAQ, HowTo, Article, LocalBusiness)
  Content Writer: Final proof — catches anything that doesn't "sound right"

STAGE 4 — Review & Publish (Michael + Hermes)
  Michael: Reviews final pages for accuracy and voice
  Hermes: Technical deployment (git push → auto-deploy)
```

### Batch Assignment Pattern

Assign content writers 3-5 tasks at a time via Linear. When the batch is ~80%
complete, assign the next batch.

**Why batches**: Prevents overload (writers are part-time), keeps focus (3-5
things is manageable), allows incremental review, lets priorities shift between
batches.

**First batch for new writers**: Orientation tasks — read the existing site,
review competitors, read interview scripts, write one practice post. This builds
domain knowledge before the real content work begins. Onboarding template in
`references/team-onboarding-template.md`.

### Weekly Cadence

- **Monday AM (Hermes)**: Assign next batch if previous is done, write new
  interview scripts if needed
- **Michael's recording sessions (flexible)**: Pick one interview from Linear
  when you have a quiet moment, record 15-25 min, upload. Aim for 1-2 per week.
- **Wednesday-Thursday (Content Writer)**: Write from active batch, review
  transcriptions, move completed tasks to In Review
- **Thursday-Friday (Michael + Hermes)**: Review submitted content, deploy,
  plan next week

### Warning Signs

- **Writer's tasks sit in Todo > 5 days**: Batch might be too big or unclear.
  Check in.
- **Michael hasn't recorded in 2+ weeks**: Pipeline stalls. Nudge via Linear.
- **Content sounds generic/AI-ish**: Writer should flag specific examples —
  their creative judgment feedback loop needs strengthening.
- **Too many tasks assigned**: Humans should say "batch limit reached" and adjust.

## Batching Across Sites

When building content for multiple sites, batch interviews by site theme:

| Site | Interview Topics | Count |
|------|-----------------|-------|
| activeoahutours.com | Tour experiences, safety, local knowledge | 4-5 interviews |
| activeoahu.com | Community, events, retreats, lifestyle | 2-3 interviews |
| yourhawaiiguide.com | Comparisons, guides, regional deep-dives | 4-5 interviews |

Create all interview tasks at once (one Linear issue per topic), post all scripts
as comments, assign all to Michael. Then the swarm works through them as he uploads
recordings — no bottleneck.

## Compliance Patterns for Restricted Geographies

When dealing with regulatory restrictions, don't make the content look guilty
with defensive banners and disclaimers. Study what competitors who ARE compliant
actually do — often they advertise the restricted destination aggressively while
their defense is operational separation (transaction at shop, customer
self-transports). Match their proven approach, not a lawyer's worst-case scenario.

Embed self-transport language naturally: "Pick up at our shop → drive yourself →
launch as a private boater." The operational separation (documented check-in at
shop, no company vehicles at the beach) is the real defense — not website warnings.
See `references/kaneohe-bay-compliance-pattern.md` for the full "Decouple and
Reframe" framework.

## Pitfalls

- **Don't ask questions you could answer from structured data.** If the engine
  can compute it, don't interview for it.
- **Don't cram multiple unrelated topics into one script.** "Kayak routes +
  beach guide + tour comparisons" is three scripts, not one.
- **Don't write the answers for him.** The script is questions only. Michael's
  voice is the value.
- **Don't skip the schema markup step.** The interview content is perfect for
  FAQ and HowTo schema — that's the SEO differentiator.
- **Don't ask yes/no questions.** Open-ended prompts get better audio. "Tell me
  about..." beats "Do you...?"
- **Michael records on his time.** Don't nag. Post the script, it's in his Linear
  queue. He'll get to it.
- **Interview tasks are human-blocked, not stalled.** When an interview-script
  issue sits in Backlog or In Progress waiting for Michael's recording, it is NOT
  a stalled task requiring intervention. Do NOT flag it as stalled or attempt to
  "fix" it. The blocker is Michael's time to record — the swarm's job is to have
  the scripts ready when he is.
- **Don't send interview scripts via chat.** They get lost. Post them to the
  Linear issue.
- **Don't assign too many tasks at once.** 3-5 per batch. New writers get lighter
  batches. Over-assigning causes paralysis, not productivity.
- **Don't let the content writer initiate from blank pages.** They need seeds —
  transcribed quotes, outlines, competitor examples, specific questions.
- **Over-cautious compliance language**: Don't make content look guilty with
  defensive banners and disclaimers — signals guilt to users and regulators alike.
- **Competitor assumptions without data**: Before claiming a competitor "can't
  do" something or "doesn't have" an asset, check their website, Ubersuggest
  rankings, and Google Maps. Michael knows the competitive landscape better than
  you — when he corrects an assumption, re-analyze from data immediately.
- **Don't leave content in the transcript.** The value is in the pipeline:
  transcript → extract → generate → publish. Don't stop at transcription.

## References

- `references/interview-script-examples.md` — Full scripts from the initial
  10-interview batch (GRO-138 through GRO-147) for pattern reference
- `references/team-onboarding-template.md` — Reusable onboarding doc structure
  for content writers joining the pipeline
- `references/ubersuggest-mcp-integration.md` — Ubersuggest MCP OAuth setup for
  keyword research
- `references/kaneohe-bay-compliance-pattern.md` — "Decouple and Reframe"
  compliance framework
- `references/competitor-seo-audit-methodology.md` — How to audit competitor SEO
- `templates/interview-script-template.md` — Reusable interview script format
