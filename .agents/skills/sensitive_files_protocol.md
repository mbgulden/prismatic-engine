---
name: sensitive-files-protocol
description: "Establishes a secure, planning-first protocol for editing credentials (.env, .npmrc, .git-credentials, config.json) and wide-scoped filesystem writes. ACTIVATE this skill when modifications to credentials or system-level configuration files are requested."
---

# Antigravity Sensitive Files & Wide-Scoped Editing Protocol

This skill enforces a rigorous, planning-first safety protocol when editing sensitive files, credentials, or performing wide-scoped operations across the filesystem.

## Core Directives

### 1. Mandatory Pre-Edit Planning (Plan-First)
Before you read, edit, or create any credential file (`.env`, `.npmrc`, `.git-credentials`, `config.json`, etc.) or write to directories outside the immediate workspace, you MUST:
*   **Active Plan Reference**: Verify that this edit is explicitly listed as a step inside an active plan (`task.md` or `implementation_plan.md`). If no plan exists, you MUST create `task.md` first.
*   **Document Intent**: Document exactly *what* key or setting will be modified and *why* it is required for the goal.

### 2. Mandatory Automatic Backups
*   **Backup First**: Before calling a write/edit tool on a sensitive file, make a copy of the existing file to the scratch directory:
    *   Path: `C:\Users\Michael Gulden\.gemini\antigravity\scratch\<filename>.bak`
*   **Restoration Path**: If the write operation fails or causes a syntax error, immediately restore the file from the backup.

### 3. Redaction and Log Safety
*   **Never Print Secrets**: You must NEVER print full API keys, database passwords, tokens, or private key contents in the chat window, stdout, or artifacts.
*   **Masking format**: Always mask secrets when referencing them in text or summaries (e.g. `GEMINI_API_KEY = AIzaSyD...[masked]`).
*   **Temp Redaction**: If you must output a `.env` file structure to verify format, replace all secret values with placeholders like `[USER_SECRET_KEY]`.

### 4. Verification & Validation Protocol
After editing any credential or system-level configuration file:
*   **Self-Diff Check**: Verify that only the intended lines were modified. Check for accidental whitespace changes, trailing carriage returns, or syntax corruptions.
*   **Dry Run / Syntax Validate**:
    *   For `.json` files: Run a parser check (like `node -e "JSON.parse(fs.readFileSync('config.json'))"`) to ensure syntax validity.
    *   For `.env` files: Verify that lines follow the standard `KEY=VALUE` format without leading/trailing quotes unless required.
*   **Verify Clean State**: Test that the app or script loads correctly with the updated file without crashing or leaking details.
