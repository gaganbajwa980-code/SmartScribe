"""
SmartScribe Phase 5C.3.3: Browser Interaction Assistant Tools.

Provides canonical assistant tool wrappers for controlled browser DOM interaction:
  - click_browser_element
  - type_browser_field
  - select_browser_option

Strict Security & Schema Requirements:
  - Schemas require structured BrowserTargetBinding parameters.
  - Parameters such as x, y, javascript, or script are strictly prohibited.
  - All calls route through BrowserDriver and the 12-step authorization chain.
"""

from typing import Dict, Any, Optional

from backend.assistant.permission_manager import RiskLevel
from backend.assistant.interaction.browser_target_binding import BrowserTargetBinding
from backend.assistant.interaction.browser_driver import BrowserDriver


def tool_click_browser_element(
    target_binding: Dict[str, Any],
    confirmation_token: Optional[str] = None,
    expected_postcondition: Optional[Any] = None,
    verify_postcondition: Optional[bool] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Controlled click on a verified safe DOM element.
    Requires structured BrowserTargetBinding dictionary. Arbitrary coordinates prohibited.
    Supports observable postcondition verification (URL change, ARIA expanded/checked, etc.).
    """
    if not target_binding or not isinstance(target_binding, dict):
        return {
            "success": False,
            "status": "policy_denied",
            "error": "target_binding parameter is required and must be a dictionary."
        }

    try:
        binding = BrowserTargetBinding.from_dict(target_binding)
    except Exception as e:
        return {
            "success": False,
            "status": "policy_denied",
            "error": f"Invalid BrowserTargetBinding schema: {e}"
        }

    driver = BrowserDriver()
    risk = RiskLevel.SENSITIVE_ACTION if binding.is_sensitive else RiskLevel.LOW_RISK_ACTION
    resp = driver.click_element(
        binding=binding,
        risk_level=risk,
        confirmation_token=confirmation_token,
        expected_postcondition=expected_postcondition,
        verify_postcondition=verify_postcondition
    )
    return resp.to_dict()


def tool_type_browser_field(
    target_binding: Dict[str, Any],
    text: str,
    confirmation_token: Optional[str] = None,
    verify_readback: Optional[bool] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Controlled text entry into an approved non-sensitive browser field.
    Strictly prohibits typing into password, token, or credit card inputs.
    Supports deterministic live DOM value read-back verification.
    """
    if not target_binding or not isinstance(target_binding, dict):
        return {
            "success": False,
            "status": "policy_denied",
            "error": "target_binding parameter is required and must be a dictionary."
        }

    try:
        binding = BrowserTargetBinding.from_dict(target_binding)
    except Exception as e:
        return {
            "success": False,
            "status": "policy_denied",
            "error": f"Invalid BrowserTargetBinding schema: {e}"
        }

    driver = BrowserDriver()
    resp = driver.type_text(
        binding=binding,
        text=text,
        risk_level=RiskLevel.LOW_RISK_ACTION,
        confirmation_token=confirmation_token,
        verify_readback=verify_readback
    )
    return resp.to_dict()


def tool_select_browser_option(
    target_binding: Dict[str, Any],
    option_value: str,
    confirmation_token: Optional[str] = None,
    verify_readback: Optional[bool] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Controlled selection of an option within a verified <select> DOM element.
    Supports deterministic live DOM value/label read-back verification.
    """
    if not target_binding or not isinstance(target_binding, dict):
        return {
            "success": False,
            "status": "policy_denied",
            "error": "target_binding parameter is required and must be a dictionary."
        }

    try:
        binding = BrowserTargetBinding.from_dict(target_binding)
    except Exception as e:
        return {
            "success": False,
            "status": "policy_denied",
            "error": f"Invalid BrowserTargetBinding schema: {e}"
        }

    driver = BrowserDriver()
    resp = driver.select_option(
        binding=binding,
        value_or_label=option_value,
        risk_level=RiskLevel.LOW_RISK_ACTION,
        confirmation_token=confirmation_token,
        verify_readback=verify_readback
    )
    return resp.to_dict()
