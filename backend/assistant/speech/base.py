from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class BaseTTSEngine(ABC):
    """
    Abstract base class for SmartScribe local speech synthesis engines.
    Provides standard interface allowing SAPI and future Piper neural engines
    to be swapped without altering AssistantEngine architecture.
    """

    @abstractmethod
    def speak(
        self,
        text: str,
        voice_id: Optional[str] = None,
        rate: Optional[int] = None,
        volume: Optional[float] = None
    ) -> bool:
        """Asynchronously synthesizes and plays speech."""
        pass

    @abstractmethod
    def stop(self) -> bool:
        """Immediately halts active audio playback and purges queued speech."""
        pass

    @abstractmethod
    def is_speaking(self) -> bool:
        """Returns True if speech output is currently active."""
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Returns engine status dictionary (speaking, voice, rate, volume)."""
        pass

    @abstractmethod
    def get_available_voices(self) -> List[Dict[str, Any]]:
        """Returns list of installed voice profiles [{'id': ..., 'name': ..., 'gender': ...}]."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Gracefully cleans up worker threads, audio devices, and COM objects."""
        pass
