"""prismatic_hello_world — canonical reference plugin package.

The directory MUST be named with underscores (not dashes) so Python's import
machinery can locate it. The PluginLoader auto-loads this plugin via the
``entry_point`` declared in ``plugin-manifest.yaml``; the import path is

    prismatic_hello_world.plugin:HelloWorldPlugin

If you copy this directory to make a new plugin, also rename the package
directory from ``prismatic_hello_world`` to ``your_plugin_name`` (underscores)
and update both the directory name and the ``entry_point`` field in the
manifest.

See ``README.md`` for the full walkthrough.
"""
