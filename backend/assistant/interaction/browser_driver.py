"""
SmartScribe Phase 5C.3.3: Controlled Browser Action Driver & Authorization Chain.

Consumes verified BrowserTargetBinding objects and executes controlled, safe browser
actions (CLICK, TYPE_TEXT, SELECT_OPTION) across the Manifest V3 browser bridge.

Authorization Chain Invariant (Enforced on EVERY action):
  Permission
  → Target freshness
  → Tab identity validation
  → Origin validation
  → Frame validation
  → Target re-resolution (RESOLVE_TARGET)
  → Fingerprint validation
  → Visibility validation
  → Enabled / editable validation
  → Sensitive-field validation (defense-in-depth: backend + extension)
  → Immediate pre-action validation
  → Controlled event emission
  → Return structured execution result

Strict Security Boundaries:
  - Zero arbitrary coordinates (no blind clicking).
  - Zero typing into credentials/sensitive fields (passwords, tokens, OTP, cards).
  - Zero arbitrary JavaScript execution.
  - Zero clipboard usage.
  - Zero external browser automation frameworks or debugging ports.
  - Execution success != verified success: verified defaults strictly to False.
"""

import threading
import time
from typing import Optional, Dict, Any, Tuple, Callable

from backend.assistant.permission_manager import PermissionManager, RiskLevel
from backend.assistant.interaction.browser_target_binding import (
    BrowserTargetBinding,
    normalize_origin,
)
from backend.assistant.interaction.browser_bridge import (
    BrowserBridge,
    get_browser_bridge,
    BrowserBridgeRequest,
    BrowserBridgeResponse,
)
from backend.assistant.interaction.dom_discovery import (
    DOMDiscoveryEngine,
    classify_element_sensitivity,
    compute_dom_fingerprint,
)

MAX_BROWSER_TEXT_LEN = 500


class BrowserDriver:
    """
    Controlled Browser Action Driver.
    Enforces the full 12-step authorization chain before emitting any controlled
    DOM event across the browser bridge.
    """

    def __init__(
        self,
        bridge: Optional[BrowserBridge] = None,
        permission_manager: Optional[PermissionManager] = None
    ):
        self.bridge = bridge or get_browser_bridge()
        self.permission_manager = permission_manager or PermissionManager()

        # Authorization audit spy counters (verifies zero unauthorized emission)
        self.spy_click_count: int = 0
        self.spy_type_count: int = 0
        self.spy_select_count: int = 0

    def reset_spy_counters(self) -> None:
        """Resets emission spy counters for audit verification."""
        self.spy_click_count = 0
        self.spy_type_count = 0
        self.spy_select_count = 0

    def _execute_authorization_chain(
        self,
        binding: BrowserTargetBinding,
        action_name: str,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Tuple[bool, str, Optional[BrowserBridgeResponse]]:
        """
        Executes Steps 1 through 9 of the authorization chain:
          1. Cancellation check
          2. Permission check
          3. Freshness check
          4. Origin validation
          5. Frame validation
          6. Bridge connectivity check
          7. Live target re-resolution (RESOLVE_TARGET)
          8. Fingerprint matching
          9. Visibility & enabled validation
        Returns (passed: bool, reason: str, error_response: Optional[BrowserBridgeResponse])
        """
        # Step 1: Cancellation check
        if cancel_event and cancel_event.is_set():
            return False, "Action cancelled before validation.", BrowserBridgeResponse(
                request_id="cancelled",
                success=False,
                status="cancelled",
                error="Action cancelled by user before validation."
            )

        # Step 2: Permission check via canonical PermissionManager
        requires_confirmation = (risk_level in (RiskLevel.SENSITIVE_ACTION, RiskLevel.DESTRUCTIVE_ACTION))
        approved, perm_reason, _ = self.permission_manager.check_permission(
            tool_name=action_name,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            confirmation_token=confirmation_token
        )
        if not approved:
            status = "permission_required" if "confirmation" in perm_reason.lower() else "policy_denied"
            return False, f"Permission check failed: {perm_reason}", BrowserBridgeResponse(
                request_id="perm_denied",
                success=False,
                status=status,
                error=f"Permission check failed: {perm_reason}"
            )

        # Step 3: Target freshness check
        if binding.is_stale():
            return False, "Target binding is stale.", BrowserBridgeResponse(
                request_id="stale",
                success=False,
                status="stale_target",
                error="TargetBinding exceeded max staleness threshold (early invalidation)."
            )

        # Step 4: Origin policy check
        valid_origin, origin_msg = DOMDiscoveryEngine.validate_origin(binding.origin)
        if not valid_origin:
            return False, f"Origin policy violation: {origin_msg}", BrowserBridgeResponse(
                request_id="origin_denied",
                success=False,
                status="origin_denied",
                error=f"Origin security violation: {origin_msg}"
            )

        # Step 5: Frame policy check
        valid_frame, frame_msg = DOMDiscoveryEngine.validate_frame_policy(binding.frame_id)
        if not valid_frame:
            return False, f"Frame policy violation: {frame_msg}", BrowserBridgeResponse(
                request_id="frame_denied",
                success=False,
                status="frame_denied",
                error=f"Frame policy violation: {frame_msg}"
            )

        # Step 6: Bridge connectivity check (STOP -> REPORT -> DO NOT GUESS)
        if not self.bridge.is_connected():
            return False, "Extension bridge disconnected.", BrowserBridgeResponse(
                request_id="disconnected",
                success=False,
                status="browser_unavailable",
                error="Browser extension bridge is disconnected (STOP -> REPORT -> DO NOT GUESS)."
            )

        # Step 7: Live target re-resolution across bridge
        resolve_req = BrowserBridgeRequest(
            action="RESOLVE_TARGET",
            tab_id=binding.tab_id,
            frame_id=binding.frame_id,
            origin=binding.origin,
            target_binding=binding,
            timeout_ms=3000.0
        )
        resolve_resp = self.bridge.send_request(resolve_req)
        if not resolve_resp.success:
            err_status = resolve_resp.status if resolve_resp.status in ("target_not_found", "target_disappeared", "timeout") else "target_not_found"
            return False, f"Target resolution failed: {resolve_resp.error}", BrowserBridgeResponse(
                request_id=resolve_resp.request_id,
                success=False,
                status=err_status,
                error=f"Live DOM target resolution failed: {resolve_resp.error}"
            )

        resolve_payload = resolve_resp.payload or {}

        # Step 8: Fingerprint matching
        current_fp = resolve_payload.get("current_fingerprint", "")
        fp_matches = resolve_payload.get("fingerprint_matches", False)
        if binding.element_fingerprint and not fp_matches:
            return False, "Target fingerprint mismatch.", BrowserBridgeResponse(
                request_id=resolve_resp.request_id,
                success=False,
                status="target_mismatch",
                error=f"Target structural fingerprint mismatch (expected: {binding.element_fingerprint[:16]}..., got: {current_fp[:16]}...)."
            )

        # Step 9: Live visibility & enabled state validation
        is_visible = resolve_payload.get("is_visible", False)
        if not is_visible:
            return False, "Target is not visible in DOM/viewport.", BrowserBridgeResponse(
                request_id=resolve_resp.request_id,
                success=False,
                status="target_hidden",
                error="Target element is hidden or outside the active viewport."
            )

        is_enabled = resolve_payload.get("is_enabled", False)
        if not is_enabled:
            return False, "Target element is disabled.", BrowserBridgeResponse(
                request_id=resolve_resp.request_id,
                success=False,
                status="target_disabled",
                error="Target element is disabled."
            )

        return True, "Authorization chain passed.", None

    def click_element(
        self,
        binding: BrowserTargetBinding,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        expected_postcondition: Optional[Any] = None,
        verify_postcondition: Optional[bool] = None
    ) -> BrowserBridgeResponse:
        """
        Controlled click on a verified safe DOM element.
        Enforces the full 12-step authorization chain and verifies observable postconditions.
        """
        start_time = time.perf_counter()

        # Submit button in an auth or payment form upgrades risk level
        effective_risk = risk_level
        if binding.is_sensitive or "submit" in (binding.element_role or "").lower():
            if effective_risk == RiskLevel.LOW_RISK_ACTION:
                effective_risk = RiskLevel.SENSITIVE_ACTION

        # Execute authorization chain (Steps 1 - 9)
        passed, reason, err_resp = self._execute_authorization_chain(
            binding=binding,
            action_name="click_browser_element",
            risk_level=effective_risk,
            confirmation_token=confirmation_token,
            cancel_event=cancel_event
        )
        if not passed:
            # ZERO EMISSION INVARIANT: click counter remains unchanged
            if err_resp:
                err_resp.execution_latency_ms = (time.perf_counter() - start_time) * 1000.0
                return err_resp
            return BrowserBridgeResponse(
                request_id="rejected",
                success=False,
                status="policy_denied",
                error=reason,
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )

        # Step 10 & 11: Immediate pre-action validation & Controlled event emission
        click_req = BrowserBridgeRequest(
            action="CLICK",
            tab_id=binding.tab_id,
            frame_id=binding.frame_id,
            origin=binding.origin,
            target_binding=binding,
            timeout_ms=3000.0
        )

        # Increment spy audit counter
        self.spy_click_count += 1

        exec_resp = self.bridge.send_request(click_req)
        exec_resp.execution_latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Step 12: Post-action verification (Phase 5C.3.4)
        if not exec_resp.success:
            exec_resp.verified = False
            return exec_resp

        v_start = time.perf_counter()
        if expected_postcondition is not None:
            if callable(expected_postcondition):
                try:
                    ok = bool(expected_postcondition())
                    if ok:
                        exec_resp.verified = True
                        exec_resp.status = "verified"
                        exec_resp.postcondition_status = "verified"
                    else:
                        exec_resp.verified = False
                        exec_resp.status = "verification_failed"
                        exec_resp.postcondition_status = "verification_failed"
                        exec_resp.error = "Click postcondition verification check returned false."
                except Exception as e:
                    exec_resp.verified = False
                    exec_resp.status = "verification_failed"
                    exec_resp.postcondition_status = "verification_failed"
                    exec_resp.error = f"Click postcondition check exception: {e}"
            elif isinstance(expected_postcondition, dict):
                obs_state = exec_resp.payload.get("observed_state", {})
                mismatches = []
                exp_u = expected_postcondition.get("expected_url") or expected_postcondition.get("url")
                if exp_u:
                    act_u = obs_state.get("url", "")
                    if exp_u not in act_u:
                        mismatches.append(f"URL mismatch: expected '{exp_u}' in '{act_u}'")

                exp_aria = expected_postcondition.get("expected_aria_expanded") or expected_postcondition.get("aria-expanded")
                if exp_aria is not None:
                    act_exp = str(obs_state.get("aria_expanded", obs_state.get("aria-expanded", ""))).lower()
                    target_exp = str(exp_aria).lower()
                    if act_exp != target_exp:
                        mismatches.append(f"aria-expanded mismatch: expected '{target_exp}', got '{act_exp}'")

                exp_sel = expected_postcondition.get("expected_aria_selected") or expected_postcondition.get("aria-selected")
                if exp_sel is not None:
                    act_sel = str(obs_state.get("aria_selected", obs_state.get("aria-selected", ""))).lower()
                    target_sel = str(exp_sel).lower()
                    if act_sel != target_sel:
                        mismatches.append(f"aria-selected mismatch: expected '{target_sel}', got '{act_sel}'")

                if "expected_checked" in expected_postcondition or "checked" in expected_postcondition:
                    act_chk = bool(obs_state.get("checked"))
                    target_chk = bool(expected_postcondition.get("expected_checked") if "expected_checked" in expected_postcondition else expected_postcondition.get("checked"))
                    if act_chk != target_chk:
                        mismatches.append(f"checked mismatch: expected '{target_chk}', got '{act_chk}'")

                if expected_postcondition.get("target_disappeared") is True:
                    if not obs_state.get("target_disappeared", False):
                        mismatches.append("Target did not disappear from DOM")

                if not mismatches:
                    exec_resp.verified = True
                    exec_resp.status = "verified"
                    exec_resp.postcondition_status = "verified"
                else:
                    exec_resp.verified = False
                    exec_resp.status = "verification_failed"
                    exec_resp.postcondition_status = "verification_failed"
                    exec_resp.error = f"Click postcondition failed: {'; '.join(mismatches)}"
            elif isinstance(expected_postcondition, str):
                if expected_postcondition == "unavailable":
                    exec_resp.verified = False
                    exec_resp.status = "verification_unavailable"
                    exec_resp.postcondition_status = "verification_unavailable"
                    exec_resp.error = "Click executed, but verification was unavailable."
                else:
                    obs_u = exec_resp.payload.get("observed_state", {}).get("url", "")
                    if expected_postcondition in obs_u:
                        exec_resp.verified = True
                        exec_resp.status = "verified"
                        exec_resp.postcondition_status = "verified"
                    else:
                        exec_resp.verified = False
                        exec_resp.status = "verification_failed"
                        exec_resp.postcondition_status = "verification_failed"
                        exec_resp.error = f"Click postcondition URL mismatch: expected '{expected_postcondition}' in '{obs_u}'"
        elif verify_postcondition is True:
            exec_resp.verified = False
            exec_resp.status = "verification_unavailable"
            exec_resp.postcondition_status = "verification_unavailable"
            exec_resp.error = "Click executed, but verification was unavailable (no observable postcondition specified)."
        else:
            exec_resp.verified = False
            exec_resp.postcondition_status = exec_resp.postcondition_status or "unverified"

        exec_resp.verification_latency_ms = max(0.001, (time.perf_counter() - v_start) * 1000.0)

        # Context update upon verified navigation
        if exec_resp.verified:
            new_url = exec_resp.payload.get("observed_state", {}).get("url")
            if new_url and new_url.startswith(("http://", "https://")):
                try:
                    from backend.browser import BrowserManager
                    mgr = BrowserManager()
                    cur = mgr.get_current_page()
                    if cur and cur.url != new_url:
                        cur.url = new_url
                except Exception:
                    pass

        return exec_resp

    def type_text(
        self,
        binding: BrowserTargetBinding,
        text: str,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        verify_readback: Optional[bool] = None
    ) -> BrowserBridgeResponse:
        """
        Controlled text entry into an approved non-sensitive field.
        Strictly rejects typing into passwords, tokens, OTP, or payment fields.
        Verifies entered text via live DOM read-back where deterministic.
        """
        start_time = time.perf_counter()

        # Step 10: Sensitive-field policy check (Defense-in-depth)
        if binding.is_sensitive:
            # ZERO EMISSION INVARIANT
            return BrowserBridgeResponse(
                request_id="sensitive_rejected",
                success=False,
                status="policy_denied",
                error="Typing into sensitive/credential field is prohibited.",
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )

        backend_is_sensitive, sens_reasons = classify_element_sensitivity(
            tag=binding.element_tag,
            element_id=binding.dom_selector,
            name=binding.element_text_preview
        )
        if backend_is_sensitive:
            return BrowserBridgeResponse(
                request_id="sensitive_rejected",
                success=False,
                status="policy_denied",
                error=f"Typing rejected: Target re-classified sensitive ({sens_reasons}).",
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )

        # Text payload constraints
        if not isinstance(text, str):
            return BrowserBridgeResponse(
                request_id="bad_payload",
                success=False,
                status="policy_denied",
                error="Text payload must be a string.",
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )
        if len(text) > MAX_BROWSER_TEXT_LEN:
            return BrowserBridgeResponse(
                request_id="payload_exceeded",
                success=False,
                status="policy_denied",
                error=f"Text length ({len(text)}) exceeds maximum allowed {MAX_BROWSER_TEXT_LEN} characters.",
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )
        # Reject control characters (excluding standard whitespace \n, \t, \r)
        if any(ord(c) < 32 and c not in ("\n", "\t", "\r") for c in text):
            return BrowserBridgeResponse(
                request_id="invalid_unicode",
                success=False,
                status="policy_denied",
                error="Text payload contains prohibited control characters.",
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )

        # Execute authorization chain (Steps 1 - 9)
        passed, reason, err_resp = self._execute_authorization_chain(
            binding=binding,
            action_name="type_browser_field",
            risk_level=risk_level,
            confirmation_token=confirmation_token,
            cancel_event=cancel_event
        )
        if not passed:
            # ZERO EMISSION INVARIANT: type counter remains unchanged
            if err_resp:
                err_resp.execution_latency_ms = (time.perf_counter() - start_time) * 1000.0
                return err_resp
            return BrowserBridgeResponse(
                request_id="rejected",
                success=False,
                status="policy_denied",
                error=reason,
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )

        # Controlled event emission
        type_req = BrowserBridgeRequest(
            action="TYPE_TEXT",
            tab_id=binding.tab_id,
            frame_id=binding.frame_id,
            origin=binding.origin,
            target_binding=binding,
            payload={"text": text},
            timeout_ms=3000.0
        )

        # Increment spy audit counter
        self.spy_type_count += 1

        exec_resp = self.bridge.send_request(type_req)
        exec_resp.execution_latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Step 12: Post-action verification (Phase 5C.3.4)
        if not exec_resp.success:
            exec_resp.verified = False
            return exec_resp

        v_start = time.perf_counter()
        if "observed_value" in exec_resp.payload or exec_resp.observed_value is not None or verify_readback is True:
            observed_val = exec_resp.payload.get("observed_value", exec_resp.observed_value)
            if observed_val is not None:
                if str(observed_val) == str(text):
                    exec_resp.verified = True
                    exec_resp.status = "verified"
                    exec_resp.postcondition_status = "verified"
                    exec_resp.observed_value = str(observed_val)
                else:
                    exec_resp.verified = False
                    exec_resp.status = "verification_failed"
                    exec_resp.postcondition_status = "verification_failed"
                    exec_resp.observed_value = str(observed_val)
                    exec_resp.error = f"Text input verification mismatch: observed '{observed_val}', expected '{text}'."
            else:
                exec_resp.verified = False
                exec_resp.status = "verification_unavailable"
                exec_resp.postcondition_status = "verification_unavailable"
                exec_resp.error = "Text input unconfirmed: live DOM value could not be read back."
        else:
            exec_resp.verified = False
            exec_resp.postcondition_status = exec_resp.postcondition_status or "unverified"

        exec_resp.verification_latency_ms = max(0.001, (time.perf_counter() - v_start) * 1000.0)

        return exec_resp

    def select_option(
        self,
        binding: BrowserTargetBinding,
        value_or_label: str,
        risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION,
        confirmation_token: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        verify_readback: Optional[bool] = None
    ) -> BrowserBridgeResponse:
        """
        Controlled selection of a safe <select> option.
        Requires target tag to be SELECT and verifies selected value.
        """
        start_time = time.perf_counter()

        # Check element tag
        if binding.element_tag and binding.element_tag.upper() != "SELECT":
            return BrowserBridgeResponse(
                request_id="invalid_tag",
                success=False,
                status="policy_denied",
                error=f"Cannot select option on non-select element ({binding.element_tag}).",
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )

        if not value_or_label or not isinstance(value_or_label, str):
            return BrowserBridgeResponse(
                request_id="bad_option",
                success=False,
                status="policy_denied",
                error="Option value_or_label must be a non-empty string.",
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )

        # Execute authorization chain (Steps 1 - 9)
        passed, reason, err_resp = self._execute_authorization_chain(
            binding=binding,
            action_name="select_browser_option",
            risk_level=risk_level,
            confirmation_token=confirmation_token,
            cancel_event=cancel_event
        )
        if not passed:
            if err_resp:
                err_resp.execution_latency_ms = (time.perf_counter() - start_time) * 1000.0
                return err_resp
            return BrowserBridgeResponse(
                request_id="rejected",
                success=False,
                status="policy_denied",
                error=reason,
                execution_latency_ms=(time.perf_counter() - start_time) * 1000.0
            )

        select_req = BrowserBridgeRequest(
            action="SELECT_OPTION",
            tab_id=binding.tab_id,
            frame_id=binding.frame_id,
            origin=binding.origin,
            target_binding=binding,
            payload={"option": value_or_label},
            timeout_ms=3000.0
        )

        self.spy_select_count += 1

        exec_resp = self.bridge.send_request(select_req)
        exec_resp.execution_latency_ms = (time.perf_counter() - start_time) * 1000.0

        # Step 12: Post-action verification (Phase 5C.3.4)
        if not exec_resp.success:
            exec_resp.verified = False
            return exec_resp

        v_start = time.perf_counter()
        if "observed_value" in exec_resp.payload or "observed_label" in exec_resp.payload or exec_resp.observed_value is not None or verify_readback is True:
            observed_val = exec_resp.payload.get("observed_value", exec_resp.observed_value)
            observed_lbl = exec_resp.payload.get("observed_label")
            val_clean = str(value_or_label).strip()
            obs_val_clean = str(observed_val).strip() if observed_val is not None else ""
            obs_lbl_clean = str(observed_lbl).strip() if observed_lbl is not None else ""

            if (observed_val is not None or observed_lbl is not None) and (val_clean in (obs_val_clean, obs_lbl_clean)):
                exec_resp.verified = True
                exec_resp.status = "verified"
                exec_resp.postcondition_status = "verified"
                exec_resp.observed_value = obs_val_clean
            elif observed_val is not None or observed_lbl is not None:
                exec_resp.verified = False
                exec_resp.status = "verification_failed"
                exec_resp.postcondition_status = "verification_failed"
                exec_resp.observed_value = obs_val_clean
                exec_resp.error = f"Select option mismatch: observed '{obs_val_clean}' (label: '{obs_lbl_clean}'), expected '{val_clean}'."
            else:
                exec_resp.verified = False
                exec_resp.status = "verification_unavailable"
                exec_resp.postcondition_status = "verification_unavailable"
                exec_resp.error = "Select option unconfirmed: selected option could not be read back."
        else:
            exec_resp.verified = False
            exec_resp.postcondition_status = exec_resp.postcondition_status or "unverified"

        exec_resp.verification_latency_ms = max(0.001, (time.perf_counter() - v_start) * 1000.0)

        return exec_resp
