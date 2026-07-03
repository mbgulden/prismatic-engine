import sqlite3
import sys
import time
from pathlib import Path

from prismatic.core.registry import PluginLoader
from prismatic.interface.plugin import PluginContext
from prismatic.telemetry import TelemetryCollector


def _wait_for_rows(
    db_path: Path, expected: int
) -> list[tuple[str, str, int, str | None]]:
    deadline = time.time() + 5
    rows: list[tuple[str, str, int, str | None]] = []
    while time.time() < deadline:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """
            SELECT plugin_name, version, success, error
            FROM telemetry_plugin_registered
            ORDER BY id
            """
        ).fetchall()
        conn.close()
        if len(rows) >= expected:
            return rows
        time.sleep(0.05)
    return rows


def test_plugin_loader_records_registration_telemetry(tmp_path):
    db_path = tmp_path / "telemetry.db"
    collector = TelemetryCollector(db_path=str(db_path))

    plugins_dir = tmp_path / "plugins"
    plugin_dir = plugins_dir / "sample_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("")
    (plugin_dir / "plugin.py").write_text(
        "from prismatic.interface.plugin import PluginContext, PrismaticPlugin\n"
        "class SamplePlugin(PrismaticPlugin):\n"
        "    def on_init(self, context: PluginContext) -> None:\n"
        "        return None\n"
        "    def register_tools(self):\n"
        "        return []\n"
    )
    (plugin_dir / "plugin-manifest.yaml").write_text(
        "name: sample-plugin\n"
        "version: 1.2.3\n"
        "entry_point: sample_plugin.plugin:SamplePlugin\n"
        "core_version_constraint: '>=1.0.0, <2.0.0'\n"
    )

    if str(plugins_dir) not in sys.path:
        sys.path.insert(0, str(plugins_dir))

    loader = PluginLoader(core_version="1.0.0", plugins_dir=str(plugins_dir))
    context = PluginContext(
        config={},
        db_connection=None,
        state_dir=str(tmp_path),
        telemetry_client=collector,
        lock_manager=None,
    )
    loader.scan_and_load_plugins(context)

    rows = _wait_for_rows(db_path, expected=1)
    assert rows == [("sample-plugin", "1.2.3", 1, None)]
