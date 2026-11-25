import os
import subprocess
import webbrowser
from urllib.parse import urlparse
from dataclasses import dataclass, field
from pathlib import Path
from backend.assistant.tools.app_discovery import discover_app

@dataclass
class LaunchSpec:
    """Structured, validated application launch specification."""
    executable: str          # Validated executable path or known safe system command
    arguments: list          # List of validated string arguments (no shell injection)
    source: str              # 'allowlist' | 'uwp_uri' | 'discovery'
    display_name: str = ""   # Human-readable name for UI messages
    def validate(self) -> tuple[bool, str]:
        """Validates the launch spec before execution. Returns (ok, reason)."""
        if not self.executable:
            return False, "No executable specified."
        if self.source == 'uwp_uri':
            # Only approved ms-* URI schemes are allowed
            ALLOWED_URI_SCHEMES = {'ms-settings:', 'ms-windows-store:'}
            if len(self.arguments) != 1 or self.arguments[0] not in ALLOWED_URI_SCHEMES:
                return False, f"Invalid or unapproved URI: {self.arguments}"
        elif self.source == 'allowlist':
            # Executable must exist on PATH or be an absolute path that exists
            exe = self.executable
            exe_path = Path(exe)
            if exe_path.is_absolute() and not exe_path.exists():
                return False, f"Executable not found: {exe}"
            # Reject prohibited extensions
            suffix = exe_path.suffix.lower()
            if suffix in {'.bat', '.cmd', '.ps1', '.vbs', '.sh', '.msi'}:
                return False, f"Prohibited executable extension: {suffix}"
        elif self.source == 'discovery':
            exe_path = Path(self.executable)
            if not exe_path.is_absolute():
                return False, "Discovery launch requires absolute path."
            if not exe_path.exists():
                return False, f"Discovered path does not exist: {self.executable}"
            suffix = exe_path.suffix.lower()
            if suffix not in {'.exe', '.com'} and suffix != '':
                return False, f"Discovered executable has prohibited extension: {suffix}"
        return True, "ok"

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

# UWP application launch specifications (explorer.exe + URI protocol)
UWP_APPLICATIONS = {
    "settings": LaunchSpec(
        executable="explorer.exe",
        arguments=["ms-settings:"],
        source="uwp_uri",
        display_name="Windows Settings"
    ),
    "store": LaunchSpec(
        executable="explorer.exe",
        arguments=["ms-windows-store:"],
        source="uwp_uri",
        display_name="Microsoft Store"
    ),
}

APP_ALIAS_MAP = {
    "browser": "chrome",
    "my browser": "chrome",
    "text editor": "notepad",
    "file explorer": "explorer",
    "vs code": "vscode",
    "visual studio code": "vscode",
    "task manager": "taskmanager",
    "microsoft store": "store",
    "ms store": "store",
    "windows store": "store",
    "windows settings": "settings",
}

def tool_open_application(application: str, **kwargs):
    """Launches a pre-approved application from the strict allowlist, UWP apps, or by discovery."""
    app_key = application.lower().strip()
    if app_key in APP_ALIAS_MAP:
        app_key = APP_ALIAS_MAP[app_key]

    from backend.assistant.tools.app_discovery import discover_app

    launched = False
    proc_pid = None
    err_msg = ""
    app_key_for_verifier = app_key

    if app_key in UWP_APPLICATIONS:
        spec = UWP_APPLICATIONS[app_key]
        ok, reason = spec.validate()
        if not ok:
            return {"success": False, "message": f"Cannot launch '{spec.display_name}': {reason}"}
        proc = subprocess.Popen([spec.executable] + spec.arguments)
        proc_pid = proc.pid if proc else None
        launched = True
    elif app_key in ALLOWED_APPLICATIONS:
        candidates = ALLOWED_APPLICATIONS[app_key]
        for cand in candidates:
            try:
                proc = subprocess.Popen([cand])
                proc_pid = proc.pid if proc else None
                launched = True
                break
            except Exception as e:
                err_msg = str(e)
                continue
    else:
        # Discovery fallback
        discovery = discover_app(app_key)
        if discovery.found and discovery.launch_candidates:
            spec = LaunchSpec(
                executable=discovery.launch_candidates[0],
                arguments=[],
                source="discovery",
                display_name=discovery.display_name
            )
            ok, reason = spec.validate()
            if ok:
                try:
                    proc = subprocess.Popen([spec.executable])
                    proc_pid = proc.pid if proc else None
                    launched = True
                    app_key_for_verifier = discovery.canonical_name
                except Exception as e:
                    err_msg = str(e)
            else:
                err_msg = f"Validation failed: {reason}"        
        if not launched:
            return {
                "success": False,
                "message": f"I couldn't safely identify an installed application named '{application}'. Try using the full application name or check if it's installed."
            }

    if launched:
        from backend.assistant.action_verifier import ActionVerifier
        verification = ActionVerifier.verify_open_application(app_key_for_verifier, pid=proc_pid)
        return {
            "success": verification.verified,
            "verified": verification.verified,
            "application": app_key_for_verifier,
            "pid": proc_pid or 0,
            "message": f"Launched {app_key_for_verifier.title()} successfully." if verification.verified else f"Launched {app_key_for_verifier.title()}, but verification failed.",
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
