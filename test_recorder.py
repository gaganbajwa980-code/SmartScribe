from backend.recorder import AudioRecorder


print("================================")
print("     SMARTSCRIBE RECORDER TEST")
print("================================")

recorder = AudioRecorder()

input(
    "\nPress ENTER to start recording..."
)

started = recorder.start()

if not started:
    print("Could not start recording.")
    exit()

print("\n🔴 Recording...")
print("Speak for about 10 seconds.")

input(
    "\nPress ENTER to stop recording..."
)

audio_file = recorder.stop()

if audio_file:

    print("\n✅ Recording saved!")
    print("File:")
    print(audio_file)

else:

    print("\n❌ No audio was recorded.")