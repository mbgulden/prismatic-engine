import sys
from pathlib import Path

pwp_dir = Path(__file__).resolve().parent
shipped_plugins_dir = pwp_dir.parent
engine_dir = shipped_plugins_dir.parent

for p in [str(engine_dir), str(shipped_plugins_dir)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import prismatic.shipped_plugins as shipped_plugins
    import prismatic.shipped_plugins.pwp as pwp_mod
    import prismatic.shipped_plugins.pwp.capabilities as caps_mod
    sys.modules["plugins"] = shipped_plugins
    sys.modules["plugins.pwp"] = pwp_mod
    sys.modules["plugins.pwp.capabilities"] = caps_mod
except Exception:
    pass
