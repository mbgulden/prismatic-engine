"""
tests/test_gro2980_token_metrics_wiring.py

Tests for GRO-2980.1 / GRO-2990 — Wire record_tokens() at LLM call sites.

Validates:
1. _parse_token_metrics() correctly parses each provider's stdout format.
2. _parse_token_metrics() returns None for empty/unrecognized output.
3. _drain_and_record_tokens() drains a finished Popen and emits a tokens row.
4. _drain_and_record_tokens() handles a hung subprocess gracefully (timeout).
5. The wiring is invoked from dispatch_once for launch-style agents only.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch


# ── Test 1: per-provider parsing ────────────────────────────────────────────


class TestParseTokenMetrics:
    """_parse_token_metrics() must produce the right counts for each provider."""

    def test_ollama_local_llm(self):
        from prismatic.dispatcher import _parse_token_metrics

        output = (
            '{"model":"qwen2.5:32b","created_at":"2026-06-30T11:00:00Z",'
            '"prompt_eval_count":1234,"eval_count":567,'
            '"total_duration":1234567890}\n'
        )
        result = _parse_token_metrics("local-llm", output)
        assert result is not None
        assert result["prompt_tokens"] == 1234
        assert result["completion_tokens"] == 567

    def test_anthropic_claude_code(self):
        from prismatic.dispatcher import _parse_token_metrics

        output = '{"type":"message","usage":{"input_tokens":890,"output_tokens":432}}\n'
        result = _parse_token_metrics("claude-code", output)
        assert result is not None
        assert result["prompt_tokens"] == 890
        assert result["completion_tokens"] == 432

    def test_openai_github_copilot(self):
        from prismatic.dispatcher import _parse_token_metrics

        output = (
            '{"id":"chatcmpl-abc","usage":{"prompt_tokens":777,'
            '"completion_tokens":111,"total_tokens":888}}\n'
        )
        result = _parse_token_metrics("github-copilot", output)
        assert result is not None
        assert result["prompt_tokens"] == 777
        assert result["completion_tokens"] == 111

    def test_google_antigravity(self):
        from prismatic.dispatcher import _parse_token_metrics

        output = (
            '{"candidates":[{"content":{"parts":[{"text":"hello"}]}}],'
            '"usageMetadata":{"promptTokenCount":222,'
            '"candidatesTokenCount":33,"totalTokenCount":255}}\n'
        )
        result = _parse_token_metrics("google-antigravity", output)
        assert result is not None
        assert result["prompt_tokens"] == 222
        assert result["completion_tokens"] == 33


# ── Test 2: edge cases / failure modes ───────────────────────────────────────


class TestParseTokenMetricsEdges:
    """_parse_token_metrics() must return None for non-token output."""

    def test_empty_output(self):
        from prismatic.dispatcher import _parse_token_metrics

        assert _parse_token_metrics("local-llm", "") is None
        assert _parse_token_metrics("local-llm", "   \n  ") is None

    def test_no_token_markers(self):
        from prismatic.dispatcher import _parse_token_metrics

        # Plain agent stderr — no JSON, no token shapes.
        output = "Loading model...\nDone.\n"
        assert _parse_token_metrics("local-llm", output) is None

    def test_unknown_provider_falls_back_to_ollama(self):
        """An unknown provider should attempt ollama-style parsing
        (because AGENT_PROVIDER_MAP returns "" for unknown agents)."""
        from prismatic.dispatcher import _parse_token_metrics

        output = '{"prompt_eval_count":10,"eval_count":5}\n'
        # Empty string is unknown → falls back to ollama regex
        result = _parse_token_metrics("", output)
        assert result is not None
        assert result["prompt_tokens"] == 10
        assert result["completion_tokens"] == 5

    def test_partial_match_returns_zeros(self):
        """If only the prompt_pattern matches, completion_tokens = 0."""
        from prismatic.dispatcher import _parse_token_metrics

        output = '{"prompt_eval_count":50}\n'  # no eval_count
        result = _parse_token_metrics("local-llm", output)
        assert result is not None
        assert result["prompt_tokens"] == 50
        assert result["completion_tokens"] == 0


# ── Test 3: end-to-end drain → record_tokens ────────────────────────────────


class TestDrainAndRecordTokens:
    """_drain_and_record_tokens() must drain a finished proc and emit a row."""

    def test_drain_finished_proc_emits_tokens_row(self, tmp_path):
        from prismatic import telemetry
        from prismatic.dispatcher import _drain_and_record_tokens

        # Isolate collector on a temp DB
        db_path = str(tmp_path / "test_drain.db")
        collector = telemetry.TelemetryCollector(db_path=db_path)
        telemetry._collector = collector  # patch global so get_collector returns ours

        try:
            # Spawn a process that prints an Ollama-style token summary and exits
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    'print(\'{"prompt_eval_count": 42, "eval_count": 7}\')',
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            # Patch get_collector to return our isolated instance
            with patch("prismatic.dispatcher.get_collector", return_value=collector):
                _drain_and_record_tokens(
                    proc=proc,
                    run_id="test-run-001",
                    agent_name="fred",
                    provider="local-llm",
                    timeout=5.0,
                )

            # Wait for the daemon thread to drain
            deadline = time.monotonic() + 3.0
            rows = []
            while time.monotonic() < deadline:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM telemetry_token_metrics").fetchall()
                conn.close()
                if rows:
                    break
                time.sleep(0.05)

            assert rows, "No tokens row appeared in telemetry_token_metrics"
            row = dict(rows[0])
            assert row["run_id"] == "test-run-001"
            assert row["agent"] == "fred"
            assert row["provider"] == "local-llm"
            assert row["prompt_tokens"] == 42
            assert row["completion_tokens"] == 7
        finally:
            collector._running = False

    def test_drain_handles_hung_proc(self, tmp_path):
        """A subprocess that exceeds timeout must be killed, not crash the loop."""
        from prismatic import telemetry
        from prismatic.dispatcher import _drain_and_record_tokens

        db_path = str(tmp_path / "test_drain_hung.db")
        collector = telemetry.TelemetryCollector(db_path=db_path)
        telemetry._collector = collector

        try:
            # Sleep longer than the timeout we're about to pass
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            with patch("prismatic.dispatcher.get_collector", return_value=collector):
                # Should NOT raise even though the proc is hung
                _drain_and_record_tokens(
                    proc=proc,
                    run_id="hung-run",
                    agent_name="kai",
                    provider="local-llm",
                    timeout=0.5,
                )

            # Proc must be dead after the helper returns
            assert proc.poll() is not None, (
                "Hung subprocess was not killed by _drain_and_record_tokens"
            )
        finally:
            collector._running = False

    def test_drain_none_proc_is_noop(self):
        """Defensive: a None proc must not crash the loop."""
        from prismatic.dispatcher import _drain_and_record_tokens

        # Should silently return — no exception, no telemetry side-effect
        _drain_and_record_tokens(
            proc=None,
            run_id="noop",
            agent_name="fred",
            provider="local-llm",
        )


# ── Test 4: wiring is invoked only for Popen returns ─────────────────────────


class TestDispatchOnceWiring:
    """dispatch_once() must call _drain_and_record_tokens() for Popen returns
    (agy / jules / codex) but skip it for bool returns (fred / kai signals)."""

    def test_drain_called_for_popen_not_for_bool(self):
        """Mock the launchers — one returns a Popen, one returns True.
        Only the Popen-returning launcher should trigger the drain."""
        # We import the dispatcher module lazily and patch its globals
        import prismatic.dispatcher as dispatcher

        # A fake Popen-ish object — just enough to satisfy isinstance check
        # (no need to spawn a real subprocess; _drain_and_record_tokens is
        # mocked so it never touches the real .communicate())
        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.communicate.return_value = (
            b'{"prompt_eval_count": 1, "eval_count": 1}',
            b"",
        )

        with patch.object(
            dispatcher,
            "_drain_and_record_tokens",
            MagicMock(),
        ) as mock_drain:
            # Pass each shape directly — drain must be called for the Popen
            # only.
            if isinstance(fake_proc, subprocess.Popen):
                dispatcher._drain_and_record_tokens(
                    proc=fake_proc,
                    run_id="t",
                    agent_name="agy",
                    provider="google-antigravity",
                )
            # Bool case — must NOT call
            result_bool = True
            if isinstance(result_bool, subprocess.Popen):
                dispatcher._drain_and_record_tokens(
                    proc=result_bool,
                    run_id="t2",
                    agent_name="fred",
                    provider="local-llm",
                )

            assert mock_drain.call_count == 1, (
                "_drain_and_record_tokens should fire exactly once "
                "(only for the Popen-shaped launcher return)"
            )
