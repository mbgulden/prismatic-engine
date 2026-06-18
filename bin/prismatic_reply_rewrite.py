#!/usr/bin/env python3
"""prismatic-reply — the Prismatic Engine post-processor wrapper.

Pipe any text through this and the rewriter shipped with the engine
will turn /home/... paths into clickable files.growthwebdev.com links.

This module is a thin wrapper around the engine's rewriter binary. It
exists so that "import prismatic_reply" works in any environment that
adds the engine bin/ directory to PYTHONPATH. No harness required.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if __name__ == "__main__":
    runpy.run_path(str(HERE / "prismatic_rewrite_paths.py"), run_name="__main__")
