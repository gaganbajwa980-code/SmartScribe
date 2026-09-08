from dataclasses import dataclass, field
from typing import Optional, Any, Dict

@dataclass
class AssistantRequest:
    """
    Standardized request model for SmartScribe Assistant operations.
    Supports queries originating from text commands or voice push-to-talk.
    """
    query: str
    source: str = "text"  # "text" | "voice"
    session_id: Optional[int] = None
    confirmation_token: Optional[str] = None
    cancel_event: Optional[Any] = None
    audio_duration_ms: Optional[float] = None
    transcription_duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
