import os
from pathlib import Path
from backend.recorder import AudioRecorder
from backend.database import Database
from backend.search_engine import bm25_search_chunks
from backend.ai_engine import AIEngine
from config import RECORDINGS_DIR

_recorder_instance = None

def _get_recorder():
    global _recorder_instance
    if _recorder_instance is None:
        _recorder_instance = AudioRecorder()
    return _recorder_instance

def tool_start_recording(**kwargs):
    """Starts microphone recording via AudioRecorder."""
    rec = _get_recorder()
    if rec.recording:
        return {"success": False, "message": "Recording is already active."}
    success = rec.start()
    return {
        "success": success,
        "message": "Microphone recording started." if success else "Failed to start recording.",
        "is_recording": rec.recording
    }

def tool_stop_recording(**kwargs):
    """Stops microphone recording and saves the audio file."""
    rec = _get_recorder()
    if not rec.recording:
        return {"success": False, "message": "No recording is currently active."}
    file_path = rec.stop()
    return {
        "success": bool(file_path and Path(file_path).exists()),
        "message": "Recording saved successfully." if file_path else "Recording stopped without data.",
        "saved_path": str(file_path) if file_path else None
    }

def tool_search_transcript(query: str, session_id: int = None, **kwargs):
    """Performs Okapi BM25 search over chunks of the active transcript session."""
    db = Database()
    if not session_id:
        items = db.get_items()
        if not items:
            return {"success": False, "message": "No sessions found in database."}
        session_id = items[0]["id"]
        
    chunks = db.get_chunks(session_id)
    if not chunks:
        # Fallback: check raw segments
        segs = db.get_segments(session_id)
        if segs:
            from backend.chunker import create_transcript_chunks
            chunks = create_transcript_chunks(segs, target_words=450, overlap_segments=1)
            db.save_chunks(session_id, chunks)

    if not chunks:
        return {"success": False, "message": f"No transcript chunks found for session {session_id}."}

    results = bm25_search_chunks(chunks, query, top_k=3)
    return {
        "success": True,
        "session_id": session_id,
        "match_count": len(results),
        "results": [
            {
                "chunk_id": r["chunk_id"],
                "start": r["start_timestamp"],
                "end": r["end_timestamp"],
                "score": r["relevance_score"],
                "text": r["text"][:200] + "..."
            }
            for r in results
        ]
    }

def tool_search_local_files(query: str = "", **kwargs):
    """Searches recordings within SmartScribe's recordings directory."""
    rec_dir = Path(RECORDINGS_DIR)
    q = query.lower().strip()
    matches = []
    for f in rec_dir.glob("*"):
        if f.is_file() and (not q or q in f.name.lower()):
            matches.append({
                "name": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "modified": f.stat().st_mtime
            })
    return {
        "success": True,
        "count": len(matches),
        "files": matches[:20]
    }

def tool_open_file(file_name: Optional[str] = None, target_spec: Optional[Any] = None, query: Optional[str] = None, **kwargs):
    """
    Opens a file or approved workspace folder in its default viewer.
    Strictly restricted to approved directories (recordings, notes, workspaces, downloads) with path-jail validation.
    Enforces structured target resolution: strictly verifies file type, expected extension, semantic keywords,
    and temporal ordering. Never opens an unintended file type on mismatch.
    """
    from backend.assistant.target_resolver import FileTargetResolver, FileTargetSpec, StructuredTarget
    from backend.assistant.action_verifier import ActionVerifier
    from backend.assistant.tools.desktop_tools import ALLOWED_DOCUMENT_EXTENSIONS, PROHIBITED_EXTENSIONS

    rec_dir = Path(RECORDINGS_DIR).resolve()
    notes_dir = (rec_dir.parent / "notes").resolve()
    notes_dir.mkdir(exist_ok=True)
    workspaces_dir = (rec_dir.parent / "workspaces").resolve()
    workspaces_dir.mkdir(exist_ok=True)
    
    downloads_dir = (Path.home() / "Downloads").resolve()
    allowed_roots = [rec_dir, notes_dir, workspaces_dir]
    if downloads_dir.exists():
        allowed_roots.append(downloads_dir)

    raw_fn = (file_name or "").strip()
    fn_lower = raw_fn.lower()

    # Path traversal and absolute path jail security guard
    if ".." in raw_fn or raw_fn.startswith("/") or raw_fn.startswith("\\") or ":" in raw_fn:
        is_safe = False
        try:
            p = Path(raw_fn).resolve()
            for root in allowed_roots:
                if os.path.commonpath([str(p), str(root)]) == str(root):
                    is_safe = True
                    break
        except Exception:
            is_safe = False
        if not is_safe:
            return {
                "success": False,
                "verified": False,
                "message": "Access denied: File or directory outside approved safe workspace folders."
            }

    # 1. Direct Folder Target Resolution
    folder_targets = {
        "downloads": downloads_dir,
        "downloads folder": downloads_dir,
        "my downloads": downloads_dir,
        "my downloads folder": downloads_dir,
        "recordings": rec_dir,
        "recordings folder": rec_dir,
        "my recordings": rec_dir,
        "my recordings folder": rec_dir,
        "notes": notes_dir,
        "notes folder": notes_dir,
        "smartscribe notes": notes_dir,
        "workspaces": workspaces_dir,
        "workspaces folder": workspaces_dir,
        "my workspaces": workspaces_dir
    }

    if fn_lower in folder_targets:
        target_fld = folder_targets[fn_lower]
        try:
            os.startfile(str(target_fld))
            verification = ActionVerifier.verify_open_folder(target_fld)
            return {
                "success": verification.verified,
                "verified": verification.verified,
                "message": f"Opened folder {target_fld.name}" if verification.verified else f"Failed to verify opening '{target_fld.name}'.",
                "path": str(target_fld),
                "is_directory": True,
                "verification": verification.to_dict()
            }
        except Exception as e:
            return {
                "success": False,
                "verified": False,
                "message": f"Failed to open folder '{target_fld.name}': {e}"
            }

    # 2. Structured Target Resolution
    if target_spec:
        if isinstance(target_spec, dict):
            spec = FileTargetSpec(**target_spec)
        elif isinstance(target_spec, FileTargetSpec):
            spec = target_spec
        else:
            spec = FileTargetResolver.parse_query(str(target_spec))
    else:
        # Resolve query string or filename
        resolve_input = query or raw_fn or "latest"
        spec = FileTargetResolver.parse_query(resolve_input)

    # Perform multi-root candidate discovery with strict constraint enforcement
    resolved = FileTargetResolver.resolve(spec, allowed_roots)

    if not resolved.is_found or not resolved.resolved_path:
        if spec.requested_name:
            err_msg = f"File does not exist: '{spec.requested_name}'"
        else:
            err_msg = resolved.error_message or f"No matching {spec.requested_type or 'file'} found."
        return {
            "success": False,
            "verified": False,
            "message": err_msg,
            "requested_target": spec.raw_query,
            "requested_type": spec.requested_type,
            "structured_target": resolved.to_dict(),
            "verification": {
                "verified": False,
                "method": "structured_target_resolution",
                "details": err_msg
            }
        }

    target = resolved.resolved_path.resolve()

    # 3. Path traversal jail check against allowed roots
    is_safe = False
    for root in allowed_roots:
        try:
            if os.path.commonpath([str(target), str(root)]) == str(root):
                is_safe = True
                break
        except Exception:
            continue

    if not is_safe:
        return {
            "success": False,
            "verified": False,
            "message": "Access denied: File or directory outside approved safe workspace folders."
        }

    if not target.exists():
        return {
            "success": False,
            "verified": False,
            "message": f"File does not exist: '{target.name}'"
        }

    # 4. File type safety verification (Reject executables and scripts)
    if target.is_file():
        ext = target.suffix.lower()
        parts = target.name.lower().split(".")
        if any(f".{p}" in PROHIBITED_EXTENSIONS for p in parts[1:]) or ext in PROHIBITED_EXTENSIONS:
            return {
                "success": False,
                "verified": False,
                "message": f"Access denied: Executable or script file '{target.name}' is prohibited."
            }

        if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
            return {
                "success": False,
                "verified": False,
                "message": f"Access denied: Unsupported or prohibited file type '{ext}'."
            }

    try:
        os.startfile(str(target))
        if target.is_dir():
            verification = ActionVerifier.verify_open_folder(target)
        else:
            verification = ActionVerifier.verify_open_file(target, structured_target=resolved)

        if spec and spec.requested_name:
            req_desc = spec.requested_name
        else:
            req_desc = f"{spec.temporal_filter or ''} {spec.requested_type or 'file'}".strip() if spec else "file"
            if spec and spec.semantic_filters:
                clean_sem = " ".join(dict.fromkeys(spec.semantic_filters))
                req_desc = f"{spec.temporal_filter or ''} {clean_sem} {spec.requested_type or 'file'}".strip()

        return {
            "success": verification.verified,
            "verified": verification.verified,
            "message": f"Opened {target.name}" if verification.verified else f"Failed to verify opening '{target.name}': {verification.details}",
            "file_name": target.name,
            "path": str(target),
            "requested_target": req_desc or target.name,
            "requested_type": spec.requested_type if spec else target.suffix.lstrip("."),
            "structured_target": resolved.to_dict(),
            "is_directory": target.is_dir(),
            "verification": verification.to_dict()
        }
    except Exception as e:
        return {
            "success": False,
            "verified": False,
            "message": f"Failed to open '{target.name}': {e}"
        }

def tool_summarize_current_session(session_id: int = None, **kwargs):
    """Triggers analysis for the current session."""
    db = Database()
    if not session_id:
        items = db.get_items()
        if not items:
            return {"success": False, "message": "No session available."}
        session_id = items[0]["id"]

    item = db.get_item(session_id)
    if not item:
        return {"success": False, "message": f"Session {session_id} not found."}

    summary = item.get("summary")
    if summary and len(summary) > 50:
        return {
            "success": True,
            "session_id": session_id,
            "title": item.get("title"),
            "summary": summary
        }

    # Generate on demand
    segs = db.get_segments(session_id)
    if not segs:
        return {"success": False, "message": "No transcript available for this session."}

    from backend.transcriber import Transcriber
    text = Transcriber.transcript_to_text(segs)
    ai = AIEngine()
    res = ai.analyze_transcript(text, item_type=item.get("type", "meeting"))
    return {
        "success": True,
        "session_id": session_id,
        "title": item.get("title"),
        "summary": res[:400] + "..."
    }

def tool_delete_recording(session_id: int = None, **kwargs):
    """Deletes a recording session (Requires Confirmation)."""
    db = Database()
    if not session_id:
        items = db.get_items()
        if not items:
            return {"success": False, "message": "No sessions found to delete."}
        session_id = items[0]["id"]

    item = db.get_item(session_id)
    if not item:
        return {"success": False, "message": f"Session {session_id} not found."}

    # Delete media file if present
    media_path = item.get("media_path")
    if media_path and os.path.exists(media_path):
        try:
            os.remove(media_path)
        except Exception:
            pass

    db.delete_item(session_id)
    return {
        "success": True,
        "session_id": session_id,
        "message": f"Session '{item.get('title')}' deleted successfully."
    }
