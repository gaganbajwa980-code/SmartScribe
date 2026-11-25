"""
SmartScribe Phase 5C.3.1: Browser Bridge Authentication & Protocol Engine.

Implements the single-session secret token security model, standardized
request/response correlation envelopes, timeout enforcement, and bridge coordination.

Key Invariants:
  1. Bridge Authentication:
     Protected by single-session secret token via X-SmartScribe-Token header.
     Validated strictly with constant-time comparison (hmac.compare_digest).
  2. Request Correlation:
     Every command has a UUID4 request_id. Responses must match request_id.
  3. Strict Boundaries & Timeouts:
     Payloads bounded to 64KB max. Timeouts clamped to 100ms - 10000ms.
  4. Truthful Verification Semantics:
     BrowserBridgeResponse defaults verified to False and postcondition_status to "unverified".
     Transport/execution success is NEVER reported as verified action success.
  5. Deterministic Lifecycle Model:
     If extension is disconnected or unreachable:
     STOP -> REPORT -> DO NOT GUESS.
     Never falls back silently to blind desktop coordinates or unanchored input.
"""

import collections
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List, Callable

from backend.assistant.interaction.browser_target_binding import BrowserTargetBinding

HEADER_SMARTSCRIBE_TOKEN = "X-SmartScribe-Token"
MAX_PAYLOAD_BYTES = 65536          # 64 KB max envelope payload
MIN_TIMEOUT_MS = 100.0            # 100 ms minimum timeout
MAX_TIMEOUT_MS = 10000.0          # 10 seconds maximum timeout
DEFAULT_TIMEOUT_MS = 3000.0       # 3 seconds default timeout


class BridgeStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    BUSY = "busy"
    ERROR = "error"


class BridgeTokenManager:
    """
    Manages the cryptographically secure single-session token for browser bridge authentication.
    Token is validated using constant-time comparison (hmac.compare_digest).
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._token = None
        return cls._instance

    def __init__(self):
        if self._token is None:
            self.generate_token()

    def generate_token(self) -> str:
        """Generates a high-entropy URL-safe 32-byte secret token."""
        self._token = secrets.token_urlsafe(32)
        return self._token

    def get_token(self) -> str:
        """Retrieves the active session token, generating one if not yet initialized."""
        if self._token is None:
            return self.generate_token()
        return self._token

    def validate_token(self, token_to_check: Optional[str]) -> bool:
        """
        Constant-time token validation.
        Returns False on mismatch, None, empty, or non-string input.
        """
        if not token_to_check or not isinstance(token_to_check, str):
            return False
        current = self.get_token()
        try:
            return hmac.compare_digest(current.encode("utf-8"), token_to_check.strip().encode("utf-8"))
        except Exception:
            return False

    def invalidate(self) -> None:
        """Invalidates current token (e.g., on server shutdown or reset)."""
        self._token = None


@dataclass
class BrowserBridgeRequest:
    """
    Standardized request envelope for browser bridge commands.
    Enforces UUID request_id correlation, bounded payload size (<= 64KB),
    and timeout clamping (100ms <= timeout_ms <= 10000ms).
    """
    action: str
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tab_id: Optional[int] = None
    frame_id: int = 0
    origin: Optional[str] = None
    target_binding: Optional[BrowserTargetBinding] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    timeout_ms: float = DEFAULT_TIMEOUT_MS

    def __post_init__(self):
        # Enforce timeout clamping
        if self.timeout_ms < MIN_TIMEOUT_MS:
            self.timeout_ms = MIN_TIMEOUT_MS
        elif self.timeout_ms > MAX_TIMEOUT_MS:
            self.timeout_ms = MAX_TIMEOUT_MS

        # Enforce payload size limit
        try:
            payload_str = json.dumps(self.payload)
            payload_bytes = len(payload_str.encode("utf-8"))
            if payload_bytes > MAX_PAYLOAD_BYTES:
                raise ValueError(
                    f"BrowserBridgeRequest payload size ({payload_bytes} bytes) "
                    f"exceeds maximum allowed {MAX_PAYLOAD_BYTES} bytes."
                )
        except (TypeError, OverflowError) as e:
            raise ValueError(f"BrowserBridgeRequest payload is not JSON-serializable: {e}")

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Returns True if the request's timeout window has elapsed."""
        current_time = now if now is not None else time.time()
        return ((current_time - self.timestamp) * 1000.0) > self.timeout_ms

    def to_dict(self) -> Dict[str, Any]:
        """Serializes request to dictionary."""
        return {
            "request_id": self.request_id,
            "action": self.action,
            "tab_id": self.tab_id,
            "frame_id": self.frame_id,
            "origin": self.origin,
            "target_binding": self.target_binding.to_dict() if self.target_binding else None,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "timeout_ms": self.timeout_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserBridgeRequest":
        """Deserializes request from dictionary."""
        tb_data = data.get("target_binding")
        tb = BrowserTargetBinding.from_dict(tb_data) if tb_data else None
        return cls(
            request_id=str(data.get("request_id", uuid.uuid4())),
            action=str(data["action"]),
            tab_id=data.get("tab_id"),
            frame_id=int(data.get("frame_id", 0)),
            origin=data.get("origin"),
            target_binding=tb,
            payload=data.get("payload") or {},
            timestamp=float(data.get("timestamp", time.time())),
            timeout_ms=float(data.get("timeout_ms", DEFAULT_TIMEOUT_MS)),
        )


@dataclass
class BrowserBridgeResponse:
    """
    Standardized response envelope from browser bridge execution.
    MANDATORY INVARIANT: verified defaults to False. Execution success != verified success.
    """
    request_id: str
    success: bool
    status: str  # passed | stale_target | origin_denied | policy_denied | timeout | disconnected | error
    verified: bool = False  # Invariant: transport/execution success != postcondition verification
    postcondition_status: str = "unverified"
    observed_value: Optional[Any] = None
    execution_latency_ms: float = 0.0
    verification_latency_ms: float = 0.0
    error: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes response to dictionary."""
        return {
            "request_id": self.request_id,
            "success": self.success,
            "status": self.status,
            "verified": self.verified,
            "postcondition_status": self.postcondition_status,
            "observed_value": self.observed_value,
            "execution_latency_ms": self.execution_latency_ms,
            "verification_latency_ms": self.verification_latency_ms,
            "error": self.error,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserBridgeResponse":
        """Deserializes response from dictionary."""
        return cls(
            request_id=str(data["request_id"]),
            success=bool(data["success"]),
            status=str(data.get("status", "unknown")),
            verified=bool(data.get("verified", False)),
            postcondition_status=str(data.get("postcondition_status", "unverified")),
            observed_value=data.get("observed_value"),
            execution_latency_ms=float(data.get("execution_latency_ms", 0.0)),
            verification_latency_ms=float(data.get("verification_latency_ms", 0.0)),
            error=data.get("error"),
            payload=data.get("payload") or {},
        )


class BrowserBridge:
    """
    Coordinator and transport manager for the browser bridge.
    Handles single-session authentication, request queueing, response correlation,
    and deterministic connection/lifecycle management.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, token_mgr: Optional[BridgeTokenManager] = None):
        if getattr(self, "_initialized", False):
            return
        self._token_mgr = token_mgr or BridgeTokenManager()
        self._state_lock = threading.Lock()
        self._pending_requests: Dict[str, Dict[str, Any]] = {}
        self._dispatch_queue: collections.deque = collections.deque()
        self._queue_condition = threading.Condition(self._state_lock)
        self._last_heartbeat: float = 0.0
        self._extension_info: Dict[str, Any] = {}
        self.heartbeat_threshold_seconds: float = 15.0
        self._direct_handler: Optional[Callable[[BrowserBridgeRequest], BrowserBridgeResponse]] = None
        self._initialized = True

    @property
    def token_manager(self) -> BridgeTokenManager:
        return self._token_mgr

    def register_extension(self, info: Dict[str, Any]) -> bool:
        """Registers connected extension capabilities and updates heartbeat."""
        with self._state_lock:
            self._extension_info = dict(info or {})
            self._last_heartbeat = time.time()
        return True

    def heartbeat(self, info: Optional[Dict[str, Any]] = None) -> None:
        """Updates last seen timestamp for the extension connection."""
        with self._state_lock:
            self._last_heartbeat = time.time()
            if info:
                self._extension_info.update(info)

    def is_connected(self, now: Optional[float] = None) -> bool:
        """
        Checks whether extension is currently considered connected.
        Returns False if heartbeat is older than heartbeat_threshold_seconds.
        """
        current_time = now if now is not None else time.time()
        with self._state_lock:
            if self._last_heartbeat <= 0.0:
                return False
            return (current_time - self._last_heartbeat) < self.heartbeat_threshold_seconds

    def register_direct_handler(
        self,
        handler: Optional[Callable[[BrowserBridgeRequest], BrowserBridgeResponse]]
    ) -> None:
        """Injects a direct execution handler for synchronous testing."""
        with self._state_lock:
            self._direct_handler = handler

    def send_request(self, request: BrowserBridgeRequest) -> BrowserBridgeResponse:
        """
        Dispatches a structured request across the bridge and waits for the correlated response.
        Enforces:
          - Fast failure if bridge is disconnected (STOP -> REPORT -> DO NOT GUESS)
          - Expiry / timeout bounds
          - Correlation by request_id
        """
        start_time = time.time()

        # 1. Direct handler bypass for testing
        with self._state_lock:
            handler = self._direct_handler

        if handler is not None:
            try:
                resp = handler(request)
                if resp.request_id != request.request_id:
                    resp.request_id = request.request_id
                return resp
            except Exception as e:
                return BrowserBridgeResponse(
                    request_id=request.request_id,
                    success=False,
                    status="error",
                    error=f"Direct handler exception: {e}",
                    execution_latency_ms=(time.time() - start_time) * 1000.0,
                )

        # 2. Connection check: STOP -> REPORT -> DO NOT GUESS
        if not self.is_connected():
            return BrowserBridgeResponse(
                request_id=request.request_id,
                success=False,
                status="disconnected",
                error="Browser extension bridge is disconnected (STOP -> REPORT -> DO NOT GUESS).",
                execution_latency_ms=(time.time() - start_time) * 1000.0,
            )

        # 3. Enqueue request and wait on event
        event = threading.Event()
        with self._queue_condition:
            self._pending_requests[request.request_id] = {
                "request": request,
                "event": event,
                "response": None,
                "enqueued_at": time.time(),
            }
            self._dispatch_queue.append(request)
            self._queue_condition.notify_all()

        # 4. Wait for response or timeout
        timeout_sec = request.timeout_ms / 1000.0
        finished = event.wait(timeout=timeout_sec)

        with self._state_lock:
            entry = self._pending_requests.pop(request.request_id, None)

        if not finished or entry is None or entry.get("response") is None:
            return BrowserBridgeResponse(
                request_id=request.request_id,
                success=False,
                status="timeout",
                error=f"Browser bridge request timed out after {request.timeout_ms:.1f}ms.",
                execution_latency_ms=(time.time() - start_time) * 1000.0,
            )

        resp = entry["response"]
        if resp.execution_latency_ms <= 0.0:
            resp.execution_latency_ms = (time.time() - start_time) * 1000.0
        return resp

    def poll_request(self, timeout_sec: float = 0.0) -> Optional[BrowserBridgeRequest]:
        """
        Called by the extension polling endpoint to retrieve the next pending request.
        Waits up to timeout_sec for a request to become available.
        Automatically purges expired requests.
        """
        timeout_sec = max(0.0, min(timeout_sec, 5.0))
        end_time = time.time() + timeout_sec

        with self._queue_condition:
            while True:
                # Update heartbeat on poll activity
                self._last_heartbeat = time.time()

                while self._dispatch_queue:
                    req = self._dispatch_queue.popleft()
                    # Check if already expired while sitting in queue
                    if req.is_expired():
                        entry = self._pending_requests.pop(req.request_id, None)
                        if entry:
                            entry["response"] = BrowserBridgeResponse(
                                request_id=req.request_id,
                                success=False,
                                status="timeout",
                                error="Request expired in bridge queue before dispatch.",
                            )
                            entry["event"].set()
                        continue
                    return req

                remaining = end_time - time.time()
                if remaining <= 0.0:
                    return None
                self._queue_condition.wait(timeout=remaining)

    def receive_response(self, response: BrowserBridgeResponse) -> bool:
        """
        Receives a response posted by the extension and correlates it to the waiting request.
        Returns True if the response was correlated, or False if no matching request was waiting.
        """
        with self._state_lock:
            entry = self._pending_requests.get(response.request_id)
            if not entry:
                return False
            entry["response"] = response
            entry["event"].set()
            return True

    def pending_count(self) -> int:
        """Returns the number of requests currently pending response."""
        with self._state_lock:
            return len(self._pending_requests)

    def queue_size(self) -> int:
        """Returns the number of requests queued for extension polling."""
        with self._state_lock:
            return len(self._dispatch_queue)

    def reset(self) -> None:
        """Resets bridge coordinator state (for testing)."""
        with self._queue_condition:
            # Wake up and cancel all pending
            for req_id, entry in list(self._pending_requests.items()):
                entry["response"] = BrowserBridgeResponse(
                    request_id=req_id,
                    success=False,
                    status="disconnected",
                    error="Bridge reset by test harness.",
                )
                entry["event"].set()
            self._pending_requests.clear()
            self._dispatch_queue.clear()
            self._last_heartbeat = 0.0
            self._extension_info.clear()
            self._direct_handler = None


def get_browser_bridge() -> BrowserBridge:
    """Convenience accessor for the BrowserBridge singleton."""
    return BrowserBridge()
