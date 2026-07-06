# Linear completion sync

`LinearTaskProvider.complete_issue()` is the canonical provider-level helper for closing an agent completion loop against Linear. It performs three operations in order:

1. Fetch the issue's current labels and team workflow metadata.
2. Post a durable Markdown completion summary comment.
3. Transition the issue to the requested final state while applying a merge-based label update.

The label update is deliberately non-destructive. Existing labels are preserved unless their names are explicitly passed in `remove_label_names`; labels in `add_label_names` are resolved by name from the issue's team and appended. If an added label cannot be resolved, the helper returns `False` before posting the comment or updating the issue, so completion handlers do not accidentally clobber unrelated routing, priority, or project labels.

Typical peer-review handoff:

```python
provider.complete_issue(
    issue_id,
    "Implemented the requested change and ran focused verification.",
    final_state="In Review",
    add_label_names=["agent:peer-review"],
    remove_label_names=["agent:ned", "dispatch:ready"],
)
```

Callers that only need a state transition and comment can omit label changes; all labels are then preserved.
