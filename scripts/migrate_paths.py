#!/usr/bin/env python3
"""
Path Migration Script — GRO-1508
Replaces hardcoded /home/ubuntu paths in skill files with $PRISMATIC_HOME references.

Conservative: skips tool binary paths (.local/bin/) and NAS mounts (mounts/).
Run: python3 /tmp/migrate_paths.py [--dry-run]
"""

import os
import sys
import re
from pathlib import Path

SKILLS_DIR = Path(os.environ.get('PRISMATIC_HOME', '/home/ubuntu')) / '.hermes' / 'profiles' / 'orchestrator' / 'skills'
DRY_RUN = '--dry-run' in sys.argv

# Paths to NEVER change
SKIP_PATTERNS = [
    r'/home/ubuntu/\.local/bin/',      # Tool binaries (agy, jules, codex)
    r'/home/ubuntu/mounts/',            # NAS mounts
    r'/home/ubuntu/\.antigravity/',     # AGY home (different from PRISMATIC_HOME)
]

def should_skip_line(line):
    """Check if this line contains a path that should NOT be migrated."""
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, line):
            return True
    return False

def replace_in_file(filepath):
    """Replace /home/ubuntu with $PRISMATIC_HOME in a single file."""
    try:
        with open(filepath, 'r') as f:
            original = f.read()
    except Exception as e:
        return False, str(e)
    
    if '/home/ubuntu' not in original:
        return False, "no matches"
    
    lines = original.split('\n')
    new_lines = []
    in_code_block = False
    code_block_lang = ''
    changes = 0
    
    for line in lines:
        if line.strip().startswith('```'):
            if in_code_block:
                # Closing code block
                in_code_block = False
                code_block_lang = ''
            else:
                # Opening code block
                in_code_block = True
                code_block_lang = line.strip()[3:].strip().lower()
            new_lines.append(line)
            continue
        
        if '/home/ubuntu' not in line:
            new_lines.append(line)
            continue
        
        if should_skip_line(line):
            new_lines.append(line)
            continue
        
        # Apply replacement
        if in_code_block and 'bash' in code_block_lang:
            # Shell: use ${PRISMATIC_HOME}
            new_line = line.replace('/home/ubuntu', '${PRISMATIC_HOME}')
        elif in_code_block and ('python' in code_block_lang or 'py' in code_block_lang):
            # Python: use os.environ.get — but only for obvious path strings
            # Be conservative: only replace if in a string context
            new_line = re.sub(
                r'["\']/home/ubuntu(/[^"\']*)?["\']',
                lambda m: f'os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "{m.group(1) or ""}"',
                line
            )
        else:
            # Markdown/text: use $PRISMATIC_HOME
            new_line = line.replace('/home/ubuntu', '$PRISMATIC_HOME')
        
        if new_line != line:
            changes += 1
        new_lines.append(new_line)
    
    new_content = '\n'.join(new_lines)
    
    if new_content == original:
        return False, "no effective changes"
    
    if not DRY_RUN:
        # Backup
        backup = str(filepath) + '.bak'
        with open(backup, 'w') as f:
            f.write(original)
        
        with open(filepath, 'w') as f:
            f.write(new_content)
        
        # Remove backup if successful
        os.remove(backup)
    
    return True, f"{changes} lines changed"


def main():
    if not SKILLS_DIR.exists():
        print(f"ERROR: Skills directory not found: {SKILLS_DIR}")
        sys.exit(1)
    
    print(f"Scanning skills in: {SKILLS_DIR}")
    print(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print()
    
    total_files = 0
    changed_files = 0
    total_changes = 0
    errors = []
    
    for root, dirs, files in os.walk(SKILLS_DIR):
        # Skip .git, __pycache__, node_modules
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('__pycache__', 'node_modules')]
        
        for filename in files:
            if filename.endswith('.bak'):
                continue
            filepath = Path(root) / filename
            total_files += 1
            
            changed, msg = replace_in_file(str(filepath))
            if changed:
                changed_files += 1
                try:
                    total_changes += int(msg.split()[0])
                except:
                    total_changes += 1
                print(f"  ✓ {filepath.relative_to(SKILLS_DIR)} — {msg}")
            elif msg.startswith('Error'):
                errors.append((str(filepath.relative_to(SKILLS_DIR)), msg))
    
    print()
    print(f"=== Migration Report ===")
    print(f"Files scanned: {total_files}")
    print(f"Files changed: {changed_files}")
    print(f"Total line changes: {total_changes}")
    
    if errors:
        print(f"Errors: {len(errors)}")
        for f, e in errors:
            print(f"  ✗ {f}: {e}")
    
    if DRY_RUN:
        print("\n[DRY RUN] No files were modified. Run without --dry-run to apply changes.")
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
