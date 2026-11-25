import time
import os
import shutil
from datetime import datetime
from pathlib import Path
from backend.resource_manager import ResourceManager
from config import RECORDINGS_DIR, DATA_DIR, MODELS_DIR, LLM_MODEL_PATH

def tool_get_system_status(**kwargs):
    """Returns real-time RAM, CPU, GPU, VRAM, drive storage, and AI job status."""
    rm = ResourceManager()
    state = rm.get_resource_state()

    drive = Path(RECORDINGS_DIR).anchor or "C:\\"
    try:
        total, used, free = shutil.disk_usage(drive)
        free_gb = round(free / (1024**3), 1)
        total_gb = round(total / (1024**3), 1)
        used_pct = round((used / total) * 100, 1)
    except Exception:
        free_gb, total_gb, used_pct = 0.0, 0.0, 0.0

    ram_pct = state["ram"]["percent"]
    cpu_pct = state["cpu"]["percent"]
    if ram_pct >= rm.THRESHOLD_HIGH or cpu_pct >= 90.0:
        overall_status = "CRITICAL" if ram_pct >= rm.THRESHOLD_CRITICAL else "HIGH"
    elif ram_pct >= rm.THRESHOLD_CAUTION or cpu_pct >= 75.0:
        overall_status = "CAUTION"
    else:
        overall_status = "HEALTHY"

    return {
        "success": True,
        "ram_total_mb": state["ram"]["total_mb"],
        "ram_used_mb": state["ram"]["used_mb"],
        "ram_available_mb": state["ram"]["available_mb"],
        "ram_percent": ram_pct,
        "cpu_percent": cpu_pct,
        "cpu_cores": state["cpu"].get("cores", os.cpu_count() or 4),
        "cpu_threads": state["cpu"].get("logical_processors", os.cpu_count() or 4),
        "gpu_available": state["gpu"]["available"],
        "gpu_name": state["gpu"]["name"],
        "gpu_utilization": state["gpu"]["utilization_percent"],
        "vram_used_mb": state["gpu"]["vram_used_mb"],
        "vram_free_mb": state["gpu"].get("vram_free_mb", 0),
        "vram_total_mb": state["gpu"]["vram_total_mb"],
        "storage_drive": drive,
        "storage_free_gb": free_gb,
        "storage_total_gb": total_gb,
        "storage_used_percent": used_pct,
        "active_jobs": state["jobs"]["active_job_count"],
        "whisper_active": state["jobs"]["whisper_active"],
        "qwen_active": state["jobs"]["qwen_active"],
        "policy_state": state["state"],
        "overall_status": overall_status
    }

def tool_get_current_time(**kwargs):
    """Returns current local time, date, day of week, and timezone."""
    now = datetime.now()
    return {
        "success": True,
        "time_str": now.strftime("%I:%M:%S %p"),
        "date_str": now.strftime("%A, %B %d, %Y"),
        "day_of_week": now.strftime("%A"),
        "iso_timestamp": now.isoformat(),
        "timezone": time.tzname[0] if time.tzname else "Local"
    }

def tool_get_disk_status(**kwargs):
    """Returns drive storage usage and recordings folder disk usage."""
    return tool_get_storage_status(**kwargs)

def tool_get_storage_status(**kwargs):
    """Returns comprehensive disk storage breakdown: drive free/total GB, recordings, models, and temp sizes."""
    drive = Path(RECORDINGS_DIR).anchor or "C:\\"
    total, used, free = shutil.disk_usage(drive)

    rec_size = sum(f.stat().st_size for f in Path(RECORDINGS_DIR).rglob('*') if f.is_file())
    rec_count = len(list(Path(RECORDINGS_DIR).glob('*.wav'))) + len(list(Path(RECORDINGS_DIR).glob('*.m4a'))) + len(list(Path(RECORDINGS_DIR).glob('*.mp4')))

    models_size = sum(f.stat().st_size for f in Path(MODELS_DIR).rglob('*') if f.is_file()) if Path(MODELS_DIR).exists() else 0
    data_size = sum(f.stat().st_size for f in Path(DATA_DIR).rglob('*') if f.is_file()) if Path(DATA_DIR).exists() else 0

    free_gb = round(free / (1024**3), 2)
    status = "HEALTHY" if free_gb > 15.0 else ("WARNING" if free_gb > 5.0 else "CRITICAL_LOW")

    return {
        "success": True,
        "drive": drive,
        "drive_total_gb": round(total / (1024**3), 2),
        "drive_free_gb": free_gb,
        "drive_used_percent": round((used / total) * 100, 1),
        "recordings_count": rec_count,
        "recordings_size_mb": round(rec_size / (1024**2), 2),
        "models_size_mb": round(models_size / (1024**2), 2),
        "data_size_mb": round(data_size / (1024**2), 2),
        "status": status
    }

def tool_get_process_summary(**kwargs):
    """Returns top CPU- and RAM-consuming processes safely along with SmartScribe active AI tasks."""
    rm = ResourceManager()
    state = rm.get_resource_state()
    proc_telemetry = rm.get_process_telemetry(top_n=5)

    return {
        "success": True,
        "grouped_processes": proc_telemetry.get("grouped_processes", []),
        "top_ram_processes": proc_telemetry.get("top_ram_processes", []),
        "top_cpu_processes": proc_telemetry.get("top_cpu_processes", []),
        "total_processes": proc_telemetry.get("total_processes", 0),
        "active_ai_jobs": state["jobs"]["jobs"],
        "whisper_active": state["jobs"]["whisper_active"],
        "qwen_active": state["jobs"]["qwen_active"],
        "governance_policy": state["state"]
    }

def tool_diagnose_smartscribe(**kwargs):
    """
    Performs comprehensive read-only diagnosis of SmartScribe subsystem components:
    Qwen availability, Faster-Whisper availability, GPU acceleration, RAM headroom, and queue status.
    """
    rm = ResourceManager()
    state = rm.get_resource_state()

    # 1. Inspect Qwen LLM
    model_path = Path(LLM_MODEL_PATH)
    qwen_file_exists = model_path.exists()
    qwen_size_mb = round(model_path.stat().st_size / (1024**2), 1) if qwen_file_exists else 0.0

    can_run_llm, llm_reason = rm.can_start_llm()
    qwen_status = {
        "model_file_present": qwen_file_exists,
        "model_size_mb": qwen_size_mb,
        "ready_to_infer": qwen_file_exists and can_run_llm,
        "active": state["jobs"]["qwen_active"],
        "notes": "Ready" if (qwen_file_exists and can_run_llm) else llm_reason
    }

    # 2. Inspect Faster-Whisper
    gpu_info = state["gpu"]
    whisper_device = "cuda" if gpu_info["available"] else "cpu"
    whisper_compute = "float16" if gpu_info["available"] else "int8"
    whisper_status = {
        "device": whisper_device,
        "compute_type": whisper_compute,
        "gpu_accelerated": gpu_info["available"],
        "active": state["jobs"]["whisper_active"],
        "ready": True
    }

    # 3. Overall health assessment
    recommendations = []
    if not qwen_file_exists:
        recommendations.append("Qwen GGUF model file is missing from models/ directory.")
    if state["ram"]["percent"] >= rm.THRESHOLD_HIGH:
        recommendations.append(f"RAM usage is elevated ({state['ram']['percent']}%). Non-essential background apps should be closed.")
    if not gpu_info["available"]:
        recommendations.append("NVIDIA GPU is not detected; Whisper will run in CPU fallback mode.")
    else:
        recommendations.append(f"GPU acceleration active: {gpu_info['name']} with {gpu_info['vram_total_mb']} MB VRAM.")

    if not recommendations or (len(recommendations) == 1 and "active" in recommendations[0]):
        overall_health = "HEALTHY"
    elif not qwen_file_exists or state["ram"]["percent"] >= rm.THRESHOLD_CRITICAL:
        overall_health = "CRITICAL"
    else:
        overall_health = "DEGRADED"

    return {
        "success": True,
        "overall_health": overall_health,
        "qwen": qwen_status,
        "whisper": whisper_status,
        "gpu": gpu_info,
        "ram": {
            "percent": state["ram"]["percent"],
            "available_mb": state["ram"]["available_mb"],
            "state": state["state"]
        },
        "active_jobs_count": state["jobs"]["active_job_count"],
        "recommendations": recommendations
    }

def tool_stop_action(**kwargs):
    """Halts active speech and any in-progress assistant actions immediately."""
    try:
        from backend.assistant.speech import SpeechManager
        SpeechManager().stop()
    except Exception:
        pass
    return {"success": True, "action": "stop", "message": "Stopped."}

def tool_cancel_action(**kwargs):
    """Cancels the pending or active assistant command."""
    try:
        from backend.assistant.speech import SpeechManager
        SpeechManager().stop()
    except Exception:
        pass
    return {"success": True, "action": "cancel", "message": "Cancelled."}

def tool_dismiss_action(**kwargs):
    """Dismisses the prompt or current interaction."""
    try:
        from backend.assistant.speech import SpeechManager
        SpeechManager().stop()
    except Exception:
        pass
    return {"success": True, "action": "dismiss", "message": "Understood."}

def tool_pause_action(**kwargs):
    """Pauses active speech and playback."""
    try:
        from backend.assistant.speech import SpeechManager
        SpeechManager().stop()
    except Exception:
        pass
    return {"success": True, "action": "pause", "message": "Paused."}

def tool_browser_navigate_back(**kwargs):
    """Navigates back to the previous webpage or view."""
    try:
        from backend.assistant.interaction.browser_bridge import get_browser_bridge
        bridge = get_browser_bridge()
        if bridge.is_connected():
            bridge.send_command("NAVIGATE_BACK", payload={})
    except Exception:
        pass
    return {"success": True, "action": "navigate_back", "message": "Navigated back."}

def tool_assistant_greeting(**kwargs):
    """Greets the user when addressed by name."""
    return {"success": True, "action": "greeting", "message": "I'm listening. How can I help?"}
