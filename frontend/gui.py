import datetime
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from backend.recorder import AudioRecorder
from backend.transcriber import Transcriber
from backend.ai_engine import AIEngine
from backend.database import Database
from backend.search_engine import search_transcript


class SmartScribeGUI:

    def __init__(self, root):
        self.root = root
        self.root.title("SmartScribe - AI Meeting & Lecture Assistant")
        self.root.geometry("1240x780")
        self.root.minsize(1020, 680)

        # -----------------------------
        # STATE & THREADING
        # -----------------------------
        self.state = "IDLE"  # "IDLE", "RECORDING", "PROCESSING", "CANCELLING"
        self.cancel_event = threading.Event()
        self.processing_thread = None
        self.recording_start_time = None
        self.timer_job = None

        # -----------------------------
        # BACKEND
        # -----------------------------
        self.recorder = AudioRecorder()
        self.transcriber = None
        self.ai_engine = None
        self.database = Database()

        # -----------------------------
        # CURRENT SESSION
        # -----------------------------
        self.current_meeting_id = None
        self.current_audio = None
        self.current_transcript = []
        self.current_analysis = ""
        self.meeting_ids = []

        # -----------------------------
        # UI SETUP
        # -----------------------------
        self.create_styles()
        self.create_header()
        self.create_progress_bar()
        self.create_main_area()
        self.create_status_bar()

        self.load_history()
        self.set_state("IDLE")

        # Window closing handler for clean resource management
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # =========================================================
    # STYLES
    # =========================================================

    def create_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Typography
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"), foreground="#1e293b")
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground="#64748b")
        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"), foreground="#1e293b")
        style.configure("Timer.TLabel", font=("Consolas", 14, "bold"), foreground="#0f172a", padding=6)
        style.configure("StatusText.TLabel", font=("Segoe UI", 9), foreground="#334155")

        # Buttons
        style.configure("Record.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Stop.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Cancel.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        style.configure("Action.TButton", font=("Segoe UI", 10), padding=5)
        style.configure("Small.TButton", font=("Segoe UI", 9), padding=4)

        # Progressbar
        style.configure("TProgressbar", thickness=6)

    # =========================================================
    # HEADER
    # =========================================================

    def create_header(self):
        header = ttk.Frame(self.root, padding=(16, 12, 16, 8))
        header.pack(fill="x")

        # App branding
        title_frame = ttk.Frame(header)
        title_frame.pack(side="left", anchor="center")

        ttk.Label(
            title_frame,
            text="🎙 SmartScribe",
            style="Title.TLabel"
        ).pack(anchor="w")

        ttk.Label(
            title_frame,
            text="AI Meeting & Lecture Assistant  •  Local Faster-Whisper & Qwen",
            style="Subtitle.TLabel"
        ).pack(anchor="w")

        # Recording & Control Toolbar
        controls = ttk.Frame(header)
        controls.pack(side="right", anchor="center")

        # Elapsed recording timer
        self.timer_label = ttk.Label(
            controls,
            text="⏱ 00:00",
            style="Timer.TLabel"
        )
        self.timer_label.pack(side="left", padx=(0, 12))

        # Start Recording Button
        self.record_button = ttk.Button(
            controls,
            text="● Start Recording",
            command=self.start_recording,
            style="Record.TButton"
        )
        self.record_button.pack(side="left", padx=4)

        # Stop & Analyze Button
        self.stop_button = ttk.Button(
            controls,
            text="■ Stop & Analyze",
            command=self.stop_recording,
            style="Stop.TButton",
            state="disabled"
        )
        self.stop_button.pack(side="left", padx=4)

        # Cancel Processing Button
        self.cancel_button = ttk.Button(
            controls,
            text="✖ Cancel",
            command=self.cancel_action,
            style="Cancel.TButton",
            state="disabled"
        )
        self.cancel_button.pack(side="left", padx=4)

    # =========================================================
    # PROGRESS BAR (Stage indicator)
    # =========================================================

    def create_progress_bar(self):
        self.progress_container = ttk.Frame(self.root, padding=(16, 0, 16, 6))
        self.progress_container.pack(fill="x")

        self.progress_bar = ttk.Progressbar(
            self.progress_container,
            mode="indeterminate",
            style="TProgressbar"
        )
        # Hidden by default when idle
        self.progress_bar_visible = False

    def show_progress_bar(self, indeterminate=True):
        if not self.progress_bar_visible:
            self.progress_bar.pack(fill="x")
            self.progress_bar_visible = True
        if indeterminate:
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(12)
        else:
            self.progress_bar.stop()
            self.progress_bar.config(mode="determinate")

    def hide_progress_bar(self):
        if self.progress_bar_visible:
            self.progress_bar.stop()
            self.progress_bar.pack_forget()
            self.progress_bar_visible = False

    def set_progress_percent(self, percent):
        if not self.progress_bar_visible:
            self.show_progress_bar(indeterminate=False)
        self.progress_bar.config(mode="determinate", value=percent)

    # =========================================================
    # MAIN AREA
    # =========================================================

    def create_main_area(self):
        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=14, pady=6)

        # -----------------------------------------------------
        # LEFT PANEL: Meeting History
        # -----------------------------------------------------
        left = ttk.Frame(main, padding=10)
        main.add(left, weight=1)

        history_header_frame = ttk.Frame(left)
        history_header_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(
            history_header_frame,
            text="📚 Meeting History",
            style="Header.TLabel"
        ).pack(side="left", anchor="w")

        # History search filter
        history_search_frame = ttk.Frame(left)
        history_search_frame.pack(fill="x", pady=(0, 6))

        self.history_search_var = tk.StringVar()
        self.history_search_entry = ttk.Entry(
            history_search_frame,
            textvariable=self.history_search_var,
            font=("Segoe UI", 9)
        )
        self.history_search_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.history_search_entry.bind("<KeyRelease>", lambda e: self.filter_history())

        ttk.Button(
            history_search_frame,
            text="✕",
            width=3,
            command=self.clear_history_filter,
            style="Small.TButton"
        ).pack(side="right")

        # History listbox with scrollbar
        list_container = ttk.Frame(left)
        list_container.pack(fill="both", expand=True)

        self.history_list = tk.Listbox(
            list_container,
            font=("Segoe UI", 9),
            selectmode="browse",
            activestyle="none",
            exportselection=False
        )
        self.history_list.pack(side="left", fill="both", expand=True)

        history_scroll = ttk.Scrollbar(
            list_container,
            orient="vertical",
            command=self.history_list.yview
        )
        history_scroll.pack(side="right", fill="y")
        self.history_list.configure(yscrollcommand=history_scroll.set)

        self.history_list.bind("<<ListboxSelect>>", self.history_selected)

        # History action buttons
        history_buttons = ttk.Frame(left)
        history_buttons.pack(fill="x", pady=(8, 0))

        ttk.Button(
            history_buttons,
            text="↻ Refresh",
            command=self.load_history,
            style="Small.TButton"
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        ttk.Button(
            history_buttons,
            text="🗑 Delete",
            command=self.delete_selected_meeting,
            style="Small.TButton"
        ).pack(side="right", fill="x", expand=True)

        # -----------------------------------------------------
        # RIGHT PANEL: Notebook (Transcript & AI Analysis)
        # -----------------------------------------------------
        right = ttk.Frame(main, padding=10)
        main.add(right, weight=4)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)

        # =====================================================
        # TAB 1: TRANSCRIPT
        # =====================================================
        transcript_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(transcript_frame, text="📝 Transcript")

        # Search within transcript toolbar
        transcript_toolbar = ttk.Frame(transcript_frame)
        transcript_toolbar.pack(fill="x", pady=(0, 8))

        ttk.Label(
            transcript_toolbar,
            text="Search Transcript:",
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 6))

        self.search_entry = ttk.Entry(
            transcript_toolbar,
            font=("Segoe UI", 9)
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.search_entry.bind("<Return>", lambda e: self.search())

        ttk.Button(
            transcript_toolbar,
            text="Search",
            command=self.search,
            style="Small.TButton"
        ).pack(side="left", padx=(0, 4))

        ttk.Button(
            transcript_toolbar,
            text="Clear",
            command=self.clear_transcript_search,
            style="Small.TButton"
        ).pack(side="left", padx=(0, 10))

        ttk.Button(
            transcript_toolbar,
            text="📋 Copy Transcript",
            command=self.copy_transcript,
            style="Small.TButton"
        ).pack(side="right")

        # Transcript text display
        transcript_container = ttk.Frame(transcript_frame)
        transcript_container.pack(fill="both", expand=True)

        self.transcript_text = tk.Text(
            transcript_container,
            wrap="word",
            font=("Consolas", 10),
            padx=10,
            pady=10
        )
        self.transcript_text.pack(side="left", fill="both", expand=True)

        transcript_scroll = ttk.Scrollbar(
            transcript_container,
            command=self.transcript_text.yview
        )
        transcript_scroll.pack(side="right", fill="y")
        self.transcript_text.configure(yscrollcommand=transcript_scroll.set)

        # =====================================================
        # TAB 2: AI ANALYSIS
        # =====================================================
        analysis_frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(analysis_frame, text="🤖 AI Analysis")

        # Analysis toolbar
        analysis_toolbar = ttk.Frame(analysis_frame)
        analysis_toolbar.pack(fill="x", pady=(0, 8))

        ttk.Label(
            analysis_toolbar,
            text="AI Insights & Structured Notes (Local Qwen)",
            font=("Segoe UI", 9, "bold"),
            foreground="#334155"
        ).pack(side="left")

        ttk.Button(
            analysis_toolbar,
            text="📋 Copy Analysis",
            command=self.copy_analysis,
            style="Small.TButton"
        ).pack(side="right")

        # Analysis text display
        analysis_container = ttk.Frame(analysis_frame)
        analysis_container.pack(fill="both", expand=True)

        self.analysis_text = tk.Text(
            analysis_container,
            wrap="word",
            font=("Segoe UI", 10),
            padx=12,
            pady=12
        )
        self.analysis_text.pack(side="left", fill="both", expand=True)

        analysis_scroll = ttk.Scrollbar(
            analysis_container,
            command=self.analysis_text.yview
        )
        analysis_scroll.pack(side="right", fill="y")
        self.analysis_text.configure(yscrollcommand=analysis_scroll.set)

    # =========================================================
    # STATUS BAR
    # =========================================================

    def create_status_bar(self):
        status_bar_frame = ttk.Frame(self.root, relief="sunken", padding=(10, 5))
        status_bar_frame.pack(fill="x", side="bottom")

        self.status = tk.StringVar()
        self.status.set("Ready — Click 'Start Recording' to begin.")

        self.status_label = ttk.Label(
            status_bar_frame,
            textvariable=self.status,
            style="StatusText.TLabel",
            anchor="w"
        )
        self.status_label.pack(side="left", fill="x", expand=True)

    # =========================================================
    # STATE MACHINE CONTROLLER
    # =========================================================

    def set_state(self, state):
        self.state = state

        if state == "IDLE":
            self.record_button.config(state="normal", text="● Start Recording")
            self.stop_button.config(state="disabled", text="■ Stop & Analyze")
            self.cancel_button.config(state="disabled", text="✖ Cancel")
            self.hide_progress_bar()
            self.stop_timer()

        elif state == "RECORDING":
            self.record_button.config(state="disabled")
            self.stop_button.config(state="normal", text="■ Stop & Analyze")
            self.cancel_button.config(state="normal", text="✖ Discard Recording")
            self.hide_progress_bar()

        elif state == "PROCESSING":
            self.record_button.config(state="disabled")
            self.stop_button.config(state="disabled")
            self.cancel_button.config(state="normal", text="✖ Cancel Processing")
            self.show_progress_bar(indeterminate=True)

        elif state == "CANCELLING":
            self.record_button.config(state="disabled")
            self.stop_button.config(state="disabled")
            self.cancel_button.config(state="disabled", text="Cancelling...")
            self.update_status("🛑 Cancelling processing... Safely stopping AI workers...")

    # =========================================================
    # RECORDING TIMER
    # =========================================================

    def start_timer(self):
        self.recording_start_time = time.time()
        self.timer_label.config(text="⏱ 00:00")
        self.timer_tick()

    def timer_tick(self):
        if self.state == "RECORDING" and self.recording_start_time is not None:
            elapsed = int(time.time() - self.recording_start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            self.timer_label.config(text=f"⏱ {mins:02d}:{secs:02d}")
            self.timer_job = self.root.after(1000, self.timer_tick)

    def stop_timer(self):
        if self.timer_job:
            try:
                self.root.after_cancel(self.timer_job)
            except Exception:
                pass
            self.timer_job = None
        self.timer_label.config(text="⏱ 00:00")
        self.recording_start_time = None

    # =========================================================
    # RECORDING ACTIONS
    # =========================================================

    def start_recording(self):
        try:
            started = self.recorder.start()
            if not started:
                return

            self.cancel_event.clear()
            self.set_state("RECORDING")
            self.start_timer()
            self.update_status("🔴 Recording in progress... Speak clearly into your microphone.")

        except Exception as error:
            messagebox.showerror("Recording Error", f"Failed to start recording:\n{error}")
            self.set_state("IDLE")

    def stop_recording(self):
        if self.state != "RECORDING":
            return

        self.stop_timer()
        self.set_state("PROCESSING")
        self.cancel_event.clear()
        self.update_status("⏳ Stopping audio capture and preparing pipeline...")

        self.processing_thread = threading.Thread(
            target=self.process_recording,
            daemon=True
        )
        self.processing_thread.start()

    def cancel_action(self):
        """
        Handles user cancellation safely whether during recording or processing.
        """
        if self.state == "RECORDING":
            # Discard live audio recording
            self.recorder.cancel()
            self.set_state("IDLE")
            self.update_status("Ready — Recording discarded.")

        elif self.state == "PROCESSING":
            # Signal worker thread to terminate early
            self.set_state("CANCELLING")
            self.cancel_event.set()

    # =========================================================
    # COMPLETE PROCESSING PIPELINE
    # =========================================================

    def process_recording(self):
        audio_file = None
        was_cancelled = False

        try:
            # -----------------------------------------------
            # STAGE 1: FINALIZE AUDIO RECORDING
            # -----------------------------------------------
            self.update_status("⚙ Saving recorded audio...")

            if self.cancel_event.is_set():
                was_cancelled = True
                return

            audio_file = self.recorder.stop()

            if self.cancel_event.is_set():
                was_cancelled = True
                return

            if not audio_file or not os.path.exists(audio_file):
                raise RuntimeError("No audio was recorded or audio file could not be created.")

            self.current_audio = audio_file

            # -----------------------------------------------
            # STAGE 2: FASTER-WHISPER TRANSCRIPTION
            # -----------------------------------------------
            if self.transcriber is None:
                self.update_status("🎙 Initializing Faster-Whisper model...")
                self.transcriber = Transcriber()

            if self.cancel_event.is_set():
                was_cancelled = True
                return

            self.update_status("🎙 Transcribing audio with Faster-Whisper...")

            def on_transcribe_progress(seg, total_dur):
                if total_dur > 0:
                    pct = min(100, int((seg["end"] / total_dur) * 100))
                    msg = (
                        f"🎙 Transcribing: {pct}% "
                        f"([{Transcriber.format_timestamp(seg['start'])}] {seg['text'][:35]}...)"
                    )
                    self.root.after(0, lambda p=pct, m=msg: (self.set_progress_percent(p), self.status.set(m)))
                else:
                    msg = f"🎙 Transcribing: [{Transcriber.format_timestamp(seg['start'])}] {seg['text'][:35]}..."
                    self.update_status(msg)

            transcript = self.transcriber.transcribe(
                audio_file,
                cancel_event=self.cancel_event,
                progress_callback=on_transcribe_progress
            )

            if self.cancel_event.is_set() or transcript is None:
                was_cancelled = True
                return

            if not transcript:
                raise RuntimeError("Faster-Whisper could not detect any speech in the recording.")

            self.current_transcript = transcript
            transcript_text = self.transcriber.transcript_to_text(transcript)

            # Display transcript in UI
            self.root.after(0, lambda: self.show_transcript(transcript_text))

            # -----------------------------------------------
            # STAGE 3: LOCAL LLM ANALYSIS (STREAMING)
            # -----------------------------------------------
            if self.cancel_event.is_set():
                was_cancelled = True
                return

            if self.ai_engine is None:
                self.update_status("🤖 Initializing local AI model (Qwen)...")
                self.ai_engine = AIEngine()

            if self.cancel_event.is_set():
                was_cancelled = True
                return

            self.update_status("🤖 Generating AI summary & action items...")
            self.root.after(0, self.show_progress_bar, True)

            # Switch notebook view to AI Analysis tab so user can watch streaming
            self.root.after(0, lambda: self.notebook.select(1))
            self.root.after(0, lambda: self.show_analysis(""))

            def on_stream_token(token):
                self.root.after(0, lambda t=token: self.append_analysis_token(t))

            analysis = self.ai_engine.analyze_transcript(
                transcript_text,
                cancel_event=self.cancel_event,
                stream_callback=on_stream_token
            )

            if self.cancel_event.is_set() or analysis is None:
                was_cancelled = True
                return

            self.current_analysis = analysis

            # -----------------------------------------------
            # STAGE 4: DATABASE PERSISTENCE
            # -----------------------------------------------
            if self.cancel_event.is_set():
                was_cancelled = True
                return

            self.update_status("💾 Saving meeting to database...")

            title = f"Session {self.get_current_time()}"
            self.database.save_meeting(
                title=title,
                audio_file=audio_file,
                transcript=transcript_text,
                analysis=analysis
            )

            self.root.after(0, self.load_history)
            self.update_status("✅ Complete! Transcript and AI analysis saved.")

        except Exception as error:
            if not self.cancel_event.is_set():
                err_msg = str(error)
                self.root.after(
                    0,
                    lambda m=err_msg: messagebox.showerror("SmartScribe Error", m)
                )
                self.update_status("❌ Processing failed.")
            else:
                was_cancelled = True

        finally:
            if was_cancelled or self.cancel_event.is_set():
                self.update_status("Ready — Processing was cancelled by user.")
                # Clean up aborted audio file to prevent orphaned disk files
                if audio_file and os.path.exists(audio_file):
                    try:
                        os.remove(audio_file)
                    except Exception:
                        pass

            self.root.after(0, lambda: self.set_state("IDLE"))

    # =========================================================
    # DISPLAY METHODS
    # =========================================================

    def show_transcript(self, text):
        self.transcript_text.delete("1.0", "end")
        self.transcript_text.insert("1.0", text)

    def show_analysis(self, text):
        self.analysis_text.delete("1.0", "end")
        self.analysis_text.insert("1.0", text)

    def append_analysis_token(self, token):
        self.analysis_text.insert("end", token)
        self.analysis_text.see("end")

    # =========================================================
    # SEARCH
    # =========================================================

    def search(self):
        query = self.search_entry.get().strip()

        if not query:
            self.clear_transcript_search()
            return

        results = search_transcript(self.current_transcript, query)

        self.transcript_text.delete("1.0", "end")

        if not results:
            self.transcript_text.insert("1.0", f'No results found for "{query}".')
            return

        self.transcript_text.insert("end", f'Search results for "{query}":\n\n')

        for result in results:
            timestamp = Transcriber.format_timestamp(result["start"])
            self.transcript_text.insert("end", f"[{timestamp}] {result['text']}\n\n")

    def clear_transcript_search(self):
        self.search_entry.delete(0, "end")
        if self.current_transcript and self.transcriber:
            self.show_transcript(self.transcriber.transcript_to_text(self.current_transcript))
        elif hasattr(self, "_active_raw_transcript"):
            self.show_transcript(self._active_raw_transcript)
        else:
            self.show_transcript("")

    # =========================================================
    # MEETING HISTORY
    # =========================================================

    def load_history(self):
        self.filter_history()

    def filter_history(self):
        query = self.history_search_var.get().strip() if hasattr(self, "history_search_var") else ""

        self.history_list.delete(0, "end")
        self.meeting_ids = []

        if query:
            meetings = self.database.search_meetings(query)
        else:
            meetings = self.database.get_all_meetings()

        for meeting_id, title, date in meetings:
            self.meeting_ids.append(meeting_id)
            self.history_list.insert("end", f"{title} | {date}")

    def clear_history_filter(self):
        self.history_search_var.set("")
        self.filter_history()

    def history_selected(self, event):
        selection = self.history_list.curselection()
        if not selection:
            return

        index = selection[0]
        if index >= len(self.meeting_ids):
            return

        meeting_id = self.meeting_ids[index]
        meeting = self.database.get_meeting(meeting_id)

        if not meeting:
            return

        self.current_meeting_id = meeting_id
        self.current_audio = meeting[3]

        transcript = meeting[4] or ""
        analysis = meeting[5] or ""

        self._active_raw_transcript = transcript
        self.show_transcript(transcript)
        self.show_analysis(analysis)

        self.current_transcript = self.convert_text_to_segments(transcript)
        self.update_status(f"Loaded: {meeting[1]}")

    def delete_selected_meeting(self):
        selection = self.history_list.curselection()
        if not selection:
            messagebox.showinfo("Delete Meeting", "Please select a meeting from the history list to delete.")
            return

        index = selection[0]
        meeting_id = self.meeting_ids[index]
        meeting = self.database.get_meeting(meeting_id)
        title = meeting[1] if meeting else f"ID #{meeting_id}"

        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete:\n\n{title}?\n\nThis action cannot be undone."
        )

        if confirm:
            self.database.delete_meeting(meeting_id)
            # If current meeting was deleted, clear UI
            if self.current_meeting_id == meeting_id:
                self.current_meeting_id = None
                self.current_transcript = []
                self.show_transcript("")
                self.show_analysis("")

            self.load_history()
            self.update_status(f"Meeting '{title}' deleted.")

    # =========================================================
    # CLIPBOARD UTILITIES
    # =========================================================

    def copy_transcript(self):
        text = self.transcript_text.get("1.0", "end-1c").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.update_status("📋 Transcript copied to clipboard!")

    def copy_analysis(self):
        text = self.analysis_text.get("1.0", "end-1c").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.update_status("📋 AI Analysis copied to clipboard!")

    # =========================================================
    # TEXT → TRANSCRIPT SEGMENTS CONVERTER
    # =========================================================

    @staticmethod
    def convert_text_to_segments(text):
        segments = []
        for line in text.splitlines():
            if "]" not in line:
                continue

            try:
                timestamp = line.split("]")[0].replace("[", "")
                content = line.split("]", 1)[1].strip()
                parts = timestamp.split(":")

                if len(parts) == 2:
                    minutes = int(parts[0])
                    seconds = int(parts[1])
                    total_seconds = minutes * 60 + seconds
                elif len(parts) == 3:
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = int(parts[2])
                    total_seconds = hours * 3600 + minutes * 60 + seconds
                else:
                    continue

                segments.append({
                    "start": total_seconds,
                    "end": total_seconds,
                    "text": content
                })
            except (ValueError, IndexError):
                continue

        return segments

    # =========================================================
    # HELPERS & LIFECYCLE
    # =========================================================

    def update_status(self, message):
        self.root.after(0, lambda: self.status.set(message))

    @staticmethod
    def get_current_time():
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    def on_closing(self):
        """
        Ensures active recording or background threads are stopped safely on window close.
        """
        if self.state == "RECORDING":
            if messagebox.askyesno("Quit SmartScribe", "Recording is in progress. Stop and exit?"):
                self.recorder.cancel()
                self.root.destroy()
        elif self.state == "PROCESSING":
            if messagebox.askyesno("Quit SmartScribe", "Processing is running. Cancel and exit?"):
                self.cancel_event.set()
                self.root.destroy()
        else:
            try:
                self.database.close()
            except Exception:
                pass
            self.root.destroy()