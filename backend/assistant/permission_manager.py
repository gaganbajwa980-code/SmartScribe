import time
import secrets
import threading
from enum import Enum
from typing import Dict, Any, Optional, Tuple

class RiskLevel(str, Enum):
    READ_ONLY = "read_only"              # Inspecting state; zero side-effects
    LOW_RISK_ACTION = "low_risk_action"  # Reversible local action (open app, start recording)
    LOW_RISK = "low_risk_action"
    SENSITIVE_ACTION = "sensitive_action"# Network I/O, writing files, downloads
    SENSITIVE = "sensitive_action"
    DESTRUCTIVE_ACTION = "destructive"   # Deletions, shutdowns, system config changes
    DESTRUCTIVE = "destructive"

class PermissionManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(PermissionManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}
        self._token_lock = threading.Lock()
        self._initialized = True

    def create_confirmation_request(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        risk_level: RiskLevel,
        summary: str,
        ttl_seconds: int = 60
    ) -> Dict[str, Any]:
        token = secrets.token_urlsafe(16)
        expires_at = time.time() + ttl_seconds
        
        req = {
            "token": token,
            "tool_name": tool_name,
            "arguments": arguments,
            "risk_level": risk_level.value if isinstance(risk_level, RiskLevel) else str(risk_level),
            "summary": summary,
            "created_at": time.time(),
            "expires_at": expires_at
        }
        
        with self._token_lock:
            # Clean expired tokens
            now = time.time()
            self._pending_confirmations = {
                t: data for t, data in self._pending_confirmations.items() if data["expires_at"] > now
            }
            self._pending_confirmations[token] = req
            
        return req

    def validate_confirmation_token(self, token: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        with self._token_lock:
            if not token or token not in self._pending_confirmations:
                return False, None, "Invalid or unrecognized confirmation token."
                
            req = self._pending_confirmations[token]
            if time.time() > req["expires_at"]:
                self._pending_confirmations.pop(token, None)
                return False, None, "Confirmation token has expired."
                
            # Pop token to ensure single-use
            self._pending_confirmations.pop(token, None)
            return True, req, "Token validated successfully."

    def check_permission(
        self,
        tool_name: str,
        risk_level: RiskLevel,
        requires_confirmation: bool,
        confirmation_token: Optional[str] = None
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if risk_level == RiskLevel.READ_ONLY:
            return True, "Approved (READ_ONLY).", None

        if risk_level == RiskLevel.LOW_RISK_ACTION and not requires_confirmation:
            return True, "Approved (LOW_RISK_ACTION).", None

        # SENSITIVE or DESTRUCTIVE requires confirmation token
        if requires_confirmation or risk_level in [RiskLevel.SENSITIVE_ACTION, RiskLevel.DESTRUCTIVE_ACTION]:
            if not confirmation_token:
                return False, "Requires explicit user confirmation.", None

            valid, req, reason = self.validate_confirmation_token(confirmation_token)
            if not valid:
                return False, f"Permission denied: {reason}", None

            if req["tool_name"] != tool_name:
                return False, "Permission denied: Confirmation token mismatch.", None

            return True, "Approved by verified user confirmation.", req

        return True, "Approved.", None
