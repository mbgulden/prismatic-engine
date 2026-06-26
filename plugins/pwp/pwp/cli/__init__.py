"""Minimal CLI shim for ``python -m pwp.cli.health``.

Real CLI lives in the PWP pipeline repo; this module only prints the
health check summary so operators can verify a fresh install.
"""

from __future__ import annotations

import json
import sys

from pwp.health import check, summarize


def main(argv=None) -> int:
    rows = check()
    summary = summarize(rows)
    print(json.dumps({"summary": summary, "rows": rows}, indent=2))
    return 0 if summary.get("fail", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))