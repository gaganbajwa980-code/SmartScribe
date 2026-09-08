import os
import subprocess
import webbrowser
from urllib.parse import urlparse

# Strict curated application allowlist
ALLOWED_APPLICATIONS = {
    "chrome": [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "code": ["code.cmd", "code"],
    "vscode": ["code.cmd", "code"],
    "explorer": ["explorer.exe"],
    "edge": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "msedge.exe"],
    "brave": [r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe", r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe", os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"), "brave.exe"],
    "paint": ["mspaint.exe"],
    "terminal": ["wt.exe"],
    "taskmgr": ["taskmgr.exe"],
    "taskmanager": ["taskmgr.exe"]
}

APP_ALIAS_MAP = {
    "browser": "chrome",
    "my browser": "chrome",
    "text editor": "notepad",
    "file explorer": "explorer",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "task manager": "taskmanager"
}

def tool_open_application(application: str, **kwargs):
    """Launches a pre-approved application from the strict allowlist."""
    app_key = application.lower().strip()
    if app_key in APP_ALIAS_MAP:
        app_key = APP_ALIAS_MAP[app_key]

    if app_key not in ALLOWED_APPLICATIONS:
        allowed_list = list(ALLOWED_APPLICATIONS.keys())
        return {
            "success": False,
            "message": f"Application '{application}' not in approved allowlist. Allowed: {allowed_list}"
        }

    candidates = ALLOWED_APPLICATIONS[app_key]
    launched = False
    proc_pid = None
    err_msg = ""
    for cand in candidates:
        try:
            # Zero shell execution: binary invoked directly
            proc = subprocess.Popen([cand])
            proc_pid = proc.pid if proc else None
            launched = True
            break
        except Exception as e:
            err_msg = str(e)
            continue

    if launched:
        from backend.assistant.action_verifier import ActionVerifier
        verification = ActionVerifier.verify_open_application(app_key, pid=proc_pid)
        return {
            "success": verification.verified,
            "verified": verification.verified,
            "application": app_key,
            "pid": proc_pid or 0,
            "message": f"Launched {app_key.title()} successfully." if verification.verified else f"Launched {app_key.title()}, but verification failed.",
            "verification": verification.to_dict()
        }
    else:
        return {
            "success": False,
            "verified": False,
            "message": f"Failed to launch '{app_key}': {err_msg or 'Executable not found on system path.'}"
        }

def tool_open_website(url: str, **kwargs):
    """Opens a safe http or https URL in the default browser."""
    clean_url = url.strip()
    
    # Reject any explicit non-http/https scheme immediately
    parsed_initial = urlparse(clean_url)
    if parsed_initial.scheme and parsed_initial.scheme.lower() not in ["http", "https"]:
        return {
            "success": False,
            "message": f"Invalid or unsafe URL scheme: '{parsed_initial.scheme}'. Only http and https allowed."
        }

    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url

    parsed = urlparse(clean_url)
    if parsed.scheme not in ["http", "https"] or not parsed.netloc:
        return {
            "success": False,
            "message": f"Invalid or unsafe URL: '{clean_url}'."
        }

    # Block localhost / file / local network probes
    blocked_hosts = ["localhost", "127.0.0.1", "0.0.0.0", "::1"]
    if parsed.hostname in blocked_hosts:
        return {
            "success": False,
            "message": "Access to local loopback hosts is prohibited."
        }

    webbrowser.open(clean_url)
    return {
        "success": True,
        "url": clean_url,
        "message": f"Opened {clean_url} in default browser."
    }
