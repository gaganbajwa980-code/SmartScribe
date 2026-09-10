r"""
SmartScribe Phase 5C.1: WindowFence Engine & Target-Binding Safety Gate.

TIMING & INTEGRITY SECURITY CONTRACT:
- Final foreground + HWND + PID + executable validation occurs immediately before input emission.
- If validation fails, zero input is emitted.
- The validation-to-emission interval is minimized (measured in microseconds).
- Post-action verification remains mandatory on all actions.
- User-space Win32 validation cannot eliminate every theoretical race condition between
  the final check and OS input emission (e.g., asynchronous user focus stealing or window
  destruction during the microseconds between inspection and injection). Therefore,
  post-action verification and immediate abort on focus loss remain strict security requirements.

UIPI / INTEGRITY MODEL:
- The security decision is based on the actual target process Mandatory Integrity Control (MIC)
  level relative to SmartScribe's process integrity level, not merely a TokenElevation boolean.
- Under Windows User Interface Privilege Isolation (UIPI), a lower-integrity process cannot send
  synthetic input or window messages to a higher-integrity process.
- WindowFence queries the true token integrity level RID of both the target process and
  SmartScribe. If target integrity level exceeds SmartScribe's integrity level, the interaction
  is deterministically rejected with FenceStatus.UIPI_ELEVATION_BOUNDARY.
- We do not rely on eventual SendInput failure as a security mechanism.
"""

import os
import sys
import time
import ctypes
from ctypes import wintypes
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any

from backend.assistant.interaction.target_binding import TargetBinding, normalize_executable_path

PROHIBITED_PROCESS_INTERACTION = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "wt.exe",
    "bash.exe",
    "conhost.exe",
    "1password.exe",
    "bitwarden.exe",
    "keepass.exe",
    "keepassxc.exe",
    "credentialui.exe",
}

# Windows Mandatory Integrity Control (MIC) Level RIDs (S-1-16-X)
INTEGRITY_LEVEL_UNTRUSTED = 0x0000        # S-1-16-0x0000 (0)
INTEGRITY_LEVEL_LOW = 0x1000              # S-1-16-0x1000 (4096)
INTEGRITY_LEVEL_MEDIUM = 0x2000           # S-1-16-0x2000 (8192)
INTEGRITY_LEVEL_MEDIUM_PLUS = 0x2100      # S-1-16-0x2100 (8448)
INTEGRITY_LEVEL_HIGH = 0x3000             # S-1-16-0x3000 (12288)
INTEGRITY_LEVEL_SYSTEM = 0x4000           # S-1-16-0x4000 (16384)
INTEGRITY_LEVEL_PROTECTED = 0x5000        # S-1-16-0x5000 (20480)

def integrity_level_name(il: Optional[int]) -> str:
    r"""Maps integer integrity level RID to human-readable Windows MIC label."""
    if il is None:
        return "UNKNOWN"
    if il >= INTEGRITY_LEVEL_PROTECTED:
        return "PROTECTED"
    if il >= INTEGRITY_LEVEL_SYSTEM:
        return "SYSTEM"
    if il >= INTEGRITY_LEVEL_HIGH:
        return "HIGH"
    if il >= INTEGRITY_LEVEL_MEDIUM_PLUS:
        return "MEDIUM_PLUS"
    if il >= INTEGRITY_LEVEL_MEDIUM:
        return "MEDIUM"
    if il >= INTEGRITY_LEVEL_LOW:
        return "LOW"
    return "UNTRUSTED"

class FenceStatus(str, Enum):
    PASSED = "passed"
    STALE_TIMESTAMP = "stale_timestamp"
    WINDOW_DESTROYED = "window_destroyed"
    NOT_FOREGROUND = "not_foreground"
    PID_MISMATCH = "pid_mismatch"
    EXECUTABLE_MISMATCH = "executable_mismatch"
    PROHIBITED_PROCESS = "prohibited_process"
    WINDOW_MINIMIZED = "window_minimized"
    WINDOW_HIDDEN = "window_hidden"
    UIPI_ELEVATION_BOUNDARY = "uipi_elevation_boundary"
    COORDINATES_OUT_OF_BOUNDS = "coordinates_out_of_bounds"
    INTERNAL_ERROR = "internal_error"

@dataclass
class FenceValidationResult:
    r"""
    Structured, machine-readable outcome of a live WindowFence pre-action validation.
    Captures exact failure reason, status enum, resolved geometry, and process context.
    """
    valid: bool
    status: FenceStatus
    reason: str
    screen_coords: Optional[Tuple[int, int]] = None
    client_coords: Optional[Tuple[int, int]] = None
    client_rect: Optional[Tuple[int, int, int, int]] = None
    window_rect: Optional[Tuple[int, int, int, int]] = None
    monitor_rect: Optional[Tuple[int, int, int, int]] = None
    process_info: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "status": self.status.value,
            "reason": self.reason,
            "screen_coords": list(self.screen_coords) if self.screen_coords else None,
            "client_coords": list(self.client_coords) if self.client_coords else None,
            "client_rect": list(self.client_rect) if self.client_rect else None,
            "window_rect": list(self.window_rect) if self.window_rect else None,
            "monitor_rect": list(self.monitor_rect) if self.monitor_rect else None,
            "process_info": self.process_info,
            "timestamp": self.timestamp
        }

class Win32Provider:
    r"""Abstract interface for Win32 API interactions, enabling deterministic mocking in tests."""
    def is_window(self, hwnd: int) -> bool:
        raise NotImplementedError
    def get_foreground_window(self) -> int:
        raise NotImplementedError
    def get_window_thread_process_id(self, hwnd: int) -> int:
        raise NotImplementedError
    def get_window_text(self, hwnd: int) -> str:
        raise NotImplementedError
    def get_client_rect(self, hwnd: int) -> Tuple[int, int, int, int]:
        raise NotImplementedError
    def get_window_rect(self, hwnd: int) -> Tuple[int, int, int, int]:
        raise NotImplementedError
    def client_to_screen(self, hwnd: int, client_x: int, client_y: int) -> Tuple[int, int]:
        raise NotImplementedError
    def is_iconic(self, hwnd: int) -> bool:
        raise NotImplementedError
    def is_window_visible(self, hwnd: int) -> bool:
        raise NotImplementedError
    def get_monitor_work_area(self, hwnd: int) -> Tuple[int, int, int, int]:
        raise NotImplementedError
    def get_process_executable_path(self, pid: int) -> Optional[str]:
        raise NotImplementedError
    def get_process_integrity_level(self, pid: int) -> Optional[int]:
        r"""Returns the Windows Mandatory Integrity Control (MIC) RID for a process PID, or None."""
        raise NotImplementedError
    def get_current_process_integrity_level(self) -> int:
        r"""Returns the Windows Mandatory Integrity Control (MIC) RID for the current process."""
        raise NotImplementedError
    def is_process_elevated(self, pid: int) -> bool:
        il = self.get_process_integrity_level(pid)
        return il is not None and il >= INTEGRITY_LEVEL_HIGH
    def is_current_process_elevated(self) -> bool:
        return self.get_current_process_integrity_level() >= INTEGRITY_LEVEL_HIGH

class NativeWin32Provider(Win32Provider):
    r"""Production Win32 API implementation using native ctypes."""
    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self.advapi32 = getattr(ctypes.windll, "advapi32", None)
        try:
            # Enable Per-Monitor V2 DPI awareness context (-4)
            self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass

    def is_window(self, hwnd: int) -> bool:
        if hwnd <= 0:
            return False
        return bool(self.user32.IsWindow(wintypes.HWND(hwnd)))

    def get_foreground_window(self) -> int:
        return int(self.user32.GetForegroundWindow() or 0)

    def get_window_thread_process_id(self, hwnd: int) -> int:
        pid = wintypes.DWORD(0)
        self.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)

    def get_window_text(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
            return buf.value.strip()
        return ""

    def get_client_rect(self, hwnd: int) -> Tuple[int, int, int, int]:
        rect = wintypes.RECT()
        if self.user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
        return (0, 0, 0, 0)

    def get_window_rect(self, hwnd: int) -> Tuple[int, int, int, int]:
        rect = wintypes.RECT()
        if self.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
        return (0, 0, 0, 0)

    def client_to_screen(self, hwnd: int, client_x: int, client_y: int) -> Tuple[int, int]:
        pt = wintypes.POINT(client_x, client_y)
        if self.user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
            return (pt.x, pt.y)
        return (client_x, client_y)

    def is_iconic(self, hwnd: int) -> bool:
        return bool(self.user32.IsIconic(wintypes.HWND(hwnd)))

    def is_window_visible(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindowVisible(wintypes.HWND(hwnd)))

    def get_monitor_work_area(self, hwnd: int) -> Tuple[int, int, int, int]:
        MONITOR_DEFAULTTONEAREST = 2
        hMon = self.user32.MonitorFromWindow(wintypes.HWND(hwnd), wintypes.DWORD(MONITOR_DEFAULTTONEAREST))
        if not hMon:
            return (-32768, -32768, 32767, 32767)

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)
            ]
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if self.user32.GetMonitorInfoW(hMon, ctypes.byref(mi)):
            return (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)
        return (-32768, -32768, 32767, 32767)

    def get_process_executable_path(self, pid: int) -> Optional[str]:
        if pid <= 0:
            return None
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        hProc = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hProc:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if self.kernel32.QueryFullProcessImageNameW(hProc, 0, buf, ctypes.byref(size)):
                return normalize_executable_path(buf.value)
            return None
        finally:
            self.kernel32.CloseHandle(hProc)

    def get_process_integrity_level(self, pid: int) -> Optional[int]:
        r"""
        Queries the actual Windows Mandatory Integrity Control (MIC) level for the process PID.
        Under Windows UIPI, lower-integrity processes cannot send input/messages to higher-integrity processes.
        """
        if pid <= 0:
            return None
        if not self.advapi32 or not self.kernel32:
            return None

        TOKEN_QUERY = 0x0008
        TokenIntegrityLevel = 25
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        is_self = (pid == os.getpid())
        if is_self:
            self.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            hProc = self.kernel32.GetCurrentProcess()
            should_close = False
        else:
            self.kernel32.OpenProcess.restype = wintypes.HANDLE
            self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            hProc = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            should_close = True
            if not hProc:
                err = self.kernel32.GetLastError()
                # Under Windows, if a medium-integrity process attempts to query a high/system integrity process,
                # OpenProcess or OpenProcessToken often yields ERROR_ACCESS_DENIED (5).
                if err == 5:
                    return INTEGRITY_LEVEL_HIGH
                return None

        try:
            hToken = wintypes.HANDLE()
            self.advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
            self.advapi32.OpenProcessToken.restype = wintypes.BOOL
            if not self.advapi32.OpenProcessToken(hProc, TOKEN_QUERY, ctypes.byref(hToken)):
                err = self.kernel32.GetLastError()
                if err == 5:
                    return INTEGRITY_LEVEL_HIGH
                return None

            try:
                ret_len = wintypes.DWORD()
                self.advapi32.GetTokenInformation(hToken, TokenIntegrityLevel, None, 0, ctypes.byref(ret_len))
                if ret_len.value == 0:
                    return self._fallback_elevation_to_il(hToken)

                buf = ctypes.create_string_buffer(ret_len.value)
                if not self.advapi32.GetTokenInformation(hToken, TokenIntegrityLevel, buf, ret_len.value, ctypes.byref(ret_len)):
                    return self._fallback_elevation_to_il(hToken)

                class SID_AND_ATTRIBUTES(ctypes.Structure):
                    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

                class TOKEN_MANDATORY_LABEL(ctypes.Structure):
                    _fields_ = [("Label", SID_AND_ATTRIBUTES)]

                tml = TOKEN_MANDATORY_LABEL.from_buffer_copy(buf)

                self.advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
                self.advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
                self.advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
                self.advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]

                p_count = self.advapi32.GetSidSubAuthorityCount(tml.Label.Sid)
                if not p_count:
                    return self._fallback_elevation_to_il(hToken)
                count = p_count.contents.value
                if count <= 0:
                    return self._fallback_elevation_to_il(hToken)

                p_rid = self.advapi32.GetSidSubAuthority(tml.Label.Sid, count - 1)
                if not p_rid:
                    return self._fallback_elevation_to_il(hToken)

                return int(p_rid.contents.value)
            finally:
                self.kernel32.CloseHandle(hToken)
        finally:
            if should_close and hProc:
                self.kernel32.CloseHandle(hProc)

    def _fallback_elevation_to_il(self, hToken: wintypes.HANDLE) -> int:
        try:
            TokenElevation = 20
            class TOKEN_ELEVATION(ctypes.Structure):
                _fields_ = [("TokenIsElevated", wintypes.DWORD)]
            elev = TOKEN_ELEVATION()
            ret_len = wintypes.DWORD()
            if self.advapi32.GetTokenInformation(
                hToken, TokenElevation, ctypes.byref(elev), ctypes.sizeof(elev), ctypes.byref(ret_len)
            ):
                return INTEGRITY_LEVEL_HIGH if elev.TokenIsElevated != 0 else INTEGRITY_LEVEL_MEDIUM
        except Exception:
            pass
        return INTEGRITY_LEVEL_MEDIUM

    def get_current_process_integrity_level(self) -> int:
        il = self.get_process_integrity_level(os.getpid())
        if il is not None:
            return il
        try:
            if bool(ctypes.windll.shell32.IsUserAnAdmin()):
                return INTEGRITY_LEVEL_HIGH
        except Exception:
            pass
        return INTEGRITY_LEVEL_MEDIUM

class WindowFence:
    r"""
    SmartScribe Phase 5C.1 Foundational Safety Gate.
    Enforces live pre-action target validation, PID and binary identity verification,
    foreground exclusivity, coordinate clamping, and UIPI integrity boundary detection.
    Validation occurs immediately prior to prospective input emission; if validation fails,
    zero input is emitted.

    CRITICAL SECURITY INVARIANT:
    This class performs read-only inspection and validation. It emits ZERO mouse or keyboard inputs.
    """
    def __init__(self, win32_provider: Optional[Win32Provider] = None):
        self.win32 = win32_provider or NativeWin32Provider()

    def validate_live_pre_action(
        self,
        binding: TargetBinding,
        requested_client_coords: Optional[Tuple[int, int]] = None,
        epsilon: int = 4,
        now: Optional[float] = None
    ) -> FenceValidationResult:
        r"""
        Performs live pre-action validation immediately prior to prospective interaction emission.
        All checks must pass concurrently at t = 0 ms.
        Note: User-space Win32 validation cannot eliminate every theoretical race condition
        between the final check and OS input emission; therefore, the validation-to-emission
        interval is minimized, and post-action verification remains mandatory.
        """
        # 1. Stale Timestamp Check (Early Invalidator)
        if binding.is_stale(now):
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.STALE_TIMESTAMP,
                reason=f"Target binding expired (> {binding.max_staleness_seconds}s old). Early invalidation triggered."
            )

        # 2. Window Existence Check
        if not self.win32.is_window(binding.window_handle):
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.WINDOW_DESTROYED,
                reason=f"Target HWND {binding.window_handle} no longer exists or handle is invalid."
            )

        # 3. Foreground Exclusivity Check
        fg_hwnd = self.win32.get_foreground_window()
        if fg_hwnd != binding.window_handle:
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.NOT_FOREGROUND,
                reason=f"Target HWND {binding.window_handle} is not in the foreground (current foreground HWND: {fg_hwnd})."
            )

        # 4. Thread / PID Ownership Check
        actual_pid = self.win32.get_window_thread_process_id(binding.window_handle)
        if actual_pid != binding.process_id:
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.PID_MISMATCH,
                reason=f"Target HWND {binding.window_handle} belongs to PID {actual_pid}, expected PID {binding.process_id}."
            )

        # 5. Process Identity & Prohibited Executable Check
        actual_exe_path = self.win32.get_process_executable_path(actual_pid)
        if not actual_exe_path:
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.EXECUTABLE_MISMATCH,
                reason=f"Could not resolve on-disk executable path for target PID {actual_pid}."
            )

        actual_exe_name = os.path.basename(actual_exe_path).lower().strip()
        expected_exe_name = (binding.executable_name or "").lower().strip()

        # Check prohibited processes
        if actual_exe_name in PROHIBITED_PROCESS_INTERACTION or expected_exe_name in PROHIBITED_PROCESS_INTERACTION:
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.PROHIBITED_PROCESS,
                reason=f"Access denied: Prohibited process interaction with shell/credential binary '{actual_exe_name}'."
            )

        # Canonical path match (prevents PID recycling)
        norm_expected_path = normalize_executable_path(binding.executable_path)
        norm_actual_path = normalize_executable_path(actual_exe_path)
        if norm_expected_path and norm_expected_path != norm_actual_path:
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.EXECUTABLE_MISMATCH,
                reason=f"Executable path mismatch: expected '{norm_expected_path}', actual '{norm_actual_path}' (potential PID recycling)."
            )

        if expected_exe_name and expected_exe_name != actual_exe_name:
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.EXECUTABLE_MISMATCH,
                reason=f"Executable image mismatch: expected '{expected_exe_name}', actual '{actual_exe_name}'."
            )

        # 6. Window Visibility & Normal Placement Check
        if self.win32.is_iconic(binding.window_handle):
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.WINDOW_MINIMIZED,
                reason=f"Target window HWND {binding.window_handle} is minimized (iconic)."
            )

        if not self.win32.is_window_visible(binding.window_handle):
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.WINDOW_HIDDEN,
                reason=f"Target window HWND {binding.window_handle} is hidden or invisible."
            )

        # 7. UIPI Privilege / Integrity Level Boundary Check
        # Security decision is based on actual target process Mandatory Integrity Control (MIC)
        # level relative to SmartScribe's integrity level, rather than merely a TokenElevation boolean.
        target_il = self.win32.get_process_integrity_level(actual_pid)
        self_il = self.win32.get_current_process_integrity_level()

        # Under Windows UIPI, interaction is forbidden if target IL exceeds caller IL.
        if target_il is not None and target_il > self_il:
            target_name = integrity_level_name(target_il)
            self_name = integrity_level_name(self_il)
            return FenceValidationResult(
                valid=False,
                status=FenceStatus.UIPI_ELEVATION_BOUNDARY,
                reason=(
                    f"UIPI integrity boundary violation: Target PID {actual_pid} has integrity level "
                    f"'{target_name}' (0x{target_il:04x}) which exceeds caller integrity level "
                    f"'{self_name}' (0x{self_il:04x}). Interaction blocked."
                ),
                process_info={
                    "pid": actual_pid,
                    "target_integrity_level": target_il,
                    "target_integrity_name": target_name,
                    "caller_integrity_level": self_il,
                    "caller_integrity_name": self_name,
                }
            )

        # 8. Coordinate & Geometry Validation
        client_rect = self.win32.get_client_rect(binding.window_handle)
        window_rect = self.win32.get_window_rect(binding.window_handle)
        monitor_rect = self.win32.get_monitor_work_area(binding.window_handle)

        client_w = client_rect[2] - client_rect[0]
        client_h = client_rect[3] - client_rect[1]

        target_client_coords = requested_client_coords
        # If no explicit requested coordinates but bounding box is present, use centroid
        if target_client_coords is None and binding.bounding_box_client:
            bb = binding.bounding_box_client
            target_client_coords = ((bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2)

        screen_coords = None
        if target_client_coords is not None:
            cx, cy = target_client_coords
            # Check client rectangle boundaries with safety inset epsilon
            if cx < epsilon or cx > (client_w - epsilon) or cy < epsilon or cy > (client_h - epsilon):
                return FenceValidationResult(
                    valid=False,
                    status=FenceStatus.COORDINATES_OUT_OF_BOUNDS,
                    reason=f"Client coordinates ({cx}, {cy}) are out of safe client bounds [0..{client_w}, 0..{client_h}] with epsilon {epsilon}px.",
                    client_coords=(cx, cy),
                    client_rect=client_rect,
                    window_rect=window_rect,
                    monitor_rect=monitor_rect
                )

            # Convert to virtual screen coordinates
            sx, sy = self.win32.client_to_screen(binding.window_handle, cx, cy)

            # Monitor work area verification (handles multi-monitor including negative coordinates)
            mon_left, mon_top, mon_right, mon_bottom = monitor_rect
            if sx < mon_left or sx >= mon_right or sy < mon_top or sy >= mon_bottom:
                return FenceValidationResult(
                    valid=False,
                    status=FenceStatus.COORDINATES_OUT_OF_BOUNDS,
                    reason=f"Transformed screen coordinates ({sx}, {sy}) fall outside monitor work area ({mon_left}, {mon_top}, {mon_right}, {mon_bottom}).",
                    screen_coords=(sx, sy),
                    client_coords=(cx, cy),
                    client_rect=client_rect,
                    window_rect=window_rect,
                    monitor_rect=monitor_rect
                )

            screen_coords = (sx, sy)

        # Title query for diagnostic/presentation logging only (non-authoritative)
        actual_title = self.win32.get_window_text(binding.window_handle)
        target_name = integrity_level_name(target_il)
        self_name = integrity_level_name(self_il)

        return FenceValidationResult(
            valid=True,
            status=FenceStatus.PASSED,
            reason="All WindowFence live invariants satisfied.",
            screen_coords=screen_coords,
            client_coords=target_client_coords,
            client_rect=client_rect,
            window_rect=window_rect,
            monitor_rect=monitor_rect,
            process_info={
                "pid": actual_pid,
                "executable_path": actual_exe_path,
                "executable_name": actual_exe_name,
                "title": actual_title,
                "target_integrity_level": target_il,
                "target_integrity_name": target_name,
                "caller_integrity_level": self_il,
                "caller_integrity_name": self_name,
                "elevated": (target_il is not None and target_il >= INTEGRITY_LEVEL_HIGH)
            }
        )

    def clamp_client_coordinates(
        self,
        client_x: int,
        client_y: int,
        client_rect: Tuple[int, int, int, int],
        epsilon: int = 4
    ) -> Tuple[int, int]:
        r"""
        Clamps client coordinates safely within the inner client rectangle.
        Note: WindowFence validates coordinate geometry and insets only. It does NOT authorize
        arbitrary screen coordinates. Prospective interaction coordinates in Phase 5C.2 must be
        derived from a verified target/control binding, never blind coordinate input.
        """
        client_w = client_rect[2] - client_rect[0]
        client_h = client_rect[3] - client_rect[1]
        clamped_x = max(epsilon, min(client_x, max(epsilon, client_w - epsilon)))
        clamped_y = max(epsilon, min(client_y, max(epsilon, client_h - epsilon)))
        return (clamped_x, clamped_y)
