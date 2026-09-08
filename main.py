import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from config import DATA_DIR
from server import app

HOST = "127.0.0.1"
PORT = 5000
APP_URL = f"http://{HOST}:{PORT}"
APP_PROFILE_DIR = DATA_DIR / "app_profile"
APP_PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def wait_for_server(timeout=12.0):
    """Waits until the server is ready to accept requests."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with urllib.request.urlopen(APP_URL, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def launch_app_window():
    """
    Attempts to launch in native Desktop App Mode using Edge or Chrome
    with an isolated user-data-dir so it runs as a dedicated desktop app.
    Falls back to the system default browser if neither is available.
    """
    candidate_browsers = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    ]

    for browser_path in candidate_browsers:
        if os.path.exists(browser_path):
            try:
                print(f"Launching SmartScribe Desktop Window via {Path(browser_path).name}...")
                process = subprocess.Popen([
                    browser_path,
                    f"--app={APP_URL}",
                    f"--user-data-dir={APP_PROFILE_DIR}",
                    "--window-size=1300,850"
                ])
                return process
            except Exception as err:
                print(f"Could not launch browser app mode: {err}")

    # Fallback to default browser
    print("Opening SmartScribe in default browser...")
    webbrowser.open(APP_URL)
    return None


def open_browser_when_ready():
    """Waits for Flask server to be live, then opens the UI."""
    if wait_for_server(timeout=12.0):
        print(f"\nSmartScribe is live at {APP_URL}")
        launch_app_window()
    else:
        print("\nWarning: Server start took longer than expected.")


def main():
    print("=" * 60)
    print("  SMARTSCRIBE — MODERN AI PRODUCTIVITY & VIDEO WORKSPACE")
    print("=" * 60)
    print(f"Starting server on {APP_URL} ...")

    # Start browser launcher in background thread
    launcher_thread = threading.Thread(target=open_browser_when_ready, daemon=True)
    launcher_thread.start()

    print("Press Ctrl+C in this terminal to stop the application.\n")

    # Run Flask server on the main thread so it stays active
    try:
        app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\nShutting down SmartScribe...")
        sys.exit(0)


if __name__ == "__main__":
    main()