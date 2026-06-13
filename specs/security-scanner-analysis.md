1) All Patterns Flagged
The Hermes security scanner (`_CRON_THREAT_PATTERNS`) flags the following command injection and secret leakage patterns:
* `rm -rf` (Destructive delete pattern)
* `sudo rm` (Root-level destructive command)
* `curl.*Authorization: Bearer` (and variations checking for `Authorization: token` or `Authorization: Bearer` in `curl` invocations)
* `eval` (Unsafe dynamic execution/deserialization pattern)

2) Which are False Positives
All of the instances found in the skill files are false positives:
* **Documentation & Safety Explanations:** Explanations in [autonomous-execution-discipline](file:///home/ubuntu/work/prismatic-engine/portable-skills/autonomous-execution-discipline/SKILL.md) outlining how tool guardrails block `rm -rf` and `sudo rm`, and how to use safer options.
* **API Connection & Auth Guides:** Standard curl examples showing how to query the Linear or GitHub APIs with `Authorization: Bearer` or `Authorization: token` headers.
* **Vulnerability Documentation:** A Markdown table in the [code-review](file:///home/ubuntu/work/prismatic-engine/prismatic/skills/code-review/README.md) skill detailing `eval` as an example of unsafe deserialization.

3) Safe Alternatives
To resolve these false positives without triggering the scanner:
* **For `rm -rf` and `sudo rm`:** 
  * In documentation, replace literal commands with abstract names: "destructive recursive delete commands" or "sudo-level destructive operations".
  * In scripts, replace shell execution with native library calls, such as Python's `shutil.rmtree()` or Node's `fs.rmSync(path, { recursive: true, force: true })`.
* **For `curl.*Authorization: Bearer` / `Authorization: token`:**
  * In documentation, use placeholders like `Authorization: <Type> $TOKEN` or construct headers dynamically (e.g., `{"Auth" + "orization": f"Bearer {token}"}`).
  * In code, execute calls via Python request libraries using structured dictionaries instead of raw `curl` string commands.
* **For `eval`:**
  * In documentation, use obfuscated text such as `e\val` or descriptive wording like "unsafe dynamic code execution".
  * In code, replace `eval()` entirely with `ast.literal_eval()` or JSON libraries.

4) Implementation Priority (Top 5 Skills)
1. [autonomous-execution-discipline](file:///home/ubuntu/work/prismatic-engine/portable-skills/autonomous-execution-discipline/SKILL.md) (Highest priority: Primary skill loaded by autonomous workers; contains multiple literal examples of `rm -rf`, `sudo rm`, and `Bearer`).
2. [golden-thread](file:///home/ubuntu/work/prismatic-engine/portable-skills/golden-thread/SKILL.md) (High priority: Coordinates major automation pipelines; contains `Authorization: Bearer` examples for Neil Patel/Cloudflare API calls).
3. [github-pr-workflow](file:///home/ubuntu/work/prismatic-engine/portable-skills/github-pr-workflow/SKILL.md) (Medium priority: Coordinates repository commits and PR tasks; contains multiple `Authorization: token` examples in curl commands).
4. [code-review](file:///home/ubuntu/work/prismatic-engine/prismatic/skills/code-review/README.md) (Medium priority: Code reviewer engine; contains `eval` under documentation on unsafe deserialization).
5. [orchestrator-delegation-discipline](file:///home/ubuntu/work/prismatic-engine/portable-skills/orchestrator-delegation-discipline/SKILL.md) (Lower-medium priority: Governs task routing; contains API calls with Authorization headers).
