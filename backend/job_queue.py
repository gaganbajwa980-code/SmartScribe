import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from backend.database import Database
from backend.transcriber import Transcriber
from backend.ai_engine import AIEngine
from backend.video_engine import VideoEngine
from backend.resource_manager import ResourceManager
from backend.chunker import create_transcript_chunks


class Job:
    def __init__(self, job_id, item_id, media_path, item_type="meeting", title=None):
        self.job_id = job_id
        self.item_id = item_id
        self.media_path = media_path
        self.item_type = item_type
        self.title = title
        self.stage = "queued"
        self.progress = 0
        self.status = "queued"  # queued, running, completed, cancelled, failed
        self.details = "Waiting in queue..."
        self.error_message = None
        self.cancel_event = threading.Event()
        self.thread = None
        self.created_at = time.time()


class JobQueue:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(JobQueue, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.db = Database()
        self.transcriber = None
        self.ai_engine = None
        self.jobs = {}
        self.subscribers = []
        self._sub_lock = threading.Lock()

    # =========================================================
    # SSE SUBSCRIBER NOTIFICATIONS
    # =========================================================

    def subscribe(self):
        q = queue.Queue(maxsize=100)
        with self._sub_lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def broadcast(self, event_type, data):
        payload = {"event": event_type, "data": data, "timestamp": time.time()}
        with self._sub_lock:
            for q in list(self.subscribers):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass

    # =========================================================
    # JOB SUBMISSION & CANCELLATION
    # =========================================================

    def enqueue(self, item_id, media_path, item_type="meeting", title=None):
        job_db_id = self.db.create_job(
            item_id=item_id,
            stage="queued",
            details=f"Queued processing for '{title or Path(media_path).name}'"
        )

        job = Job(
            job_id=job_db_id,
            item_id=item_id,
            media_path=media_path,
            item_type=item_type,
            title=title
        )
        self.jobs[job_db_id] = job

        self.broadcast("job_queued", self._job_dict(job))

        worker = threading.Thread(
            target=self._run_job,
            args=(job,),
            daemon=True
        )
        job.thread = worker
        worker.start()

        return job_db_id

    def cancel_job(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return False

        if job.status in ["completed", "failed", "cancelled"]:
            return False

        job.cancel_event.set()
        job.status = "cancelled"
        job.stage = "cancelled"
        job.details = "Processing was cancelled by user."

        self.db.update_job(
            job_id=job.job_id,
            stage="cancelled",
            status="cancelled",
            details=job.details
        )
        if job.item_id:
            self.db.update_item(job.item_id, status="cancelled")

        self.broadcast("job_cancelled", self._job_dict(job))
        return True

    def get_job(self, job_id):
        job = self.jobs.get(job_id)
        if job:
            return self._job_dict(job)
        return None

    def get_all_jobs(self):
        return [self._job_dict(j) for j in self.jobs.values()]

    # =========================================================
    # PIPELINE EXECUTION WORKER
    # =========================================================

    def _run_job(self, job):
        extracted_wav = None
        try:
            job.status = "running"
            self.db.update_job(job.job_id, status="running")
            if job.item_id:
                self.db.update_item(job.item_id, status="processing")

            media_p = Path(job.media_path)
            if not media_p.exists():
                raise FileNotFoundError(f"Media file missing: {job.media_path}")

            # -------------------------------------------------
            # STAGE 1: PROBE & EXTRACT AUDIO
            # -------------------------------------------------
            self._update_job_state(job, "extracting_audio", 5, "Probing media and extracting audio track...")

            if job.cancel_event.is_set():
                return

            probe_info = VideoEngine.probe(str(media_p))
            duration = probe_info.get("duration", 0.0)

            # Generate thumbnail if video
            thumbnail_path = None
            if probe_info.get("has_video"):
                thumbnail_path = VideoEngine.extract_thumbnail(str(media_p))

            if job.item_id:
                self.db.update_item(
                    job.item_id,
                    duration=duration,
                    thumbnail_path=thumbnail_path,
                    media_type="video/mp4" if probe_info.get("has_video") else "audio/wav"
                )

            # Check if file is already a 16kHz WAV or requires decoding
            if media_p.suffix.lower() == ".wav" and probe_info.get("audio_sample_rate") == 16000:
                audio_file_for_whisper = str(media_p)
            else:
                extracted_wav = VideoEngine.extract_audio(
                    str(media_p),
                    cancel_event=job.cancel_event
                )
                audio_file_for_whisper = extracted_wav

            if job.cancel_event.is_set():
                return

            # -------------------------------------------------
            # STAGE 2: FASTER-WHISPER TRANSCRIPTION
            # -------------------------------------------------
            self._update_job_state(job, "transcribing", 20, "Transcribing speech with Faster-Whisper...")

            if self.transcriber is None:
                self._update_job_state(job, "transcribing", 22, "Loading Faster-Whisper model into memory...")
                self.transcriber = Transcriber()

            if job.cancel_event.is_set():
                return

            def on_transcribe_progress(seg, total_dur):
                if total_dur > 0:
                    pct = 20 + int((seg["end"] / total_dur) * 45)
                    pct = min(68, max(20, pct))
                    msg = f"Transcribing: [{Transcriber.format_timestamp(seg['start'])}] {seg['text'][:30]}..."
                    self._update_job_state(job, "transcribing", pct, msg)

            resource_mgr = ResourceManager()
            resource_mgr.register_ai_job(job.job_id, job_type="whisper")
            try:
                segments = self.transcriber.transcribe(
                    audio_file_for_whisper,
                    cancel_event=job.cancel_event,
                    progress_callback=on_transcribe_progress
                )
            finally:
                resource_mgr.unregister_ai_job(job.job_id)

            if job.cancel_event.is_set() or segments is None:
                return

            if not segments:
                raise RuntimeError("No speech detected in media.")

            # Save segments and timestamp-preserving chunks to DB immediately
            if job.item_id:
                self.db.save_segments(job.item_id, segments)
                chunks = create_transcript_chunks(segments, target_words=450, overlap_segments=1)
                self.db.save_chunks(job.item_id, chunks)

            transcript_text = self.transcriber.transcript_to_text(segments)
            self._update_job_state(job, "transcribing", 70, f"Transcription complete: {len(segments)} segments.")

            # -------------------------------------------------
            # STAGE 3: LOCAL AI ANALYSIS (RESOURCE GATED)
            # -------------------------------------------------
            if job.cancel_event.is_set():
                return

            def on_resource_wait(reason):
                self._update_job_state(job, "queued", 71, f"Resource Manager: {reason}")

            safe = resource_mgr.wait_for_resources(
                job_type="llm",
                timeout=45.0,
                cancel_event=job.cancel_event,
                progress_callback=on_resource_wait
            )
            if job.cancel_event.is_set():
                return

            self._update_job_state(job, "analyzing", 72, "Loading local Qwen model for analysis...")

            if self.ai_engine is None:
                self.ai_engine = AIEngine()

            if job.cancel_event.is_set():
                return

            # -------------------------------------------------
            # STAGE 3: SMART MODE & ADAPTIVE INTELLIGENCE
            # -------------------------------------------------
            smart_mode, confidence = AIEngine.detect_smart_mode(
                title=job.title,
                sample_transcript=transcript_text[:800]
            )
            # If user explicitly selected a valid mode, honor it
            if job.item_type in ["lecture", "podcast", "interview", "tutorial", "presentation", "meeting"]:
                smart_mode = job.item_type
                confidence = "high"

            self._update_job_state(
                job,
                "analyzing",
                75,
                f"Smart Mode: Detected '{smart_mode.title()}' ({confidence}). Generating intelligence..."
            )

            resource_mgr.register_ai_job(job.job_id, job_type="llm")
            try:
                analysis_text = self.ai_engine.analyze_by_smart_mode(
                    transcript_text,
                    mode=smart_mode,
                    item_title=job.title,
                    cancel_event=job.cancel_event
                )
            finally:
                resource_mgr.unregister_ai_job(job.job_id)

            if job.cancel_event.is_set() or analysis_text is None:
                return

            parsed = AIEngine.parse_smart_mode_sections(analysis_text, mode=smart_mode)

            if job.item_id:
                self.db.update_item(
                    job.item_id,
                    smart_mode=smart_mode,
                    smart_mode_confidence=confidence,
                    summary=parsed["summary"] or analysis_text[:300],
                    key_points=parsed["key_points"],
                    action_items_text=parsed["action_items"],
                    topics=parsed["topics"],
                    raw_analysis=analysis_text,
                    content_intelligence=json.dumps(parsed.get("custom_sections", []))
                )

                # Parse action items into checklist table if present
                if parsed["action_items"]:
                    for line in parsed["action_items"].splitlines():
                        clean = line.strip().lstrip("-*•0123456789. ")
                        if clean and "no explicit action" not in clean.lower():
                            self.db.add_action_item(job.item_id, clean)

            # -------------------------------------------------
            # STAGE 4: INSTANT CHAPTERS & TIMELINE
            # -------------------------------------------------
            if job.cancel_event.is_set():
                return

            self._update_job_state(job, "chapters", 90, "Structuring chapters and timeline markers...")

            chapters = AIEngine.parse_timestamps_to_chapters(
                analysis_text,
                segments=segments,
                total_duration=duration
            )
            if chapters and job.item_id:
                self.db.save_chapters(job.item_id, chapters)

            # -------------------------------------------------
            # STAGE 5: STUDY MATERIAL (FAST FROM KEY CONCEPTS)
            # -------------------------------------------------
            if job.item_type == "lecture" and not job.cancel_event.is_set():
                self._update_job_state(job, "study_generation", 95, "Generating flashcards and interactive quiz...")
                study_set = self.ai_engine.generate_study_set(
                    summary=parsed.get("summary", ""),
                    key_points=parsed.get("key_points", ""),
                    cancel_event=job.cancel_event
                )
                if study_set and job.item_id:
                    self.db.save_study_material(job.item_id, "flashcards", "Lecture Flashcards", study_set.get("flashcards", []))
                    self.db.save_study_material(job.item_id, "quiz", "Lecture Practice Quiz", study_set.get("quiz", []))
                    self.db.save_study_material(job.item_id, "cram_sheet", "Exam Cram Sheet", study_set.get("cram_sheet", []))

            # Clean up extracted temporary audio file to free disk space
            if extracted_wav and os.path.exists(extracted_wav):
                try:
                    os.remove(extracted_wav)
                except Exception:
                    pass

            # -------------------------------------------------
            # COMPLETION
            # -------------------------------------------------
            if job.cancel_event.is_set():
                return

            self._update_job_state(job, "completed", 100, "Processing complete! Knowledge workspace is ready.")
            job.status = "completed"
            self.db.update_job(job.job_id, status="completed", progress=100)
            if job.item_id:
                self.db.update_item(job.item_id, status="completed")

            self.broadcast("job_completed", self._job_dict(job))

        except Exception as err:
            if not job.cancel_event.is_set():
                err_msg = str(err)
                print(f"Job {job.job_id} failed: {err_msg}")
                job.status = "failed"
                job.error_message = err_msg
                self.db.update_job(
                    job.job_id,
                    status="failed",
                    error_message=err_msg,
                    details=f"Failed: {err_msg}"
                )
                if job.item_id:
                    self.db.update_item(job.item_id, status="failed")
                self.broadcast("job_failed", self._job_dict(job))
            else:
                job.status = "cancelled"
                self.broadcast("job_cancelled", self._job_dict(job))

        finally:
            # Clean up transient extracted wav if different from original media
            if extracted_wav and os.path.exists(extracted_wav) and extracted_wav != job.media_path:
                try:
                    os.remove(extracted_wav)
                except Exception:
                    pass

    def _update_job_state(self, job, stage, progress, details):
        job.stage = stage
        job.progress = progress
        job.details = details
        self.db.update_job(
            job.job_id,
            stage=stage,
            progress=progress,
            details=details
        )
        self.broadcast("job_progress", self._job_dict(job))

    def _job_dict(self, job):
        return {
            "id": job.job_id,
            "item_id": job.item_id,
            "title": job.title or "Untitled Session",
            "item_type": job.item_type,
            "stage": job.stage,
            "progress": job.progress,
            "status": job.status,
            "details": job.details,
            "error_message": job.error_message
        }
