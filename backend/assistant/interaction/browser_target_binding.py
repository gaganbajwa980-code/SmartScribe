"""
SmartScribe Phase 5C.3.1: BrowserTargetBinding Contract.

Cryptographically and structurally immutable contract binding a browser interaction step
to a verified browser tab, frame, origin, URL path, and DOM element.

Architecture & Security Invariants:
  1. Authoritative Identity:
     tab_id, window_id, origin, url_path, frame_id, dom_selector, and element_fingerprint
     form the primary security identity. A matching title or app name NEVER compensates
     for a mismatched origin, tab, or element fingerprint.
  2. Temporal Boundary:
     is_stale() is strictly an early invalidation check. A fresh timestamp NEVER authorizes
     execution by itself. Live pre-action target validation is mandatory.
  3. Sensitive Target Protection:
     is_sensitive marks password fields, hidden tokens, credit cards, or security controls.
  4. Immutability:
     BrowserTargetBinding is a frozen dataclass. Any mutation attempt raises FrozenInstanceError.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse


def normalize_origin(url_or_origin: str) -> str:
    """
    Normalizes a URL or origin to canonical scheme://host[:port] in lowercase.
    Rejects invalid schemes, relative URLs, or internal schemes (chrome://, about:, edge://).
    """
    if not url_or_origin or not isinstance(url_or_origin, str):
        return ""
    val = url_or_origin.strip()
    lower_val = val.lower()

    # Reject known internal browser schemes
    if lower_val.startswith(("about:", "chrome:", "edge:", "file:", "data:", "javascript:", "blob:")):
        return ""

    if not lower_val.startswith(("http://", "https://")):
        if "://" in lower_val:
            return ""  # Disallow other schemes with ://
        val = "https://" + val
    try:
        parsed = urlparse(val)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            return ""
        netloc = parsed.netloc.lower()
        if not netloc:
            return ""
        return f"{scheme}://{netloc}"
    except Exception:
        return ""


def normalize_url_path(url_or_path: str) -> str:
    """
    Extracts and normalizes the URL path component (e.g. '/dashboard').
    Defaults to '/' if empty or invalid.
    """
    if not url_or_path or not isinstance(url_or_path, str):
        return "/"
    val = url_or_path.strip()
    if "://" in val:
        try:
            parsed = urlparse(val)
            path = parsed.path
            return path if path else "/"
        except Exception:
            return "/"
    if not val.startswith("/"):
        val = "/" + val
    return val


@dataclass(frozen=True)
class BrowserTargetBinding:
    """
    Cryptographically and structurally immutable contract binding a browser interaction step
    to a verified browser tab, frame, origin, URL path, and DOM element.

    Primary Security Identity (Authoritative Invariant - Live Match Required):
      - tab_id: Browser tab identifier (integer assigned by browser)
      - window_id: Browser window identifier
      - origin: Canonical origin (scheme + netloc, e.g. 'https://app.example.com')
      - url_path: Target URL path (e.g. '/checkout')
      - frame_id: Document frame identifier (0 for top-level document)
      - dom_selector: CSS / DOM selector string
      - element_fingerprint: Structural hash of element tag, id, class, and attributes

    Target Component Identity:
      - target_type: 'dom_element' | 'tab' | 'frame'
      - element_tag: HTML tag name in uppercase (e.g. 'BUTTON', 'INPUT', 'A')
      - element_role: ARIA role or computed role (e.g. 'button', 'link', 'textbox')
      - bounding_box_viewport: Viewport rect (left, top, right, bottom)
      - is_visible: Whether element is computed visible and rendered in viewport
      - is_enabled: Whether element is not disabled
      - is_sensitive: Whether element is classified as sensitive (password, card, token)

    Secondary Context Only (Non-Authoritative):
      - page_title: Title of document
      - element_text_preview: Text preview max 100 characters
      - browser_display_name: Browser binary/application name

    Temporal Boundary:
      - created_at: Creation timestamp
      - max_staleness_seconds: Early invalidation threshold (default 2.0s)

    Invariants:
      1. Tab ID, window ID, origin, and element fingerprint form the authoritative identity.
      2. Page title and element text preview are NEVER security authorization.
      3. A stale timestamp invalidates the binding early.
      4. A fresh timestamp NEVER authorizes execution by itself. Live pre-action validation is mandatory.
    """
    # -------------------------------------------------------------
    # 1. PRIMARY SECURITY IDENTITY (Authoritative Invariant)
    # -------------------------------------------------------------
    tab_id: int
    window_id: int
    origin: str
    url_path: str = "/"
    frame_id: int = 0
    dom_selector: str = ""
    element_fingerprint: str = ""

    # -------------------------------------------------------------
    # 2. TARGET COMPONENT IDENTITY (Live DOM State)
    # -------------------------------------------------------------
    target_type: str = "dom_element"
    element_tag: str = ""
    element_role: str = ""
    bounding_box_viewport: Optional[Tuple[float, float, float, float]] = None
    is_visible: bool = True
    is_enabled: bool = True
    is_sensitive: bool = False

    # -------------------------------------------------------------
    # 3. SECONDARY CONTEXT ONLY (Non-Authoritative Context)
    # -------------------------------------------------------------
    page_title: str = ""
    element_text_preview: str = ""
    browser_display_name: str = ""

    # -------------------------------------------------------------
    # 4. TEMPORAL BOUNDARY (Early Invalidator ONLY - NOT Authorization)
    # -------------------------------------------------------------
    created_at: float = field(default_factory=time.time)
    max_staleness_seconds: float = 2.0

    def is_stale(self, now: Optional[float] = None) -> bool:
        """
        Early invalidation check: Returns True if binding exceeds maximum lifetime.
        Note: is_stale() == False is NOT authorization to execute. Live validation is still required.
        """
        current_time = now if now is not None else time.time()
        return (current_time - self.created_at) > self.max_staleness_seconds

    def to_dict(self) -> Dict[str, Any]:
        """Serializes binding to JSON-serializable dictionary."""
        return {
            "tab_id": self.tab_id,
            "window_id": self.window_id,
            "origin": self.origin,
            "url_path": self.url_path,
            "frame_id": self.frame_id,
            "dom_selector": self.dom_selector,
            "element_fingerprint": self.element_fingerprint,
            "target_type": self.target_type,
            "element_tag": self.element_tag,
            "element_role": self.element_role,
            "bounding_box_viewport": list(self.bounding_box_viewport) if self.bounding_box_viewport else None,
            "is_visible": self.is_visible,
            "is_enabled": self.is_enabled,
            "is_sensitive": self.is_sensitive,
            "page_title": self.page_title,
            "element_text_preview": self.element_text_preview,
            "browser_display_name": self.browser_display_name,
            "created_at": self.created_at,
            "max_staleness_seconds": self.max_staleness_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserTargetBinding":
        """Deserializes binding from dictionary with normalization."""
        bb = data.get("bounding_box_viewport")
        return cls(
            tab_id=int(data["tab_id"]),
            window_id=int(data["window_id"]),
            origin=normalize_origin(data.get("origin", "")),
            url_path=normalize_url_path(data.get("url_path", "/")),
            frame_id=int(data.get("frame_id", 0)),
            dom_selector=str(data.get("dom_selector", "")),
            element_fingerprint=str(data.get("element_fingerprint", "")),
            target_type=str(data.get("target_type", "dom_element")),
            element_tag=str(data.get("element_tag", "")).upper(),
            element_role=str(data.get("element_role", "")).lower(),
            bounding_box_viewport=tuple(float(x) for x in bb) if bb else None,
            is_visible=bool(data.get("is_visible", True)),
            is_enabled=bool(data.get("is_enabled", True)),
            is_sensitive=bool(data.get("is_sensitive", False)),
            page_title=str(data.get("page_title", "")),
            element_text_preview=str(data.get("element_text_preview", ""))[:100],
            browser_display_name=str(data.get("browser_display_name", "")),
            created_at=float(data.get("created_at", time.time())),
            max_staleness_seconds=float(data.get("max_staleness_seconds", 2.0)),
        )
