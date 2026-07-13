import pytest
import threading
import time
from prismatic.interface.plugin import PluginValidationError
from prismatic.interface.manifest_schema import validate_manifest
from prismatic.interface.ui import (
    NullUIHandle,
    UIBusHandle,
    NoUIHostError,
    UIBus,
    UIBusEvent,
)

def test_null_ui_handle():
    handle = NullUIHandle()
    # emit is a no-op, shouldn't raise any errors
    handle.emit({"type": "log", "message": "hello"})
    
    # request_input should raise NoUIHostError
    with pytest.raises(NoUIHostError, match="No UI host is attached"):
        handle.request_input(
            prompt_id="test",
            prompt="Hello?",
            schema={"type": "object"},
        )

def test_ui_bus_handle_emit():
    bus = UIBus()
    handle = UIBusHandle(pipeline_id="pipe-123", bus=bus)
    
    handle.emit({"type": "log", "message": "hello"})
    assert not bus.empty()
    event = bus.get()
    assert isinstance(event, UIBusEvent)
    assert event.type == "log"
    assert event.pipeline_id == "pipe-123"
    assert event.payload == {"type": "log", "message": "hello"}

def test_ui_bus_handle_request_input_success():
    bus = UIBus()
    handle = UIBusHandle(pipeline_id="pipe-123", bus=bus)
    
    response_data = {"approved": True}
    
    def simulate_ui_host():
        # Wait until event is in queue
        while bus.empty():
            time.sleep(0.01)
        event = bus.get()
        assert event.type == "input_request"
        assert event.payload["prompt_id"] == "test_prompt"
        # Respond back
        handle.receive_response("test_prompt", response_data)
        
    thread = threading.Thread(target=simulate_ui_host)
    thread.start()
    
    res = handle.request_input(
        prompt_id="test_prompt",
        prompt="Approve?",
        schema={"type": "object"},
        timeout_seconds=2,
    )
    thread.join()
    assert res == response_data

def test_ui_bus_handle_request_input_timeout_default():
    bus = UIBus()
    handle = UIBusHandle(pipeline_id="pipe-123", bus=bus)
    
    # Timeout with default should return default
    res = handle.request_input(
        prompt_id="test_prompt",
        prompt="Approve?",
        schema={"type": "object"},
        timeout_seconds=1,
        default={"approved": False},
    )
    assert res == {"approved": False}

def test_ui_bus_handle_request_input_timeout_error():
    bus = UIBus()
    handle = UIBusHandle(pipeline_id="pipe-123", bus=bus)
    
    # Timeout without default should raise TimeoutError
    with pytest.raises(TimeoutError, match="Input request 'test_prompt' timed out"):
        handle.request_input(
            prompt_id="test_prompt",
            prompt="Approve?",
            schema={"type": "object"},
            timeout_seconds=1,
        )

def test_manifest_validation_modes_list():
    # Valid string and list modes
    manifest_list = {
        "name": "test-plugin",
        "version": "1.0.0",
        "entry_point": "test_plugin.plugin:TestPlugin",
        "core_version_constraint": ">=1.0.0",
        "modes": ["headless", "interactive"],
    }
    validate_manifest(manifest_list)

    manifest_single_list = {
        "name": "test-plugin",
        "version": "1.0.0",
        "entry_point": "test_plugin.plugin:TestPlugin",
        "core_version_constraint": ">=1.0.0",
        "modes": ["headless"],
    }
    validate_manifest(manifest_single_list)

    # Invalid list elements
    manifest_invalid = {
        "name": "test-plugin",
        "version": "1.0.0",
        "entry_point": "test_plugin.plugin:TestPlugin",
        "core_version_constraint": ">=1.0.0",
        "modes": ["headless", "invalid"],
    }
    with pytest.raises(PluginValidationError, match="Field 'modes' must be one of"):
        validate_manifest(manifest_invalid)

def test_manifest_validation_ui_rich():
    manifest = {
        "name": "test-plugin",
        "version": "1.0.0",
        "entry_point": "test_plugin.plugin:TestPlugin",
        "core_version_constraint": ">=1.0.0",
        "ui": {
            "surfaces": [
                {"kind": "panel", "id": "build_log", "title": "Build log"},
                {"kind": "command_palette", "id": "actions", "title": "Actions"},
            ],
            "interrupt_points": [
                {
                    "stage": "build_site",
                    "prompt": "Approve?",
                    "schema": {"type": "object"},
                }
            ],
            "web": {"base_path": "/plugins/pwp"},
            "chat": [{"adapter": "telegram", "command_prefix": "/pwp"}],
            "header": [{"label": "Issue", "from": "context.issue_id"}],
        }
    }
    validate_manifest(manifest)
