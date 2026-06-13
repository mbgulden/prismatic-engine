## DESIGN: Portable Skill Export Format — 2026-06-13

## 1. Portable Skill Format

To make Prismatic Engine skills portable across different host systems, environments, and directories, a self-contained, standardized package layout is defined. Each skill is packaged under a directory name matching its unique slug, containing its manifest, core instruction file, and any supporting files.

### Directory Structure

```
portable-skills/
└── <category>/
    └── <skill-name>/
        ├── manifest.yaml
        ├── SKILL.md
        ├── scripts/
        │   ├── build_report.py
        │   └── validate_env.sh
        └── references/
            ├── architecture-diagram.md
            └── api-capabilities.md
```

### Templating of SKILL.md (and supporting files)

To prevent breaking references, absolute paths are converted to environment-resolved variables. The Prismatic Engine supports two mechanisms for resolving these variables: **Runtime Resolution** (preferred, where variables are dynamically replaced in memory when the agent loads the skill context) and **Installation-Time Compilation** (where variables are permanently compiled to the host's paths upon installation).

The standard variables used are:

*   **`$PRISMATIC_HOME`**: Resolves to the user's home/base directory on the target system (e.g., `/home/ubuntu` or `/root`).
*   **`$SKILLS_DIR`**: Resolves to the active Prismatic Engine skills repository folder (e.g., `/home/ubuntu/.hermes/profiles/orchestrator/skills`).
*   **`$WORKSPACE_DIR`**: Resolves to the active codebase or target workspace directory where engineering work is performed (e.g., `/home/ubuntu/work`).
*   **`$APP_DATA_DIR`**: Resolves to the application's configuration and cache store (e.g., `/home/ubuntu/.gemini/antigravity-cli`).
*   **`$LOGS_DIR`**: Resolves to the engine's orchestrator log directories (e.g., `/home/ubuntu/.hermes/profiles/orchestrator/logs`).
*   **`$TMP_DIR`**: Resolves to the system temporary directory (e.g., `/tmp`).

#### Example templated snippet in SKILL.md:
```markdown
## Sandbox Path Resolution
When executing tools inside the isolated environment, ensure that you symlink the active project space:
```bash
ln -s $WORKSPACE_DIR/my-project ~/work
```
For deep-dive templates, access the stylesheets at:
`$SKILLS_DIR/human-design/hd-individual-deep-dive/templates/report.css`
```

### Manifest Schema (`manifest.yaml`)

Each portable skill includes a `manifest.yaml` at its root detailing metadata, environment needs, and load behaviors:

```yaml
name: "antigravity-cli-orchestration"
version: "1.0.6"
category: "agent-orchestration"
description: "Operate Google Antigravity CLI (agy) as a swarm orchestration tool."
install_path: "agent-orchestration/antigravity-cli-orchestration"

# Triggers define when the Prismatic Engine should inject this skill into the LLM's context window
triggers:
  - type: "command_prefix"
    pattern: "agy"
  - type: "workspace_file"
    pattern: "**/antigravity-cli/**"
  - type: "file_extension"
    pattern: "*.agy"

# System CLI utilities required by scripts or manual commands in the skill
required_tools:
  - name: "agy"
    version_requirement: ">=1.0.5"
  - name: "ffmpeg"
    version_requirement: "any"
  - name: "pandoc"
    version_requirement: "any"

# Dependencies on other Prismatic Engine skills
dependencies:
  - name: "prismatic-validation-pipeline"
    version: "^1.0.0"
  - name: "agy-subagent-communication"
    version: ">=2.0.0"
```

---

## 2. Export Pipeline

The export pipeline scans existing skill directories, strips system-specific paths, templates them with the logical environment variables, generates the `manifest.yaml` from frontmatter metadata, and validates the output.

### Python Export Script Design (`export_skill.py`)

```python
#!/usr/bin/env python3
import os
import re
import yaml
import shutil
import argparse

# Configurable mappings from most specific to least specific
PATH_REPLACEMENTS = [
    (r'/home/ubuntu/\.hermes/profiles/orchestrator/skills', '$SKILLS_DIR'),
    (r'/home/ubuntu/work', '$WORKSPACE_DIR'),
    (r'/home/ubuntu/\.gemini/antigravity-cli', '$APP_DATA_DIR'),
    (r'/home/ubuntu/\.hermes/profiles/orchestrator/logs', '$LOGS_DIR'),
    (r'/home/ubuntu', '$PRISMATIC_HOME'),
    (r'/tmp', '$TMP_DIR'),
]

def template_content(content: str) -> str:
    """Replaces hardcoded paths with standard Prismatic variables."""
    templated = content
    for pattern, placeholder in PATH_REPLACEMENTS:
        templated = re.sub(pattern, placeholder, templated)
    return templated

def parse_frontmatter(skill_md_path: str):
    """Extracts frontmatter metadata from the top of SKILL.md."""
    with open(skill_md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Matches markdown frontmatter blocks: --- \n metadata \n ---
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if not match:
        return {}, content
    
    metadata = yaml.safe_load(match.group(1))
    body = content[match.end():]
    return metadata, body

def validate_exported_file(filepath: str) -> bool:
    """Verifies that no residual hardcoded paths remain in the exported file."""
    suspicious_patterns = [
        r'/home/ubuntu',
        r'/Users/',
        r'/home/[a-zA-Z0-9_-]+/(?!(\.hermes|work|\.gemini))',
    ]
    
    is_valid = True
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for idx, line in enumerate(f, start=1):
            for pattern in suspicious_patterns:
                if re.search(pattern, line):
                    print(f"[VALIDATION WARNING] Hardcoded path candidate in {filepath}:{idx} -> {line.strip()}")
                    is_valid = False
    return is_valid

def export_skill(src_dir: str, dest_dir: str, category: str):
    skill_md_src = os.path.join(src_dir, 'SKILL.md')
    if not os.path.exists(skill_md_src):
        raise FileNotFoundError(f"Missing SKILL.md in {src_dir}")
        
    metadata, body = parse_frontmatter(skill_md_src)
    skill_name = metadata.get('name', os.path.basename(src_dir.rstrip('/')))
    
    target_skill_dir = os.path.join(dest_dir, category, skill_name)
    os.makedirs(target_skill_dir, exist_ok=True)
    
    # Write Templated SKILL.md
    templated_body = template_content(body)
    with open(os.path.join(target_skill_dir, 'SKILL.md'), 'w', encoding='utf-8') as f:
        f.write(f"---\nname: {skill_name}\ncategory: {category}\n---\n\n" + templated_body)
        
    # Generate manifest.yaml
    manifest = {
        'name': skill_name,
        'version': metadata.get('version', '1.0.0'),
        'category': category,
        'description': metadata.get('description', ''),
        'install_path': f"{category}/{skill_name}",
        'triggers': metadata.get('triggers', [{'type': 'command_prefix', 'pattern': skill_name}]),
        'required_tools': metadata.get('required_tools', []),
        'dependencies': metadata.get('dependencies', [])
    }
    
    with open(os.path.join(target_skill_dir, 'manifest.yaml'), 'w', encoding='utf-8') as f:
        yaml.safe_dump(manifest, f, default_flow_style=False)
        
    # Copy and template supporting directories (references, scripts)
    for folder in ['scripts', 'references']:
        src_folder = os.path.join(src_dir, folder)
        if os.path.exists(src_folder):
            dest_folder = os.path.join(target_skill_dir, folder)
            os.makedirs(dest_folder, exist_ok=True)
            for item in os.listdir(src_folder):
                src_item = os.path.join(src_folder, item)
                dest_item = os.path.join(dest_folder, item)
                if os.path.isfile(src_item):
                    with open(src_item, 'r', encoding='utf-8', errors='ignore') as fi:
                        file_content = fi.read()
                    templated_file_content = template_content(file_content)
                    with open(dest_item, 'w', encoding='utf-8') as fo:
                        fo.write(templated_file_content)
                elif os.path.isdir(src_item):
                    shutil.copytree(src_item, dest_item, dirs_exist_ok=True) # Binary assets copy directly
                    
    # Validation scan
    print(f"Validating export for {skill_name}...")
    success = True
    for root, _, files in os.walk(target_skill_dir):
        for file in files:
            if file.endswith(('.md', '.sh', '.py', '.yaml', '.json')):
                if not validate_exported_file(os.path.join(root, file)):
                    success = False
                    
    if success:
        print(f"[SUCCESS] Exported {skill_name} to {target_skill_dir} successfully.")
    else:
        print(f"[PARTIAL SUCCESS] Exported {skill_name} with validation warnings.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Export a local skill to the portable format.")
    parser.add_argument("--src", required=True, help="Source skill directory path")
    parser.add_argument("--dest", default="./portable-skills", help="Destination portable-skills/ package path")
    parser.add_argument("--category", required=True, help="Skill category (e.g. agent-orchestration)")
    args = parser.parse_args()
    export_skill(args.src, args.dest, args.category)
```

---

## 3. Import/Install Pipeline

Installing a portable skill involves checking prerequisites, verifying dependencies, resolving variables to the target system's paths, and safely managing file naming collisions.

### Installation Process Flow

1.  **Read Manifest**: The installer reads `manifest.yaml` from the package.
2.  **Verify Prerequisites**: 
    *   Iterates through `required_tools` and checks the local shell environment (`which <tool>` and parse version output). Raises warning or error if a dependency is missing.
    *   Checks `dependencies` list against already installed skills.
3.  **Conflict Check**: Inspects the destination path (`$SKILLS_DIR/<category>/<skill-name>`).
4.  **Write Files**: Copies files into the location, applying the chosen compilation policy.

### Conflict Resolution Strategy

When a destination path already contains files, the installer uses semantic versioning to resolve conflicts:

*   **Version Comparison**: If the installed skill has a lower version than the incoming skill, the installer executes an upgrade.
*   **Backup Mechanics**: Before writing changes, the existing directory is archived locally under `$SKILLS_DIR/.curator_backups/<skill-name>-<timestamp>.bak/` to allow seamless rollback.
*   **Force Flag**: If versions are equal, installation is skipped unless `--force` is passed, in which case it overwrites.
*   **Dry Run**: An optional `--dry-run` flag parses the operation and details changes without writing files.

### Variable Resolution Strategies

#### Option A: Runtime Resolution (Recommended)
The installer copies the files exactly as they are (containing `$WORKSPACE_DIR`, `$SKILLS_DIR`, etc.). When loading the skill context for the LLM during an active session, the Prismatic Engine dynamically interpolates the variables using its active environment mappings.
*   *Advantage*: Highly resilient to path shifts; if the user changes projects, `$WORKSPACE_DIR` automatically adapts to the new workspace path without reinstallation.

#### Option B: Installation-Time Compilation (Fallback)
The installer compiles the variables into absolute system paths matching the current target host environment.
*   *Advantage*: Works natively with legacy agents that read raw text documents directly from the local file system without an interpolation middleware.

---

## 4. Priority List (First 15 Skills to Export)

The following 15 skills represent high-value, highly utilized capabilities containing system-specific elements that will yield immediate portability benefits when exported:

### Category 1: Agent-Orchestration Skills

1.  **`antigravity-cli-orchestration`**
    *   *Description*: Guides the coordination of multiple agents, sandbox management, and model selection.
    *   *Path Mappings*: Replaces references to user home, `/tmp`, and `/home/ubuntu/work/agentic-swarm-ops` with `$APP_DATA_DIR` and `$WORKSPACE_DIR`.
    *   *Key Tools*: `agy`.
2.  **`antigravity-cli-session-recovery`**
    *   *Description*: Contains steps to recover suspended session logs, parse state indicators, and bypass terminal locking.
    *   *Path Mappings*: Templates paths mapping to CLI cache histories.
    *   *Key Tools*: `agy`, `screen`/`tmux`.
3.  **`agy-delegate-goals-not-tasks`**
    *   *Description*: Principles for agent goal decomposition, avoiding tight-looped execution blocks, and setting background tasks.
    *   *Path Mappings*: Templates sandbox workspace layouts.
    *   *Key Tools*: None.
4.  **`agy-subagent-communication`**
    *   *Description*: Details on formatting cross-agent notifications, context windows, and child-parent updates.
    *   *Path Mappings*: Replaces absolute database paths and IPC file locations.
    *   *Key Tools*: None.
5.  **`orchestrator-delegation-discipline`**
    *   *Description*: Establines rules for two-attempts-max failures, fallback routes, and escalation points.
    *   *Path Mappings*: Templates issue queue files and logs.
    *   *Key Tools*: None.

### Category 2: Content Skills

6.  **`baoyu-article-illustrator`**
    *   *Description*: System for parsing texts, creating infographics, and generating schemas.
    *   *Path Mappings*: Templates output directories where PDF templates and CSS assets are stored.
    *   *Key Tools*: `pandoc`, `librsvg`, `python3`.
7.  **`pixel-art`**
    *   *Description*: Automation pipelines for creating 16-bit sprite sheets and graphical assets using model rendering.
    *   *Path Mappings*: Replaces catalog directory structures.
    *   *Key Tools*: `imagemagick`, `python3`.
8.  **`manim-video`**
    *   *Description*: Workflow to code, preview, and render mathematical animation scripts.
    *   *Path Mappings*: Templates output video directory.
    *   *Key Tools*: `manim`, `ffmpeg`.
9.  **`songwriting-and-ai-music`**
    *   *Description*: Guides metadata formulation, lyric composition, and music file parsing.
    *   *Path Mappings*: Templates output audio locations.
    *   *Key Tools*: `ffmpeg`.
10. **`expert-interview-content-production`**
    *   *Description*: Pipeline to translate audio transcripts into structured technical articles.
    *   *Path Mappings*: Replaces transcript file paths and blog distribution paths.
    *   *Key Tools*: `python3`.

### Category 3: DevOps Skills

11. **`cloudflare-deployment`**
    *   *Description*: Configures cloud hosting workers, key-value routing tables, and builds.
    *   *Path Mappings*: Replaces project credential directories.
    *   *Key Tools*: `wrangler`.
12. **`kubernetes-gpu-llm-serving`**
    *   *Description*: Guides local deployment configurations for large language models on GPUs inside container pods.
    *   *Path Mappings*: Templates system configurations, driver libraries, and log volumes.
    *   *Key Tools*: `kubectl`, `helm`.
13. **`homelab-inventory-management`**
    *   *Description*: Scripts to monitor homelab system inventories, networking tables, and services.
    *   *Path Mappings*: Replaces hardcoded IP registries and network schema paths.
    *   *Key Tools*: `nmap`, `ssh`.
14. **`webhook-subscriptions`**
    *   *Description*: Coordinates continuous integration signals and webhook subscription handshakes.
    *   *Path Mappings*: Replaces SSL certificates and payload drop zones.
    *   *Key Tools*: `openssl`, `curl`.
15. **`kanban-orchestrator`**
    *   *Description*: Manages workflow lanes, issue synchronization, and task lists.
    *   *Path Mappings*: Templates output locations of project state boards.
    *   *Key Tools*: `git`.
