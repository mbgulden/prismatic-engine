import sys
from pathlib import Path

# Repo root on sys.path so `prismatic.shipped_plugins.pwp.*` imports resolve
# however the test harness is invoked. (The old `plugins.pwp` namespace and
# its sys.modules aliasing shim were removed in the #376 rescue; nothing
# references them anymore.)
pwp_dir = Path(__file__).resolve().parent
engine_dir = pwp_dir.parent.parent.parent

if str(engine_dir) not in sys.path:
    sys.path.insert(0, str(engine_dir))
