#!/usr/bin/env python3
"""Executable wrapper for the strict task-admission AGY launcher."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prismatic.task_admission_agy_launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
