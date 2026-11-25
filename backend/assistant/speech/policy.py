import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class SpeechConfig:
    """User-configurable speech response settings."""
    speech_enabled: bool = True
    speak_fast_actions: bool = True
    speak_long_answers: bool = False
    voice_id: str = "default"
    speech_rate: int = 175         # WPM: validated 140 - 220
    speech_volume: float = 0.85    # Scale: validated 0.0 - 1.0

    def __post_init__(self):
        # Validate and clamp rate
        if not isinstance(self.speech_rate, int) or self.speech_rate < 140:
            self.speech_rate = 140
        elif self.speech_rate > 220:
            self.speech_rate = 220

        # Validate and clamp volume
        if not isinstance(self.speech_volume, (int, float)) or self.speech_volume < 0.0:
            self.speech_volume = 0.0
        elif self.speech_volume > 1.0:
            self.speech_volume = 1.0


def sanitize_for_speech(text: str, max_words: int = 50) -> str:
    """
    Cleans raw text to produce safe, natural-sounding speech.
    Strips XML/HTML tags, ChatML tokens, Markdown tables, code blocks,
    raw URLs, and internal metadata. Enforces a strict word clamp (<= 50 words).
    """
    if not text or not isinstance(text, str):
        return ""

    s = text.strip()

    # 1. Strip ChatML and system tokens
    s = re.sub(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>", " ", s)

    # 2. Strip XML/HTML tags
    s = re.sub(r"<[^>]+>", " ", s)

    # 3. Strip code blocks and inline code
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"`[^`]+`", " ", s)

    # 4. Strip markdown tables (| ... |)
    s = re.sub(r"\|[^\n]+\|", " ", s)

    # 5. Convert / strip URLs to avoid reading out http-colon-slash-slash
    s = re.sub(r"https?://(?:www\.)?([a-zA-Z0-9.-]+)(?:/\S*)?", r"\1", s)

    # 6. Strip internal metadata markers (e.g. Confirmation Token: ..., Tool: ...)
    s = re.sub(r"\bConfirmation\s+Token\s*:\s*[a-zA-Z0-9_-]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bTool\s+Result\s*:\s*\{[^}]*\}", " ", s, flags=re.IGNORECASE)

    # 7. Strip markdown bullet stars, hashes, and decorative symbols
    s = re.sub(r"[#*_~`>\[\]\(\)]", " ", s)

    # 8. Collapse excessive whitespace
    s = re.sub(r"\s+", " ", s).strip()

    # 9. Hard word clamp (<= 50 words)
    words = s.split()
    if len(words) > max_words:
        s = " ".join(words[:max_words]) + "."

    return s


class SpeechPolicy:
    """
    Determines whether an assistant response should be spoken aloud,
    and formats concise spoken utterances to prevent acoustic spam.
    """
    def __init__(self, config: Optional[SpeechConfig] = None):
        self.config = config or SpeechConfig()

    def evaluate(
        self,
        query: str,
        response_text: str,
        intent_result: Optional[Dict[str, Any]] = None,
        source: str = "text"
    ) -> Optional[str]:
        """
        Evaluates an assistant response and returns the clean text to speak,
        or None if the response should remain purely visual/silent.
        """
        if not self.config.speech_enabled:
            return None

        intent_data = intent_result or {}
        intent_name = intent_data.get("intent", "")
        exec_path = intent_data.get("execution_path", "")
        tool_name = intent_data.get("target_tool", "") or intent_data.get("tool_name", "")
        status = intent_data.get("status", "")

        # 0. Verification status guards
        if tool_name in ("click_browser_element", "type_browser_field", "select_browser_option"):
            if status == "verification_failed" or (status == "failed" and "verification" in response_text.lower()):
                return "The action was performed, but the expected result could not be confirmed."
            if status in ("unconfirmed", "verification_unavailable"):
                return "Action completed, but verification was unavailable."
            if status in ("policy_denied", "frame_denied", "navigation_blocked") or (status == "failed" and ("prohibited" in response_text.lower() or "blocked" in response_text.lower())):
                return "Action blocked by safety policy."
            if status in ("stale_target", "target_mismatch", "target_not_found"):
                return "Action could not be completed because the page target changed."
        else:
            if status == "verification_failed" or (status == "failed" and "verification" in response_text.lower()):
                return "The action was performed, but verification could not be confirmed."
            if status in ("unconfirmed", "verification_unavailable"):
                return "Action completed, but verification was unavailable."
            if status in ("policy_denied", "frame_denied", "navigation_blocked") or (status == "failed" and ("prohibited" in response_text.lower() or "blocked" in response_text.lower())):
                return "Action blocked by safety policy."
            if status in ("stale_target", "target_mismatch", "target_not_found"):
                return "Action could not be completed because the page target changed."

        # 1. Security / Blocked Commands
        if exec_path == "blocked" or intent_name == "prompt_injection_blocked":
            return "Security alert: command blocked."

        # 1b. Plan Path / Multi-Step Task Confirmations & Summaries
        if exec_path == "plan_path" or tool_name == "execute_plan" or intent_name == "task_planning":
            if status in ("waiting_for_confirmation", "awaiting_approval", "requires_confirmation"):
                return sanitize_for_speech(response_text, max_words=25)
            if status in ("failed", "cancelled", "validation_failed"):
                return sanitize_for_speech(response_text, max_words=25)
            if status == "partial_success":
                return sanitize_for_speech(response_text, max_words=25)
            return sanitize_for_speech(response_text, max_words=25)

        # 2. Fast Path Action Confirmations & Telemetry
        if self.config.speak_fast_actions and (exec_path == "fast_path" or tool_name):
            if tool_name == "open_application":
                app = intent_data.get("arguments", {}).get("application", "Application")
                if app == "chrome":
                    app_display = "Google Chrome"
                elif app == "vscode":
                    app_display = "Visual Studio Code"
                else:
                    app_display = app.title()
                return f"{app_display} is open."
            if tool_name == "create_note":
                title = intent_data.get("arguments", {}).get("title") or intent_data.get("data", {}).get("note_title", "note")
                return f"Note {title} has been created and verified."
            if tool_name == "create_folder":
                fld = intent_data.get("arguments", {}).get("folder_name") or intent_data.get("data", {}).get("folder_name", "folder")
                parent = intent_data.get("arguments", {}).get("parent") or intent_data.get("data", {}).get("parent", "workspaces")
                return f"Folder {fld} created in {parent}."
            if tool_name == "open_folder":
                fld = intent_data.get("arguments", {}).get("folder_name") or intent_data.get("data", {}).get("folder_name", "folder")
                return f"Opened folder {fld} in File Explorer."
            if tool_name == "open_website":
                url = intent_data.get("arguments", {}).get("url", "website")
                domain = url.split("://")[-1].split("/")[0] if "://" in url else url
                return f"Opening {domain} in your browser."
            if tool_name == "open_file":
                if status == "failed" or not intent_data.get("success", True):
                    return sanitize_for_speech(response_text, max_words=20)
                req_tgt = intent_data.get("data", {}).get("requested_target") or intent_data.get("arguments", {}).get("requested_target")
                fn = intent_data.get("data", {}).get("file_name")
                if fn and req_tgt and req_tgt.lower() != fn.lower():
                    clean_tgt = re.sub(r"\bpdf\b", "PDF", req_tgt, flags=re.IGNORECASE)
                    return sanitize_for_speech(f"Opened {clean_tgt}: {fn}.", max_words=20)
                elif fn:
                    return sanitize_for_speech(f"Opened {fn}.", max_words=20)
                return sanitize_for_speech(response_text, max_words=20)
            if tool_name == "start_recording":
                return "Meeting recording started."
            if tool_name == "stop_recording":
                return "Meeting recording stopped and saved."
            if tool_name == "get_current_time":
                return sanitize_for_speech(response_text, max_words=20)
            if tool_name in ("get_disk_status", "get_storage_status"):
                return sanitize_for_speech(response_text, max_words=25)
            if tool_name == "get_system_status":
                return sanitize_for_speech(response_text, max_words=25)
            if tool_name == "get_process_summary":
                return sanitize_for_speech(response_text, max_words=25)
            if tool_name == "diagnose_smartscribe":
                return sanitize_for_speech(response_text, max_words=25)
            if tool_name == "read_current_page":
                return sanitize_for_speech(response_text, max_words=25)
            if tool_name == "get_active_window":
                return sanitize_for_speech(response_text, max_words=25)
            if tool_name == "find_desktop_files":
                return sanitize_for_speech(response_text, max_words=25)
            if tool_name == "delete_recording":
                return "This recording deletion requires your explicit confirmation."
            if tool_name in ("click_browser_element", "type_browser_field", "select_browser_option"):
                is_v = intent_data.get("verified", False) or intent_data.get("data", {}).get("verified", False)
                p_status = intent_data.get("postcondition_status") or intent_data.get("data", {}).get("postcondition_status")
                if is_v or p_status == "verified":
                    if tool_name == "type_browser_field":
                        return "Text entered and verified."
                    if tool_name == "click_browser_element":
                        return "Clicked and navigation confirmed."
                    if tool_name == "select_browser_option":
                        return "Option selected and verified."
                    return "Browser action completed and verified."
                elif p_status == "verification_unavailable" or status in ("unconfirmed", "verification_unavailable"):
                    return "Action completed, but verification was unavailable."
                elif p_status == "verification_failed" or status == "verification_failed":
                    return "The action was performed, but the expected result could not be confirmed."
                elif status in ("policy_denied", "frame_denied", "navigation_blocked"):
                    return "Action blocked by safety policy."
                else:
                    return sanitize_for_speech(response_text, max_words=20)

        # 3. Hybrid Path: System Health, Desktop Activity & Diagnostic Summaries
        if exec_path == "hybrid_path" or intent_name in ("analyze_system_status", "diagnose_performance", "interpret_activity", "plan_workspace_setup"):
            return sanitize_for_speech(response_text, max_words=30)

        # 4. Long Lecture / Media Summaries -> DO NOT speak entire text
        if tool_name == "summarize_current_session" or intent_name in ("summarize_current_session", "summarize_session"):
            return "Summary generated and displayed on your screen."

        # 5. Browser Intelligence Summaries -> DO NOT speak entire text
        if tool_name == "summarize_current_page" or intent_name == "summarize_current_page":
            return "Article summary generated and displayed on your screen."

        # 6. Browser Key Points / Q&A / Explanations
        if tool_name in ("extract_key_points", "answer_about_current_page", "explain_current_page"):
            return sanitize_for_speech(response_text, max_words=35)

        # 7. General AI Path / Qwen Responses
        if exec_path == "ai_path" or not tool_name:
            if not self.config.speak_long_answers and len(response_text.split()) > 40:
                # Truncate to first 1-2 sentences
                sentences = re.split(r"(?<=[.!?])\s+", response_text.strip())
                short_summary = sentences[0] if sentences else response_text
                if len(short_summary.split()) > 35:
                    short_summary = " ".join(short_summary.split()[:35]) + "..."
                return sanitize_for_speech(short_summary, max_words=40)
            return sanitize_for_speech(response_text, max_words=50)

        # Default fallback clamp
        return sanitize_for_speech(response_text, max_words=40)
