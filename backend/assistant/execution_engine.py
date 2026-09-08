import time
from typing import Dict, Any, Optional
from backend.assistant.tool_registry import ToolRegistry
from backend.assistant.permission_manager import PermissionManager, RiskLevel
from backend.assistant.action_verifier import ActionVerifier, ActionStatus

class ExecutionEngine:
    def __init__(self):
        self.registry = ToolRegistry()
        self.perm_mgr = PermissionManager()

    def _generate_summary(self, tool_name: str, data: Any, args: Dict[str, Any]) -> str:
        """Generates a concise, user-friendly human-readable summary of the execution."""
        if not isinstance(data, dict):
            return str(data)

        if tool_name == "get_system_status":
            ram = data.get("ram_percent", "N/A")
            cpu = data.get("cpu_percent", "N/A")
            status = data.get("overall_status", "HEALTHY")
            gpu = data.get("gpu_name") or "None"
            vram_u = data.get("vram_used_mb", 0)
            vram_t = data.get("vram_total_mb", 0)
            free_gb = data.get("storage_free_gb", "N/A")
            return f"System status ({status}): RAM is at {ram}%, CPU at {cpu}%. GPU: {gpu} ({vram_u}/{vram_t} MB VRAM). Storage: {free_gb} GB free."

        if tool_name == "get_current_time":
            t_str = data.get("time_str", "")
            d_str = data.get("date_str", "")
            tz = data.get("timezone", "")
            return f"Current time is {t_str} on {d_str} ({tz})."

        if tool_name in ("get_disk_status", "get_storage_status"):
            free = data.get("drive_free_gb", "N/A")
            drive = data.get("drive", "")
            used_pct = data.get("drive_used_percent", "N/A")
            recs = data.get("recordings_count", 0)
            recs_mb = data.get("recordings_size_mb", 0)
            return f"Storage: {free} GB free on {drive} ({used_pct}% used). {recs} recordings ({recs_mb} MB) saved."

        if tool_name == "get_process_summary":
            top_ram = data.get("top_ram_processes", [])
            top_cpu = data.get("top_cpu_processes", [])
            tot = data.get("total_processes", 0)
            ram_desc = f"{top_ram[0]['name']} ({top_ram[0]['ram_mb']} MB)" if top_ram else "N/A"
            cpu_desc = f"{top_cpu[0]['name']} ({top_cpu[0]['cpu_percent']}%)" if top_cpu else "N/A"
            jobs = data.get("active_ai_jobs", [])
            return f"Top RAM: {ram_desc}. Top CPU: {cpu_desc}. Total processes: {tot}. Active AI jobs: {len(jobs)}."

        if tool_name == "diagnose_smartscribe":
            health = data.get("overall_health", "HEALTHY")
            qwen = "Ready" if data.get("qwen", {}).get("ready_to_infer") else "Not Ready"
            whisper_dev = data.get("whisper", {}).get("device", "cpu")
            whisper_accel = "CUDA (float16)" if data.get("whisper", {}).get("gpu_accelerated") else "CPU (int8)"
            gpu_name = data.get("gpu", {}).get("name") or "None"
            ram_pct = data.get("ram", {}).get("percent", 0)
            return f"SmartScribe Health ({health}): Qwen2.5-3B is {qwen}. Faster-Whisper active on {whisper_accel}. GPU: {gpu_name}. RAM: {ram_pct}%."

        if tool_name == "open_file":
            return data.get("message", "File opened successfully.")

        if tool_name == "open_application":
            app = args.get("application", "Application")
            return data.get("message", f"Launched {app.title()} successfully.")

        if tool_name == "open_website":
            url = args.get("url", "")
            return data.get("message", f"Opened {url} in default browser.")

        if tool_name == "start_recording":
            return data.get("message", "Microphone recording started.")

        if tool_name == "stop_recording":
            return data.get("message", "Microphone recording stopped and saved.")

        if tool_name == "search_local_files":
            cnt = data.get("count", 0)
            return f"Found {cnt} matching recordings."

        if tool_name == "search_transcript":
            cnt = data.get("match_count", 0)
            return f"Found {cnt} matching chunks in transcript."

        if tool_name == "summarize_current_session":
            title = data.get("title", "Session")
            summary = data.get("summary", "")
            return f"Summary for '{title}': {summary[:200]}..."

        if tool_name == "read_current_page":
            return data.get("human_readable_summary") or data.get("message", "Read current page.")

        if tool_name in ("summarize_current_page", "extract_key_points", "answer_about_current_page", "explain_current_page"):
            return data.get("human_readable_summary") or data.get("response") or data.get("message", "Webpage analysis complete.")

        if tool_name == "get_active_window":
            app = data.get("application", "Desktop")
            title = data.get("window_title", "")
            if title and title != app:
                return f"Currently active: {app} - '{title}'."
            return f"Currently active: {app}."

        if tool_name == "find_desktop_files":
            cnt = data.get("count", 0)
            files = data.get("files", [])
            q = args.get("query", "")
            if not files:
                return f"No files matching '{q}' were found in approved folders." if q else "No matching files were found in approved folders."
            names = ", ".join(f["name"] for f in files[:3])
            more = f" and {cnt - 3} more" if cnt > 3 else ""
            return f"Found {cnt} file(s): {names}{more}."

        if tool_name == "create_note":
            name = data.get("file_name", "note")
            verified_tag = " (verified on disk)" if data.get("verified") else ""
            return f"Created note '{name}'{verified_tag}."

        if tool_name == "create_folder":
            name = data.get("folder_name", "folder")
            parent = data.get("parent", "workspaces")
            verified_tag = " (verified)" if data.get("verified") else ""
            return f"Created folder '{name}' in {parent}{verified_tag}."

        if tool_name == "open_folder":
            name = data.get("folder_name", "folder")
            return f"Opened folder '{name}' in File Explorer."

        return data.get("message", "Executed successfully.")

    def execute_intent(
        self,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        confirmation_token: Optional[str] = None
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        tool_name = intent_result.get("target_tool")
        if not tool_name:
            return {
                "success": False,
                "status": "error",
                "tool_name": None,
                "tool": None,
                "data": {},
                "human_readable_summary": "No tool targeted by intent.",
                "message": "No tool targeted by intent.",
                "error_code": "NO_TARGET_TOOL",
                "error_message": "Intent did not specify a target tool.",
                "duration_ms": 0.0
            }

        tool = self.registry.get(tool_name)
        if not tool:
            return {
                "success": False,
                "status": "error",
                "tool_name": tool_name,
                "tool": tool_name,
                "data": {},
                "human_readable_summary": f"Tool '{tool_name}' is not registered.",
                "message": f"Tool '{tool_name}' is not registered.",
                "error_code": "TOOL_NOT_FOUND",
                "error_message": f"Tool '{tool_name}' is not registered.",
                "duration_ms": 0.0
            }

        # Step 1: Validate arguments against JSON Schema and allowlists
        raw_args = dict(intent_result.get("arguments", {}))
        if context and "session_id" in context and "session_id" not in raw_args:
            raw_args["session_id"] = context["session_id"]

        is_valid, val_msg, clean_args = self.registry.validate_args(tool_name, raw_args)
        if not is_valid:
            dur = round((time.perf_counter() - t0) * 1000, 2)
            return {
                "success": False,
                "status": "validation_error",
                "tool_name": tool_name,
                "tool": tool_name,
                "data": {},
                "human_readable_summary": val_msg,
                "message": val_msg,
                "error_code": "VALIDATION_ERROR",
                "error_message": val_msg,
                "duration_ms": dur
            }

        # Step 2: Permission check
        allowed, perm_msg, req = self.perm_mgr.check_permission(
            tool_name=tool_name,
            risk_level=tool.risk_level,
            requires_confirmation=tool.requires_confirmation,
            confirmation_token=confirmation_token
        )

        if not allowed:
            req_data = self.perm_mgr.create_confirmation_request(
                tool_name=tool_name,
                arguments=clean_args,
                risk_level=tool.risk_level,
                summary=f"Action '{tool_name}' requires explicit confirmation."
            )
            dur = round((time.perf_counter() - t0) * 1000, 2)
            return {
                "success": False,
                "status": "waiting_for_confirmation",
                "tool_name": tool_name,
                "tool": tool_name,
                "data": {},
                "human_readable_summary": perm_msg,
                "message": perm_msg,
                "confirmation_token": req_data["token"],
                "confirmation_prompt": req_data["summary"],
                "arguments": clean_args,
                "error_code": "CONFIRMATION_REQUIRED",
                "error_message": perm_msg,
                "duration_ms": dur
            }

        # Step 3: Safe execution
        try:
            result_data = tool.execution_fn(**clean_args)
            dur = round((time.perf_counter() - t0) * 1000, 2)
            
            # Step 4: Action Verification
            verification = ActionVerifier.verify(tool_name, clean_args, result_data if isinstance(result_data, dict) else {})
            
            exec_success = result_data.get("success", True) if isinstance(result_data, dict) else True
            is_verif_failure = (
                (not verification.verified) and
                (exec_success or (isinstance(result_data, dict) and "verification" in result_data))
            )
            
            if exec_success and verification.verified:
                final_status = ActionStatus.SUCCEEDED.value
                final_success = True
                summary_msg = self._generate_summary(tool_name, result_data, clean_args)
            elif is_verif_failure:
                final_status = ActionStatus.VERIFICATION_FAILED.value
                final_success = False
                summary_msg = f"Action '{tool_name}' executed, but verification failed: {verification.details}"
            else:
                final_status = ActionStatus.FAILED.value
                final_success = False
                summary_msg = self._generate_summary(tool_name, result_data, clean_args)

            data_payload = result_data if isinstance(result_data, dict) else {"result": result_data}
            if isinstance(data_payload, dict):
                data_payload["verified"] = verification.verified
                data_payload["verification"] = verification.to_dict()

            return {
                "success": final_success,
                "verified": verification.verified,
                "status": final_status,
                "tool_name": tool_name,
                "tool": tool_name,
                "data": data_payload,
                "human_readable_summary": summary_msg,
                "message": summary_msg,
                "verification": verification.to_dict(),
                "error_code": None if final_success else ("VERIFICATION_FAILED" if final_status == ActionStatus.VERIFICATION_FAILED.value else "TOOL_ERROR"),
                "error_message": None if final_success else (verification.details if final_status == ActionStatus.VERIFICATION_FAILED.value else (result_data.get("message") if isinstance(result_data, dict) else "Tool failed")),
                "duration_ms": dur
            }
        except Exception as e:
            dur = round((time.perf_counter() - t0) * 1000, 2)
            err_str = str(e)
            return {
                "success": False,
                "status": ActionStatus.FAILED.value,
                "tool_name": tool_name,
                "tool": tool_name,
                "data": {},
                "human_readable_summary": f"Failed to execute tool '{tool_name}': {err_str}",
                "message": f"Failed to execute tool '{tool_name}': {err_str}",
                "error_code": "TOOL_ERROR",
                "error_message": err_str,
                "duration_ms": dur
            }
