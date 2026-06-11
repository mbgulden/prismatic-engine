# Prismatic Engine: Portable Skills Installation Guide

Follow these three simple steps to install any of the portable skills from this bundle into your local Hermes environment.

---

### Step 1: Locate Your Hermes Skills Directory

Determine the location of the skills folder for your active Hermes profile. 
* By default, the main profile skills directory is:
  ```bash
  ~/.hermes/skills/
  ```
* If you are using a custom profile (e.g., `orchestrator`), it will be located under:
  ```bash
  ~/.hermes/profiles/<profile_name>/skills/
  ```

*If the target category subdirectories do not exist yet, they will be created when copying in Step 2.*

---

### Step 2: Copy Skills to the Corresponding Category Folders

Copy the desired skill directories from this bundle into their respective category folders in your profile's skills directory. 

You can copy all 7 skills at once by running the following commands (replace `~/.hermes/skills` with your specific profile's skills path if using profiles):

```bash
# Define target skills folder
TARGET_SKILLS_DIR="$HOME/.hermes/skills"

# Create category directories if they do not exist
mkdir -p "$TARGET_SKILLS_DIR/agent-orchestration"
mkdir -p "$TARGET_SKILLS_DIR/orchestration"
mkdir -p "$TARGET_SKILLS_DIR/engineering"
mkdir -p "$TARGET_SKILLS_DIR/software-development"
mkdir -p "$TARGET_SKILLS_DIR/github"
mkdir -p "$TARGET_SKILLS_DIR/email"

# Copy each skill directory
cp -r orchestrator-delegation-discipline "$TARGET_SKILLS_DIR/agent-orchestration/"
cp -r autonomous-execution-discipline "$TARGET_SKILLS_DIR/agent-orchestration/"
cp -r golden-thread "$TARGET_SKILLS_DIR/orchestration/"
cp -r static-site-seo-fix "$TARGET_SKILLS_DIR/engineering/"
cp -r systematic-debugging "$TARGET_SKILLS_DIR/software-development/"
cp -r github-pr-workflow "$TARGET_SKILLS_DIR/github/"
cp -r himalaya "$TARGET_SKILLS_DIR/email/"
```

---

### Step 3: Verify and Enable the Installed Skills

Verify that Hermes successfully recognizes the newly added skills:

1. **List all active skills**:
   ```bash
   hermes skills list
   ```
   Look for the newly installed skills in the table. They should appear with a `local` source type and be `enabled`.

2. **Preload skills when launching a session**:
   You can now load a skill explicitly for a chat or CLI session using the `--skills` or `-s` flag:
   ```bash
   hermes --skills golden-thread,autonomous-execution-discipline
   ```
