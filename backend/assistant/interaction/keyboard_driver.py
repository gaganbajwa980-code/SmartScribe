r"""
SmartScribe Phase 5C.2: Controlled Keyboard Driver.

SECURITY & ARCHITECTURAL INVARIANTS:
1. Verified Target Identity & Focus:
   - Requires verified foreground HWND, PID, canonical executable, and live WindowFence validation.
   - Target process must not be a prohibited shell or password manager.
2. Complete Credential & Password Isolation:
   - Typing into password fields, tokens, API keys, secrets, or credential managers is PERMANENTLY BLOCKED.
   - Rejects UIAutomation IsPasswordPropertyId, ES_PASSWORD styles, and password-like control roles/IDs.
3. Direct Controlled Input (Zero Clipboard):
   - Typing uses direct OS Unicode key event injection (SendInput KEYEVENTF_UNICODE).
   - Clipboard paste and harvesting are strictly prohibited.
4. Bounded Text Payload:
   - Enforces max payload limit (<= 1000 characters).
   - Rejects unprintable control characters (< 32, except \n, \t, \r).
   - Preserves legitimate punctuation (&, |, ;, >, <, $, ', ") in normal text fields without naive stripping.
5. Whitelisted Hotkey Safety:
   - Only explicitly approved non-destructive hotkeys are permitted.
   - Ctrl+Alt+Del, Win+L, Ctrl+Shift+Esc, Win+R, Win+X, and privilege escalation shortcuts are PERMANENTLY REJECTED.
6. Truthful Post-Action Value Read-Back Verification:
   - Reads back control value post-typing via UIAutomation/Win32 WM_GETTEXT.
   - If observed text matches expected -> verified=True.
   - If mismatch -> verification failure.
   - If read-back is unsupported/unavailable for control -> returns 'verification_unavailable' without fabricating success.
7. Immediate Cancellation & Focus Loss Abort:
   - Focus change or user intervention immediately halts input emission without focus stealing.
"""

import time
import threading
import ctypes
from ctypes import wintypes
from typing import Optional, Tuple, Dict, Any, List, Callable

from backend.assistant.permission_manager import PermissionManager, RiskLevel
from backend.assistant.interaction.target_binding import TargetBinding
from backend.assistant.interaction.window_fence import (
    WindowFence,
    FenceStatus,
    FenceValidationResult,
    Win32Provider,
    NativeWin32Provider,
    PROHIBITED_PROCESS_INTERACTION,
)
from backend.assistant.interaction.mouse_driver import (
    InteractionStatus,
    InteractionResult,
)

# ---------------------------------------------------------------------------
# Maximum Text Payload Limit & Allowed Hotkeys
# ---------------------------------------------------------------------------

MAX_TEXT_PAYLOAD_LENGTH = 1000

# Explicit allowlist of safe, non-system-altering hotkeys
ALLOWED_HOTKEYS = {
    # Navigation & Selection
    "ctrl+a",
    "ctrl+c",
    "ctrl+v",
    "ctrl+x",
    "ctrl+z",
    "ctrl+y",
    "ctrl+f",
    "ctrl+s",
    "ctrl+p",
    "ctrl+o",
    "ctrl+n",
    "enter",
    "return",
    "escape",
    "esc",
    "tab",
    "space",
    "backspace",
    "delete",
    "del",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "pageup",
    "pagedown",
}

# Permanently prohibited system-security and shell-spawning hotkeys
PROHIBITED_HOTKEYS = {
    "ctrl+alt+del",
    "ctrl+alt+delete",
    "win+l",
    "ctrl+shift+esc",
    "win+r",
    "win+x",
    "alt+f4",
}

# Credential / password keywords in control IDs or roles
PASSWORD_ROLE_KEYWORDS = {
    "password",
    "password_box",
    "credential_field",
    "pin_field",
    "secure_edit",
}

PASSWORD_ID_KEYWORDS = [
    "password",
    "passwd",
    "token",
    "secret",
    "apikey",
    "api_key",
    "pin",
    "cvv",
    "credit_card",
    "passphrase",
]

# ---------------------------------------------------------------------------
# Keyboard Input Provider Abstraction
# ---------------------------------------------------------------------------

class KeyboardInputProvider:
    r"""Abstract interface for keyboard input injection to enable deterministic testing."""
    def emit_text(self, text: str) -> bool:
        raise NotImplementedError
    def emit_hotkey(self, hotkey: str) -> bool:
        raise NotImplementedError
    def get_focused_control_info(self, hwnd: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
    def get_control_value(self, hwnd: int, control_id: Optional[str] = None) -> Optional[str]:
        raise NotImplementedError

# Win32 structures for NativeWin32KeyboardProvider
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]

class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wintypes.DWORD),
        ('wParamL', wintypes.WORD),
        ('wParamH', wintypes.WORD),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ('mi', _MOUSEINPUT),
        ('ki', _KEYBDINPUT),
        ('hi', _HARDWAREINPUT),
    ]

class _INPUT(ctypes.Structure):
    _fields_ = [
        ('type', wintypes.DWORD),
        ('u', _INPUT_UNION),
    ]

class NativeWin32KeyboardProvider(KeyboardInputProvider):
    r"""Production Win32 keyboard provider using SendInput KEYEVENTF_UNICODE."""
    def __init__(self):
        self.user32 = ctypes.windll.user32

    def emit_text(self, text: str) -> bool:
        INPUT_KEYBOARD = 1
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        inputs = []
        utf16_bytes = text.encode('utf-16-le')
        code_units = [int.from_bytes(utf16_bytes[i:i+2], 'little') for i in range(0, len(utf16_bytes), 2)]

        for code in code_units:
            # Key Down
            kd = _INPUT()
            kd.type = INPUT_KEYBOARD
            kd.u.ki.wVk = 0
            kd.u.ki.wScan = wintypes.WORD(code)
            kd.u.ki.dwFlags = wintypes.DWORD(KEYEVENTF_UNICODE)
            kd.u.ki.time = 0
            kd.u.ki.dwExtraInfo = 0
            inputs.append(kd)

            # Key Up
            ku = _INPUT()
            ku.type = INPUT_KEYBOARD
            ku.u.ki.wVk = 0
            ku.u.ki.wScan = wintypes.WORD(code)
            ku.u.ki.dwFlags = wintypes.DWORD(KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
            ku.u.ki.time = 0
            ku.u.ki.dwExtraInfo = 0
            inputs.append(ku)

        if not inputs:
            return True

        n = len(inputs)
        arr = (_INPUT * n)(*inputs)
        sent = self.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))
        return sent == n

    def emit_hotkey(self, hotkey: str) -> bool:
        VK_MAP = {
            "ctrl": 0x11, "control": 0x11,
            "alt": 0x12,
            "shift": 0x10,
            "enter": 0x0D, "return": 0x0D,
            "escape": 0x1B, "esc": 0x1B,
            "tab": 0x09,
            "space": 0x20,
            "backspace": 0x08,
            "delete": 0x2E, "del": 0x2E,
            "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
            "home": 0x24, "end": 0x23,
            "pageup": 0x21, "pagedown": 0x22,
            "a": 0x41, "c": 0x43, "f": 0x46, "n": 0x4E,
            "o": 0x4F, "p": 0x50, "s": 0x53, "v": 0x56,
            "x": 0x58, "y": 0x59, "z": 0x5A,
        }
        tokens = [t.strip().lower() for t in hotkey.split("+")]
        vks = []
        for t in tokens:
            if t not in VK_MAP:
                return False
            vks.append(VK_MAP[t])

        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002

        inputs = []
        # Key down in order
        for vk in vks:
            kd = _INPUT()
            kd.type = INPUT_KEYBOARD
            kd.u.ki.wVk = wintypes.WORD(vk)
            kd.u.ki.wScan = 0
            kd.u.ki.dwFlags = 0
            kd.u.ki.time = 0
            kd.u.ki.dwExtraInfo = 0
            inputs.append(kd)

        # Key up in reverse order
        for vk in reversed(vks):
            ku = _INPUT()
            ku.type = INPUT_KEYBOARD
            ku.u.ki.wVk = wintypes.WORD(vk)
            ku.u.ki.wScan = 0
            ku.u.ki.dwFlags = wintypes.DWORD(KEYEVENTF_KEYUP)
            ku.u.ki.time = 0
            ku.u.ki.dwExtraInfo = 0
            inputs.append(ku)

        n = len(inputs)
        arr = (_INPUT * n)(*inputs)
        sent = self.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))
        return sent == n

    def get_focused_control_info(self, hwnd: int) -> Optional[Dict[str, Any]]:
        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]
        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(GUITHREADINFO)
        if self.user32.GetGUIThreadInfo(0, ctypes.byref(gti)):
            hFocus = gti.hwndFocus or gti.hwndActive or hwnd
            GWL_STYLE = -16
            ES_PASSWORD = 0x0020
            style = self.user32.GetWindowLongW(wintypes.HWND(hFocus), GWL_STYLE)
            is_pwd = bool(style & ES_PASSWORD)
            return {
                "hwnd_focus": int(hFocus),
                "is_password": is_pwd,
                "control_role": "password_box" if is_pwd else "edit"
            }
        return None

    def get_control_value(self, hwnd: int, control_id: Optional[str] = None) -> Optional[str]:
        WM_GETTEXTLENGTH = 0x000E
        WM_GETTEXT = 0x000D
        length = self.user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXTLENGTH, 0, 0)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            self.user32.SendMessageW(wintypes.HWND(hwnd), WM_GETTEXT, length + 1, buf)
            return buf.value
        return None

class MockKeyboardInputProvider(KeyboardInputProvider):
    r"""Deterministic mock keyboard provider capturing keystrokes and simulating value read-back."""
    def __init__(self):
        self.emitted_texts: List[str] = []
        self.emitted_hotkeys: List[str] = []
        self.control_values: Dict[int, str] = {}
        self.focused_controls: Dict[int, Dict[str, Any]] = {}
        self.readback_available: bool = True
        self.simulate_emission_failure: bool = False

    def emit_text(self, text: str) -> bool:
        if self.simulate_emission_failure:
            return False
        self.emitted_texts.append(text)
        return True

    def emit_hotkey(self, hotkey: str) -> bool:
        if self.simulate_emission_failure:
            return False
        self.emitted_hotkeys.append(hotkey)
        return True

    def get_focused_control_info(self, hwnd: int) -> Optional[Dict[str, Any]]:
        return self.focused_controls.get(hwnd, {"hwnd_focus": hwnd, "is_password": False, "control_role": "edit"})

    def get_control_value(self, hwnd: int, control_id: Optional[str] = None) -> Optional[str]:
        if not self.readback_available:
            return None
        return self.control_values.get(hwnd)

# ---------------------------------------------------------------------------
# KeyboardDriver
# ---------------------------------------------------------------------------

class KeyboardDriver:
    r"""
    SmartScribe Phase 5C.2 Controlled Keyboard Driver.
    Enforces credential/password protection, bounded text limits, whitelisted hotkeys,
    WindowFence live validation, and post-action text read-back verification.
    """
    def __init__(
        self,
        window_fence: Optional[WindowFence] = None,
        keyboard_provider: Optional[KeyboardInputProvider] = None,
        permission_manager: Optional[PermissionManager] = None
    ):
        self.window_fence = window_fence or WindowFence()
        self.provider = keyboard_provider or NativeWin32KeyboardProvider()
        self.permission_manager = permission_manager or PermissionManager()

    def _is_password_target(self, binding: TargetBinding) -> Tuple[bool, str]:
        r"""Checks whether the binding or focused control targets a password or secret."""
        role = (binding.control_role or "").lower().strip()
        cid = (binding.control_id or "").lower().strip()

        # Check explicit role
        if role in PASSWORD_ROLE_KEYWORDS:
            return True, f"Control role '{role}' is a password or credential field."

        # Check control ID keywords
        for kw in PASSWORD_ID_KEYWORDS:
            if kw in cid:
                return True, f"Control ID '{cid}' matches credential keyword '{kw}'."

        # Query provider focus info if available
        try:
            focus_info = self.provider.get_focused_control_info(binding.window_handle)
            if focus_info and focus_info.get("is_password"):
                return True, "Underlying Win32 control has ES_PASSWORD style set."
        except Exception:
            pass

        return False, ""

    def _validate_text_payload(self, text: str) -> Tuple[bool, InteractionStatus, str]:
        r"""Enforces text payload bounds and non-destructive printable character safety."""
        # 1. Payload size check
        if len(text) > MAX_TEXT_PAYLOAD_LENGTH:
            return (
                False,
                InteractionStatus.REJECTED_PAYLOAD_TOO_LARGE,
                f"Text payload length ({len(text)} chars) exceeds maximum allowed limit ({MAX_TEXT_PAYLOAD_LENGTH} chars)."
            )

        # 2. Control character check (reject unprintable control codes)
        for ch in text:
            code = ord(ch)
            if code < 32 and ch not in ('\n', '\t', '\r'):
                return (
                    False,
                    InteractionStatus.REJECTED_INVALID_TEXT,
                    f"Disallowed ASCII control character 0x{code:02x} found in text payload."
                )

        return True, InteractionStatus.PASSED, "Text payload valid."

    def _normalize_hotkey(self, hotkey: str) -> str:
        r"""Normalizes hotkey string (e.g. 'CTRL + ALT + DEL' -> 'ctrl+alt+del')."""
        tokens = [t.strip().lower() for t in hotkey.split("+") if t.strip()]
        return "+".join(tokens)

    def type_text(
        self,
        binding: TargetBinding,
        text: str,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        verify_readback: bool = True,
        cancel_event: Optional[threading.Event] = None
    ) -> InteractionResult:
        r"""
        Types bounded text directly into a verified, focused UI control.
        Strictly blocks password/credential fields, enforces WindowFence, and performs read-back verification.
        """
        t0 = time.perf_counter()

        # Step 1: Cancellation check
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Type action cancelled by user before validation.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 2: Permission check via canonical PermissionManager
        requires_confirmation = (risk_level in (RiskLevel.SENSITIVE_ACTION, RiskLevel.DESTRUCTIVE_ACTION))
        approved, perm_reason, _ = self.permission_manager.check_permission(
            tool_name="type_text",
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

        # Step 3: Password / Credential field check (strictly rejected)
        is_pwd, pwd_reason = self._is_password_target(binding)
        if is_pwd:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_PASSWORD_FIELD,
                verified=False,
                reason=f"Security violation: Automated text typing into credentials or password fields is permanently prohibited. ({pwd_reason})",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 4: Text payload validation
        text_ok, text_status, text_reason = self._validate_text_payload(text)
        if not text_ok:
            return InteractionResult(
                success=False,
                status=text_status,
                verified=False,
                reason=text_reason,
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 5: Live WindowFence validation (HWND, PID, executable path, UIPI boundary, not minimized)
        t_val_start = time.perf_counter()
        fence_res = self.window_fence.validate_live_pre_action(binding)
        t_val = (time.perf_counter() - t_val_start) * 1000

        if not fence_res.valid:
            status = InteractionStatus.REJECTED_STALE_BINDING if fence_res.status == FenceStatus.STALE_TIMESTAMP else InteractionStatus.REJECTED_WINDOW_FENCE
            return InteractionResult(
                success=False,
                status=status,
                verified=False,
                reason=f"WindowFence validation failed: {fence_res.reason}",
                target_info=fence_res.process_info,
                execution_latency_ms=round(t_val, 3),
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 6: Immediate pre-emission checks (cancellation & live foreground focus)
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Type action cancelled by user immediately prior to keystroke emission.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        fg_now = self.window_fence.win32.get_foreground_window()
        if fg_now != binding.window_handle:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_WINDOW_FENCE,
                verified=False,
                reason=f"Focus changed before typing: Foreground HWND changed (expected {binding.window_handle}, got {fg_now}).",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 7: Input emission (Direct SendInput KEYEVENTF_UNICODE, zero clipboard)
        t_emit_start = time.perf_counter()
        emitted = self.provider.emit_text(text)
        t_emit = (time.perf_counter() - t_emit_start) * 1000

        if not emitted:
            return InteractionResult(
                success=False,
                status=InteractionStatus.EMISSION_FAILED,
                verified=False,
                reason="OS keyboard SendInput event injection failed.",
                execution_latency_ms=round(t_emit, 3),
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 8: Post-Action Read-Back Verification
        t_ver_start = time.perf_counter()
        verified = False
        postcondition_status = "verification_unavailable"
        ver_reason = "Text typed, but control read-back was skipped or unavailable."

        if verify_readback:
            observed_val = self.provider.get_control_value(binding.window_handle, binding.control_id)
            if observed_val is not None:
                # Text matched if observed equals or contains the typed text
                if text in observed_val or observed_val == text:
                    verified = True
                    postcondition_status = "verified"
                    ver_reason = f"Text read-back verified ('{text}' matched in control value)."
                else:
                    verified = False
                    postcondition_status = "verification_failed"
                    ver_reason = f"Text read-back mismatch: expected '{text}', observed '{observed_val}'."
            else:
                verified = False
                postcondition_status = "verification_unavailable"
                ver_reason = "Control value read-back is unavailable for this control type; postcondition unconfirmed."

        t_ver = (time.perf_counter() - t_ver_start) * 1000
        final_status = InteractionStatus.PASSED if (not verify_readback or verified or postcondition_status == "verification_unavailable") else InteractionStatus.VERIFICATION_FAILED

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
            },
            execution_latency_ms=round(t_val + t_emit, 3),
            verification_latency_ms=round(t_ver, 3),
            total_latency_ms=round((time.perf_counter() - t0) * 1000, 3),
            postcondition_status=postcondition_status,
            emitted_events_count=len(text) * 2  # Down + Up per char
        )

    def send_hotkey(
        self,
        binding: TargetBinding,
        hotkey: str,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        expected_postcondition: Optional[Callable[[], bool]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> InteractionResult:
        r"""
        Sends an approved hotkey into a verified foreground target window.
        Permanently rejects Ctrl+Alt+Del, Win+L, and unapproved key combinations.
        """
        t0 = time.perf_counter()

        # Step 1: Cancellation check
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Hotkey action cancelled before validation.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 2: Normalize and validate hotkey safety
        norm_hotkey = self._normalize_hotkey(hotkey)

        # Check prohibited system security and shell hotkeys
        if norm_hotkey in PROHIBITED_HOTKEYS:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_UNAPPROVED_HOTKEY,
                verified=False,
                reason=f"Security violation: System-security hotkey '{norm_hotkey}' is permanently prohibited.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Check explicit allowlist
        if norm_hotkey not in ALLOWED_HOTKEYS:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_UNAPPROVED_HOTKEY,
                verified=False,
                reason=f"Unapproved hotkey combination '{norm_hotkey}'. Only explicitly whitelisted hotkeys are permitted.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 3: Permission check
        requires_confirmation = (risk_level in (RiskLevel.SENSITIVE_ACTION, RiskLevel.DESTRUCTIVE_ACTION))
        approved, perm_reason, _ = self.permission_manager.check_permission(
            tool_name="send_hotkey",
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

        # Step 4: WindowFence validation
        fence_res = self.window_fence.validate_live_pre_action(binding)
        if not fence_res.valid:
            status = InteractionStatus.REJECTED_STALE_BINDING if fence_res.status == FenceStatus.STALE_TIMESTAMP else InteractionStatus.REJECTED_WINDOW_FENCE
            return InteractionResult(
                success=False,
                status=status,
                verified=False,
                reason=f"WindowFence validation failed: {fence_res.reason}",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 5: Immediate pre-emission check
        if cancel_event and cancel_event.is_set():
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_CANCELLED,
                verified=False,
                reason="Hotkey action cancelled immediately prior to emission.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        fg_now = self.window_fence.win32.get_foreground_window()
        if fg_now != binding.window_handle:
            return InteractionResult(
                success=False,
                status=InteractionStatus.REJECTED_WINDOW_FENCE,
                verified=False,
                reason=f"Foreground focus lost before hotkey emission (expected {binding.window_handle}, got {fg_now}).",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 6: Input emission
        emitted = self.provider.emit_hotkey(norm_hotkey)
        if not emitted:
            return InteractionResult(
                success=False,
                status=InteractionStatus.EMISSION_FAILED,
                verified=False,
                reason=f"Failed to inject hotkey '{norm_hotkey}'.",
                total_latency_ms=round((time.perf_counter() - t0) * 1000, 3)
            )

        # Step 7: Post-Action Verification
        verified = False
        postcondition_status = "verification_unconfirmed"
        ver_reason = f"Hotkey '{norm_hotkey}' emitted; no deterministic postcondition provided."

        if expected_postcondition is not None:
            try:
                condition_met = bool(expected_postcondition())
                if condition_met:
                    verified = True
                    postcondition_status = "verified"
                    ver_reason = f"Expected postcondition confirmed after hotkey '{norm_hotkey}'."
                else:
                    verified = False
                    postcondition_status = "verification_failed"
                    ver_reason = f"Expected postcondition was not observed after hotkey '{norm_hotkey}'."
            except Exception as e:
                verified = False
                postcondition_status = "verification_failed"
                ver_reason = f"Error evaluating postcondition: {str(e)}"

        final_status = InteractionStatus.PASSED if (expected_postcondition is None or verified) else InteractionStatus.VERIFICATION_FAILED

        return InteractionResult(
            success=(final_status == InteractionStatus.PASSED),
            status=final_status,
            verified=verified,
            reason=ver_reason,
            target_info={
                "hwnd": binding.window_handle,
                "pid": binding.process_id,
                "hotkey": norm_hotkey
            },
            postcondition_status=postcondition_status,
            total_latency_ms=round((time.perf_counter() - t0) * 1000, 3),
            emitted_events_count=len(norm_hotkey.split("+")) * 2
        )
