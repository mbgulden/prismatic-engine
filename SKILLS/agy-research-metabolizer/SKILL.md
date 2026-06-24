---
name: agy-research-metabolizer
description: >-
  Perform high-agency, multi-source technical and strategic discovery,
  generating structured report bundles while preserving autonomous investigation.
---

# AGY Research Metabolizer

## Trigger
Load this skill when tasked with a research objective, technical audit, competitive analysis, or architectural discovery.

## Overview
The **AGY Research Metabolizer** is designed to guide high-agency research agents through multi-source investigations. It operates under a core philosophy: **ground research in provided anchors first, then aggressively expand exploration beyond those anchors as necessary to satisfy the objective.**

This skill enforces a structured 6-phase ingestion and synthesis process, uses a standardized confidence spectrum for traceability, and compiles output into tailored report bundles.

---

## 1. Input Specification

Every research metabolization task must be initialized with a configuration block matching the schema below:

```json
{
  "topic": "string",
  "objective": "string",
  "audience": "string",
  "downstream_use": "string",
  "known_anchors": ["string (file URLs, repos, web URLs)"],
  "allowed_expansion": "boolean",
  "desired_report_bundle": "brief | standard | deep | architecture | content-engine | competitive | golden-path",
  "depth": "quick | standard | deep"
}
```

---

## 2. The 6-Phase Processing Prompts (Templates)

Research agents should execute the following prompt sequences or run equivalent internal loops:

### Phase 1: Source Mapping & Gaps
Use this template to catalog constraints and initialize the search matrix.

```
SYSTEM INSTRUCTION: You are in Phase 1 (Source Mapping) of the Research Metabolizer loop.
Analyze the following inputs:
Topic: {topic}
Objective: {objective}
Known Anchors: {known_anchors}
Allowed Expansion: {allowed_expansion}

Tasks:
1. Validate all Known Anchors. Check their existence and read their contents.
2. Identify initial critical gaps: What crucial information is missing from the anchors to solve the objective?
3. Generate search queries, target API documentation links, and GitHub search terms.
4. Output a JSON Source Map containing 'verified_anchors' and 'expansion_targets'.
```

### Phase 2: Evidence Ledger Compilation
Use this template to record raw findings continuously. Do not synthesize yet; capture the raw facts.

```
SYSTEM INSTRUCTION: You are in Phase 2 (Evidence Ledger Compilation).
Create/append to a structured table called the 'Evidence Ledger'.
For each finding, extract:
- Unique ID: L01, L02, etc.
- Target Fact: The precise technical or strategic finding.
- Source Citation: Absolute file path, line number, or URL.
- Confidence Level: HIGH (Verified via code/live run), MEDIUM (Likely via documentation/forums), or LOW (Speculative).
- Verification Rationale: Why is it at this confidence level?

Rule: Never make a factual assertion in the final report that is not registered in this ledger.
```

### Phase 3 & 4: Synthesis & Triangulation
Use this template to resolve conflicts and construct the historical and future trajectory of the topic.

```
SYSTEM INSTRUCTION: You are in Phase 3 & 4 (Synthesis & Trajectory).
Using the Evidence Ledger, perform the following analyses:
1. Conflict Resolution: Highlight any discrepancies between sources (e.g. documentation claiming a feature works, but source code showing it is unimplemented).
2. Timeline & Trajectory: Map the legacy design decisions (how we got here), the current state, and the future trajectory.
3. Draw a Mermaid diagram representing the current system/data flows under investigation.
```

### Phase 5 & 6: Insights & Report Delivery
Use this template to generate the final Report Bundle.

```
SYSTEM INSTRUCTION: You are in Phase 5 & 6 (Insights & Report Delivery).
Construct the output report matching the desired bundle type: {desired_report_bundle}.
Ensure the report matches the target audience: {audience} and downstream use: {downstream_use}.

Guidelines:
- Insert bracketed references to the Evidence Ledger (e.g., [Ledger #L01]).
- List non-obvious insights (security risks, operational friction, undocumented limits).
- Formulate tiered actionable recommendations (Must / Should / Could).
```

---

## 3. Confidence Spectrum Rules

Agents must strictly tag findings in the Evidence Ledger using this standard:

*   **`[HIGH (Verified)]`**: Fact checked against active code implementations, verified via local execution/unit tests, or extracted from official RFC/specification documents.
*   **`[MEDIUM (Likely)]`**: Fact corroborated by secondary sources (community discussions, blog posts, developer wikis) or inferred from reliable system behaviors.
*   **`[LOW (Speculative)]`**: AI extrapolation, old forum threads (over 1 year old), or developer opinions. Requires active prototyping or unit testing to verify.

---

## 4. Autonomy & Expansion Guidelines

To prevent "pigeonholing" (where the agent refuses to look outside the immediate workspace or provided files):
1.  **Anchors First:** The agent must inspect and fully read all files and directories specified in `known_anchors` first.
2.  **Autonomy to Expand:** If `allowed_expansion` is true, the agent is explicitly authorized and encouraged to:
    *   Search the web for community discussions, GitHub issues, and official API references.
    *   Clone remote git repositories (in sandboxed environments) to inspect reference implementations.
    *   Query developer forums and read third-party package dependencies.
3.  **Refinement Loop:** If a local configuration file refers to a dependency that isn't in the workspace, the agent should search for that dependency's code or documentation.

---

## 5. Anti-Patterns to Avoid

*   ❌ **Pigeonholing (Anchor-Locked):** Stopping research because the answer was not in the immediate workspace or provided anchors, despite having external access.
*   ❌ **Blind Trust (Doc-Fidelity):** Trusting outdated documentation (e.g., READMEs) when the actual code implementation behaves differently.
*   ❌ **Unlabeled Speculation:** Presenting guesses or predictions about system performance, security, or capability without a `[LOW]` confidence label and a clear path to verify it.
*   ❌ **No-Provenance Reports:** Writing reports containing assertions like "Wasmtime is 10% faster than Wasmer" without citing the exact benchmark script, hardware setup, or source.
*   ❌ **Wall of Text Output:** Outputting standard reports when specialized formats (like `architecture` or `golden-path`) are requested.

---

## 6. Launch & Profile Mapping

Ensure you invoke the correct launch flags depending on the task's position in the development lifecycle:

| Phase | Purpose | Target Command |
|---|---|---|
| **Discovery** | Research, survey, competitive audit | `agy --print "RESEARCH <topic>..."` |
| **Audit** | Analyze local files against external sources | `agy --print --add-dir <repo> "AUDIT <topic>..."` |
| **Execution** | Turn research into active code changes | `agy --prompt-interactive --add-dir <repo> "Implement <spec>"` |
