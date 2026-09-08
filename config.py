import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

RECORDINGS_DIR = BASE_DIR / "recordings"
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"

DATABASE_PATH = DATA_DIR / "smartscribe.db"

LLM_MODEL_PATH = MODELS_DIR / "model.gguf"

WHISPER_MODEL = "base"
WHISPER_DEVICE = os.environ.get("SMARTSCRIBE_WHISPER_DEVICE", "auto")
WHISPER_COMPUTE_TYPE = "int8"
WHISPER_BEAM_SIZE = 1

# Threading settings for CPU performance
CPU_CORES = os.cpu_count() or 4
LLM_THREADS = max(1, min(8, CPU_CORES - 2))
WHISPER_THREADS = max(4, min(8, CPU_CORES - 2))

SAMPLE_RATE = 44100
CHANNELS = 1


RECORDINGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)