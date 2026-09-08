import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from config import RECORDINGS_DIR, BASE_DIR
from backend.assistant.desktop_context import get_active_window_info

NOTES_DIR = BASE_DIR / "notes"
WORKSPACES_DIR = BASE_DIR / "workspaces"
NOTES_DIR.mkdir(exist_ok=True)
WORKSPACES_DIR.mkdir(exist_ok=True)

# Strict document and media extension allowlist
ALLOWED_DOCUMENT_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".docx", ".pptx", ".xlsx", ".csv",
    ".wav", ".mp3", ".mp4", ".m4a", ".png", ".jpg", ".jpeg", ".json"
}

# Strict executable/script denylist
PROHIBITED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".py", ".sh",
    ".msi", ".dll", ".sys", ".scr", ".com", ".pif", ".cpl", ".reg"
}

def tool_get_active_window(**kwargs) -> Dict[str, Any]:
    """
    Returns the user's currently active foreground window title, process ID, and application name.
    Strictly read-only and zero private data exposure.
    """
    info = get_active_window_info()
    return {
        "success": True,
        "window_title": info.get("window_title", "Desktop"),
        "process_name": info.get("process_name", "explorer.exe"),
        "application": info.get("application", "Windows Desktop"),
        "pid": info.get("pid", 0)
    }

def get_approved_roots() -> List[Path]:
    """Returns the list of explicitly approved file search/access roots."""
    roots = [
        Path(RECORDINGS_DIR).resolve(),
        NOTES_DIR.resolve(),
        WORKSPACES_DIR.resolve()
    ]
    downloads = (Path.home() / "Downloads").resolve()
    if downloads.exists():
        roots.append(downloads)
    return roots

def tool_find_desktop_files(query: str = "", file_type: Optional[str] = None, limit: int = 10, **kwargs) -> Dict[str, Any]:
    """
    Safely searches files across approved roots (recordings, notes, workspaces, Downloads).
    Deterministic token and temporal matching.
    """
    q = (query or "").lower().strip()
    ft = (file_type or "").lower().strip()
    
    roots = get_approved_roots()
    matches = []

    # Detect temporal intent
    now = time.time()
    yesterday_start = now - (48 * 3600)
    yesterday_end = now - (18 * 3600)
    is_yesterday_query = "yesterday" in q
    is_latest_query = any(w in q for w in ("latest", "recent", "newest", "last"))

    # Detect type filters from query
    target_exts = set(ALLOWED_DOCUMENT_EXTENSIONS)
    if "pdf" in q or ft == "pdf":
        target_exts = {".pdf"}
    elif any(w in q for w in ("recording", "audio", "voice")) or ft in ("audio", "recording"):
        target_exts = {".wav", ".mp3", ".m4a", ".mp4"}
    elif any(w in q for w in ("note", "notes", "doc", "document")) or ft in ("notes", "doc"):
        target_exts = {".md", ".txt", ".docx", ".pdf"}

    # Clean search keywords
    clean_words = [
        w for w in q.split()
        if w not in ("find", "my", "the", "latest", "recent", "yesterday", "file", "files", "pdf", "notes", "recording", "show", "me")
    ]

    for root in roots:
        if not root.exists():
            continue
        try:
            for item in root.glob("*"):
                if not item.is_file():
                    continue

                ext = item.suffix.lower()
                if ext not in target_exts or ext in PROHIBITED_EXTENSIONS:
                    continue

                mtime = item.stat().st_mtime
                if is_yesterday_query and not (yesterday_start <= mtime <= yesterday_end):
                    continue

                name_lower = item.name.lower()
                
                # Check keyword match
                if clean_words:
                    if not any(w in name_lower for w in clean_words):
                        continue

                mtime_dt = datetime.fromtimestamp(mtime)
                matches.append({
                    "name": item.name,
                    "path": str(item),
                    "size_kb": round(item.stat().st_size / 1024, 1),
                    "extension": ext,
                    "directory": root.name,
                    "modified": mtime,
                    "modified_str": mtime_dt.strftime("%Y-%m-%d %H:%M")
                })
        except Exception:
            continue

    # Sort matches
    if is_latest_query or not clean_words:
        matches.sort(key=lambda f: f["modified"], reverse=True)
    else:
        # Prioritize exact keyword matches then recency
        def score(f):
            exact = sum(1 for w in clean_words if w in f["name"].lower())
            return (exact, f["modified"])
        matches.sort(key=score, reverse=True)

    results = matches[:limit]
    return {
        "success": True,
        "query": query,
        "count": len(results),
        "total_found": len(matches),
        "files": results
    }

def sanitize_filesystem_name(name: str) -> str:
    """Strips illegal Windows filesystem characters, control tokens, and traversal sequences."""
    if not name or not isinstance(name, str):
        return ""
    # Strip ChatML and system control markers
    s = re.sub(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>", " ", name)
    # Strip HTML/XML
    s = re.sub(r"<[^>]+>", " ", s)
    # Remove traversal sequences
    s = s.replace("..", "")
    # Remove Windows prohibited filename characters: \ / : * ? " < > |
    s = re.sub(r'[\\/:*?"<>|]', "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s

def tool_create_note(title: str, content: str = "", extension: str = ".md", **kwargs) -> Dict[str, Any]:
    """
    Safely creates a new markdown or text note inside the approved notes directory.
    Verifies disk persistence and readability before returning.
    """
    # Traversal reject
    if ".." in str(title) or "/" in str(title) or "\\" in str(title):
        return {
            "success": False,
            "verified": False,
            "message": "Access denied: Cannot create note outside approved notes directory."
        }

    clean_title = sanitize_filesystem_name(title)
    if not clean_title:
        return {
            "success": False,
            "verified": False,
            "message": "Note creation failed: Title contains invalid or prohibited characters."
        }

    # Reject prohibited/executable extensions
    ext = (extension or ".md").lower().strip()
    if not ext.startswith("."):
        ext = f".{ext}"
    if ext not in {".md", ".txt"}:
        ext = ".md"

    # Reject double extension spoofing
    parts = clean_title.lower().split(".")
    if any(f".{p}" in PROHIBITED_EXTENSIONS for p in parts):
        return {
            "success": False,
            "verified": False,
            "message": "Access denied: Prohibited file extension in note title."
        }

    # If title already ends with .md or .txt, keep it, otherwise append
    if clean_title.lower().endswith(".md") or clean_title.lower().endswith(".txt"):
        filename = clean_title
    else:
        filename = f"{clean_title}{ext}"

    target = (NOTES_DIR / filename).resolve()

    # Traversal jail check
    try:
        if os.path.commonpath([str(target), str(NOTES_DIR.resolve())]) != str(NOTES_DIR.resolve()):
            return {
                "success": False,
                "verified": False,
                "message": "Access denied: Cannot create note outside approved notes directory."
            }
    except Exception:
        return {
            "success": False,
            "verified": False,
            "message": "Access denied: Invalid target path."
        }

    try:
        target.write_text(content, encoding="utf-8")
        from backend.assistant.action_verifier import ActionVerifier
        verification = ActionVerifier.verify_create_note(target, expected_content=content)

        return {
            "success": verification.verified,
            "verified": verification.verified,
            "note_title": clean_title,
            "file_name": target.name,
            "path": str(target),
            "size_bytes": target.stat().st_size if target.exists() else 0,
            "message": f"Created note '{target.name}' successfully in notes folder.",
            "verification": verification.to_dict()
        }
    except Exception as e:
        return {
            "success": False,
            "verified": False,
            "message": f"Failed to create note '{clean_title}': {e}"
        }

def tool_create_folder(folder_name: str, parent: str = "workspaces", **kwargs) -> Dict[str, Any]:
    """
    Safely creates a new directory inside approved workspace or notes folders.
    Verifies directory creation on disk before returning.
    """
    if ".." in str(folder_name) or ".." in str(parent):
        return {
            "success": False,
            "verified": False,
            "message": "Access denied: Cannot create directory outside approved workspace roots."
        }

    raw_parts = [p.strip() for p in re.split(r"[/\\]+", str(folder_name)) if p.strip()]
    if not raw_parts:
        return {
            "success": False,
            "verified": False,
            "message": "Folder creation failed: Empty folder name."
        }

    sanitized_parts = [sanitize_filesystem_name(p) for p in raw_parts]
    if any(not p for p in sanitized_parts):
        return {
            "success": False,
            "verified": False,
            "message": "Folder creation failed: Folder name contains invalid or prohibited characters."
        }

    clean_rel = Path(*sanitized_parts)

    parent_clean = (parent or "workspaces").strip()
    if parent_clean.lower() == "notes":
        root = NOTES_DIR
    elif parent_clean.lower() == "workspaces":
        root = WORKSPACES_DIR
    elif (WORKSPACES_DIR / parent_clean).exists() and (WORKSPACES_DIR / parent_clean).is_dir():
        root = (WORKSPACES_DIR / parent_clean).resolve()
    elif (NOTES_DIR / parent_clean).exists() and (NOTES_DIR / parent_clean).is_dir():
        root = (NOTES_DIR / parent_clean).resolve()
    else:
        root = WORKSPACES_DIR

    target = (root / clean_rel).resolve()

    # Traversal jail check: target MUST be inside WORKSPACES_DIR or NOTES_DIR
    allowed_roots = [WORKSPACES_DIR.resolve(), NOTES_DIR.resolve()]
    is_safe = False
    for ar in allowed_roots:
        try:
            if os.path.commonpath([str(target), str(ar)]) == str(ar):
                is_safe = True
                break
        except Exception:
            continue

    if not is_safe:
        return {
            "success": False,
            "verified": False,
            "message": "Access denied: Cannot create directory outside approved workspace roots."
        }

    try:
        target.mkdir(parents=True, exist_ok=True)
        from backend.assistant.action_verifier import ActionVerifier
        verification = ActionVerifier.verify_create_folder(target)

        clean_display_name = "/".join(sanitized_parts)
        return {
            "success": verification.verified,
            "verified": verification.verified,
            "folder_name": clean_display_name,
            "parent": parent_clean,
            "path": str(target),
            "message": f"Created folder '{clean_display_name}' in {parent_clean}.",
            "verification": verification.to_dict()
        }
    except Exception as e:
        return {
            "success": False,
            "verified": False,
            "message": f"Failed to create folder '{'/'.join(sanitized_parts)}': {e}"
        }

def tool_open_folder(folder_name: str, **kwargs) -> Dict[str, Any]:
    """
    Safely opens an approved workspace directory in Windows Explorer.
    """
    if ".." in str(folder_name):
        return {
            "success": False,
            "verified": False,
            "message": "Access denied: Directory traversal detected."
        }

    q = (folder_name or "").lower().strip()

    target: Optional[Path] = None
    if q in ("notes", "notes folder"):
        target = NOTES_DIR.resolve()
    elif q in ("recordings", "recordings folder"):
        target = Path(RECORDINGS_DIR).resolve()
    elif q in ("downloads", "downloads folder"):
        target = (Path.home() / "Downloads").resolve()
    elif q in ("workspaces", "workspaces folder"):
        target = WORKSPACES_DIR.resolve()
    elif "note" in q and "/" not in q and "\\" not in q:
        target = NOTES_DIR.resolve()
    elif "recording" in q and "/" not in q and "\\" not in q:
        target = Path(RECORDINGS_DIR).resolve()
    elif "download" in q and "/" not in q and "\\" not in q:
        target = (Path.home() / "Downloads").resolve()
    elif "workspace" in q and "/" not in q and "\\" not in q:
        target = WORKSPACES_DIR.resolve()
    else:
        # Check subfolder in workspaces or notes
        parts = [sanitize_filesystem_name(p) for p in re.split(r"[/\\]+", q) if p.strip()]
        clean_rel = Path(*parts) if parts else Path(sanitize_filesystem_name(q))
        cand_ws = (WORKSPACES_DIR / clean_rel).resolve()
        cand_nt = (NOTES_DIR / clean_rel).resolve()
        if cand_ws.exists() and cand_ws.is_dir():
            target = cand_ws
        elif cand_nt.exists() and cand_nt.is_dir():
            target = cand_nt
        else:
            return {
                "success": False,
                "verified": False,
                "message": f"Folder '{folder_name}' not found in approved workspace roots."
            }

    # Verify against approved roots
    allowed_roots = get_approved_roots()
    is_safe = False
    for root in allowed_roots:
        try:
            if os.path.commonpath([str(target), str(root)]) == str(root):
                is_safe = True
                break
        except Exception:
            continue

    if not is_safe or not target.exists():
        return {
            "success": False,
            "verified": False,
            "message": f"Access denied or folder not found: '{folder_name}'"
        }

    try:
        os.startfile(str(target))
        from backend.assistant.action_verifier import ActionVerifier
        verification = ActionVerifier.verify_open_folder(target)
        return {
            "success": verification.verified,
            "verified": verification.verified,
            "folder_name": target.name,
            "path": str(target),
            "message": f"Opened folder '{target.name}'.",
            "verification": verification.to_dict()
        }
    except Exception as e:
        return {
            "success": False,
            "verified": False,
            "message": f"Failed to open folder '{target.name}': {e}"
        }

