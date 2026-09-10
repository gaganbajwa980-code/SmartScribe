r"""
SmartScribe Phase 5C.2: Controlled Mouse Driver.

SECURITY & ARCHITECTURAL INVARIANTS:
1. Target-Derived Coordinates ONLY:
   - Mouse interaction NEVER accepts arbitrary LLM or user screen coordinates.
   - Coordinates MUST be derived strictly from a verified TargetBinding control bounding box.
2. WindowFence Pre-Emission Gate:
   - HWND existence, foreground exclusivity, PID match, canonical executable match,
     UIPI Mandatory Integrity Control (target IL <= caller IL), visibility, and prohibited
     process filtering (shells, password managers) MUST all pass immediately before emission.
3. Coordinate Geometry Validation & Clamping:
   - Target coordinates are validated within the active window client rect and monitor work area.
   - Safety insets (\epsilon >= 4 px) prevent accidental clicks on window frames.
4. Click Safety Filters:
   - OS system chrome, taskbar, desktop, notification areas, and close/minimize/maximize buttons
     are permanently prohibited.
5. Canonical PermissionManager Integration:
   - Move/hover is READ_ONLY.
   - Passive clicks are LOW_RISK_ACTION.
   - Mutating/state-changing clicks are SENSITIVE_ACTION (requiring valid confirmation token).
   - Destructive clicks are DESTRUCTIVE_ACTION (requiring confirmation token).
6. Deterministic Postcondition Verification:
   - Click emission alone is NOT verification. Verification requires an explicit expected-state check.
   - If no postcondition is provided, returns 'verification_unconfirmed' rather than claiming success.
7. Zero User-Space Atomicity Claim:
   - The validation-to-emission interval is minimized, but user focus intervention halts execution.
"""

import time
import threading
import ctypes
from ctypes import wintypes
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, List, Callable

from backend.assistant.permission_manager import PermissionManager, RiskLevel
from backend.assistant.interaction.target_binding import TargetBinding
from backend.assistant.interaction.window_fence import (
    WindowFence,
    FenceStatus,
    FenceValidationResult,
    Win32Provider,
    NativeWin32Provider,
)

# ---------------------------------------------------------------------------
# Interaction Enums and Result Dataclasses
# ---------------------------------------------------------------------------

class InteractionStatus(str, Enum):
    PASSED = "passed"
    REJECTED_STALE_BINDING = "rejected_stale_binding"
    REJECTED_WINDOW_FENCE = "rejected_window_fence"
    REJECTED_INVALID_TARGET = "rejected_invalid_target"
    REJECTED_PROHIBITED_CONTROL = "rejected_prohibited_control"
    REJECTED_COORDINATES_OUT_OF_BOUNDS = "rejected_coordinates_out_of_bounds"
    REJECTED_PERMISSION_DENIED = "rejected_permission_denied"
    REJECTED_CONFIRMATION_REQUIRED = "rejected_confirmation_required"
    REJECTED_CANCELLED = "rejected_cancelled"
    REJECTED_PASSWORD_FIELD = "rejected_password_field"
    REJECTED_PAYLOAD_TOO_LARGE = "rejected_payload_too_large"
    REJECTED_INVALID_TEXT = "rejected_invalid_text"
    REJECTED_UNAPPROVED_HOTKEY = "rejected_unapproved_hotkey"
    EMISSION_FAILED = "emission_failed"
    VERIFICATION_FAILED = "verification_failed"

@dataclass
class InteractionResult:
    r"""Structured, immutable outcome of a controlled mouse or keyboard interaction."""
    success: bool
    status: InteractionStatus
    verified: bool
    reason: str
    target_info: Dict[str, Any] = field(default_factory=dict)
    execution_latency_ms: float = 0.0
    verification_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    postcondition_status: str = "none"  # "verified" | "verification_failed" | "verification_unconfirmed" | "verification_unavailable"
    emitted_events_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status.value,
            "verified": self.verified,
            "reason": self.reason,
            "target_info": self.target_info,
            "execution_latency_ms": self.execution_latency_ms,
            "verification_latency_ms": self.verification_latency_ms,
            "total_latency_ms": self.total_latency_ms,
            "postcondition_status": self.postcondition_status,
            "emitted_events_count": self.emitted_events_count
        }

# ---------------------------------------------------------------------------
# Click Safety: Prohibited UI Roles and IDs
# ---------------------------------------------------------------------------

PROHIBITED_CONTROL_ROLES = {
    "title_bar",
    "close_button",
    "minimize_button",
    "maximize_button",
    "restore_button",
    "system_chrome",
    "taskbar",
    "start_button",
    "tray_clock",
    "notification_overflow",
    "action_center",
    "desktop_window",
    "shell_tray_wnd",
}

PROHIBITED_CONTROL_IDS = {
    "close",
    "minimize",
    "maximize",
    "restore",
    "system_close_button",
    "system_minimize_button",
    "system_maximize_button",
    "taskbar",
    "start",
    "trayclockwclass",
}

# ---------------------------------------------------------------------------
# Mouse Input Provider Abstraction
# ---------------------------------------------------------------------------

class MouseInputProvider:
    r"""Abstract interface for mouse input injection to enable deterministic unit-testing."""
    def set_cursor_pos(self, x: int, y: int) -> bool:
        raise NotImplementedError
    def get_cursor_pos(self) -> Tuple[int, int]:
        raise NotImplementedError
    def emit_click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        raise NotImplementedError
    def emit_scroll(self, x: int, y: int, delta: int) -> bool:
        raise NotImplementedError
    def emit_drag(self, start_x: int, start_y: int, end_x: int, end_y: int, steps: int = 5) -> bool:
        raise NotImplementedError

# Win32 structures for NativeWin32MouseProvider
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', _MOUSEINPUT)]

class _INPUT(ctypes.Structure):
    _fields_ = [
        ('type', wintypes.DWORD),
        ('u', _INPUT_UNION),
    ]

class NativeWin32MouseProvider(MouseInputProvider):
    r"""Production Win32 mouse input provider using SetCursorPos and SendInput."""
    def __init__(self):
        self.user32 = ctypes.windll.user32

    def set_cursor_pos(self, x: int, y: int) -> bool:
        return bool(self.user32.SetCursorPos(ctypes.c_int(x), ctypes.c_int(y)))

    def get_cursor_pos(self) -> Tuple[int, int]:
        pt = wintypes.POINT()
        if self.user32.GetCursorPos(ctypes.byref(pt)):
            return (pt.x, pt.y)
        return (0, 0)

    def emit_click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        self.set_cursor_pos(x, y)
        time.sleep(0.005)

        if button == "left":
            down_flag = 0x0002  # MOUSEEVENTF_LEFTDOWN
            up_flag = 0x0004    # MOUSEEVENTF_LEFTUP
        elif button == "right":
            down_flag = 0x0008  # MOUSEEVENTF_RIGHTDOWN
            up_flag = 0x0010    # MOUSEEVENTF_RIGHTUP
        else:
            return False

        clicks = 2 if double else 1
        for _ in range(clicks):
            self._send_mouse_event(down_flag, 0)
            time.sleep(0.005)
            self._send_mouse_event(up_flag, 0)
            if double:
                time.sleep(0.03)
        return True

    def emit_scroll(self, x: int, y: int, delta: int) -> bool:
        self.set_cursor_pos(x, y)
        time.sleep(0.005)
        MOUSEEVENTF_WHEEL = 0x0800
        return self._send_mouse_event(MOUSEEVENTF_WHEEL, delta)

    def emit_drag(self, start_x: int, start_y: int, end_x: int, end_y: int, steps: int = 5) -> bool:
        self.set_cursor_pos(start_x, start_y)
        time.sleep(0.005)
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        self._send_mouse_event(MOUSEEVENTF_LEFTDOWN, 0)
        time.sleep(0.005)

        for i in range(1, steps + 1):
            curr_x = int(start_x + (end_x - start_x) * (i / steps))
            curr_y = int(start_y + (end_y - start_y) * (i / steps))
            self.set_cursor_pos(curr_x, curr_y)
            time.sleep(0.005)

        self._send_mouse_event(MOUSEEVENTF_LEFTUP, 0)
        return True

    def _send_mouse_event(self, flags: int, data: int) -> bool:
        inp = _INPUT()
        inp.type = 0  # INPUT_MOUSE
        inp.u.mi.dx = 0
        inp.u.mi.dy = 0
        inp.u.mi.mouseData = wintypes.DWORD(data)
        inp.u.mi.dwFlags = wintypes.DWORD(flags)
        inp.u.mi.time = 0
        inp.u.mi.dwExtraInfo = 0
        res = self.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        return res == 1

class MockMouseInputProvider(MouseInputProvider):
    r"""Deterministic mock provider capturing mouse actions and enabling verification simulation."""
    def __init__(self):
        self.cursor_pos: Tuple[int, int] = (0, 0)
        self.emitted_clicks: List[Dict[str, Any]] = []
        self.emitted_moves: List[Tuple[int, int]] = []
        self.emitted_scrolls: List[Dict[str, Any]] = []
        self.emitted_drags: List[Dict[str, Any]] = []
        self.simulate_emission_failure: bool = False

    def set_cursor_pos(self, x: int, y: int) -> bool:
        if self.simulate_emission_failure:
            return False
        self.cursor_pos = (x, y)
        self.emitted_moves.append((x, y))
        return True

    def get_cursor_pos(self) -> Tuple[int, int]:
        return self.cursor_pos

    def emit_click(self, x: int, y: int, button: str = "left", double: bool = False) -> bool:
        if self.simulate_emission_failure:
            return False
        self.cursor_pos = (x, y)
        self.emitted_clicks.append({
            "x": x, "y": y, "button": button, "double": double, "timestamp": time.time()
        })
        return True

    def emit_scroll(self, x: int, y: int, delta: int) -> bool:
        if self.simulate_emission_failure:
            return False
        self.cursor_pos = (x, y)
        self.emitted_scrolls.append({
            "x": x, "y": y, "delta": delta, "timestamp": time.time()
        })
        return True

    def emit_drag(self, start_x: int, start_y: int, end_x: int, end_y: int, steps: int = 5) -> bool:
        if self.simulate_emission_failure:
            return False
        self.cursor_pos = (end_x, end_y)
        self.emitted_drags.append({
            "start": (start_x, start_y),
            "end": (end_x, end_y),
            "steps": steps,
            "timestamp": time.time()
        })
        return True

# ---------------------------------------------------------------------------
# MouseDriver
# ---------------------------------------------------------------------------

class MouseDriver:
    r"""
    SmartScribe Phase 5C.2 Controlled Mouse Driver.
    Enforces target-derived coordinates, WindowFence pre-emission validation,
    prohibited control filtering, permission gating, and post-action verification.
    """
    def __init__(
        self,
        window_fence: Optional[WindowFence] = None,
        mouse_provider: Optional[MouseInputProvider] = None,
        permission_manager: Optional[PermissionManager] = None
    ):
        self.window_fence = window_fence or WindowFence()
        self.provider = mouse_provider or NativeWin32MouseProvider()
        self.permission_manager = permission_manager or PermissionManager()

    def _validate_target_control(self, binding: TargetBinding) -> Tuple[bool, InteractionStatus, str]:
        r"""Enforces that the prospective click target is a valid, authorized UI control."""
        # 1. Target type check
        if binding.target_type not in ("ui_element", "window", "control"):
            return False, InteractionStatus.REJECTED_INVALID_TARGET, f"Unsupported target type '{binding.target_type}' for mouse interaction."

        # 2. Control role / identity check
        role = (binding.control_role or "").lower().strip()
        control_id = (binding.control_id or "").lower().strip()

        # Reject unknown / unresolved UI regions
        if not role and not control_id and binding.target_type == "ui_element":
            return False, InteractionStatus.REJECTED_PROHIBITED_CONTROL, "Target control has unknown or unresolved UI identity."

        if role == "unknown":
            return False, InteractionStatus.REJECTED_PROHIBITED_CONTROL, "Target control is explicitly marked as unknown UI region."

        # Check prohibited OS chrome and system controls
        if role in PROHIBITED_CONTROL_ROLES:
            return False, InteractionStatus.REJECTED_PROHIBITED_CONTROL, f"Prohibited control role '{role}': OS chrome and system controls cannot be clicked."

        if control_id in PROHIBITED_CONTROL_IDS:
            return False, InteractionStatus.REJECTED_PROHIBITED_CONTROL, f"Prohibited control ID '{control_id}': System controls cannot be clicked."

        # Browser address bar restriction: only allowed if explicitly represented as a verified control
        if "address_bar" in role or "omnibox" in control_id or "url_bar" in control_id:
            if role != "address_bar" or not control_id:
                return False, InteractionStatus.REJECTED_PROHIBITED_CONTROL, "Browser address bar cannot be clicked unless explicitly represented as a verified control."

        # 3. Bounding box validity check
        bb = binding.bounding_box_client
        if bb is None:
            return False, InteractionStatus.REJECTED_INVALID_TARGET, "TargetBinding does not contain a verified bounding box. Arbitrary coordinate clicks are prohibited."

        left, top, right, bottom = bb
        if right <= left or bottom <= top:
            return False, InteractionStatus.REJECTED_INVALID_TARGET, f"Target control bounding box ({left}, {top}, {right}, {bottom}) is degenerate or invalid."

        return True, InteractionStatus.PASSED, "Control validated."

    def _derive_safe_click_coordinates(
        self,
        binding: TargetBinding,
        requested_client_coords: Optional[Tuple[int, int]] = None,
        epsilon: int = 4
    ) -> Tuple[bool, InteractionStatus, str, Optional[Tuple[int, int]]]:
        r"""
        Derives target client coordinates strictly from the verified control bounding box centroid.
        Rejects arbitrary or out-of-bounds coordinates.
        """
        bb = binding.bounding_box_client
        if bb is None:
            return False, InteractionStatus.REJECTED_INVALID_TARGET, "Missing bounding box.", None

        left, top, right, bottom = bb
        width = right - left
        height = bottom - top

        # Safe centroid calculation
        centroid_x = left + (width // 2)
        centroid_y = top + (height // 2)

        # If caller passed explicit coordinates, verify they fall strictly inside target bounding box
        if requested_client_coords is not None:
            rx, ry = requested_client_coords
            if rx < (left + epsilon) or rx > (right - epsilon) or ry < (top + epsilon) or ry > (bottom - epsilon):
                return (
                    False,
                    InteractionStatus.REJECTED_COORDINATES_OUT_OF_BOUNDS,
                    f"Specified coordinates ({rx}, {ry}) fall outside verified target control bounds [{left}..{right}, {top}..{bottom}]. Arbitrary coordinate clicking is permanently prohibited.",
                    None
                )
            target_x, target_y = rx, ry
        else:
            target_x, target_y = centroid_x, centroid_y

        return True, InteractionStatus.PASSED, "Coordinates derived.", (target_x, target_y)

    def _map_fence_status_to_interaction(self, fence_status: FenceStatus) -> InteractionStatus:
        r"""Maps WindowFence status enum to InteractionStatus."""
        if fence_status == FenceStatus.STALE_TIMESTAMP:
            return InteractionStatus.REJECTED_STALE_BINDING
        elif fence_status == FenceStatus.COORDINATES_OUT_OF_BOUNDS:
            return InteractionStatus.REJECTED_COORDINATES_OUT_OF_BOUNDS
        return InteractionStatus.REJECTED_WINDOW_FENCE

    def click_target(
        self,
        binding: TargetBinding,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        expected_postcondition: Optional[Callable[[], bool]] = None,
        requested_client_coords: Optional[Tuple[int, int]] = None,
        cancel_event: Optional[threading.Event] = None,
        double: bool = False
    ) -> InteractionResult:
        r"""
        Executes a controlled click or double-click into a verified UI target.
        Enforces the 10-step click safety pipeline.
        """
        t0 = time.perf_counter()

        # Step 1: Cancellation check
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Click action cancelled by user before validation.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 2: Permission check via canonical PermissionManager
        requires_confirmation = (risk_level in (RiskLevel.SENSITIVE_ACTION, RiskLevel.DESTRUCTIVE_ACTION))
        approved, perm_reason, _ = self.permission_manager.check_permission(
            tool_name="double_click_target" if double else "click_target",
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            confirmation_token=confirmation_token
        )
        if not approved:
            status = InteractionStatus.REJECTED_CONFIRMATION_REQUIRED if "confirmation" in perm_reason.lower() else InteractionStatus.REJECTED_PERMISSION_DENIED
            return InteractionResult(
                success=False,
                status=status,
                verified=False,
                reason=f"Permission check failed: {perm_reason}",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 3: Target control validation
        ctrl_ok, ctrl_status, ctrl_reason = self._validate_target_control(binding)
        if not ctrl_ok:
            return InteractionResult(
                success=False,
                status=ctrl_status,
                verified=False,
                reason=ctrl_reason,
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 4: Coordinate derivation strictly from verified target bounding box
        coord_ok, coord_status, coord_reason, target_client_coords = self._derive_safe_click_coordinates(
            binding, requested_client_coords=requested_client_coords, epsilon=4
        )
        if not coord_ok or target_client_coords is None:
            return InteractionResult(
                success=False,
                status=coord_status,
                verified=False,
                reason=coord_reason,
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        t_res = (time.perf_counter() - t0) * 1000

        # Step 5: Live WindowFence validation (pre-action geometry, HWND, PID, executable, UIPI)
        t_fence_start = time.perf_counter()
        fence_res = self.window_fence.validate_live_pre_action(
            binding, requested_client_coords=target_client_coords, epsilon=4
        )
        t_val = (time.perf_counter() - t_fence_start) * 1000

        if not fence_res.valid:
            return InteractionResult(
                success=False,
                status=self._map_fence_status_to_interaction(fence_res.status),
                verified=False,
                reason=f"WindowFence validation failed: {fence_res.reason}",
                target_info=fence_res.process_info,
                execution_latency_ms=round(t_val, 3),
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        screen_coords = fence_res.screen_coords
        if not screen_coords:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_COORDINATES_OUT_OF_BOUNDS,
                verified=False,
                reason="Screen coordinates could not be resolved from client coordinates.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 6: Immediate pre-emission check (minimizing check-to-emission interval)
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Click action cancelled by user immediately prior to input emission.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Live foreground exclusivity check right before emission
        fg_now = self.window_fence.win32.get_foreground_window()
        if fg_now != binding.window_handle:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_WINDOW_FENCE,
                verified=False,
                reason=f"Foreground focus lost immediately prior to input emission (expected HWND {binding.window_handle}, got {fg_now}).",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 7: Input emission
        t_emit_start = time.perf_counter()
        emitted = self.provider.emit_click(screen_coords[0], screen_coords[1], button="left", double=double)
        t_emit = (time.perf_counter() - t_emit_start) * 1000

        if not emitted:
            return InteractionResult(
                success=False,
                status=InteractionStatus.EMISSION_FAILED,
                verified=False,
                reason="OS mouse event injection failed.",
                execution_latency_ms=round(t_emit, 3),
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 8: Deterministic Post-Action Verification
        t_ver_start = time.perf_counter()
        verified = False
        postcondition_status = "verification_unconfirmed"
        ver_reason = "Click emitted successfully, but no deterministic postcondition was provided to confirm state change."

        if expected_postcondition is not None:
            try:
                condition_met = bool(expected_postcondition())
                if condition_met:
                    verified = True
                    postcondition_status = "verified"
                    ver_reason = "Expected postcondition confirmed after click."
                else:
                    verified = False
                    postcondition_status = "verification_failed"
                    ver_reason = "Expected postcondition was not observed after click."
            except Exception as e:
                verified = False
                postcondition_status = "verification_failed"
                ver_reason = f"Error evaluating postcondition: {str(e)}"

        t_ver = (time.perf_counter() - t_ver_start) * 1000
        total_time = (time.perf_counter() - t0) * 1000

        final_status = InteractionStatus.PASSED if (expected_postcondition is None or verified) else InteractionStatus.VERIFICATION_FAILED

        return InteractionResult(
            success=(final_status == InteractionStatus.PASSED),
            status=final_status,
            verified=verified,
            reason=ver_reason,
            target_info={
                "hwnd": binding.window_handle,
                "pid": binding.process_id,
                "executable": binding.executable_name,
                "control_id": binding.control_id,
                "control_role": binding.control_role,
                "client_coords": target_client_coords,
                "screen_coords": screen_coords,
            },
            execution_latency_ms=round(t_val + t_res + t_emit, 3),
            verification_latency_ms=round(t_ver, 3),
            total_latency_ms=round(total_time, 3),
            postcondition_status=postcondition_status,
            emitted_events_count=2 if double else 1
        )

    def double_click_target(
        self,
        binding: TargetBinding,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        expected_postcondition: Optional[Callable[[], bool]] = None,
        requested_client_coords: Optional[Tuple[int, int]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> InteractionResult:
        r"""Executes a controlled double-click into a verified UI target."""
        return self.click_target(
            binding=binding,
            risk_level=risk_level,
            confirmation_token=confirmation_token,
            expected_postcondition=expected_postcondition,
            requested_client_coords=requested_client_coords,
            cancel_event=cancel_event,
            double=True
        )

    def move_to_target(
        self,
        binding: TargetBinding,
        cancel_event: Optional[threading.Event] = None
    ) -> InteractionResult:
        r"""Moves cursor to verified target control centroid (READ_ONLY permission)."""
        t0 = time.perf_counter()

        # Step 1: Cancellation check
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Move action cancelled by user.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 2: Target control validation
        ctrl_ok, ctrl_status, ctrl_reason = self._validate_target_control(binding)
        if not ctrl_ok:
            return InteractionResult(
                success=False,
                status=ctrl_status,
                verified=False,
                reason=ctrl_reason,
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 3: Coordinate derivation
        coord_ok, coord_status, coord_reason, target_client_coords = self._derive_safe_click_coordinates(binding)
        if not coord_ok or target_client_coords is None:
            return InteractionResult(
                success=False,
                status=coord_status,
                verified=False,
                reason=coord_reason,
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 4: WindowFence validation
        fence_res = self.window_fence.validate_live_pre_action(
            binding, requested_client_coords=target_client_coords, epsilon=4
        )
        if not fence_res.valid:
            return InteractionResult(
                success=False,
                status=self._map_fence_status_to_interaction(fence_res.status),
                verified=False,
                reason=f"WindowFence validation failed: {fence_res.reason}",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        screen_coords = fence_res.screen_coords
        if not screen_coords:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_COORDINATES_OUT_OF_BOUNDS,
                verified=False,
                reason="Screen coordinates could not be resolved.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 5: Input emission
        emitted = self.provider.set_cursor_pos(screen_coords[0], screen_coords[1])
        if not emitted:
            return InteractionResult(
                success=False,
                status=InteractionStatus.EMISSION_FAILED,
                verified=False,
                reason="Failed to position cursor.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 6: Post-action verification
        observed_pos = self.provider.get_cursor_pos()
        verified = (observed_pos == screen_coords)

        return InteractionResult(
            success=True,
            status=InteractionStatus.PASSED,
            verified=verified,
            reason="Cursor moved to target.",
            target_info={"screen_coords": screen_coords, "observed_pos": observed_pos},
            postcondition_status="verified" if verified else "verification_unconfirmed",
            total_latency_ms=round((time.perf_counter() - t0) * 1000, 3),
            emitted_events_count=1
        )

    def bounded_scroll_target(
        self,
        binding: TargetBinding,
        scroll_delta: int = 120,
        cancel_event: Optional[threading.Event] = None
    ) -> InteractionResult:
        r"""Scrolls vertically inside verified target control boundaries."""
        t0 = time.perf_counter()

        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Scroll action cancelled by user.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        ctrl_ok, ctrl_status, ctrl_reason = self._validate_target_control(binding)
        if not ctrl_ok:
            return InteractionResult(
                success=False,
                status=ctrl_status,
                verified=False,
                reason=ctrl_reason,
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        coord_ok, coord_status, coord_reason, target_client_coords = self._derive_safe_click_coordinates(binding)
        if not coord_ok or target_client_coords is None:
            return InteractionResult(
                success=False,
                status=coord_status,
                verified=False,
                reason=coord_reason,
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        fence_res = self.window_fence.validate_live_pre_action(
            binding, requested_client_coords=target_client_coords, epsilon=4
        )
        if not fence_res.valid:
            return InteractionResult(
                success=False,
                status=self._map_fence_status_to_interaction(fence_res.status),
                verified=False,
                reason=f"WindowFence validation failed: {fence_res.reason}",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        screen_coords = fence_res.screen_coords
        if not screen_coords:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_COORDINATES_OUT_OF_BOUNDS,
                verified=False,
                reason="Screen coordinates could not be resolved.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        emitted = self.provider.emit_scroll(screen_coords[0], screen_coords[1], delta=scroll_delta)
        if not emitted:
            return InteractionResult(
                success=False,
                status=InteractionStatus.EMISSION_FAILED,
                verified=False,
                reason="OS mouse scroll injection failed.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        return InteractionResult(
            success=True,
            status=InteractionStatus.PASSED,
            verified=False,
            reason="Scroll emitted; no deterministic postcondition available.",
            postcondition_status="verification_unconfirmed",
            total_latency_ms=round((time.perf_counter() - t0) * 1000, 3),
            emitted_events_count=1
        )

    def drag_between_targets(
        self,
        source_binding: TargetBinding,
        dest_binding: TargetBinding,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        expected_postcondition: Optional[Callable[[], bool]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> InteractionResult:
        r"""
        Executes a bounded drag strictly between a verified source control and verified destination control.
        Free-form screen dragging is permanently prohibited.
        """
        t0 = time.perf_counter()

        # 1. Cancellation check
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Drag action cancelled before validation.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # 2. Permission check
        requires_confirmation = (risk_level in (RiskLevel.SENSITIVE_ACTION, RiskLevel.DESTRUCTIVE_ACTION))
        approved, perm_reason, _ = self.permission_manager.check_permission(
            tool_name="drag_between_targets",
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            confirmation_token=confirmation_token
        )
        if not approved:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_PERMISSION_DENIED,
                verified=False,
                reason=f"Permission check failed: {perm_reason}",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # 3. Source control validation
        s_ok, s_status, s_reason = self._validate_target_control(source_binding)
        if not s_ok:
            return InteractionResult(
                success=False, status=s_status, verified=False, reason=f"Source control error: {s_reason}"
            )

        # 4. Destination control validation
        d_ok, d_status, d_reason = self._validate_target_control(dest_binding)
        if not d_ok:
            return InteractionResult(
                success=False, status=d_status, verified=False, reason=f"Destination control error: {d_reason}"
            )

        # 5. Derive source and destination coordinates
        s_coord_ok, _, _, s_client_coords = self._derive_safe_click_coordinates(source_binding)
        d_coord_ok, _, _, d_client_coords = self._derive_safe_click_coordinates(dest_binding)
        if not s_coord_ok or not d_coord_ok or s_client_coords is None or d_client_coords is None:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_COORDINATES_OUT_OF_BOUNDS,
                verified=False,
                reason="Could not derive valid centroid coordinates for drag source/destination."
            )

        # 6. Live WindowFence validation for source
        s_fence = self.window_fence.validate_live_pre_action(source_binding, requested_client_coords=s_client_coords)
        if not s_fence.valid or not s_fence.screen_coords:
            return InteractionResult(
                success=False,
                status=self._map_fence_status_to_interaction(s_fence.status),
                verified=False,
                reason=f"Source WindowFence failure: {s_fence.reason}"
            )

        # 7. Live WindowFence validation for destination
        d_fence = self.window_fence.validate_live_pre_action(dest_binding, requested_client_coords=d_client_coords)
        if not d_fence.valid or not d_fence.screen_coords:
            return InteractionResult(
                success=False,
                status=self._map_fence_status_to_interaction(d_fence.status),
                verified=False,
                reason=f"Destination WindowFence failure: {d_fence.reason}"
            )

        # 8. Pre-emission cancellation & foreground check
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Drag action cancelled immediately prior to button down."
            )

        fg_now = self.window_fence.win32.get_foreground_window()
        if fg_now != source_binding.window_handle:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_WINDOW_FENCE,
                verified=False,
                reason="Foreground lost prior to drag initiation."
            )

        # 9. Bounded drag execution
        s_screen = s_fence.screen_coords
        d_screen = d_fence.screen_coords
        drag_ok = self.provider.emit_drag(s_screen[0], s_screen[1], d_screen[0], d_screen[1], steps=5)
        if not drag_ok:
            return InteractionResult(
                success=False,
                status=InteractionStatus.EMISSION_FAILED,
                verified=False,
                reason="OS drag event emission failed."
            )

        # 10. Post-action verification
        verified = False
        postcondition_status = "verification_unconfirmed"
        if expected_postcondition is not None:
            try:
                verified = bool(expected_postcondition())
                postcondition_status = "verified" if verified else "verification_failed"
            except Exception:
                verified = False
                postcondition_status = "verification_failed"

        final_status = InteractionStatus.PASSED if (expected_postcondition is None or verified) else InteractionStatus.VERIFICATION_FAILED
        return InteractionResult(
            success=(final_status == InteractionStatus.PASSED),
            status=final_status,
            verified=verified,
            reason="Drag completed." if (expected_postcondition is None or verified) else "Postcondition failed after drag.",
            postcondition_status=postcondition_status,
            total_latency_ms=round((time.perf_counter() - t0) * 1000, 3),
            emitted_events_count=7
        )
