import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

class NoUIHostError(Exception):
    """Raised when request_input is called but no UI host is attached."""
    pass

@dataclass
class UIBusEvent:
    type: str
    pipeline_id: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

class UIBus:
    """Thread-safe event bus for UI events."""
    def __init__(self) -> None:
        self._queue: queue.Queue[UIBusEvent] = queue.Queue()

    def put(self, event: UIBusEvent) -> None:
        self._queue.put(event)

    def get(self, timeout: Optional[float] = None) -> UIBusEvent:
        return self._queue.get(timeout=timeout)

    def empty(self) -> bool:
        return self._queue.empty()

class UIHandle:
    """Base interface for UI interactions within plugins."""
    def emit(self, event: Dict[str, Any]) -> None:
        """Publishes a UI-relevant event."""
        pass

    def request_input(
        self,
        prompt_id: str,
        prompt: str,
        schema: Dict[str, Any],
        timeout_seconds: Optional[int] = None,
        default: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Requests input from the attached UI host. Blocks until a response is received."""
        raise NoUIHostError("No UI host is attached to handle input requests.")

class NullUIHandle(UIHandle):
    """Default no-op implementation of UIHandle."""
    pass

class UIBusHandle(UIHandle):
    """UI interaction handle that publishes events and blocks for input via a UIBus."""
    def __init__(self, pipeline_id: str, bus: UIBus):
        self.pipeline_id = pipeline_id
        self.bus = bus
        self._pending_inputs: Dict[str, tuple[threading.Event, Optional[Dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def emit(self, event: Dict[str, Any]) -> None:
        bus_event = UIBusEvent(
            type=event.get("type", "generic"),
            pipeline_id=self.pipeline_id,
            payload=event
        )
        self.bus.put(bus_event)

    def request_input(
        self,
        prompt_id: str,
        prompt: str,
        schema: Dict[str, Any],
        timeout_seconds: Optional[int] = None,
        default: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = threading.Event()
        with self._lock:
            self._pending_inputs[prompt_id] = (event, None)

        bus_event = UIBusEvent(
            type="input_request",
            pipeline_id=self.pipeline_id,
            payload={
                "prompt_id": prompt_id,
                "prompt": prompt,
                "schema": schema,
                "timeout_seconds": timeout_seconds,
                "default": default,
            }
        )
        self.bus.put(bus_event)

        timeout = timeout_seconds if timeout_seconds is not None else 600
        signaled = event.wait(timeout=timeout)

        with self._lock:
            _, response = self._pending_inputs.pop(prompt_id, (None, None))

        if not signaled or response is None:
            if default is not None:
                return default
            raise TimeoutError(f"Input request '{prompt_id}' timed out after {timeout} seconds.")
        return response

    def receive_response(self, prompt_id: str, response: Dict[str, Any]) -> None:
        with self._lock:
            if prompt_id in self._pending_inputs:
                event, _ = self._pending_inputs[prompt_id]
                self._pending_inputs[prompt_id] = (event, response)
                event.set()
