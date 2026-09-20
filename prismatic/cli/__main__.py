"""Main execution entry point when running `python -m prismatic.cli`."""

import sys
from prismatic.cli import run

if __name__ == "__main__":
    sys.exit(run())
