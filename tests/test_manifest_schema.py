import pytest
import logging
from prismatic.interface.manifest_schema import validate_manifest
from prismatic.interface.plugin import PluginValidationError

@pytest.fixture
def base_manifest():
    return {
        "name": "test-plugin",
        "version": "1.0.0",
        "entry_point": "test_plugin.plugin:TestPlugin",
        "core_version_constraint": ">=1.0.0",
    }

def test_valid_base_manifest(base_manifest):
    # Should not raise any error
    validate_manifest(base_manifest)

def test_invalid_name(base_manifest):
    base_manifest["name"] = "InvalidName"
    with pytest.raises(PluginValidationError, match="Plugin name .* must contain only alphanumeric lowercase"):
        validate_manifest(base_manifest)

def test_modes_valid(base_manifest):
    for mode in ["headless", "interactive", "both"]:
        base_manifest["modes"] = mode
        validate_manifest(base_manifest)

def test_modes_invalid(base_manifest):
    base_manifest["modes"] = "invalid_mode"
    with pytest.raises(PluginValidationError, match="Field 'modes' must be one of"):
        validate_manifest(base_manifest)

def test_ui_valid(base_manifest):
    base_manifest["ui"] = {
        "surfaces": ["web", "chat"],
        "web": True,
        "chat": {"theme": "dark"},
        "interrupt_points": ["before_init"],
        "header": "Custom Header"
    }
    validate_manifest(base_manifest)

def test_ui_invalid_keys(base_manifest):
    base_manifest["ui"] = {
        "surfaces": ["web"],
        "invalid_key": True
    }
    with pytest.raises(PluginValidationError, match="Field 'ui' contains invalid keys"):
        validate_manifest(base_manifest)

def test_ui_invalid_types(base_manifest):
    base_manifest["ui"] = {
        "surfaces": "not-a-list"
    }
    with pytest.raises(PluginValidationError, match="Field 'ui.surfaces' must be a list of strings"):
        validate_manifest(base_manifest)

def test_permissions_valid(base_manifest):
    base_manifest["permissions"] = {
        "network": ["api.github.com"],
        "filesystem": True,
        "secrets": ["API_KEY"],
        "bus": ["event_a"]
    }
    validate_manifest(base_manifest)

def test_permissions_invalid_keys(base_manifest):
    base_manifest["permissions"] = {
        "network": True,
        "invalid_perm": False
    }
    with pytest.raises(PluginValidationError, match="Field 'permissions' contains invalid keys"):
        validate_manifest(base_manifest)

def test_permissions_invalid_types(base_manifest):
    base_manifest["permissions"] = {
        "network": [123]  # not strings
    }
    with pytest.raises(PluginValidationError, match="Field 'permissions.network' must be a boolean, dictionary, string, or list of strings"):
        validate_manifest(base_manifest)

def test_dependencies_valid(base_manifest):
    base_manifest["dependencies"] = {
        "pip": ["requests>=2.0.0"],
        "plugins": ["other-plugin>=1.0.0"],
        "system": ["curl"]
    }
    validate_manifest(base_manifest)

def test_dependencies_invalid_keys(base_manifest):
    base_manifest["dependencies"] = {
        "pip": [],
        "invalid_dep": []
    }
    with pytest.raises(PluginValidationError, match="Field 'dependencies' contains invalid keys"):
        validate_manifest(base_manifest)

def test_dependencies_invalid_types(base_manifest):
    base_manifest["dependencies"] = {
        "pip": "not-a-list"
    }
    with pytest.raises(PluginValidationError, match="Field 'dependencies.pip' must be a list of strings"):
        validate_manifest(base_manifest)

def test_events_published_subscribed_valid(base_manifest):
    base_manifest["events_published"] = ["event.a", "event.b"]
    base_manifest["events_subscribed"] = ["event.c"]
    validate_manifest(base_manifest)

def test_events_published_invalid_types(base_manifest):
    base_manifest["events_published"] = "not-a-list"
    with pytest.raises(PluginValidationError, match="Field 'events_published' must be a list of strings"):
        validate_manifest(base_manifest)

def test_events_subscribed_invalid_types(base_manifest):
    base_manifest["events_subscribed"] = ["event.c", 123]
    with pytest.raises(PluginValidationError, match="Field 'events_subscribed' must be a list of strings"):
        validate_manifest(base_manifest)

def test_legacy_interactive_mode_warning(base_manifest, caplog):
    base_manifest["interactive_mode"] = True
    with caplog.at_level(logging.WARNING):
        validate_manifest(base_manifest)
    assert any("interactive_mode" in message for message in caplog.messages)
