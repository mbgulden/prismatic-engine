import threading
import time
import pytest
from pathlib import Path
import importlib.util
import sys

# Import supervisor module dynamically
REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "agy_sandbox_event_supervisor.py"

def load_supervisor_module():
    spec = importlib.util.spec_from_file_location("agy_sandbox_event_supervisor", SUPERVISOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def test_wait_for_completion_exits_on_shutdown():
    sup_mod = load_supervisor_module()
    
    class MockSupervisor(sup_mod.EventDrivenSupervisor):
        def __init__(self):
            self.max_concurrent = 1
            self.model = "mock-model"
            self.token_pool = None
            self.launch_jitter_range = (0.0, 0.0)
            self.backoff_range = (0.0, 0.0)
            self.lane_mode = "off"
            self.active_project = "all"
            self.backlog_age_days = 30
            self.long_run = True
            
            # Events
            self.shutdown_event = threading.Event()
            self.new_work_event = threading.Event()
            self.idle_event = threading.Event()
            self.idle_event.set() # Start in idle state
            self.workers = []
            
            # Mocks for other attributes to prevent errors
            self.completed_issues = set()
            self.results = []
            self.results_lock = threading.Lock()
            self.active_count = 0
            self.active_lock = threading.Lock()
            
        def shutdown(self):
            self.shutdown_event.set()
            self.new_work_event.set()
            self.idle_event.set()

    supervisor = MockSupervisor()
    
    def run_wait():
        supervisor.wait_for_completion(idle_timeout=2.0, max_cap_sec=2.0, long_run=True)
        
    t = threading.Thread(target=run_wait)
    t.start()
    
    # Let it run for a brief moment
    time.sleep(0.2)
    assert t.is_alive(), "Supervisor exited too early without shutdown signal"
    
    # Trigger shutdown
    supervisor.shutdown()
    
    # Wait for the thread to exit. It should exit almost immediately.
    t.join(timeout=2.0)
    assert not t.is_alive(), "Supervisor wait_for_completion did not exit on shutdown signal"
