import os
import sys
import threading
from pathlib import Path

# Ensure NVIDIA CUDA runtime DLL directories are registered in Windows DLL search path and PATH
for mod_name in ["nvidia.cublas", "nvidia.cudnn"]:
    try:
        mod = __import__(mod_name, fromlist=["__path__"])
        paths = getattr(mod, "__path__", None)
        if paths:
            bin_dir = Path(list(paths)[0]) / "bin"
            if bin_dir.exists():
                bin_str = str(bin_dir)
                if bin_str not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = bin_str + os.pathsep + os.environ.get("PATH", "")
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(bin_str)
    except Exception:
        pass

from faster_whisper import WhisperModel

from config import (
    WHISPER_MODEL,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_BEAM_SIZE,
    WHISPER_THREADS
)


class Transcriber:
    _instance = None
    _singleton_lock = threading.Lock()
    _inference_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super(Transcriber, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, device=None, compute_type=None):
        if getattr(self, "_initialized", False):
            return

        self.model = None
        self.device = "cpu"
        self.compute_type = "int8"
        self._lock = Transcriber._inference_lock

        target_device = device or WHISPER_DEVICE

        # Dynamic Hardware Probe: Attempt CUDA first if requested or set to auto
        if target_device in ["cuda", "auto"]:
            try:
                import ctranslate2
                import numpy as np
                cuda_types = ctranslate2.get_supported_compute_types("cuda")
                if cuda_types:
                    preferred_compute = compute_type or ("float16" if "float16" in cuda_types else "int8_float16")
                    print(f"[Transcriber] Probing CUDA for Faster-Whisper ({WHISPER_MODEL}, {preferred_compute})...")
                    test_model = WhisperModel(
                        WHISPER_MODEL,
                        device="cuda",
                        compute_type=preferred_compute,
                        cpu_threads=WHISPER_THREADS,
                        num_workers=2
                    )
                    # Verify dynamic library loading (e.g. cublas64_12.dll) via 0.5s dummy audio test
                    dummy_audio = np.zeros(8000, dtype=np.float32)
                    list(test_model.transcribe(dummy_audio, beam_size=1)[0])

                    self.model = test_model
                    self.device = "cuda"
                    self.compute_type = preferred_compute
                    print(f"[Transcriber] Whisper loaded successfully on CUDA ({self.compute_type})!")
            except Exception as err:
                print(f"[Transcriber] CUDA is not usable ({err}). Gracefully falling back to CPU.")
                self.model = None

        # Reliable CPU Fallback
        if self.model is None:
            cpu_compute = compute_type or WHISPER_COMPUTE_TYPE
            print(f"[Transcriber] Loading Whisper on CPU ({WHISPER_MODEL}, {cpu_compute}, {WHISPER_THREADS} threads)...")
            self.model = WhisperModel(
                WHISPER_MODEL,
                device="cpu",
                compute_type=cpu_compute,
                cpu_threads=WHISPER_THREADS,
                num_workers=2
            )
            self.device = "cpu"
            self.compute_type = cpu_compute
            print(f"[Transcriber] Whisper loaded successfully on CPU ({self.compute_type})!")

        self._initialized = True

    def get_device_info(self):
        return {
            "device": self.device,
            "compute_type": self.compute_type,
            "model": WHISPER_MODEL,
            "threads": WHISPER_THREADS
        }

    def transcribe(
        self,
        audio_file,
        beam_size=None,
        cancel_event=None,
        progress_callback=None
    ):
        if cancel_event and cancel_event.is_set():
            return None

        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            print("[Transcriber] Another transcription is active. Waiting for lock...")
            while not acquired:
                if cancel_event and cancel_event.is_set():
                    print("[Transcriber] Transcription cancelled while waiting for lock.")
                    return None
                acquired = self._lock.acquire(timeout=0.1)

        try:
            if cancel_event and cancel_event.is_set():
                return None

            actual_beam_size = beam_size if beam_size is not None else WHISPER_BEAM_SIZE

            # Optimized decoding parameters for fast, low-memory inference
            segments, info = self.model.transcribe(
                audio_file,
                beam_size=actual_beam_size,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400),
                condition_on_previous_text=False,
                temperature=0.0,
                compression_ratio_threshold=2.4,
                no_speech_threshold=0.6
            )

            transcript = []
            total_duration = getattr(info, "duration", 0.0) or 0.0

            for segment in segments:
                # Check for user cancellation between segments
                if cancel_event and cancel_event.is_set():
                    print("[Transcriber] Transcription cancelled by user.")
                    return None

                text = segment.text.strip()
                if not text:
                    continue

                seg_data = {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": text
                }
                transcript.append(seg_data)

                if progress_callback:
                    try:
                        progress_callback(seg_data, total_duration)
                    except Exception:
                        pass

            # Final cancellation check before returning
            if cancel_event and cancel_event.is_set():
                return None

            return transcript

        finally:
            self._lock.release()

    def transcribe_command(
        self,
        audio_data,
        cancel_event=None
    ) -> str:
        """
        Low-latency, single-utterance speech transcription for voice commands.
        Accepts a 1D float32 numpy array, audio file path, or binary stream.
        Uses beam_size=1 and greedy decoding for maximum responsiveness.
        """
        if cancel_event and cancel_event.is_set():
            return ""

        import numpy as np

        # Fast sanity validation on raw numpy inputs
        if isinstance(audio_data, np.ndarray):
            if audio_data.size < 1600:  # Less than 0.1s at 16kHz
                return ""
            if np.max(np.abs(audio_data)) < 0.001:  # Absolute silence
                return ""

        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            while not acquired:
                if cancel_event and cancel_event.is_set():
                    return ""
                acquired = self._lock.acquire(timeout=0.05)

        try:
            if cancel_event and cancel_event.is_set():
                return ""

            segments, _ = self.model.transcribe(
                audio_data,
                beam_size=1,
                temperature=0.0,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=250),
                condition_on_previous_text=False,
                compression_ratio_threshold=2.4,
                no_speech_threshold=0.6
            )

            texts = []
            for seg in segments:
                if cancel_event and cancel_event.is_set():
                    return ""
                t = seg.text.strip()
                if t:
                    texts.append(t)

            return " ".join(texts).strip()

        except Exception as e:
            print(f"[Transcriber] Voice transcription error: {e}")
            return ""
        finally:
            self._lock.release()

    @staticmethod
    def format_timestamp(seconds):

        seconds = int(seconds)

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60

        if hours > 0:
            return (
                f"{hours:02d}:"
                f"{minutes:02d}:"
                f"{seconds:02d}"
            )

        return (
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    @staticmethod
    def transcript_to_text(transcript):

        lines = []

        for segment in transcript:
            start_sec = segment.get("start_time", segment.get("start", 0.0))
            timestamp = Transcriber.format_timestamp(start_sec)

            lines.append(
                f"[{timestamp}] {segment['text']}"
            )

        return "\n".join(lines)