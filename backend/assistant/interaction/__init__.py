"""
SmartScribe Phase 5C.2: Controlled Computer Interaction Foundation.
Exports TargetBinding, WindowFence, MouseDriver, KeyboardDriver, providers, and security enums.
"""

from backend.assistant.interaction.target_binding import TargetBinding
from backend.assistant.interaction.window_fence import (
    WindowFence,
    FenceStatus,
    FenceValidationResult,
    Win32Provider,
    NativeWin32Provider,
    PROHIBITED_PROCESS_INTERACTION,
    INTEGRITY_LEVEL_UNTRUSTED,
    INTEGRITY_LEVEL_LOW,
    INTEGRITY_LEVEL_MEDIUM,
    INTEGRITY_LEVEL_MEDIUM_PLUS,
    INTEGRITY_LEVEL_HIGH,
    INTEGRITY_LEVEL_SYSTEM,
    INTEGRITY_LEVEL_PROTECTED,
    integrity_level_name,
)
from backend.assistant.interaction.mouse_driver import (
    MouseDriver,
    InteractionStatus,
    InteractionResult,
    MouseInputProvider,
    NativeWin32MouseProvider,
    MockMouseInputProvider,
    PROHIBITED_CONTROL_ROLES,
    PROHIBITED_CONTROL_IDS,
)
from backend.assistant.interaction.keyboard_driver import (
    KeyboardDriver,
    KeyboardInputProvider,
    NativeWin32KeyboardProvider,
    MockKeyboardInputProvider,
    MAX_TEXT_PAYLOAD_LENGTH,
    ALLOWED_HOTKEYS,
    PROHIBITED_HOTKEYS,
)
from backend.assistant.interaction.browser_target_binding import (
    BrowserTargetBinding,
    normalize_origin,
    normalize_url_path,
)
from backend.assistant.interaction.browser_bridge import (
    BrowserBridge,
    get_browser_bridge,
    BrowserBridgeRequest,
    BrowserBridgeResponse,
    BridgeTokenManager,
    BridgeStatus,
    HEADER_SMARTSCRIBE_TOKEN,
    MAX_PAYLOAD_BYTES,
    MIN_TIMEOUT_MS,
    MAX_TIMEOUT_MS,
    DEFAULT_TIMEOUT_MS,
)
from backend.assistant.interaction.dom_discovery import (
    DOMDiscoveryEngine,
    compute_dom_fingerprint,
    classify_element_sensitivity,
    TargetGeometry,
    MAX_DISCOVERED_TARGETS,
    INTERACTIVE_TAGS,
    INTERACTIVE_ROLES,
    SENSITIVE_INPUT_TYPES,
    SENSITIVE_KEYWORDS,
)
from backend.assistant.interaction.browser_driver import (
    BrowserDriver,
    MAX_BROWSER_TEXT_LEN,
)


__all__ = [
    # 5C.1 Foundations
    "TargetBinding",
    "WindowFence",
    "FenceStatus",
    "FenceValidationResult",
    "Win32Provider",
    "NativeWin32Provider",
    "PROHIBITED_PROCESS_INTERACTION",
    "INTEGRITY_LEVEL_UNTRUSTED",
    "INTEGRITY_LEVEL_LOW",
    "INTEGRITY_LEVEL_MEDIUM",
    "INTEGRITY_LEVEL_MEDIUM_PLUS",
    "INTEGRITY_LEVEL_HIGH",
    "INTEGRITY_LEVEL_SYSTEM",
    "INTEGRITY_LEVEL_PROTECTED",
    "integrity_level_name",
    # 5C.2 Mouse Interaction
    "MouseDriver",
    "InteractionStatus",
    "InteractionResult",
    "MouseInputProvider",
    "NativeWin32MouseProvider",
    "MockMouseInputProvider",
    "PROHIBITED_CONTROL_ROLES",
    "PROHIBITED_CONTROL_IDS",
    # 5C.2 Keyboard Interaction
    "KeyboardDriver",
    "KeyboardInputProvider",
    "NativeWin32KeyboardProvider",
    "MockKeyboardInputProvider",
    "MAX_TEXT_PAYLOAD_LENGTH",
    "ALLOWED_HOTKEYS",
    "PROHIBITED_HOTKEYS",
    # 5C.3.1 Browser Interaction Foundations
    "BrowserTargetBinding",
    "normalize_origin",
    "normalize_url_path",
    "BrowserBridge",
    "get_browser_bridge",
    "BrowserBridgeRequest",
    "BrowserBridgeResponse",
    "BridgeTokenManager",
    "BridgeStatus",
    "HEADER_SMARTSCRIBE_TOKEN",
    "MAX_PAYLOAD_BYTES",
    "MIN_TIMEOUT_MS",
    "MAX_TIMEOUT_MS",
    "DEFAULT_TIMEOUT_MS",
    # 5C.3.2 Safe DOM Discovery & Classification
    "DOMDiscoveryEngine",
    "compute_dom_fingerprint",
    "classify_element_sensitivity",
    "TargetGeometry",
    "MAX_DISCOVERED_TARGETS",
    "INTERACTIVE_TAGS",
    "INTERACTIVE_ROLES",
    "SENSITIVE_INPUT_TYPES",
    "SENSITIVE_KEYWORDS",
    # 5C.3.3 Controlled Browser Action Driver
    "BrowserDriver",
    "MAX_BROWSER_TEXT_LEN",
]

