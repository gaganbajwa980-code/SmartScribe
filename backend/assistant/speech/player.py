import time
from typing import Optional

from backend.assistant.speech.base import BaseTTSEngine

class SpeechPlayer:
    """
    Coordinates speech playback timing, cancellation events,
    and verification of stopped playback state for barge-in.
    """
    def __init__(self, engine: BaseTTSEngine):
        self.engine = engine

    def play(self, text: str, voice_id: Optional[str] = None, rate: Optional[int] = None, volume: Optional[float] = None) -> bool:
        """Plays sanitized speech text asynchronously."""
        return self.engine.speak(text, voice_id=voice_id, rate=rate, volume=volume)

    def stop(self) -> float:
        """
        Commands engine to stop and measures the precise latency
        from stop request until playback is confirmed silenced.
        Returns latency in milliseconds.
        """
        t0 = time.perf_counter()
        self.engine.stop()
        # Verify speech is no longer active
        self.wait_stopped(timeout=0.3)
        return (time.perf_counter() - t0) * 1000

    def wait_stopped(self, timeout: float = 0.5) -> bool:
        """
        Polls until the engine confirms speech is completely stopped.
        Returns True if stopped within timeout, False otherwise.
        """
        t0 = time.perf_counter()
        while self.engine.is_speaking():
            if time.perf_counter() - t0 >= timeout:
                return False
            time.sleep(0.01)
        return True

    def is_speaking(self) -> bool:
        return self.engine.is_speaking()
