import threading
import time
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import importlib.util
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "agy_sandbox_event_supervisor.py"

def load_supervisor_module():
    spec = importlib.util.spec_from_file_location("agy_sandbox_event_supervisor", SUPERVISOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def test_supervisor_shutdown_waits_for_completion_and_labels_interrupted():
    sup_mod = load_supervisor_module()
    
    # Mock LinearClient
    mock_linear = MagicMock()
    
    class TestSupervisor(sup_mod.EventDrivenSupervisor):
        def __init__(self):
            self.shutdown_event = threading.Event()
            self.new_work_event = threading.Event()
            self.idle_event = threading.Event()
            self.workers = []
            self.linear_client = mock_linear

    supervisor = TestSupervisor()
    
    # Create mock running process
    mock_proc = MagicMock()
    # poll returns None initially (still running), then 0 (exited)
    mock_proc.poll.side_effect = [None, None, 0]
    
    # Inject active process
    with sup_mod._ACTIVE_PROCS_LOCK:
        sup_mod._ACTIVE_PROCS.clear()
        sup_mod._ACTIVE_PROCS["GRO-1234"] = mock_proc
        
    # We patch time.sleep to avoid actual delays in tests
    with patch("time.sleep") as mock_sleep:
        supervisor.shutdown()
        
        # Check that we polled the process and waited
        assert mock_proc.poll.call_count >= 2
        assert mock_sleep.call_count >= 1
        
    # Since the process finished during wait, it should NOT be terminated or labeled
    mock_proc.terminate.assert_not_called()
    mock_linear.add_labels.assert_not_called()

def test_supervisor_shutdown_kills_and_labels_stuck_processes():
    sup_mod = load_supervisor_module()
    
    # Mock LinearClient
    mock_linear = MagicMock()
    
    class TestSupervisor(sup_mod.EventDrivenSupervisor):
        def __init__(self):
            self.shutdown_event = threading.Event()
            self.new_work_event = threading.Event()
            self.idle_event = threading.Event()
            self.workers = []
            self.linear_client = mock_linear

    supervisor = TestSupervisor()
    
    # Create mock running process that stays running
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    
    # Inject active process
    with sup_mod._ACTIVE_PROCS_LOCK:
        sup_mod._ACTIVE_PROCS.clear()
        sup_mod._ACTIVE_PROCS["GRO-5678"] = mock_proc
        
    # Patch time.sleep and time.time to simulate timeout
    # We want time.time to advance past 60s
    time_values = [0.0]
    for i in range(150):
        time_values.append(time_values[-1] + 0.5) # advances 0.5s per iteration
        
    with patch("time.sleep") as mock_sleep, \
         patch("time.time", side_effect=time_values):
        supervisor.shutdown()
        
        # Verify it terminated the process
        mock_proc.terminate.assert_called_once()
        
        # Verify it added label 'supervisor:interrupted'
        mock_linear.add_labels.assert_called_once_with("GRO-5678", ["supervisor:interrupted"])
