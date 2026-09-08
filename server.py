import json
import os
import re
import time
from pathlib import Path
from flask import (
    Flask, request, jsonify, render_template, send_from_directory,
    Response, stream_with_context
)
from werkzeug.utils import secure_filename

from config import (
    BASE_DIR, RECORDINGS_DIR, DATA_DIR, MODELS_DIR,
    WHISPER_MODEL, WHISPER_DEVICE, LLM_THREADS, CPU_CORES
)
from backend.database import Database
from backend.recorder import AudioRecorder
from backend.video_engine import VideoEngine, THUMBNAILS_DIR
from backend.job_queue import JobQueue
from backend.ai_engine import AIEngine
from backend.transcriber import Transcriber

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "frontend" / "templates"),
    static_folder=str(BASE_DIR / "frontend" / "static")
)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2GB max upload

from backend.assistant.voice_capture import VoiceCapture

db = Database()
recorder = AudioRecorder()
assistant_voice_capture = VoiceCapture()
job_queue = JobQueue()
ai_engine_ref = None


def get_ai_engine():
    global ai_engine_ref
    if ai_engine_ref is None:
        ai_engine_ref = AIEngine()
    return ai_engine_ref


# =============================================================
# WEB CLIENT ROUTES
# =============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/media/<path:filename>")
def serve_media(filename):
    base_dir = RECORDINGS_DIR.resolve()
    try:
        target = (base_dir / filename).resolve()
        target.relative_to(base_dir)
    except (ValueError, RuntimeError):
        return jsonify({"error": "Access denied: Path traversal detected"}), 403

    if not target.is_file():
        return jsonify({"error": "Media file not found"}), 404

    return send_from_directory(str(target.parent), target.name, conditional=True)


@app.route("/thumbnails/<path:filename>")
def serve_thumbnail(filename):
    base_dir = THUMBNAILS_DIR.resolve()
    try:
        target = (base_dir / filename).resolve()
        target.relative_to(base_dir)
    except (ValueError, RuntimeError):
        return jsonify({"error": "Access denied: Path traversal detected"}), 403

    if not target.is_file():
        return jsonify({"error": "Thumbnail not found"}), 404

    return send_from_directory(str(target.parent), target.name)


# =============================================================
# SERVER-SENT EVENTS (SSE) STREAM
# =============================================================

@app.route("/api/stream/events")
def stream_events():
    def event_generator():
        q = job_queue.subscribe()
        try:
            # Initial ping
            yield f"data: {json.dumps({'event': 'connected', 'timestamp': time.time()})}\n\n"
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield f"data: {json.dumps(msg)}\n\n"
                except Exception:
                    # Heartbeat
                    yield f": ping {time.time()}\n\n"
        finally:
            job_queue.unsubscribe(q)

    return Response(
        stream_with_context(event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )


# =============================================================
# ITEMS API (CRUD, SEARCH, FILTER)
# =============================================================

@app.route("/api/items", methods=["GET"])
def list_items():
    item_type = request.args.get("type", "all")
    query = request.args.get("search", "").strip()

    if query:
        items = db.search_items(query)
        if item_type and item_type.lower() != "all":
            items = [i for i in items if i.get("type") == item_type.lower()]
    else:
        items = db.get_all_items(item_type=item_type)

    return jsonify({"items": items, "count": len(items)})


@app.route("/api/items/<int:item_id>", methods=["GET"])
def get_item_detail(item_id):
    item = db.get_item(item_id)
    if not item:
        return jsonify({"error": "Item not found"}), 404

    segments = db.get_segments(item_id)
    chapters = db.get_chapters(item_id)
    bookmarks = db.get_bookmarks(item_id)
    notes = db.get_notes(item_id)
    action_items = db.get_action_items(item_id)
    study_materials = db.get_study_materials(item_id)
    chat_history = db.get_chat_messages(item_id)

    # Format media URL for HTML5 player
    media_url = None
    if item.get("media_path"):
        p = Path(item["media_path"])
        if p.exists():
            media_url = f"/media/{p.name}"

    thumb_url = None
    if item.get("thumbnail_path"):
        tp = Path(item["thumbnail_path"])
        if tp.exists():
            thumb_url = f"/thumbnails/{tp.name}"

    speakers = db.get_item_speakers(item_id)
    content_intel = []
    try:
        content_intel = json.loads(item.get("content_intelligence") or "[]")
    except Exception:
        content_intel = []

    return jsonify({
        "item": item,
        "media_url": media_url,
        "thumbnail_url": thumb_url,
        "segments": segments,
        "chapters": chapters,
        "bookmarks": bookmarks,
        "notes": notes,
        "action_items": action_items,
        "study_materials": study_materials,
        "chat_history": chat_history,
        "speakers": speakers,
        "content_intelligence": content_intel
    })


@app.route("/api/items/<int:item_id>", methods=["PATCH"])
def update_item_endpoint(item_id):
    data = request.get_json() or {}
    db.update_item(item_id, **data)
    return jsonify({"success": True})


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def delete_item_endpoint(item_id):
    item = db.get_item(item_id)
    if not item:
        return jsonify({"error": "Item not found"}), 404

    # Clean up associated media files if inside recordings directory
    if item.get("media_path"):
        try:
            mp = Path(item["media_path"])
            if mp.exists() and RECORDINGS_DIR in mp.parents:
                mp.unlink()
        except Exception:
            pass

    if item.get("thumbnail_path"):
        try:
            tp = Path(item["thumbnail_path"])
            if tp.exists():
                tp.unlink()
        except Exception:
            pass

    db.delete_item(item_id)
    return jsonify({"success": True})


# =============================================================
# BOOKMARKS, NOTES, ACTION ITEMS
# =============================================================

@app.route("/api/items/<int:item_id>/bookmarks", methods=["POST"])
def add_bookmark(item_id):
    data = request.get_json() or {}
    bm_id = db.add_bookmark(
        item_id=item_id,
        timestamp=data.get("timestamp", 0.0),
        tag=data.get("tag", "important"),
        title=data.get("title", "Bookmark"),
        note=data.get("note", "")
    )
    return jsonify({"id": bm_id, "success": True})


@app.route("/api/bookmarks/<int:bm_id>", methods=["DELETE"])
def delete_bookmark(bm_id):
    db.delete_bookmark(bm_id)
    return jsonify({"success": True})


@app.route("/api/items/<int:item_id>/notes", methods=["POST"])
def add_note(item_id):
    data = request.get_json() or {}
    note_id = db.create_note(
        item_id=item_id,
        title=data.get("title", "Note"),
        content=data.get("content", ""),
        tags=data.get("tags", "")
    )
    return jsonify({"id": note_id, "success": True})


@app.route("/api/notes/<int:note_id>", methods=["PATCH"])
def update_note(note_id):
    data = request.get_json() or {}
    db.update_note(
        note_id=note_id,
        title=data.get("title", "Note"),
        content=data.get("content", ""),
        tags=data.get("tags", "")
    )
    return jsonify({"success": True})


@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    db.delete_note(note_id)
    return jsonify({"success": True})


@app.route("/api/action_items/<int:ai_id>/toggle", methods=["POST"])
def toggle_action(ai_id):
    data = request.get_json() or {}
    completed = data.get("completed", 0)
    db.toggle_action_item(ai_id, completed)
    return jsonify({"success": True})


# =============================================================
# RECORDING API
# =============================================================

@app.route("/api/record/start", methods=["POST"])
def start_recording():
    if recorder.is_recording():
        return jsonify({"error": "Recording already in progress"}), 400

    started = recorder.start()
    if not started:
        return jsonify({"error": "Could not access microphone"}), 500

    return jsonify({"success": True, "status": "recording"})


@app.route("/api/record/stop", methods=["POST"])
def stop_recording():
    if not recorder.is_recording():
        return jsonify({"error": "No recording active"}), 400

    data = request.get_json() or {}
    title = data.get("title") or f"Session {time.strftime('%Y-%m-%d %H:%M')}"
    item_type = data.get("type", "meeting")

    audio_file = recorder.stop()
    if not audio_file or not os.path.exists(audio_file):
        return jsonify({"error": "No audio captured"}), 400

    item_id = db.create_item(
        title=title,
        item_type=item_type,
        media_path=audio_file,
        media_type="audio/wav",
        status="processing"
    )

    job_id = job_queue.enqueue(
        item_id=item_id,
        media_path=audio_file,
        item_type=item_type,
        title=title
    )

    return jsonify({
        "success": True,
        "item_id": item_id,
        "job_id": job_id
    })


@app.route("/api/record/cancel", methods=["POST"])
def cancel_recording():
    recorder.cancel()
    return jsonify({"success": True, "status": "cancelled"})


@app.route("/api/record/status", methods=["GET"])
def record_status():
    return jsonify({
        "is_recording": recorder.is_recording(),
        "error": recorder.error
    })


# =============================================================
# IMPORT MEDIA API (FILE UPLOAD & ONLINE URL)
# =============================================================

@app.route("/api/import/file", methods=["POST"])
def import_file():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    title = request.form.get("title") or Path(file.filename).stem
    item_type = request.form.get("type", "video")

    safe_name = secure_filename(file.filename)
    ts = time.strftime("%Y%m%d_%H%M%S")
    target_path = RECORDINGS_DIR / f"{ts}_{safe_name}"
    file.save(str(target_path))

    # Probe media type
    probe_info = VideoEngine.probe(str(target_path))
    detected_type = "video" if probe_info.get("has_video") else "audio"
    if item_type in ["meeting", "lecture"]:
        final_type = item_type
    else:
        final_type = detected_type

    item_id = db.create_item(
        title=title,
        item_type=final_type,
        media_path=str(target_path),
        media_type=f"video/{target_path.suffix.lstrip('.')}" if probe_info.get("has_video") else "audio/wav",
        duration=probe_info.get("duration", 0.0),
        status="processing"
    )

    job_id = job_queue.enqueue(
        item_id=item_id,
        media_path=str(target_path),
        item_type=final_type,
        title=title
    )

    return jsonify({
        "success": True,
        "item_id": item_id,
        "job_id": job_id,
        "title": title
    })


@app.route("/api/import/url", methods=["POST"])
def import_url():
    data = request.get_json() or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    item_type = data.get("type", "video")
    title_override = data.get("title", "")

    try:
        download_res = VideoEngine.download_online_media(url)
        media_path = download_res["file_path"]
        title = title_override or download_res.get("title") or "Imported Video"
        duration = download_res.get("duration", 0.0)

        item_id = db.create_item(
            title=title,
            item_type=item_type,
            media_path=media_path,
            duration=duration,
            status="processing"
        )

        job_id = job_queue.enqueue(
            item_id=item_id,
            media_path=media_path,
            item_type=item_type,
            title=title
        )

        return jsonify({
            "success": True,
            "item_id": item_id,
            "job_id": job_id,
            "title": title
        })

    except Exception as err:
        return jsonify({"error": str(err)}), 400


# =============================================================
# JOB QUEUE & CANCELLATION API
# =============================================================

@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    return jsonify({"jobs": job_queue.get_all_jobs()})


@app.route("/api/jobs/<int:job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    cancelled = job_queue.cancel_job(job_id)
    return jsonify({"success": cancelled})


# =============================================================
# AI INTELLIGENCE & GROUNDED CHAT API
# =============================================================

@app.route("/api/chat", methods=["POST"])
def chat_endpoint():
    data = request.get_json() or {}
    item_id = data.get("item_id")
    message = data.get("message", "").strip()

    if not item_id or not message:
        return jsonify({"error": "item_id and message are required"}), 400

    item = db.get_item(item_id)
    if not item:
        return jsonify({"error": "Item not found"}), 404

    segments = db.get_segments(item_id)
    transcript_text = Transcriber.transcript_to_text(segments)
    if not transcript_text:
        transcript_text = item.get("raw_analysis", "")

    history = db.get_chat_messages(item_id)
    engine = get_ai_engine()

    # Save user message
    db.save_chat_message(item_id, "user", message)

    response_text = engine.chat_with_content(
        transcript=transcript_text,
        user_query=message,
        history=history
    )

    if not response_text:
        response_text = "I could not generate an answer based on the transcript."

    # Parse any timestamps mentioned in answer for citation buttons
    timestamp_citations = []
    matches = re.findall(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", response_text)
    for m in matches:
        parts = [int(p) for p in m.split(":")]
        secs = parts[0]*60 + parts[1] if len(parts) == 2 else parts[0]*3600 + parts[1]*60 + parts[2]
        timestamp_citations.append({"label": f"[{m}]", "seconds": secs})

    db.save_chat_message(item_id, "assistant", response_text, timestamp_citations)

    return jsonify({
        "reply": response_text,
        "citations": timestamp_citations
    })


@app.route("/api/study/generate", methods=["POST"])
def generate_study_endpoint():
    data = request.get_json() or {}
    item_id = data.get("item_id")

    segments = db.get_segments(item_id)
    transcript_text = Transcriber.transcript_to_text(segments)
    if not transcript_text:
        return jsonify({"error": "No transcript available for study material generation"}), 400

    item = db.get_item(item_id) or {}
    summary = item.get("summary", "")
    key_points = item.get("key_points", "")

    engine = get_ai_engine()
    study_set = engine.generate_study_set(
        summary=summary,
        key_points=key_points,
        transcript_sample=transcript_text[:1000]
    )

    flashcards_id = db.save_study_material(
        item_id, "flashcards", "Flashcards", study_set.get("flashcards", [])
    )
    quiz_id = db.save_study_material(
        item_id, "quiz", "Interactive Practice Quiz", study_set.get("quiz", [])
    )
    cram_id = db.save_study_material(
        item_id, "cram_sheet", "Exam Cram Sheet", study_set.get("cram_sheet", [])
    )

    return jsonify({
        "success": True,
        "study_set": study_set,
        "ids": {
            "flashcards": flashcards_id,
            "quiz": quiz_id,
            "cram_sheet": cram_id
        }
    })


@app.route("/api/script/generate", methods=["POST"])
def generate_script_endpoint():
    data = request.get_json() or {}
    item_id = data.get("item_id")
    script_type = data.get("script_type", "youtube")
    tone = data.get("tone", "professional")
    length = data.get("length", "medium")

    segments = db.get_segments(item_id)
    transcript_text = Transcriber.transcript_to_text(segments)
    if not transcript_text:
        return jsonify({"error": "No transcript found"}), 400

    engine = get_ai_engine()
    script = engine.generate_script(
        transcript=transcript_text,
        script_type=script_type,
        tone=tone,
        length=length
    )

    return jsonify({"script": script, "type": script_type})


# =============================================================
# SMARTSCRIBE 2.0 INTELLIGENCE ROUTES
# =============================================================

@app.route("/api/items/<int:item_id>/smart_mode", methods=["POST"])
def update_smart_mode_endpoint(item_id):
    data = request.get_json() or {}
    new_mode = data.get("smart_mode", "meeting")
    reanalyze = data.get("reanalyze", False)

    db.update_item(item_id, smart_mode=new_mode, smart_mode_confidence="user_defined")

    if reanalyze:
        segments = db.get_segments(item_id)
        transcript_text = Transcriber.transcript_to_text(segments)
        item = db.get_item(item_id) or {}
        if transcript_text:
            engine = get_ai_engine()
            raw = engine.analyze_by_smart_mode(transcript_text, mode=new_mode, item_title=item.get("title", ""))
            parsed = AIEngine.parse_smart_mode_sections(raw, mode=new_mode)
            db.update_item(
                item_id,
                summary=parsed["summary"],
                key_points=parsed["key_points"],
                action_items_text=parsed["action_items"],
                topics=parsed["topics"],
                raw_analysis=raw,
                content_intelligence=json.dumps(parsed.get("custom_sections", []))
            )
            return jsonify({"success": True, "reanalyzed": True, "parsed": parsed})

    return jsonify({"success": True, "smart_mode": new_mode})


@app.route("/api/items/<int:item_id>/speakers/rename", methods=["POST"])
def rename_speaker_endpoint(item_id):
    data = request.get_json() or {}
    old_name = data.get("old_name")
    new_name = data.get("new_name")
    role = data.get("role")
    color = data.get("color", "#06b6d4")

    if not old_name or not new_name:
        return jsonify({"error": "old_name and new_name are required"}), 400

    db.rename_speaker(item_id, old_name, new_name, role=role, color=color)
    speakers = db.get_item_speakers(item_id)
    return jsonify({"success": True, "speakers": speakers})


@app.route("/api/items/<int:item_id>/speakers", methods=["GET"])
def get_speakers_endpoint(item_id):
    speakers = db.get_item_speakers(item_id)
    return jsonify({"speakers": speakers})


@app.route("/api/transform", methods=["POST"])
def transform_endpoint():
    data = request.get_json() or {}
    item_id = data.get("item_id")
    target_format = data.get("target_format", "youtube_script")
    tone = data.get("tone", "professional")
    length = data.get("length", "medium")

    segments = db.get_segments(item_id)
    transcript_text = Transcriber.transcript_to_text(segments)
    if not transcript_text:
        return jsonify({"error": "No transcript available for transformation"}), 400

    engine = get_ai_engine()
    transformed_text = engine.transform_content(
        transcript=transcript_text,
        target_format=target_format,
        tone=tone,
        length=length
    )

    return jsonify({
        "success": True,
        "format": target_format,
        "content": transformed_text
    })


@app.route("/api/search/deep", methods=["GET"])
def search_deep_endpoint():
    q = request.args.get("q", "").strip()
    item_type = request.args.get("type")
    workspace_id = request.args.get("workspace_id", type=int)

    results = db.search_deep(q, item_type=item_type, workspace_id=workspace_id)
    return jsonify(results)


# =============================================================
# WORKSPACES API
# =============================================================

@app.route("/api/workspaces", methods=["GET"])
def get_workspaces_endpoint():
    workspaces = db.get_all_workspaces()
    return jsonify({"workspaces": workspaces})


@app.route("/api/workspaces", methods=["POST"])
def create_workspace_endpoint():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Workspace name is required"}), 400
    description = data.get("description", "")
    color = data.get("color", "#6366f1")

    ws_id = db.create_workspace(name, description, color)
    return jsonify({"success": True, "id": ws_id, "name": name})


@app.route("/api/workspaces/<int:ws_id>", methods=["DELETE"])
def delete_workspace_endpoint(ws_id):
    db.delete_workspace(ws_id)
    return jsonify({"success": True})


@app.route("/api/items/<int:item_id>/workspace", methods=["POST"])
def assign_workspace_endpoint(item_id):
    data = request.get_json() or {}
    ws_id = data.get("workspace_id")
    db.assign_item_workspace(item_id, ws_id)
    return jsonify({"success": True})


# =============================================================
# SPEAKING COACH API
# =============================================================

@app.route("/api/coach/analyze", methods=["POST"])
def coach_analyze_endpoint():
    data = request.get_json() or {}
    item_id = data.get("item_id")
    title = data.get("title", "Speech Practice")

    if not item_id:
        return jsonify({"error": "item_id is required"}), 400

    item = db.get_item(item_id)
    segments = db.get_segments(item_id)
    transcript = Transcriber.transcript_to_text(segments)

    if not transcript:
        return jsonify({"error": "No speech transcript found"}), 400

    duration = item.get("duration", 0.0) if item else 0.0
    engine = get_ai_engine()
    result = engine.analyze_speech_performance(transcript, duration=duration)

    session_id = db.save_coach_session(
        title=title,
        duration=result["duration"],
        word_count=result["word_count"],
        wpm=result["wpm"],
        pace_rating=result["pace_rating"],
        filler_count=result["filler_count"],
        filler_breakdown=result["filler_breakdown"],
        feedback=result["coach_feedback"],
        transcript=transcript
    )

    result["session_id"] = session_id
    return jsonify({"success": True, "report": result})


@app.route("/api/coach/sessions", methods=["GET"])
def get_coach_sessions_endpoint():
    sessions = db.get_coach_sessions()
    return jsonify({"sessions": sessions})


@app.route("/api/coach/sessions/<int:session_id>", methods=["DELETE"])
def delete_coach_session_endpoint(session_id):
    db.delete_coach_session(session_id)
    return jsonify({"success": True})


# =============================================================
# "TEACH ME" SOCRATIC TUTOR API
# =============================================================

@app.route("/api/teach/step", methods=["POST"])
def teach_step_endpoint():
    data = request.get_json() or {}
    item_id = data.get("item_id")
    level = data.get("level", "beginner")
    user_reply = data.get("user_reply")
    step_index = data.get("step_index", 1)

    segments = db.get_segments(item_id)
    transcript = Transcriber.transcript_to_text(segments)
    if not transcript:
        return jsonify({"error": "No transcript found"}), 400

    engine = get_ai_engine()
    response = engine.teach_step(transcript, level=level, user_reply=user_reply, step_index=step_index)
    return jsonify({"success": True, "step_index": step_index, "response": response})


# =============================================================
# EXPORT API (MARKDOWN, TXT, JSON)
# =============================================================

@app.route("/api/export/<int:item_id>/<export_format>", methods=["GET"])
def export_item(item_id, export_format):
    item = db.get_item(item_id)
    if not item:
        return jsonify({"error": "Item not found"}), 404

    segments = db.get_segments(item_id)
    chapters = db.get_chapters(item_id)
    notes = db.get_notes(item_id)
    actions = db.get_action_items(item_id)

    title_slug = re.sub(r"[^\w\-_\. ]", "_", item["title"])

    if export_format == "json":
        data = {
            "item": item,
            "segments": segments,
            "chapters": chapters,
            "notes": notes,
            "action_items": actions
        }
        return Response(
            json.dumps(data, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{title_slug}.json"'}
        )

    elif export_format == "txt":
        lines = [
            f"=== {item['title']} ===",
            f"Date: {item['created_at']} | Type: {item['type'].upper()} | Duration: {item['duration']:.1f}s",
            "",
            "--- SUMMARY ---",
            item.get("summary") or "No summary available.",
            "",
            "--- KEY POINTS ---",
            item.get("key_points") or "No key points recorded.",
            "",
            "--- ACTION ITEMS ---",
            item.get("action_items_text") or "No action items recorded.",
            "",
            "--- FULL TRANSCRIPT ---"
        ]
        for s in segments:
            lines.append(f"[{Transcriber.format_timestamp(s['start_time'])}] {s['speaker']}: {s['text']}")

        return Response(
            "\n".join(lines),
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{title_slug}.txt"'}
        )

    else:  # Default Markdown
        md = [
            f"# {item['title']}",
            f"**Type:** {item['type'].title()}  |  **Date:** {item['created_at']}  |  **Duration:** {item['duration']:.1f}s",
            "",
            "## 📋 Executive Summary",
            item.get("summary") or "*No summary available.*",
            "",
            "## 💡 Key Takeaways",
            item.get("key_points") or "*No key points recorded.*",
            "",
            "## ✅ Action Items",
            item.get("action_items_text") or "*No action items.*",
            "",
            "## ⏱ Chapters & Timeline"
        ]
        for ch in chapters:
            md.append(f"- **`{Transcriber.format_timestamp(ch['start_time'])}`** {ch['title']}: {ch.get('description', '')}")

        md.extend([
            "",
            "## 📝 Full Transcript",
            ""
        ])
        for s in segments:
            md.append(f"> **`{Transcriber.format_timestamp(s['start_time'])}`** *{s['speaker']}*: {s['text']}\n")

        return Response(
            "\n".join(md),
            mimetype="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{title_slug}.md"'}
        )


# =============================================================
# SYSTEM STATUS API
# =============================================================

@app.route("/api/system/resources", methods=["GET"])
def system_resources():
    from backend.resource_manager import ResourceManager
    rm = ResourceManager()
    return jsonify({
        "success": True,
        "resources": rm.get_resource_state()
    })


@app.route("/api/items/<int:item_id>/chunks", methods=["GET"])
def get_item_chunks(item_id):
    chunks = db.get_chunks(item_id)
    return jsonify({
        "success": True,
        "item_id": item_id,
        "chunks": chunks,
        "count": len(chunks)
    })


# =============================================================
# ASSISTANT / JARVIS API (PHASE 3B)
# =============================================================

@app.route("/api/assistant/command", methods=["POST"])
def assistant_command():
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    session_id = data.get("session_id")
    confirmation_token = data.get("confirmation_token")

    if not query:
        return jsonify({"success": False, "message": "Query cannot be empty."}), 400

    from backend.assistant import AssistantEngine
    engine = AssistantEngine()
    result = engine.process_command(
        query=query,
        session_id=session_id,
        confirmation_token=confirmation_token
    )
    return jsonify(result)


@app.route("/api/assistant/confirm", methods=["POST"])
def assistant_confirm():
    data = request.get_json() or {}
    token = data.get("confirmation_token")
    action = data.get("action", "approve")

    if not token:
        return jsonify({"success": False, "message": "Missing confirmation_token."}), 400

    from backend.assistant.permission_manager import PermissionManager
    from backend.assistant.tool_registry import ToolRegistry
    perm_mgr = PermissionManager()

    if action == "deny":
        with perm_mgr._token_lock:
            perm_mgr._pending_confirmations.pop(token, None)
        return jsonify({"success": True, "status": "cancelled", "message": "Action cancelled by user."})

    valid, req, reason = perm_mgr.validate_confirmation_token(token)
    if not valid:
        return jsonify({"success": False, "status": "error", "message": reason}), 400

    tool = ToolRegistry().get(req["tool_name"])
    if not tool:
        return jsonify({"success": False, "status": "error", "message": "Tool not found."}), 404

    try:
        if req["tool_name"] == "execute_plan":
            req["arguments"]["already_validated"] = True
        res = tool.execution_fn(**req["arguments"])
        return jsonify({
            "success": True,
            "status": "complete",
            "tool": req["tool_name"],
            "data": res,
            "message": "Action confirmed and executed successfully."
        })
    except Exception as e:
        return jsonify({"success": False, "status": "execution_failed", "error": str(e)}), 500


# =============================================================
# VOICE ASSISTANT API (PHASE 3E)
# =============================================================

@app.route("/api/assistant/voice", methods=["POST"])
def assistant_voice():
    """
    Unified voice input endpoint:
    - If multipart file 'audio' is present: saves to temporary WAV, transcribes via Faster-Whisper, and processes through AssistantEngine.
    - If JSON with 'audio_base64' is present: decodes base64 and processes.
    - If JSON with action 'capture' is present: records synchronously from backend microphone and processes.
    """
    import tempfile
    import base64
    from backend.assistant import AssistantEngine
    engine = AssistantEngine()

    session_id = request.form.get("session_id") or (request.get_json(silent=True) or {}).get("session_id")
    confirmation_token = request.form.get("confirmation_token") or (request.get_json(silent=True) or {}).get("confirmation_token")

    # 1. Multipart Audio Upload (from browser MediaRecorder or Web Audio)
    if "audio" in request.files:
        audio_file = request.files["audio"]
        if not audio_file or not audio_file.filename:
            return jsonify({"success": False, "status": "failed", "message": "No audio file uploaded."}), 400

        suffix = Path(audio_file.filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            audio_file.save(tmp_path)

        try:
            result = engine.process_voice(
                audio_data=tmp_path,
                session_id=int(session_id) if session_id else None,
                confirmation_token=confirmation_token
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return jsonify(result)

    # 2. JSON Payload
    data = request.get_json(silent=True) or {}

    if data.get("audio_base64"):
        raw_bytes = base64.b64decode(data["audio_base64"])
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(raw_bytes)
        try:
            result = engine.process_voice(
                audio_data=tmp_path,
                session_id=int(session_id) if session_id else None,
                confirmation_token=confirmation_token
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return jsonify(result)

    if data.get("action") == "capture":
        duration = float(data.get("duration", 3.0))
        try:
            audio_arr = assistant_voice_capture.capture(duration=duration)
            result = engine.process_voice(
                audio_data=audio_arr,
                session_id=int(session_id) if session_id else None,
                confirmation_token=confirmation_token
            )
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "status": "failed", "message": str(e)}), 500

    return jsonify({"success": False, "status": "failed", "message": "Invalid voice request format."}), 400


@app.route("/api/assistant/voice/start", methods=["POST"])
def assistant_voice_start():
    try:
        ok = assistant_voice_capture.start()
        if not ok:
            return jsonify({"success": False, "message": "Voice capture is already active."}), 400
        return jsonify({"success": True, "status": "listening"})
    except Exception as e:
        return jsonify({"success": False, "status": "failed", "error": str(e)}), 500


@app.route("/api/assistant/voice/stop", methods=["POST"])
def assistant_voice_stop():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    confirmation_token = data.get("confirmation_token")

    try:
        audio_arr = assistant_voice_capture.stop()
        from backend.assistant import AssistantEngine
        engine = AssistantEngine()
        result = engine.process_voice(
            audio_data=audio_arr,
            session_id=int(session_id) if session_id else None,
            confirmation_token=confirmation_token
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "status": "failed", "error": str(e)}), 500


@app.route("/api/assistant/voice/cancel", methods=["POST"])
def assistant_voice_cancel():
    try:
        assistant_voice_capture.cancel()
        return jsonify({"success": True, "status": "cancelled"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/assistant/voice/status", methods=["GET"])
def assistant_voice_status():
    return jsonify({
        "listening": assistant_voice_capture.is_listening(),
        "error": assistant_voice_capture.error
    })


# =============================================================
# LOCAL VOICE RESPONSE / TTS API (PHASE 3I)
# =============================================================

@app.route("/api/assistant/speech/stop", methods=["POST"])
def assistant_speech_stop():
    """
    Idempotent barge-in stop endpoint. Immediately silences active speech.
    Returns cancellation latency in milliseconds.
    """
    try:
        from backend.assistant.speech import SpeechManager
        latency_ms = SpeechManager().stop()
        return jsonify({
            "success": True,
            "status": "stopped",
            "cancellation_latency_ms": latency_ms
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/assistant/speech/status", methods=["GET"])
def assistant_speech_status():
    """
    Returns current speech engine and playback status.
    """
    try:
        from backend.assistant.speech import SpeechManager
        status = SpeechManager().get_status()
        return jsonify({
            "success": True,
            "speaking": status.get("speaking", False),
            "voice": status.get("voice", "Default"),
            "voice_id": status.get("voice_id", ""),
            "rate": status.get("rate", 175),
            "volume": status.get("volume", 0.85),
            "speech_enabled": status.get("speech_enabled", True)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/assistant/speech/voices", methods=["GET"])
def assistant_speech_voices():
    """
    Lists all available system speech voices.
    """
    try:
        from backend.assistant.speech import SpeechManager
        voices = SpeechManager().get_available_voices()
        return jsonify({
            "success": True,
            "voices": voices
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/assistant/tools", methods=["GET"])
def assistant_tools():
    from backend.assistant.tool_registry import ToolRegistry
    reg = ToolRegistry()
    return jsonify({
        "success": True,
        "tools": reg.list_tools()
    })


@app.route("/api/system/status", methods=["GET"])
def system_status():
    return jsonify({
        "cpu_cores": CPU_CORES,
        "llm_threads": LLM_THREADS,
        "whisper_model": WHISPER_MODEL,
        "whisper_device": WHISPER_DEVICE,
        "model_file_exists": Path(MODELS_DIR / "model.gguf").exists(),
        "is_recording": recorder.is_recording(),
        "active_jobs_count": len([j for j in job_queue.get_all_jobs() if j["status"] == "running"])
    })


# =====================================================================
# BROWSER INTELLIGENCE API (PHASE 3G)
# =====================================================================

@app.route("/api/browser/page", methods=["POST"])
def browser_receive_page():
    """
    Receives rendered DOM or page data from Manifest V3 extension or bookmarklet.
    Validates, sanitizes, and updates active BrowserPageContext.
    Strictly strips credentials and private browser fields.
    """
    try:
        data = request.get_json(silent=True) or {}
        from backend.browser import BrowserManager
        mgr = BrowserManager()
        ctx = mgr.process_extension_payload(data)
        return jsonify({
            "success": True,
            "url": ctx.url,
            "domain": ctx.domain,
            "title": ctx.title,
            "word_count": ctx.word_count,
            "status": ctx.extraction_status,
            "source": ctx.source_type
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/browser/current", methods=["GET"])
def browser_get_current():
    """
    Returns current active safe BrowserPageContext without credentials.
    """
    try:
        from backend.browser import BrowserManager
        mgr = BrowserManager()
        ctx = mgr.get_current_page()
        if not ctx:
            return jsonify({"has_page": False, "page": None})
        return jsonify({
            "has_page": True,
            "page": ctx.to_dict()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/browser/fetch", methods=["POST"])
def browser_fetch_url():
    """
    Tier 1 URL ingestion: Fetches public URL safely with SSRF protections.
    """
    try:
        data = request.get_json(silent=True) or {}
        url = data.get("url")
        if not url:
            return jsonify({"success": False, "error": "URL is required"}), 400

        from backend.browser import BrowserManager
        mgr = BrowserManager()
        ctx = mgr.ingest_url(url)
        return jsonify({
            "success": ctx.extraction_status in ("success", "partial", "filtered"),
            "status": ctx.extraction_status,
            "page": ctx.to_dict()
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/browser/page", methods=["DELETE"])
def browser_clear_page():
    """
    Clears active browser page context from memory.
    """
    try:
        from backend.browser import BrowserManager
        mgr = BrowserManager()
        mgr.clear_current_page()
        return jsonify({"success": True, "message": "Browser page context cleared."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    print("Starting SmartScribe Local AI Server on http://127.0.0.1:5000 ...")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
