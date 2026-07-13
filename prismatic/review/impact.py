"""High-impact task classification logic.

Reference: okf/operations/phase2-quality-gates-plan.md (Gap 8)
"""

from __future__ import annotations


def is_high_impact(task: dict) -> bool:
    """Determine if a task requires peer review.

    High-impact criteria:
    - Has 'priority:high' or 'priority:urgent' label
    - Cross-repo changes (modifies files in 2+ repos)
    - Infra changes (modifies prismatic/, deploy/, infra/)
    - New feature implementation (type:feature)
    """
    if not isinstance(task, dict):
        return False

    # Extract labels
    labels = set()
    labels_raw = task.get("labels") or []
    if isinstance(labels_raw, dict):
        labels_raw = labels_raw.get("nodes") or []
    
    for label in labels_raw:
        if isinstance(label, str):
            labels.add(label.lower())
        elif isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str):
                labels.add(name.lower())

    # 1. Has 'priority:high' or 'priority:urgent' label
    if "priority:high" in labels or "priority:urgent" in labels:
        return True
    
    # Optional field check fallback
    priority_val = task.get("priority")
    if isinstance(priority_val, str) and priority_val.lower() in ("high", "urgent"):
        return True

    # 4. New feature implementation (type:feature)
    if "type:feature" in labels:
        return True
    
    type_val = task.get("type")
    if isinstance(type_val, str) and type_val.lower() == "feature":
        return True

    # 2. Cross-repo changes (modifies files in 2+ repos)
    repos = set()
    
    # Direct check of repos or repositories key
    for key in ("repos", "repositories", "repositories_modified", "repos_modified"):
        val = task.get(key)
        if isinstance(val, (list, set, tuple)):
            repos.update(str(x).lower() for x in val if x)
        elif isinstance(val, str):
            repos.add(val.lower())

    # Check files / paths to extract repository names
    for key in ("files", "changed_files", "modified_files", "paths"):
        val = task.get(key)
        if isinstance(val, dict):
            repos.update(str(k).lower() for k in val.keys())
        elif isinstance(val, (list, set, tuple)):
            for item in val:
                if isinstance(item, dict):
                    repo_name = item.get("repo") or item.get("repository") or item.get("repo_name")
                    if repo_name:
                        repos.add(str(repo_name).lower())
                elif isinstance(item, str):
                    if ":" in item:
                        repos.add(item.split(":", 1)[0].lower())
                        continue
                    
                    p = item.replace("\\", "/").strip("/")
                    parts = p.split("/")
                    if len(parts) > 1:
                        # Exclude common single-repo directories to prevent treating them as separate repos
                        known_repos = {
                            "prismatic-engine", "prismatic-engine-site", "prismatic-engine-staging",
                            "prismatic-hub-ui", "prismatic-merge", "prismatic-pe-native-crons",
                            "prismatic-pwp-ubersuggest-auth", "prismatic-web-plugin",
                            "repo_a", "repo_b", "repo_c", "repo-a", "repo-b", "repo1", "repo2",
                            "repository-a", "repository-b"
                        }
                        if parts[0].lower() in known_repos:
                            repos.add(parts[0].lower())
                        elif parts[0].lower() not in ("prismatic", "tests", "deploy", "infra", "scripts", "config", "ops", "plugins", "src", "app"):
                            repos.add(parts[0].lower())

    if len(repos) >= 2:
        return True

    # 3. Infra changes (modifies prismatic/, deploy/, infra/)
    for key in ("files", "changed_files", "modified_files", "paths"):
        val = task.get(key)
        if isinstance(val, dict):
            for files_list in val.values():
                if isinstance(files_list, (list, set, tuple)):
                    for f in files_list:
                        if isinstance(f, str):
                            p_normalized = f.replace("\\", "/").strip("/")
                            parts = p_normalized.split("/")
                            if any(part in ("prismatic", "deploy", "infra") for part in parts):
                                return True
        elif isinstance(val, (list, set, tuple)):
            for item in val:
                path_str = None
                if isinstance(item, dict):
                    path_str = item.get("path") or item.get("file") or item.get("filepath")
                elif isinstance(item, str):
                    path_str = item
                
                if isinstance(path_str, str):
                    p_normalized = path_str.replace("\\", "/").strip("/")
                    parts = p_normalized.split("/")
                    if any(part in ("prismatic", "deploy", "infra") for part in parts):
                        return True

    return False
