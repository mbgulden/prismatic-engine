"""Regression: tier A focused gate must classify .py diffs by CONTENT.

PR #649 (comment-only TTL truth fix to ``prismatic/lock.py`` and
``prismatic/core/locking.py``) failed the tier A focused gate: the
extension-based docs exemption covers only non-.py files, so a
comment-only ``.py`` edit still flowed to the fail-closed
"no test files resolved" branch (no ``test_lock.py`` /
``test_locking.py`` exists). The gate must treat a comment- or
docstring-only ``.py`` change as "no test surface" (pass) while a
real code change to the same file still fails closed (#619 invariant).

Fix: ``VerificationWorker._run_focused_check`` now classifies each
changed ``.py`` file via ``_is_code_change`` (code-token multiset of
base vs new source, comments/docstrings stripped), and the CI gate
driver feeds it the unified diff + real merge-base.

These tests build a real git repo per case (so ``git show <base>:<path>``
works, exactly like CI), commit a base, mutate the file, and run the
focused check with the real ``git diff`` output.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from prismatic.review_factory.verifier import VerificationWorker

# Base module: module docstring, a comment-annotated constant, and a
# function with its own docstring. Deliberately has NO matching
# test_locky_module.py anywhere in the fixture repo, so the fail-closed
# branch is reachable for real code changes.
_BASE_MODULE = (
    "'''Lock fixture module.'''\n"
    "\n"
    "TTL_MS = 3_600_000  # lock TTL\n"
    "\n"
    "\n"
    "def acquire() -> bool:\n"
    "    '''Acquire the lock.'''\n"
    "    return True\n"
)

# (a) comment reword + module docstring reword: zero code-token change.
_COMMENT_ONLY_MODULE = (
    "'''Lock fixture module — corrected TTL truth.'''\n"
    "\n"
    "TTL_MS = 3_600_000  # lock TTL: 1 hour, not 5 minutes\n"
    "\n"
    "\n"
    "def acquire() -> bool:\n"
    "    '''Acquire the lock (blocking).'''\n"
    "    return True\n"
)

# (b) value change: a NUMBER token change.
_CODE_ONLY_MODULE = (
    "'''Lock fixture module.'''\n"
    "\n"
    "TTL_MS = 3_600_001  # lock TTL\n"
    "\n"
    "\n"
    "def acquire() -> bool:\n"
    "    '''Acquire the lock.'''\n"
    "    return True\n"
)

# (c) comment reword AND a real code change (return False).
_MIXED_MODULE = (
    "'''Lock fixture module.'''\n"
    "\n"
    "TTL_MS = 3_600_000  # lock TTL: 1 hour, not 5 minutes\n"
    "\n"
    "\n"
    "def acquire() -> bool:\n"
    "    '''Acquire the lock.'''\n"
    "    return False\n"
)

# (d) base and variant that swap two constant *values*. Identical code-token
# multiset, different *order* — a real behavior change a multiset comparison
# would misclassify as "not code".
_SWAP_BASE = "'''Lock fixture module.'''\n\nA = 1\nB = 2\n"
_SWAP_MODULE = "'''Lock fixture module.'''\n\nA = 2\nB = 1\n"


# ── Fixture helpers ──────────────────────────────────────────────────


def _init_repo(path: Path, module_src: str) -> str:
    """Init a git repo at *path* with the module committed.

    Returns the base commit SHA.
    """
    path.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "rf-test@example.com")
    git("config", "user.name", "RF Test")
    (path / "locky_module.py").write_text(module_src, encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    return git("rev-parse", "HEAD")


def _run_focused(path: Path, base: str) -> object:
    """Mutate the module file and run the focused check on it.

    The job carries the real full-context ``git diff`` (base...worktree)
    and the real base commit — the same shape the CI gate driver builds
    (``--unified=99999``).
    """
    worker = VerificationWorker(repo_path=path)
    changed = [
        p
        for p in subprocess.run(
            ["git", "-C", str(path), "diff", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if p
    ]
    diff_text = subprocess.run(
        ["git", "-C", str(path), "diff", "--unified=99999"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    job = SimpleNamespace(
        changed_paths_json=json.dumps(changed),
        diff_text=diff_text,
        base_commit=base,
    )
    return worker._run_focused_check(job)


def _mutate(path: Path, new_src: str) -> None:
    (path / "locky_module.py").write_text(new_src, encoding="utf-8")


# ── (a) comment/docstring-only .py change -> PASS ────────────────────


class TestCommentOnlyExemption:
    def test_comment_and_docstring_only_py_change_passes(self, tmp_path: Path):
        """PR #649 shape: word-level comment reword, TTL constant unchanged.

        Must pass the focused check (no test surface) instead of failing
        closed on the missing test_locky_module.py.
        """
        base = _init_repo(tmp_path, _BASE_MODULE)
        _mutate(tmp_path, _COMMENT_ONLY_MODULE)
        res = _run_focused(tmp_path, base)
        assert res.passed is True, res.stderr
        assert res.exit_code == 0

    def test_docstring_only_py_change_passes(self, tmp_path: Path):
        """Rewording only the function docstring also carries no code change."""
        base = _init_repo(tmp_path, _BASE_MODULE)
        _mutate(
            tmp_path,
            _BASE_MODULE.replace(
                "'''Acquire the lock.'''", "'''Acquire the lock (non-blocking).'''"
            ),
        )
        res = _run_focused(tmp_path, base)
        assert res.passed is True, res.stderr


# ── (b)/(c) code changes still FAIL-CLOSED (#619 invariant) ──────────


class TestFailClosedPreserved:
    def test_code_only_py_change_without_test_still_fails_closed(self, tmp_path: Path):
        """(b) A real value change with no resolving test must FAIL."""
        base = _init_repo(tmp_path, _BASE_MODULE)
        _mutate(tmp_path, _CODE_ONLY_MODULE)
        res = _run_focused(tmp_path, base)
        assert res.passed is False
        assert res.exit_code == 1
        assert "FAIL-CLOSED" in res.stderr
        assert "locky_module.py" in res.stderr

    def test_mixed_code_and_comment_change_without_test_still_fails_closed(
        self, tmp_path: Path
    ):
        """(c) Comment reword PLUS a code change: the code change wins."""
        base = _init_repo(tmp_path, _BASE_MODULE)
        _mutate(tmp_path, _MIXED_MODULE)
        res = _run_focused(tmp_path, base)
        assert res.passed is False
        assert res.exit_code == 1
        assert "FAIL-CLOSED" in res.stderr
        assert "locky_module.py" in res.stderr

    def test_token_swap_change_without_test_still_fails_closed(self, tmp_path: Path):
        """(d) A=1,B=2 -> A=2,B=1 value swap is a real code change.

        Gate-level guard for the #619 invariant: an order-insensitive
        (multiset) comparison would treat this as "no code change" and
        pass it, defeating the fail-closed guarantee. The ordered
        comparison must route it to FAIL-CLOSED.
        """
        base = _init_repo(tmp_path, _SWAP_BASE)
        _mutate(tmp_path, _SWAP_MODULE)
        res = _run_focused(tmp_path, base)
        assert res.passed is False
        assert res.exit_code == 1
        assert "FAIL-CLOSED" in res.stderr
        assert "locky_module.py" in res.stderr

    def test_no_diff_available_still_fails_closed(self, tmp_path: Path):
        """Daemon/tmp_path shape: no unified diff -> extension-only fallback.

        Preserves the #619 behavior for jobs that carry no diff text at
        all (existing TestFocusedCheckFailClosed relies on this shape).
        """
        base = _init_repo(tmp_path, _BASE_MODULE)
        _mutate(tmp_path, _CODE_ONLY_MODULE)
        worker = VerificationWorker(repo_path=tmp_path)
        job = SimpleNamespace(
            changed_paths_json=json.dumps(["locky_module.py"]),
            diff_text="",
            base_commit=base,
        )
        res = worker._run_focused_check(job)
        assert res.passed is False
        assert res.exit_code == 1


# ── Unit level: the classifier itself ────────────────────────────────


class TestIsCodeChange:
    def _repo(self, tmp_path: Path, src: str) -> tuple[Path, str]:
        base = _init_repo(tmp_path, src)
        return tmp_path, base

    def test_comment_only_is_not_code(self, tmp_path: Path):
        from prismatic.review_factory.verifier import _is_code_change

        path, base = self._repo(tmp_path, _BASE_MODULE)
        _mutate(path, _COMMENT_ONLY_MODULE)
        diff = subprocess.run(
            ["git", "-C", str(path), "diff"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert _is_code_change(path, base, diff, "locky_module.py") is False

    def test_code_change_is_code(self, tmp_path: Path):
        from prismatic.review_factory.verifier import _is_code_change

        path, base = self._repo(tmp_path, _BASE_MODULE)
        _mutate(path, _CODE_ONLY_MODULE)
        diff = subprocess.run(
            ["git", "-C", str(path), "diff"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert _is_code_change(path, base, diff, "locky_module.py") is True

    def test_mixed_change_is_code(self, tmp_path: Path):
        from prismatic.review_factory.verifier import _is_code_change

        path, base = self._repo(tmp_path, _BASE_MODULE)
        _mutate(path, _MIXED_MODULE)
        diff = subprocess.run(
            ["git", "-C", str(path), "diff"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert _is_code_change(path, base, diff, "locky_module.py") is True

    def test_token_swap_is_code(self, tmp_path: Path):
        """(d) A pure value swap is a real code change, not 'comment-only'.

        A = 1, B = 2 vs A = 2, B = 1 have an identical code-token
        *multiset* but a different *order*. An order-insensitive
        comparison would misclassify this as "not code" and let the
        change through the fail-closed gate; the ordered comparison
        must catch it.
        """
        from prismatic.review_factory.verifier import _is_code_change

        path, base = self._repo(tmp_path, _SWAP_BASE)
        _mutate(path, _SWAP_MODULE)
        diff = subprocess.run(
            ["git", "-C", str(path), "diff"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert _is_code_change(path, base, diff, "locky_module.py") is True

    def test_new_file_fails_closed(self, tmp_path: Path):
        """No base blob (file did not exist at base) -> treat as code."""
        from prismatic.review_factory.verifier import _is_code_change

        path, base = self._repo(tmp_path, _BASE_MODULE)
        diff = subprocess.run(
            ["git", "-C", str(path), "diff"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert _is_code_change(path, base, diff, "brand_new_module.py") is True

    def test_unparseable_new_source_fails_closed(self, tmp_path: Path):
        """Broken Python (e.g. mid-edit) -> cannot prove comment-only."""
        from prismatic.review_factory.verifier import _is_code_change

        path, base = self._repo(tmp_path, _BASE_MODULE)
        _mutate(path, "def broken(:\n    # oops\n")
        diff = subprocess.run(
            ["git", "-C", str(path), "diff"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert _is_code_change(path, base, diff, "locky_module.py") is True

    def test_non_py_path_is_never_code(self, tmp_path: Path):
        from prismatic.review_factory.verifier import _is_code_change

        path, base = self._repo(tmp_path, _BASE_MODULE)
        assert _is_code_change(path, base, "some diff text", "README.md") is False


# ── The diff-reconstruction path (real CI shape) ─────────────────────
#
# In CI the verifier materializes only the candidate tree into a plain
# read-only directory (no .git), so the base blob is UNREACHABLE and the
# full-context unified diff is the classifier's only content source.
# These drive ``_is_code_change`` with a placeholder base commit to force
# exactly that path — the one the base-blob tests above cannot reach.


class TestDiffReconstructionPath:
    def _full_diff(self, path: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(path), "diff", "--unified=99999"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def test_comment_only_via_diff_is_not_code(self, tmp_path: Path):
        from prismatic.review_factory.verifier import _is_code_change

        _init_repo(tmp_path, _BASE_MODULE)
        _mutate(tmp_path, _COMMENT_ONLY_MODULE)
        diff = self._full_diff(tmp_path)
        # Placeholder base forces the reconstruction path (no git blob).
        assert _is_code_change(tmp_path, "0" * 40, diff, "locky_module.py") is False

    def test_code_only_via_diff_is_code(self, tmp_path: Path):
        from prismatic.review_factory.verifier import _is_code_change

        _init_repo(tmp_path, _BASE_MODULE)
        _mutate(tmp_path, _CODE_ONLY_MODULE)
        diff = self._full_diff(tmp_path)
        assert _is_code_change(tmp_path, "0" * 40, diff, "locky_module.py") is True

    def test_mixed_via_diff_is_code(self, tmp_path: Path):
        from prismatic.review_factory.verifier import _is_code_change

        _init_repo(tmp_path, _BASE_MODULE)
        _mutate(tmp_path, _MIXED_MODULE)
        diff = self._full_diff(tmp_path)
        assert _is_code_change(tmp_path, "0" * 40, diff, "locky_module.py") is True

    def test_token_swap_via_diff_is_code(self, tmp_path: Path):
        from prismatic.review_factory.verifier import _is_code_change

        _init_repo(tmp_path, _SWAP_BASE)
        _mutate(tmp_path, _SWAP_MODULE)
        diff = self._full_diff(tmp_path)
        assert _is_code_change(tmp_path, "0" * 40, diff, "locky_module.py") is True

    def test_no_newline_at_eof_comment_only_via_diff_is_not_code(self, tmp_path: Path):
        """Regression: a file with no trailing newline makes git emit a
        ``\\ No newline at end of file`` marker line. That marker is not
        content; the reconstruction must skip it or the line counts no
        longer match the hunk header and the whole classification fails
        closed (a comment-only edit would be misclassified as code).
        """
        from prismatic.review_factory.verifier import _is_code_change

        _init_repo(tmp_path, "X = 1  # lock TTL\nY = 2")  # no trailing NL
        _mutate(tmp_path, "X = 1  # lock TTL: 1 hour\nY = 2")  # comment reword
        diff = self._full_diff(tmp_path)
        # Sanity: the marker is really present in this diff.
        assert "No newline at end of file" in diff
        assert _is_code_change(tmp_path, "0" * 40, diff, "locky_module.py") is False
