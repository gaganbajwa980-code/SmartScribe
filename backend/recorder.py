import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write

from config import SAMPLE_RATE, CHANNELS, RECORDINGS_DIR


class AudioRecorder:
    """
    Records audio from the computer's default input device.
    """

    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        self.channels = CHANNELS

        self.recording = False
        self.audio_data = []
        self.thread = None
        self.current_file = None
        self.error = None

    def _record(self):
        self.audio_data = []
        self.error = None

        def callback(indata, frames, time_info, status):
            if status:
                print("Audio status:", status)

            if self.recording:
                self.audio_data.append(indata.copy())

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                callback=callback
            ):
                while self.recording:
                    sd.sleep(100)

        except Exception as error:
            self.error = str(error)
            self.recording = False

    def start(self):
        """
        Start recording.
        Returns True if recording starts successfully.
        """

        if self.recording:
            return False

        timestamp = time.strftime("%Y%m%d_%H%M%S")

        self.current_file = (
            RECORDINGS_DIR /
            f"recording_{timestamp}.wav"
        )

        self.recording = True

        self.thread = threading.Thread(
            target=self._record,
            daemon=True
        )

        self.thread.start()

        return True

    def stop(self):
        """
        Stop recording and save the WAV file.
        """

        if not self.recording:
            return None

        self.recording = False

        if self.thread:
            self.thread.join()

        if self.error:
            raise RuntimeError(
                f"Audio recording failed: {self.error}"
            )

        if not self.audio_data:
            return None

        audio = np.concatenate(
            self.audio_data,
            axis=0
        )

        write(
            str(self.current_file),
            self.sample_rate,
            audio
        )

        return str(self.current_file)

    def cancel(self):
        """
        Stop recording and discard audio data without saving to disk.
        """
        if not self.recording:
            return

        self.recording = False

        if self.thread:
            self.thread.join()

        self.audio_data = []

        if self.current_file and Path(self.current_file).exists():
            try:
                Path(self.current_file).unlink()
            except Exception:
                pass

        self.current_file = None

    def is_recording(self):
        return self.recording