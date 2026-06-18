#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — Git Pre-Commit Hook
# Handles Python linting, YAML validation, shell checking, and path portability.
# ==============================================================================
set -euo pipefail

echo "🔍 [Prismatic Commit Gate] Running pre-commit validation..."

# 1. Gather staged files (excluding deletions)
STAGED_FILES=$(git diff --cached --name-only --diff-filter=d)
if [[ -z "$STAGED_FILES" ]]; then
    echo "✅ No staged files to validate."
    exit 0
fi

# 2. Hardcoded path validation (Portability check)
# Enforces that no runtime files reference hardcoded directories like /home/ubuntu
VIOLATING_FILES=()
for file in $STAGED_FILES; do
    # Only scan source and configuration files
    if [[ "$file" =~ \.(py|sh|js|yaml|yml|json)$ ]]; then
        # Exclude governance, config, hook script files which legitimately
        # reference absolute paths for validation purposes
        if [[ "$file" == "PRISMATIC_ENGINE.yaml" || "$file" =~ ^config/ || "$file" =~ ^scripts/ || "$file" =~ ^portable-skills/ ]]; then
            continue
        fi

        if grep -F "/home/ubuntu" "$file" > /dev/null; then
            echo "❌ Path Portability Failure: Absolute path '/home/ubuntu' found in $file"
            VIOLATING_FILES+=("$file")
        fi
    fi
done

if [[ ${#VIOLATING_FILES[@]} -gt 0 ]]; then
    echo "🚨 Commit aborted. Hardcoded paths detected. Use environment variables (e.g. \$PRISMATIC_HOME) or relative paths."
    exit 1
fi
echo "✅ Path portability check passed."

# 3. Lint Gates (best-effort — tools may not be installed)
WARNINGS=0

# Python Checks (ruff)
PYTHON_FILES=$(echo "$STAGED_FILES" | grep -E '\.py$' || true)
if [[ -n "$PYTHON_FILES" ]]; then
    if command -v ruff &>/dev/null; then
        echo "🐍 Linting Python files with ruff..."
        ruff check $PYTHON_FILES || WARNINGS=$((WARNINGS + 1))

        echo "🧹 Checking formatting with ruff format..."
        ruff format --check $PYTHON_FILES || WARNINGS=$((WARNINGS + 1))
    else
        echo "⚠️  ruff not installed — skipping Python lint. Install: pip install ruff"
    fi
fi

# YAML Checks (yamllint)
YAML_FILES=$(echo "$STAGED_FILES" | grep -E '\.(yaml|yml)$' || true)
if [[ -n "$YAML_FILES" ]]; then
    if command -v yamllint &>/dev/null; then
        echo "⚙️  Validating YAML configurations..."
        yamllint $YAML_FILES || WARNINGS=$((WARNINGS + 1))
    else
        echo "⚠️  yamllint not installed — skipping YAML validation. Install: pip install yamllint"
    fi
fi

# Shell Checks (shellcheck)
SHELL_FILES=$(echo "$STAGED_FILES" | grep -E '\.sh$' || true)
if [[ -n "$SHELL_FILES" ]]; then
    if command -v shellcheck &>/dev/null; then
        echo "🐚 Checking shell scripts with shellcheck..."
        shellcheck $SHELL_FILES || WARNINGS=$((WARNINGS + 1))
    else
        echo "⚠️  shellcheck not installed — skipping shell check. Install: apt install shellcheck"
    fi
fi

if [[ $WARNINGS -gt 0 ]]; then
    echo "⚠️  [Prismatic Commit Gate] $WARNINGS lint warning(s). Review before pushing."
else
    echo "✅ [Prismatic Commit Gate] All lint gates passed successfully."
fi
exit 0
