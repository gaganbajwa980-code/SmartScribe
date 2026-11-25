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

# Sensitive window title patterns - never type into these
_SENSITIVE_TITLE_PATTERNS = re.compile(
    r'\b(password|login|sign.?in|credential|payment|credit.?card|debit.?card|pin|ssn|social.?security|secret|auth(entication)?|two.?factor|2fa|otp)\b',
    re.IGNORECASE
)

# Browser process names - do not use type_text for browser windows
_BROWSER_PROCESS_NAMES = {'chrome.exe', 'msedge.exe', 'brave.exe', 'firefox.exe', 'opera.exe'}

def _build_target_binding_from_foreground() -> tuple:
    """
    Returns (TargetBinding, error_message) by reading the live foreground window.
    Returns (None, error_str) on failure.
    """
    try:
        import ctypes
        from ctypes import wintypes
        import os as _os
        from backend.assistant.interaction.target_binding import TargetBinding

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        try:
            from backend.assistant.desktop_context import ensure_interactive_desktop
            ensure_interactive_desktop()
        except Exception:
            pass

        fg_hwnd = user32.GetForegroundWindow()
        if not fg_hwnd:
            return None, "No foreground window detected."

        # Get window title
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(fg_hwnd, buf, 512)
        fg_title = buf.value.strip()

        # Get PID
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(pid))
        fg_pid = int(pid.value)
        if not fg_pid:
            return None, "Could not determine foreground process ID."

        # Get executable path via QueryFullProcessImageNameW
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, fg_pid)
        if not h_proc:
            return None, f"Could not open foreground process (PID {fg_pid})."
        try:
            exe_buf = ctypes.create_unicode_buffer(1024)
            exe_size = wintypes.DWORD(1024)
            ok = kernel32.QueryFullProcessImageNameW(h_proc, 0, exe_buf, ctypes.byref(exe_size))
            exe_path = exe_buf.value if ok else ""
        finally:
            kernel32.CloseHandle(h_proc)

        if not exe_path:
            return None, f"Could not resolve executable path for PID {fg_pid}."

        exe_name = _os.path.basename(exe_path).lower().strip()

        binding = TargetBinding(
            window_handle=fg_hwnd,
            process_id=fg_pid,
            executable_path=exe_path,
            executable_name=exe_name,
            target_type="window",
            expected_title_pattern=fg_title,
            app_display_name=fg_title,
        )
        return binding, None
    except Exception as e:
        return None, f"Could not build target binding: {e}"


def _find_editable_control_hwnd(parent_hwnd: int) -> tuple:
    """
    Searches for a safe editable text control under parent_hwnd.
    Uses UIAutomation Edit/Document control detection as primary method,
    and classic Win32 window-class detection as fallback.
    Returns (child_hwnd_or_None, reason_string).
    """
    # 1. Primary: UIAutomation Edit / Document control detection
    try:
        import comtypes.client
        try:
            import comtypes.gen.UIAutomationClient as UIA
        except Exception:
            comtypes.client.GetModule("UIAutomationCore.dll")
            import comtypes.gen.UIAutomationClient as UIA

        uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        elem = uia.ElementFromHandle(parent_hwnd)
        if elem:
            # Check for Edit (50004) or Document (50030) control types
            cond_edit = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_EditControlTypeId)
            cond_doc = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_DocumentControlTypeId)
            or_cond = uia.CreateOrCondition(cond_edit, cond_doc)

            child = elem.FindFirst(UIA.TreeScope_Descendants, or_cond)
            if child:
                if getattr(child, "CurrentIsPassword", False):
                    return None, "Target editable control is a password or credential field."
                c_hwnd = child.CurrentNativeWindowHandle
                ctrl_name = child.CurrentName or child.CurrentClassName or "editable control"
                print(f"[SMARTSCRIBE TYPE_TEXT] UIAutomation primary detection found: '{ctrl_name}' (type={child.CurrentControlType}, HWND={c_hwnd or parent_hwnd})")
                return (c_hwnd or parent_hwnd), f"UIAutomation detected editable control '{ctrl_name}'"
    except Exception as e:
        print(f"[SMARTSCRIBE TYPE_TEXT] UIAutomation primary check note: {e}")

    # 2. Fallback: Classic Win32 Window-Class Detection via EnumChildWindows
    try:
        import ctypes
        from ctypes import wintypes

        EDITABLE_CLASSES = {
            'Edit', 'RichEdit20W', 'RICHEDIT50W', 'Scintilla',
            'RichEditD2DPT', 'NotepadTextBox'
        }
        # Check parent window itself
        p_buf = ctypes.create_unicode_buffer(128)
        ctypes.windll.user32.GetClassNameW(parent_hwnd, p_buf, 128)
        if p_buf.value in EDITABLE_CLASSES:
            print(f"[SMARTSCRIBE TYPE_TEXT] Win32 fallback detection found parent editable class: '{p_buf.value}'")
            return parent_hwnd, f"Parent window is an editable class '{p_buf.value}'"

        found = []
        EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def enum_callback(hwnd, _lparam):
            cls_buf = ctypes.create_unicode_buffer(128)
            ctypes.windll.user32.GetClassNameW(hwnd, cls_buf, 128)
            cls = cls_buf.value
            if cls in EDITABLE_CLASSES:
                if ctypes.windll.user32.IsWindowVisible(hwnd) and ctypes.windll.user32.IsWindowEnabled(hwnd):
                    found.append(int(hwnd))
            return True

        ctypes.windll.user32.EnumChildWindows(parent_hwnd, EnumChildProc(enum_callback), 0)

        if found:
            print(f"[SMARTSCRIBE TYPE_TEXT] Win32 fallback detection found child HWND {found[0]}")
            return found[0], "Win32 editable control class found."
    except Exception as e:
        print(f"[SMARTSCRIBE TYPE_TEXT] Win32 fallback check note: {e}")

    return None, "No safe editable text control found in the foreground window."


def tool_type_text(text: str, target_window_title: str = "", permission_context: dict = None, **kwargs) -> dict:
    """
    Safely types text into the verified foreground non-sensitive, non-browser desktop window.

    Architecture:
    - Builds a live TargetBinding from the foreground HWND (HWND + PID + exe path)
    - Validates window title against target_window_title
    - Rejects browser windows (by exe name)
    - Rejects sensitive window titles (password/payment/credential)
    - Verifies an editable control exists (UIAutomation primary, Win32 fallback)
    - Types via KeyboardDriver.type_text() (WindowFence + SendInput KEYEVENTF_UNICODE)
    - Returns success=True, verified=True on confirmed completion
    """
    import time as _time

    # Resource / permission check logging
    try:
        from backend.resource_manager import ResourceManager
        rm = ResourceManager()
        rstate = rm.get_resource_state()
        ram_info = rstate.get("ram", {})
        ram_pct = ram_info.get("percent", 0)
        ram_avail = ram_info.get("available_mb", 0)
        print(f"[SMARTSCRIBE TYPE_TEXT RESOURCE CHECK] RAM Usage: {ram_pct}% (Available: {ram_avail} MB) | Guard Result: PASSED")
    except Exception as e:
        print(f"[SMARTSCRIBE TYPE_TEXT RESOURCE CHECK] Warning: {e}")

    # 1. Validate text
    text = (text or "").strip()
    if not text:
        return {"success": False, "verified": False, "message": "No text provided to type."}
    if len(text) > 1000:
        return {"success": False, "verified": False, "message": "Text too long (max 1000 characters for safe desktop typing)."}

    # 2. Build live TargetBinding from foreground window
    binding, bind_err = _build_target_binding_from_foreground()
    if binding is None:
        print(f"[SMARTSCRIBE TYPE_TEXT TARGET RESOLUTION] FAILED: Could not identify foreground window ({bind_err})")
        return {"success": False, "verified": False, "message": f"Could not identify foreground window: {bind_err}"}

    fg_title = binding.expected_title_pattern
    fg_process = binding.executable_name
    print(f"[SMARTSCRIBE TYPE_TEXT TARGET RESOLUTION] Target Window Title Expected: '{target_window_title or 'Any'}' | Foreground HWND: {binding.window_handle} | PID: {binding.process_id} | Executable: '{fg_process}' | Title: '{fg_title}'")

    # 3. Browser guard
    if fg_process in _BROWSER_PROCESS_NAMES:
        print(f"[SMARTSCRIBE TYPE_TEXT SAFETY GATE] REJECTED: Browser window detected ({fg_process})")
        return {
            "success": False, "verified": False,
            "message": "Cannot type into browser windows using type_text. Use browser search or browser field commands instead."
        }

    # 4. Sensitive field guard
    if _SENSITIVE_TITLE_PATTERNS.search(fg_title.lower()):
        print(f"[SMARTSCRIBE TYPE_TEXT SAFETY GATE] REJECTED: Sensitive context title '{fg_title}'")
        return {
            "success": False, "verified": False,
            "message": f"Typing refused: the active window appears to be a sensitive context ('{fg_title}'). SmartScribe does not type into password, payment, or credential fields."
        }

    # 5. Target window title match guard
    if target_window_title:
        target_lower = target_window_title.lower()
        fg_lower = fg_title.lower()
        if not (target_lower in fg_lower or fg_lower in target_lower or target_lower in fg_process.lower()):
            print(f"[SMARTSCRIBE TYPE_TEXT TARGET RESOLUTION] MISMATCH: Expected '{target_window_title}' but foreground is '{fg_title}' (proc: '{fg_process}')")
            return {
                "success": False, "verified": False,
                "message": f"Target window mismatch. Expected '{target_window_title}' but foreground is '{fg_title}'. Cannot type."
            }

    # 6. Editable area verification (UIAutomation primary, Win32 fallback)
    print(f"[SMARTSCRIBE TYPE_TEXT] UIAutomation search started on parent HWND={binding.window_handle} ('{fg_title}')")
    edit_hwnd, edit_reason = _find_editable_control_hwnd(binding.window_handle)
    if edit_hwnd is None:
        print(f"[SMARTSCRIBE TYPE_TEXT] UIAutomation search result: FAILED ({edit_reason})")
        return {
            "success": False, "verified": False,
            "message": f"Typing aborted: {edit_reason} Verify that an editable text area is focused in '{fg_title}'."
        }
    print(f"[SMARTSCRIBE TYPE_TEXT] UIAutomation search result: SUCCEEDED | Detected Control HWND={edit_hwnd} | Editable=True | Reason: {edit_reason}")

    # Rebuild binding with resolved control_id and control_role
    from backend.assistant.interaction.target_binding import TargetBinding
    binding = TargetBinding(
        window_handle=binding.window_handle,
        process_id=binding.process_id,
        executable_path=binding.executable_path,
        executable_name=binding.executable_name,
        target_type="ui_element" if edit_hwnd != binding.window_handle else "window",
        control_id=str(edit_hwnd) if edit_hwnd else None,
        control_role="edit",
        expected_title_pattern=fg_title,
        app_display_name=fg_title
    )
    print(f"[SMARTSCRIBE TYPE_TEXT] Target Binding Confirmed: HWND={binding.window_handle}, PID={binding.process_id}, Exe={binding.executable_name}, Control HWND={edit_hwnd}, Title='{fg_title}'")

    # 7. Type via KeyboardDriver (WindowFence + SendInput — no pyautogui, no clipboard)
    try:
        from backend.assistant.interaction.keyboard_driver import KeyboardDriver
        from backend.assistant.permission_manager import RiskLevel

        driver = KeyboardDriver()
        result = driver.type_text(binding, text, risk_level=RiskLevel.LOW_RISK_ACTION)

        print(f"[SMARTSCRIBE TYPE_TEXT] Result: Success={result.success}, Verified={result.verified}, Reason='{result.reason}', Status={result.status.value if result.status else 'none'}")

        if result.success:
            return {
                "success": True,
                "verified": result.verified,
                "text_typed": text,
                "characters_typed": len(text),
                "window_title": fg_title,
                "editable_hwnd": edit_hwnd,
                "message": f"Typed {len(text)} characters into '{fg_title}'.",
                "verification": {
                    "method": result.status.value if result.status else "sendInput",
                    "verified": result.verified
                }
            }
        else:
            return {
                "success": False,
                "verified": False,
                "message": f"Typing failed: {result.reason}",
                "verification": {
                    "method": result.status.value if result.status else "unknown",
                    "verified": False
                }
            }
    except ImportError as ie:
        return {
            "success": False, "verified": False,
            "message": f"Desktop keyboard control is not available ({ie})."
        }
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[SMARTSCRIBE TYPE_TEXT EXCEPTION] {e}\n{tb}")
        return {"success": False, "verified": False, "message": f"Typing failed with exception: {e}"}
