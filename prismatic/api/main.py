"""CLI entry point for Prismatic API Gateway.

Usage:
    python -m prismatic.api.main --port 8000 --reload
"""

from prismatic.api.server import run

if __name__ == "__main__":
    run()
