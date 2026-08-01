"""Unit test for W1 counter-discipline log updater."""

import tempfile
from pathlib import Path

from pe.deploy.counter_hook import record_counter_discipline_move


def test_record_counter_discipline_move():
    with tempfile.TemporaryDirectory() as tmp_dir:
        count_file = Path(tmp_dir) / "proactive-count.json"

        entry = record_counter_discipline_move(
            action="test_move",
            details={"test": True},
            count_file=count_file,
        )

        assert entry["n"] == 1
        assert entry["action"] == "test_move"
        assert count_file.exists()

        entry2 = record_counter_discipline_move(
            action="test_move_2",
            count_file=count_file,
        )
        assert entry2["n"] == 2
