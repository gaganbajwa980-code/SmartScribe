import threading
import time
from typing import Optional
import numpy as np
import sounddevice as sd

class VoiceCapture:
    """
    Captures short-duration microphone audio directly into an in-memory
    16kHz mono float32 numpy buffer for low-latency Whisper transcription.
    
    Operates independently from AudioRecorder, does not touch disk files or
    database records, and releases microphone resources immediately upon completion.
    """
    
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.channels = 1
        self._listening = False
        self._buffer = []
        self._stream: Optional[sd.InputStream] = None
        self._error: Optional[str] = None
        self._lock = threading.Lock()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            pass
        if self._listening:
            self._buffer.append(indata.copy())

    def start(self) -> bool:
        """
        Begins non-blocking microphone stream into the memory buffer.
        Returns True on success, False if already listening or on device error.
        Guarantees any active speech response is stopped before listening to prevent self-transcription.
        """
        # Stop any active speech before acquiring microphone
        try:
            from backend.assistant.speech import SpeechManager
            sm = SpeechManager()
            if sm.is_speaking():
                sm.stop()
                sm.wait_stopped(timeout=0.2)
        except Exception:
            pass

        with self._lock:
            if self._listening:
                return False

            self._buffer = []
            self._error = None

            try:
                self._stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="float32",
                    callback=self._audio_callback
                )
                self._stream.start()
                self._listening = True
                return True
            except Exception as e:
                self._error = str(e)
                self._listening = False
                if self._stream:
                    try:
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
                raise RuntimeError(f"Microphone initialization failed: {e}")

    def stop(self) -> np.ndarray:
        """
        Stops the microphone stream, releases audio hardware, and returns
        the captured audio as a 1D float32 numpy array.
        """
        with self._lock:
            if not self._listening and not self._stream:
                return np.array([], dtype=np.float32)

            self._listening = False

            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as e:
                    self._error = str(e)
                finally:
                    self._stream = None

            if self._error:
                raise RuntimeError(f"Voice capture encountered an error: {self._error}")

            if not self._buffer:
                return np.array([], dtype=np.float32)

            audio_data = np.concatenate(self._buffer, axis=0).flatten()
            self._buffer = []
            return audio_data

    def cancel(self) -> None:
        """
        Aborts capture immediately, releases audio hardware, and discards all buffered data.
        """
        with self._lock:
            self._listening = False
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                finally:
                    self._stream = None
            self._buffer = []
            self._error = None

    def capture(self, duration: float = 3.0, cancel_event=None) -> np.ndarray:
        """
        Synchronously captures audio for the specified duration in seconds.
        """
        self.start()
        start_time = time.time()
        try:
            while time.time() - start_time < duration:
                if cancel_event and cancel_event.is_set():
                    self.cancel()
                    return np.array([], dtype=np.float32)
                time.sleep(0.05)
        except Exception:
            self.cancel()
            raise

        return self.stop()

    def is_listening(self) -> bool:
        with self._lock:
            return self._listening

    @property
    def error(self) -> Optional[str]:
        return self._error
