"""
SmartScribe Phase 5C.3.2: Safe DOM Discovery, Fingerprinting & Sensitive-Field Classification.

Architecture & Security Invariants:
  1. Safe DOM Discovery:
     Discovers visible, enabled, interactive DOM controls (buttons, links, inputs, selects, ARIA controls).
     Strictly bounds target count (MAX_DISCOVERED_TARGETS) to prevent DOM exhaustion.
  2. Deterministic Fingerprinting:
     Derived from canonical tag, role, id, name, type, form context, and structural path.
     Uses SHA-256 cryptographic hashing (sha256:<hex>).
     Does not rely solely on transient text, screen coordinates, or volatile array indexes.
  3. Sensitive-Field Classification:
     Multi-attribute conservative classifier inspecting input types (password, hidden),
     autocomplete attributes, name/id/class/placeholder/label credential patterns,
     and authentication/payment form contexts.
     Conservative policy: any ambiguous credential-like field is marked sensitive.
  4. Data Minimization:
     Zero cookies, zero storage, zero credentials, bounded text previews (<= 100 chars).
  5. Frame & Origin Policy:
     Top-level (frame_id=0) supported. Cross-origin and sandboxed iframes rejected.
     Prohibited schemes (chrome://, about:, edge://, data:, javascript:) rejected.
  6. Zero Action Emission Invariant:
     Pure observation layer: zero clicks, zero keystrokes, zero clipboard access, zero OS synthetic input.
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, List, Set
from urllib.parse import urlparse

from backend.assistant.interaction.browser_target_binding import (
    BrowserTargetBinding,
    normalize_origin,
    normalize_url_path,
)

MAX_DISCOVERED_TARGETS = 100
MAX_TEXT_PREVIEW_LEN = 100

# Canonical interactive HTML tags and interactive ARIA roles
INTERACTIVE_TAGS = {"BUTTON", "A", "INPUT", "TEXTAREA", "SELECT"}
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "checkbox", "radio",
    "combobox", "option", "tab", "menuitem", "switch"
}

# Credential & Sensitive Patterns for Multi-Attribute Classification
SENSITIVE_INPUT_TYPES = {"password", "hidden"}
SENSITIVE_AUTOCOMPLETE_TOKENS = {
    "current-password", "new-password", "one-time-code", "cc-number",
    "cc-exp", "cc-csc", "cc-type", "cc-exp-month", "cc-exp-year",
    "transaction-amount", "bday", "tel", "transaction-currency"
}

SENSITIVE_KEYWORDS = [
    "password", "passwd", "pwd", "secret", "token", "auth", "login",
    "signin", "otp", "2fa", "mfa", "cvv", "cvc", "creditcard", "cardnumber",
    "card_number", "ssn", "pin", "security_code", "security", "sec_code",
    "passcode", "api_key", "apikey"
]

SENSITIVE_FORM_KEYWORDS = ["login", "signin", "auth", "checkout", "payment", "account", "security"]

# Prohibited internal or unsafe schemes
PROHIBITED_SCHEMES = {
    "chrome", "edge", "about", "chrome-extension", "data", "blob", "javascript", "file"
}


@dataclass(frozen=True)
class TargetGeometry:
    """Viewport-relative geometry of an interactive DOM element."""
    left: float
    top: float
    right: float
    bottom: float
    width: float
    height: float

    def to_tuple(self) -> Tuple[float, float, float, float]:
        return (self.left, self.top, self.right, self.bottom)

    def is_valid(self) -> bool:
        return (
            self.width > 0 and
            self.height > 0 and
            isinstance(self.left, (int, float)) and
            isinstance(self.top, (int, float))
        )


def compute_dom_fingerprint(
    tag: str,
    role: str = "",
    element_id: str = "",
    name: str = "",
    input_type: str = "",
    href: str = "",
    form_context: str = "",
    structural_path: str = ""
) -> str:
    """
    Computes a deterministic SHA-256 fingerprint for a DOM element.
    Derived purely from stable structural properties.
    """
    norm_tag = str(tag or "").strip().upper()
    norm_role = str(role or "").strip().lower()
    norm_id = str(element_id or "").strip()
    norm_name = str(name or "").strip()
    norm_type = str(input_type or "").strip().lower()
    norm_href = str(href or "").strip()
    norm_form = str(form_context or "").strip().lower()
    norm_path = str(structural_path or "").strip()

    identity_payload = (
        f"tag:{norm_tag}|role:{norm_role}|id:{norm_id}|name:{norm_name}|"
        f"type:{norm_type}|href:{norm_href}|form:{norm_form}|path:{norm_path}"
    )

    digest = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def classify_element_sensitivity(
    tag: str,
    input_type: str = "",
    autocomplete: str = "",
    element_id: str = "",
    name: str = "",
    classes: str = "",
    placeholder: str = "",
    aria_label: str = "",
    label_text: str = "",
    form_context: str = ""
) -> Tuple[bool, List[str]]:
    """
    Conservative multi-attribute sensitivity classifier.
    Returns (is_sensitive: bool, detected_reasons: List[str]).
    """
    reasons: List[str] = []

    norm_type = str(input_type or "").strip().lower()
    if norm_type in SENSITIVE_INPUT_TYPES:
        reasons.append(f"input_type:{norm_type}")

    norm_ac = str(autocomplete or "").strip().lower()
    if any(tok in norm_ac for tok in SENSITIVE_AUTOCOMPLETE_TOKENS):
        reasons.append(f"autocomplete:{norm_ac}")

    # Inspect attributes against sensitive credential keywords
    attributes_to_check = {
        "id": str(element_id or "").lower(),
        "name": str(name or "").lower(),
        "class": str(classes or "").lower(),
        "placeholder": str(placeholder or "").lower(),
        "aria-label": str(aria_label or "").lower(),
        "label": str(label_text or "").lower(),
    }

    for attr_name, attr_val in attributes_to_check.items():
        clean_val = attr_val.replace("_", "").replace("-", "")
        for kw in SENSITIVE_KEYWORDS:
            clean_kw = kw.replace("_", "").replace("-", "")
            if kw in attr_val or clean_kw in clean_val:
                reasons.append(f"{attr_name}_pattern:{kw}")

    # Inspect enclosing form context
    norm_form = str(form_context or "").strip().lower()
    if norm_form and any(f_kw in norm_form for f_kw in SENSITIVE_FORM_KEYWORDS):
        # If inside a login or payment form, and element is an input (not submit button)
        norm_tag = str(tag or "").strip().upper()
        if norm_tag == "INPUT" and norm_type not in ("submit", "button", "reset"):
            reasons.append(f"auth_form_context:{norm_form}")

    is_sensitive = len(reasons) > 0
    return is_sensitive, sorted(list(set(reasons)))


class DOMDiscoveryEngine:
    """
    Backend coordinator for safe browser DOM discovery and validation.
    Receives raw target structures from the extension bridge, applies backend-side
    validation, enforces origin and frame isolation policies, and reconstructs
    immutable BrowserTargetBinding objects.
    """

    @staticmethod
    def validate_origin(origin: str) -> Tuple[bool, str]:
        """Validates origin against prohibited internal schemes."""
        if not origin or not isinstance(origin, str):
            return False, "Missing or invalid origin."
        norm = normalize_origin(origin)
        if not norm:
            return False, f"Origin '{origin}' is invalid or uses a prohibited scheme."
        try:
            parsed = urlparse(norm)
            if parsed.scheme.lower() in PROHIBITED_SCHEMES:
                return False, f"Prohibited scheme: {parsed.scheme}"
        except Exception as e:
            return False, f"Malformed origin: {e}"
        return True, norm

    @staticmethod
    def validate_frame_policy(frame_id: int) -> Tuple[bool, str]:
        """
        Enforces frame isolation policy.
        Top-level (frame_id == 0) is primary supported discovery surface.
        Cross-origin and untrusted frames are rejected.
        """
        if frame_id != 0:
            return False, f"Frame {frame_id} rejected: Only top-level frame (frame_id=0) is authorized."
        return True, "Top-level frame authorized."

    @classmethod
    def process_discovery_payload(
        cls,
        payload: Dict[str, Any],
        expected_origin: str,
        tab_id: int,
        window_id: int
    ) -> List[BrowserTargetBinding]:
        """
        Validates raw discovery payload from the browser extension and produces
        immutable BrowserTargetBinding objects.
        """
        if not isinstance(payload, dict):
            raise ValueError("Discovery payload must be a JSON dictionary.")

        raw_origin = payload.get("origin", expected_origin)
        valid_origin, norm_origin = cls.validate_origin(raw_origin)
        if not valid_origin:
            raise PermissionError(f"Origin security violation: {norm_origin}")

        raw_targets = payload.get("targets", [])
        if not isinstance(raw_targets, list):
            raise ValueError("Payload 'targets' field must be a list.")

        # Enforce target count upper bound
        clamped_targets = raw_targets[:MAX_DISCOVERED_TARGETS]
        bindings: List[BrowserTargetBinding] = []

        url_path = normalize_url_path(payload.get("url_path", "/"))
        page_title = str(payload.get("title", ""))[:100]

        for item in clamped_targets:
            if not isinstance(item, dict):
                continue

            frame_id = int(item.get("frame_id", 0))
            frame_valid, _ = cls.validate_frame_policy(frame_id)
            if not frame_valid:
                continue  # Reject non-top-level frame target

            tag = str(item.get("tag", "")).strip().upper()
            role = str(item.get("role", "")).strip().lower()

            # Filter non-interactive elements
            if tag not in INTERACTIVE_TAGS and role not in INTERACTIVE_ROLES:
                continue

            # Parse visibility & enabled flags
            is_visible = bool(item.get("is_visible", True))
            is_enabled = bool(item.get("is_enabled", True))

            # Non-visible targets are excluded from action-capable bindings
            if not is_visible:
                continue

            # Geometry
            bb_data = item.get("bounding_box") or item.get("bounding_box_viewport")
            bb_tuple = None
            if bb_data and isinstance(bb_data, (list, tuple)) and len(bb_data) >= 4:
                try:
                    bb_tuple = (
                        float(bb_data[0]), float(bb_data[1]),
                        float(bb_data[2]), float(bb_data[3])
                    )
                except (ValueError, TypeError):
                    bb_tuple = None
            elif isinstance(bb_data, dict):
                try:
                    bb_tuple = (
                        float(bb_data.get("left", 0.0)),
                        float(bb_data.get("top", 0.0)),
                        float(bb_data.get("right", 0.0)),
                        float(bb_data.get("bottom", 0.0))
                    )
                except (ValueError, TypeError):
                    bb_tuple = None

            # Selector
            selector = str(item.get("dom_selector", "")).strip()

            # Text preview (bounded, data-minimization)
            text_preview = str(item.get("text_preview", "")).strip()
            if len(text_preview) > MAX_TEXT_PREVIEW_LEN:
                text_preview = text_preview[:MAX_TEXT_PREVIEW_LEN]

            # Independent backend sensitivity classification
            backend_is_sensitive, _ = classify_element_sensitivity(
                tag=tag,
                input_type=str(item.get("type", "")),
                autocomplete=str(item.get("autocomplete", "")),
                element_id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                classes=str(item.get("classes", "")),
                placeholder=str(item.get("placeholder", "")),
                aria_label=str(item.get("aria_label", "")),
                label_text=str(item.get("label_text", "")),
                form_context=str(item.get("form_context", ""))
            )
            # Conservative OR: either extension or backend detected sensitivity
            is_sensitive = bool(item.get("is_sensitive", False)) or backend_is_sensitive

            # If sensitive, sanitize preview text to prevent credential leaking
            if is_sensitive and tag == "INPUT":
                text_preview = "[SENSITIVE FIELD]"

            # Fingerprint calculation / verification
            fp = str(item.get("element_fingerprint", "")).strip()
            if not fp.startswith("sha256:"):
                # Compute deterministic backend fingerprint
                fp = compute_dom_fingerprint(
                    tag=tag,
                    role=role,
                    element_id=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    input_type=str(item.get("type", "")),
                    href=str(item.get("href", "")),
                    form_context=str(item.get("form_context", "")),
                    structural_path=str(item.get("structural_path", selector))
                )

            binding = BrowserTargetBinding(
                tab_id=tab_id,
                window_id=window_id,
                origin=norm_origin,
                url_path=url_path,
                frame_id=frame_id,
                dom_selector=selector,
                element_fingerprint=fp,
                target_type="dom_element",
                element_tag=tag,
                element_role=role,
                bounding_box_viewport=bb_tuple,
                is_visible=is_visible,
                is_enabled=is_enabled,
                is_sensitive=is_sensitive,
                page_title=page_title,
                element_text_preview=text_preview,
                browser_display_name=str(payload.get("browser", "Chrome/Edge")),
                created_at=time.time(),
                max_staleness_seconds=2.0
            )
            bindings.append(binding)

        return bindings
