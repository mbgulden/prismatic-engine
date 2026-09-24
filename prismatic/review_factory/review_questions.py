"""The closed 5-question judgment pack for the review-factory L2 judge.

This is the complete remit from the Jev validation-loop plan (\u00a73). Jev may
judge ONLY what these five questions ask, and it MUST abstain (escalate, low
confidence) when the artifact lacks the context to answer. Jev gets no other
instructions.

New module by design: it does NOT touch ``prismatic.jev.questions`` (the
primitive owns that schema; the pack here is the call site's remit).

Jev NEVER re-runs, re-derives, or second-guesses any deterministic result:
the deterministic floor's outputs are inputs to these questions, never
their subject.
"""

from __future__ import annotations

# -- the five questions, verbatim -------------------------------------------
#
# Each block below is (title, question, guidance). The question strings are
# quoted verbatim from the plan; the rendered prompt embeds the full blocks
# unchanged. ``QUESTION_BLOCKS`` holds the exact rendered strings so tests
# can assert verbatim presence.

_Q1_TITLE = "Intent-vs-implementation"
_Q1 = "Does this diff do what `intent.goals` said, and nothing material it didn't say?"
_Q1_GUIDANCE = "Cite specific diff hunks vs specific goals."

_Q2_TITLE = "Blast-radius reasoning"
_Q2 = "If this change is wrong in the worst plausible way, what breaks?"
_Q2_GUIDANCE = "Severity is about consequence, not probability."

_Q3_TITLE = "Cross-change interaction"
_Q3 = "Given the concurrently-open artifacts listed in context, does this conflict or duplicate?"
_Q3_GUIDANCE = "Needs world knowledge no script has."

_Q4_TITLE = "Author-drift"
_Q4 = "Does this change the system's behavior in a direction inconsistent with the stated intent or the repo's established patterns?"
_Q4_GUIDANCE = "Requires reading the room."

_Q5_TITLE = "Green-but-smells-wrong"
_Q5 = "The deterministic floor passed. Is there a reason a careful reviewer would still hold this?"
_Q5_GUIDANCE = (
    "e.g., a test that asserts nothing, a migration with no rollback note, "
    "a security-sensitive file touched without mention in the brief."
)

# (question name, title) pairs, in pack order. Names are the typed-question
# ids used on the wire; titles are the human labels from the plan.
REMIT_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("q_intent", _Q1_TITLE),
    ("q_blast_radius", _Q2_TITLE),
    ("q_cross_change", _Q3_TITLE),
    ("q_author_drift", _Q4_TITLE),
    ("q_green_smell", _Q5_TITLE),
)

_QUESTION_TEXTS: tuple[str, ...] = (_Q1, _Q2, _Q3, _Q4, _Q5)
_QUESTION_GUIDANCE: tuple[str, ...] = (
    _Q1_GUIDANCE,
    _Q2_GUIDANCE,
    _Q3_GUIDANCE,
    _Q4_GUIDANCE,
    _Q5_GUIDANCE,
)

QUESTION_BLOCKS: tuple[str, ...] = tuple(
    f'{i}. {title}: "{question}" ({guidance})'
    for i, ((_, title), question, guidance) in enumerate(
        zip(REMIT_QUESTIONS, _QUESTION_TEXTS, _QUESTION_GUIDANCE, strict=True),
        start=1,
    )
)

# Verbatim question text only (no titles/guidance): the per-question prompts
# on the wire are the question text itself, nothing more.
QUESTION_TEXTS: tuple[str, ...] = _QUESTION_TEXTS

# -- abstain rules ------------------------------------------------------------
# Jev MUST abstain (which the judge maps to PAUSE -> ESCALATE, low
# confidence) when the artifact lacks the context to answer.

ABSTAIN_RULES: tuple[str, ...] = (
    "intent.plan_ref is missing",
    "the diff is truncated on a security-sensitive path",
    "intent.goals are contradictory, or empty (question 1 is then unanswerable)",
)

# -- rendered prompt ----------------------------------------------------------
# Deliberate vocabulary note: outside the quoted question blocks above, this
# prompt never uses {lint, test, re-run, check} as instructions. The
# deterministic results are inputs, never subjects.

_PROMPT_PREAMBLE = """You are the judgment layer of a review pipeline. A deterministic verifier already ran and judged this work CLEAN; its results are inputs to your judgment, never subjects for you to redo.

Jev may judge ONLY what a deterministic process cannot decide. Work through the five questions below. For each, cite specific diff hunks and specific goals where relevant.
"""

_PROMPT_ABSTAIN = (
    "ABSTAIN RULES -- abstain instead of answering when the artifact lacks "
    "the context to judge:\n" + "".join(f"- {rule}.\n" for rule in ABSTAIN_RULES)
)

_PROMPT_HARD_RULES = """HARD RULES:
- Your output vocabulary is {CLEAR, PAUSE} plus reasons. You do not decide merges.
- You never redo, re-derive, or second-guess any deterministic result.
- You write no code, mutate no state, and open no network connections.
- You never invent goals, plans, or acceptance criteria.
"""


def render_review_prompt() -> str:
    """Render the full judgment prompt: preamble + 5 verbatim questions.

    The five question blocks are embedded verbatim (see QUESTION_BLOCKS);
    the surrounding prose carries no other instructions to Jev.
    """
    parts = [_PROMPT_PREAMBLE.rstrip(), ""]
    parts.extend(QUESTION_BLOCKS)
    parts.extend(["", _PROMPT_ABSTAIN.rstrip(), "", _PROMPT_HARD_RULES.rstrip(), ""])
    return "\n".join(parts)
