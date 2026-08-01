"""Python harness to run Uvicorn and Puppeteer visual test.
"""

import subprocess
import sys
import time

print("Starting Uvicorn Gateway server on port 8899...")
proc = subprocess.Popen(
    ["python", "-m", "uvicorn", "prismatic.gateway.server:app", "--port", "8899", "--host", "127.0.0.1"]
)
time.sleep(3)

try:
    print("Running Puppeteer visual audit script...")
    res = subprocess.run(
        ["node", "tests/run_pup_visual_test.js"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd="/home/ubuntu/work/prismatic-engine",
    )
    print("=== PUPPETEER STDOUT ===")
    print(res.stdout)
    if res.stderr:
        print("=== PUPPETEER STDERR ===")
        print(res.stderr)
    if res.returncode != 0:
        sys.exit(res.returncode)
finally:
    print("Shutting down Gateway server...")
    proc.terminate()
    proc.wait()
