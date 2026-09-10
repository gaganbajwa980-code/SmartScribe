import os
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any

def normalize_executable_path(path: str) -> str:
    r"""
    Normalizes a Windows file or executable path for deterministic equality checks:
    - Resolves relative paths to absolute
    - Resolves case-insensitivity on Windows (os.path.normcase)
    - Strips trailing separators and normalizes slashes
    """
    if not path or not isinstance(path, str):
        return ""
    try:
        norm = os.path.normcase(os.path.abspath(os.path.normpath(path.strip())))
        return norm
    except Exception:
        return path.lower().strip()

@dataclass(frozen=True)
class TargetBinding:
    r"""
    Cryptographically and structurally immutable contract binding an interaction step
    to a verified operating system window and UI component.

    Primary Security Identity (Authoritative Invariant):
      - window_handle (HWND)
      - process_id (PID)
      - executable_path (canonical on-disk image path)
      - executable_name (process image name)

    Target Component:
      - target_type ("window" | "ui_element" | "browser_tab" | "dom_element")
      - control_id (UIAutomation AutomationId / RuntimeId / DOM selector)
      - control_role (UIAutomation ControlType / DOM Role)
      - bounding_box_client (left, top, right, bottom) in client rect coordinates

    Secondary Context Only (Non-Authoritative):
      - expected_title_pattern (qualitative regex pattern for sanity diagnostics)
      - app_display_name (presentation label for Command Center UI cards)

    Temporal Metadata:
      - created_at (timestamp of binding creation)
      - max_staleness_seconds (early invalidation threshold; exceeding forces re-inspection)

    Invariants:
      1. HWND, PID, and executable_path are authoritative.
      2. Title/app name are never security authorization.
      3. A stale timestamp invalidates the binding early.
      4. A fresh timestamp NEVER authorizes execution by itself. Live pre-action validation is mandatory.
    """
    # -------------------------------------------------------------
    # 1. PRIMARY SECURITY IDENTITY (Mandatory Invariant - Live Match)
    # -------------------------------------------------------------
    window_handle: int                        # Win32 HWND descriptor
    process_id: int                           # Win32 Process Identifier (PID)
    executable_path: str                      # Canonical on-disk image path (QueryFullProcessImageNameW)
    executable_name: str                      # Process binary name (e.g. "chrome.exe", "notepad.exe")

    # -------------------------------------------------------------
    # 2. TARGET COMPONENT IDENTITY (Element / DOM Resolution)
    # -------------------------------------------------------------
    target_type: str = "window"               # "window" | "ui_element" | "browser_tab" | "dom_element"
    control_id: Optional[str] = None          # UIAutomation AutomationId / RuntimeId / DOM selector
    control_role: Optional[str] = None        # UIAutomation ControlType / DOM Role (e.g. "button", "link")
    bounding_box_client: Optional[Tuple[int, int, int, int]] = None  # (left, top, right, bottom) in client rect

    # -------------------------------------------------------------
    # 3. SECONDARY CONTEXT ONLY (Non-Authoritative - Qualitative Sanity)
    # -------------------------------------------------------------
    expected_title_pattern: str = ""          # Qualitative regex pattern for human diagnostic logs
    app_display_name: str = ""                # Presentation label for Command Center UI cards

    # -------------------------------------------------------------
    # 4. TEMPORAL BOUNDARY (Early Invalidator ONLY - NOT Authorization)
    # -------------------------------------------------------------
    created_at: float = field(default_factory=time.time)
    max_staleness_seconds: float = 3.0        # Early invalidation threshold (exceeding forces re-inspection)

    def is_stale(self, now: Optional[float] = None) -> bool:
        """
        Early invalidation check: Returns True if binding exceeds maximum lifetime.
        Note: is_stale() == False is NOT authorization to execute. Live validation is still required.
        """
        current_time = now if now is not None else time.time()
        return (current_time - self.created_at) > self.max_staleness_seconds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_handle": self.window_handle,
            "process_id": self.process_id,
            "executable_path": self.executable_path,
            "executable_name": self.executable_name,
            "target_type": self.target_type,
            "control_id": self.control_id,
            "control_role": self.control_role,
            "bounding_box_client": list(self.bounding_box_client) if self.bounding_box_client else None,
            "expected_title_pattern": self.expected_title_pattern,
            "app_display_name": self.app_display_name,
            "created_at": self.created_at,
            "max_staleness_seconds": self.max_staleness_seconds
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TargetBinding":
        bb = data.get("bounding_box_client")
        return cls(
            window_handle=int(data["window_handle"]),
            process_id=int(data["process_id"]),
            executable_path=normalize_executable_path(data.get("executable_path", "")),
            executable_name=str(data.get("executable_name", "")).lower().strip(),
            target_type=str(data.get("target_type", "window")),
            control_id=data.get("control_id"),
            control_role=data.get("control_role"),
            bounding_box_client=tuple(bb) if bb else None,
            expected_title_pattern=str(data.get("expected_title_pattern", "")),
            app_display_name=str(data.get("app_display_name", "")),
            created_at=float(data.get("created_at", time.time())),
            max_staleness_seconds=float(data.get("max_staleness_seconds", 3.0))
        )
