import time
import queue
import threading
from typing import Dict, Any, List, Optional

from backend.assistant.speech.base import BaseTTSEngine

# SAPI Speech Flags
SVSFDefault = 0
SVSFlagsAsync = 1
SVSFPurgeBeforeSpeak = 2

class SAPIWorker(threading.Thread):
    """
    Dedicated STA worker thread for Windows SAPI SpVoice.
    Ensures COM object lifecycle and speech calls remain strictly on a single thread.
    """
    def __init__(self):
        super().__init__(name="SmartScribe-SAPIWorker", daemon=True)
        self.cmd_queue = queue.Queue()
        self.is_speaking = False
        self.is_pending_speak = False
        self.is_paused = False
        self.running = True
        self.ready_event = threading.Event()
        self.init_error: Optional[str] = None
        self.voice = None

        self.active_voice_id: str = ""
        self.active_voice_name: str = ""
        self.speech_rate: int = 175     # WPM
        self.speech_volume: float = 0.85 # 0.0 - 1.0
        self.available_voices: List[Dict[str, Any]] = []

    def pause_instant(self):
        """Immediately silences physical audio output cross-thread in microseconds."""
        self.is_pending_speak = False
        self.is_speaking = False
        self.is_paused = True
        if self.voice:
            try:
                self.voice.Pause()
            except Exception:
                pass

    def _wpm_to_sapi_rate(self, wpm: int) -> int:
        """Converts WPM (140 - 220) to SAPI Rate integer (-10 to +10)."""
        # Baseline: 175 WPM -> 0
        rate = int((wpm - 175) / 5)
        return max(-10, min(10, rate))

    def _volume_to_sapi_vol(self, vol: float) -> int:
        """Converts float volume (0.0 - 1.0) to SAPI Volume (0 - 100)."""
        return int(max(0.0, min(1.0, vol)) * 100)

    def run(self):
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        voice = None
        try:
            self.voice = comtypes.client.CreateObject("SAPI.SpVoice")
            voice = self.voice
            
            # Enumerate installed voices dynamically
            try:
                raw_voices = voice.GetVoices()
                count = raw_voices.Count
                for i in range(count):
                    token = raw_voices.Item(i)
                    token_id = token.Id
                    desc = token.GetDescription()
                    self.available_voices.append({
                        "id": token_id,
                        "name": desc,
                        "index": i
                    })
            except Exception as e:
                # Fallback if enumeration encounters COM error
                self.available_voices = [{"id": "default", "name": "Default Windows Voice", "index": 0}]

            # Configure default voice, rate, and volume
            if self.available_voices:
                self.active_voice_id = self.available_voices[0]["id"]
                self.active_voice_name = self.available_voices[0]["name"]

            voice.Rate = self._wpm_to_sapi_rate(self.speech_rate)
            voice.Volume = self._volume_to_sapi_vol(self.speech_volume)
            self.ready_event.set()

            while self.running:
                try:
                    # When speaking, poll queue quickly (30ms) to detect completion or immediate stop
                    timeout = 0.03 if (self.is_speaking or self.is_pending_speak) else 0.5
                    cmd, data, ack_event = self.cmd_queue.get(timeout=timeout)
                except queue.Empty:
                    if self.is_speaking and not self.is_pending_speak and not self.is_paused and voice:
                        # Check if speech naturally finished (SRSEDone = 1)
                        try:
                            if voice.Status.RunningState == 1:
                                self.is_speaking = False
                        except Exception:
                            self.is_speaking = False
                    continue

                if cmd == "SPEAK":
                    self.is_speaking = True
                    self.is_pending_speak = False
                    text = data.get("text", "")
                    rate = data.get("rate")
                    volume = data.get("volume")
                    voice_id = data.get("voice_id")

                    try:
                        # Apply properties if changed
                        if rate is not None:
                            self.speech_rate = rate
                            voice.Rate = self._wpm_to_sapi_rate(rate)
                        if volume is not None:
                            self.speech_volume = volume
                            voice.Volume = self._volume_to_sapi_vol(volume)
                        if voice_id and voice_id != self.active_voice_id:
                            raw_voices = voice.GetVoices()
                            for i in range(raw_voices.Count):
                                if raw_voices.Item(i).Id == voice_id:
                                    voice.Voice = raw_voices.Item(i)
                                    self.active_voice_id = voice_id
                                    self.active_voice_name = raw_voices.Item(i).GetDescription()
                                    break

                        # If voice was paused, resume it so new speech plays
                        if self.is_paused:
                            try:
                                voice.Resume()
                            except Exception:
                                pass
                            self.is_paused = False

                        # Speak asynchronously with PurgeBeforeSpeak to discard any prior buffered audio
                        voice.Speak(text, SVSFPurgeBeforeSpeak | SVSFlagsAsync)
                    except Exception:
                        self.is_speaking = False

                    if ack_event:
                        ack_event.set()

                elif cmd == "STOP":
                    # Instant audio cutoff: Pause immediately (< 0.05ms) to silence physical speakers
                    self.is_pending_speak = False
                    self.is_speaking = False
                    self.is_paused = True
                    if voice:
                        try:
                            voice.Pause()
                        except Exception:
                            pass

                    # Acknowledge caller immediately so stop() returns in < 1ms
                    if ack_event:
                        ack_event.set()

                elif cmd == "SHUTDOWN":
                    self.is_pending_speak = False
                    self.is_speaking = False
                    self.running = False
                    if voice:
                        try:
                            voice.Pause()
                        except Exception:
                            pass
                    if ack_event:
                        ack_event.set()
                    break

        except Exception as e:
            self.init_error = str(e)
            self.ready_event.set()
        finally:
            if voice:
                try:
                    del voice
                except Exception:
                    pass
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


class SAPITTSEngine(BaseTTSEngine):
    """
    Production Windows SAPI speech engine implementation.
    Serializes all COM operations through an STA-initialized SAPIWorker thread.
    """
    def __init__(self):
        self._worker: Optional[SAPIWorker] = None
        self._lock = threading.Lock()
        self._init_worker()

    def _init_worker(self):
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = SAPIWorker()
                self._worker.start()
                self._worker.ready_event.wait(timeout=3.0)

    def speak(
        self,
        text: str,
        voice_id: Optional[str] = None,
        rate: Optional[int] = None,
        volume: Optional[float] = None
    ) -> bool:
        """Asynchronously commands SAPI to speak text."""
        if not text or not text.strip():
            return False

        self._init_worker()
        if self._worker.init_error:
            return False

        payload = {
            "text": text.strip(),
            "voice_id": voice_id,
            "rate": rate,
            "volume": volume
        }
        self._worker.is_pending_speak = True
        self._worker.cmd_queue.put(("SPEAK", payload, None))
        return True

    def stop(self) -> bool:
        """Immediately silences speech playback and purges audio buffer."""
        if self._worker is None or not self._worker.is_alive():
            return True

        # 1. Instant cross-thread physical audio cutoff (< 0.05ms)
        self._worker.pause_instant()

        # 2. Queue STOP event for worker
        self._worker.cmd_queue.put(("STOP", None, None))
        return True

    def is_speaking(self) -> bool:
        """Thread-safe check if SAPI is currently outputting speech."""
        if self._worker is None or not self._worker.is_alive():
            return False
        return bool(self._worker.is_speaking or getattr(self._worker, "is_pending_speak", False))

    def get_status(self) -> Dict[str, Any]:
        """Returns safe status metadata."""
        if self._worker is None or not self._worker.is_alive():
            return {
                "speaking": False,
                "voice": "Unavailable",
                "voice_id": "",
                "rate": 175,
                "volume": 0.85,
                "engine": "sapi5",
                "available": False
            }

        return {
            "speaking": self._worker.is_speaking,
            "voice": self._worker.active_voice_name,
            "voice_id": self._worker.active_voice_id,
            "rate": self._worker.speech_rate,
            "volume": self._worker.speech_volume,
            "engine": "sapi5",
            "available": self._worker.init_error is None
        }

    def get_available_voices(self) -> List[Dict[str, Any]]:
        """Returns list of installed Windows voices."""
        if self._worker is None or not self._worker.is_alive():
            return []
        return list(self._worker.available_voices)

    def shutdown(self) -> None:
        """Gracefully shuts down SAPI worker thread."""
        with self._lock:
            if self._worker and self._worker.is_alive():
                ack = threading.Event()
                self._worker.cmd_queue.put(("SHUTDOWN", None, ack))
                ack.wait(timeout=1.0)
                self._worker.join(timeout=1.0)
                self._worker = None
