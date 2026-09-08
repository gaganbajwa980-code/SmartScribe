from enum import Enum
import time
from typing import Dict, Any

class AssistantState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    UNDERSTANDING = "UNDERSTANDING"
    USING_TOOL = "USING_TOOL"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    AI_REASONING = "AI_REASONING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    THINKING = "THINKING"
    COMPLETE = "COMPLETE"
    SPEAKING = "SPEAKING"
    FAILED = "FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    CANCELLED = "CANCELLED"

def create_state_event(state: AssistantState, details: str = "", payload: Dict[str, Any] = None) -> Dict[str, Any]:
    return {
        "state": state.value if isinstance(state, AssistantState) else str(state),
        "details": details,
        "payload": payload or {},
        "timestamp": time.time()
    }
