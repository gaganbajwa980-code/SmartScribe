import threading
from typing import Optional, Dict, Any, List

from backend.assistant.speech.base import BaseTTSEngine
from backend.assistant.speech.sapi_engine import SAPITTSEngine
from backend.assistant.speech.policy import SpeechPolicy, SpeechConfig, sanitize_for_speech
from backend.assistant.speech.player import SpeechPlayer

class SpeechManager:
    """
    Singleton coordinator for SmartScribe local speech response.
    Encapsulates SpeechPolicy, SAPITTSEngine, and instant barge-in cancellation.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(SpeechManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, engine: Optional[BaseTTSEngine] = None, config: Optional[SpeechConfig] = None):
        if getattr(self, "_initialized", False):
            return

        self.config = config or SpeechConfig()
        self.policy = SpeechPolicy(self.config)
        self.engine = engine or SAPITTSEngine()
        self.player = SpeechPlayer(self.engine)
        self._initialized = True

    def speak(
        self,
        text: str,
        voice_id: Optional[str] = None,
        rate: Optional[int] = None,
        volume: Optional[float] = None
    ) -> bool:
        """Sanitizes and asynchronously speaks the provided text."""
        sanitized = sanitize_for_speech(text, max_words=50)
        if not sanitized:
            return False

        use_rate = rate if rate is not None else self.config.speech_rate
        use_vol = volume if volume is not None else self.config.speech_volume
        use_voice = voice_id if voice_id is not None and voice_id != "default" else None

        return self.player.play(sanitized, voice_id=use_voice, rate=use_rate, volume=use_vol)

    def process_and_speak(
        self,
        query: str,
        response_text: str,
        intent_result: Optional[Dict[str, Any]] = None,
        source: str = "text"
    ) -> Optional[str]:
        """
        Evaluates SpeechPolicy for an Assistant response.
        If policy approves, speaks the concise sanitized utterance.
        Returns the spoken text, or None if kept silent.
        """
        candidate = self.policy.evaluate(
            query=query,
            response_text=response_text,
            intent_result=intent_result,
            source=source
        )
        if not candidate:
            return None

        success = self.speak(candidate)
        return candidate if success else None

    def stop(self) -> float:
        """
        Immediately halts active speech and purges audio buffer.
        Returns cancellation latency in milliseconds.
        """
        return self.player.stop()

    def wait_stopped(self, timeout: float = 0.5) -> bool:
        """Waits until audio is confirmed completely silenced."""
        return self.player.wait_stopped(timeout=timeout)

    def is_speaking(self) -> bool:
        """Returns True if speech output is currently playing."""
        return self.player.is_speaking()

    def get_status(self) -> Dict[str, Any]:
        """Returns clean status dictionary."""
        status = self.engine.get_status()
        status["speech_enabled"] = self.config.speech_enabled
        status["speak_fast_actions"] = self.config.speak_fast_actions
        status["speak_long_answers"] = self.config.speak_long_answers
        return status

    def get_available_voices(self) -> List[Dict[str, Any]]:
        """Enumerates available installed system voices."""
        return self.engine.get_available_voices()

    def set_config(self, config: SpeechConfig):
        """Updates user speech configuration."""
        self.config = config
        self.policy.config = config

    def shutdown(self):
        """Releases all speech engine and thread resources."""
        self.stop()
        self.engine.shutdown()
