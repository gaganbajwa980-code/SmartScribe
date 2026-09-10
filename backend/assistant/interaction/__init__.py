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
]

