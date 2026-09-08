from faster_whisper import WhisperModel

print("Loading Whisper model...")

model = WhisperModel(
    "base",
    device="cpu",
    compute_type="int8"
)

print("Whisper loaded successfully!")

audio_file = input(
    "\nEnter the path of a WAV/MP3 audio file: "
)

print("\nTranscribing...")

segments, info = model.transcribe(
    audio_file,
    beam_size=5
)

print("\nTRANSCRIPT:\n")

for segment in segments:

    print(
        f"[{segment.start:.2f}s - {segment.end:.2f}s] "
        f"{segment.text.strip()}"
    )

print("\nTranscription completed.")