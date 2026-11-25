import re
from enum import Enum
from typing import Dict, Any, Optional

class ExecutionPath(str, Enum):
    FAST_PATH = "fast_path"      # Deterministic local tool execution (< 50ms, zero LLM overhead)
    HYBRID_PATH = "hybrid_path"  # Tool executes first, then Qwen synthesizes natural language
    AI_PATH = "ai_path"          # Reasoning, explanation, or transcript synthesis via Qwen2.5-3B
    PLAN_PATH = "plan_path"      # Multi-step planning and sequential execution
    BLOCKED = "blocked"          # Adversarial or security violation

class IntentCategory(str, Enum):
    SYSTEM_CONTROL = "system_control"
    APP_AUTOMATION = "app_automation"
    MEDIA_QUERY = "media_query"
    BROWSER_INTELLIGENCE = "browser_intelligence"
    DESKTOP_ACTION = "desktop_action"
    TASK_PLANNING = "task_planning"
    GENERAL_KNOWLEDGE = "general_knowledge"
    SECURITY_VIOLATION = "security_violation"

# Strict app alias mapping
APP_MAP = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "browser": "chrome",
    "notepad": "notepad",
    "text editor": "notepad",
    "calculator": "calculator",
    "calc": "calculator",
    "vscode": "vscode",
    "vs code": "vscode",
    "code": "vscode",
    "visual studio code": "vscode",
    "explorer": "explorer",
    "file explorer": "explorer",
    "edge": "edge",
    "microsoft edge": "edge",
    "brave": "brave",
    "brave browser": "brave",
    "paint": "paint",
    "mspaint": "paint",
    "terminal": "terminal",
    "windows terminal": "terminal",
    "taskmanager": "taskmanager",
    "task manager": "taskmanager",
    "taskmgr": "taskmgr",
    "settings": "settings",
    "windows settings": "settings",
    "microsoft store": "store",
    "ms store": "store",
    "windows store": "store",
    "store": "store"
}

# Safe, deterministic known-site alias registry
KNOWN_WEBSITE_ALIASES: Dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "wikipedia": "https://www.wikipedia.org",
    "github": "https://github.com",
    "google": "https://www.google.com",
    "reddit": "https://www.reddit.com",
    "stackoverflow": "https://stackoverflow.com",
    "canvas": "https://canvas.instructure.com",
    "huggingface": "https://huggingface.co",
    "arxiv": "https://arxiv.org",
}

APP_NAMES_PATTERN = r"(?:windows\s+settings|microsoft\s+store|windows\s+store|ms\s+store|settings|store|google\s+chrome|chrome|browser|microsoft\s+edge|edge|brave\s+browser|brave|paint|mspaint|windows\s+terminal|terminal|task\s+manager|taskmanager|taskmgr|text\s+editor|notepad|calculator|calc|visual\s+studio\s+code|vs\s+code|vscode|code|file\s+explorer|explorer)"

APP_NEGATIVE = re.compile(
    r"\b(why\s+is|why\s+does|why\s+did|why|should\s+i|how\s+does|how\s+come|is\s+it|is\s+\w+\s+safe|don't\s+want|do\s+not\s+want|stop|close|kill|terminate|prevent|hate|uninstall|remove|disable|explain\s+why)\b",
    re.IGNORECASE
)

APP_POSITIVE = re.compile(
    rf"^(?:(?:could|can)\s+you\s+(?:please\s+)?)?(?:open|launch|start|fire\s+up|bring\s+up|run|go\s+to|switch\s+to|take\s+me\s+to)\s+(?:the\s+|my\s+)?({APP_NAMES_PATTERN})\b|"
    rf"^i\s+need\s+(?:the\s+|my\s+)?({APP_NAMES_PATTERN})\b|"
    rf"^(?:please\s+)?(?:open|launch|start|go\s+to|switch\s+to|take\s+me\s+to)\s+(?:the\s+|my\s+)?({APP_NAMES_PATTERN})\b",
    re.IGNORECASE
)

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt\s+override", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.IGNORECASE),
    re.compile(r"format\s+c:", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+database\b", re.IGNORECASE),
    re.compile(r"\bshutdown\s+/s\b", re.IGNORECASE),
]

SYSTEM_STATUS_REASONING_PATTERNS = re.compile(
    r"\b("
    r"why\s+(?:is|are|does|did|would)|"
    r"how\s+come|"
    r"what\s+should\s+i\s+do|"
    r"how\s+(?:can|do)\s+i\s+(?:fix|improve|reduce|lower|cool|free\s+up|speed)|"
    r"how\s+to\s+(?:fix|improve|reduce|lower|cool|free\s+up|speed)|"
    r"troubleshoot|"
    r"overheating|"
    r"too\s+hot"
    r")\b",
    re.IGNORECASE
)

SYSTEM_STATUS_QUERY_PATTERNS = re.compile(
    r"\b("
    r"how('s|\s+is)\s+my\s+(?:laptop|pc|computer|system)(?:\s+(?:doing|performing|running))?|"
    r"how\s+are\s+my\s+(?:system\s+)?resources(?:\s+doing)?|"
    r"is\s+my\s+(?:laptop|pc|computer|system)\s+(?:doing\s+)?(?:okay|healthy|fine|good|running\s+fine)|"
    r"tell\s+me\s+(?:the\s+)?health\s+of\s+my\s+(?:pc|laptop|computer|system)|"
    r"what('s|\s+is)\s+(?:my\s+)?(?:computer|laptop|pc|system)\s+status|"
    r"system\s+status|"
    r"resource\s+status|"
    r"hardware\s+status|"
    r"ram\s+usage|"
    r"cpu\s+usage|"
    r"memory\s+usage|"
    r"gpu\s+status|"
    r"what('s|\s+is)\s+my\s+ram(?:\s+usage)?|"
    r"what('s|\s+is)\s+my\s+cpu(?:\s+usage)?|"
    r"what('s|\s+is)\s+my\s+gpu|"
    r"what\s+gpu\s+do\s+i\s+have|"
    r"is\s+my\s+gpu\s+free|"
    r"check\s+(?:gpu|ram|cpu|resources|hardware|memory)|"
    r"how\s+much\s+(?:ram|cpu)\s*(?:is\s+free|used)?|"
    r"system\s+resources"
    r")\b",
    re.IGNORECASE
)

class IntentRouter:
    """
    Centralized Intent Router.
    Classifies user input into:
    - FAST PATH: Deterministic local tool execution (< 50ms, zero LLM overhead)
    - HYBRID PATH: Tool executes first, then Qwen synthesizes natural language
    - AI PATH: Reasoning, explanation, or transcript synthesis via Qwen2.5-3B
    - BLOCKED: Malicious or prompt injection attempts
    """

    def route(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw_q = query.strip()
        # 1. Wake word normalization & harmless prefix stripping
        q_stripped = re.sub(r"^(?:(?:hey|okay|ok|hi|hello)\s+)?smartscribe[,\s:]*", "", raw_q, flags=re.IGNORECASE).strip()
        q_stripped = re.sub(r"^(?:please[,\s]+|(?:could|can)\s+you\s+(?:please\s+)?)", "", q_stripped, flags=re.IGNORECASE).strip()

        # If user only called SmartScribe by name (e.g. "Hey SmartScribe", "SmartScribe")
        if not q_stripped and re.search(r"\bsmartscribe\b", raw_q, re.IGNORECASE):
            return {
                "intent": "assistant_greeting",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 1.0,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "assistant_greeting",
                "arguments": {},
                "requires_context": False,
                "raw_query": raw_q
            }

        q = q_stripped if q_stripped else raw_q
        q_clean = q.rstrip(".?!;,").strip()
        q_lower = q_clean.lower()

        # -------------------------------------------------------------
        # 0. SECURITY & PROMPT INJECTION GUARDS
        # -------------------------------------------------------------
        for inj_pat in INJECTION_PATTERNS:
            if inj_pat.search(q_lower):
                return {
                    "intent": "prompt_injection_blocked",
                    "category": IntentCategory.SECURITY_VIOLATION.value,
                    "confidence": 1.0,
                    "execution_path": ExecutionPath.BLOCKED.value,
                    "target_tool": None,
                    "arguments": {"reason": "Potential adversarial prompt injection detected."},
                    "requires_context": False,
                    "raw_query": raw_q
                }

        # -------------------------------------------------------------
        # 0.25. FAST PATH CONTROL ACTIONS (Deterministic, < 5ms, Zero LLM)
        # -------------------------------------------------------------
        # Stop
        if re.match(r"^(?:stop(?:\s+(?:it|that|now|playback|reading|talking|speaking|listening|everything))?|halt|abort|shut\s+up|be\s+quiet|silence)$", q_lower):
            return {
                "intent": "stop_action",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 1.0,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "stop_action",
                "arguments": {},
                "requires_context": False,
                "raw_query": raw_q
            }

        # Cancel
        if re.match(r"^(?:cancel(?:\s+(?:that|this|it|action|request|command|plan|execution|operation))?|disregard)$", q_lower):
            return {
                "intent": "cancel_action",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 1.0,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "cancel_action",
                "arguments": {},
                "requires_context": False,
                "raw_query": raw_q
            }

        # Dismiss / Never mind
        if re.match(r"^(?:never\s*mind|forget\s+(?:it|that|about\s+it)|dismiss|ignore\s+(?:that|this|it))$", q_lower):
            return {
                "intent": "dismiss_action",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 1.0,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "dismiss_action",
                "arguments": {},
                "requires_context": False,
                "raw_query": raw_q
            }

        # Pause
        if re.match(r"^(?:pause(?:\s+(?:it|that|now|playback|speech))?|hold\s+on|wait\s+a\s+(?:sec|second|moment|minute))$", q_lower):
            return {
                "intent": "pause_action",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 1.0,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "pause_action",
                "arguments": {},
                "requires_context": False,
                "raw_query": raw_q
            }

        # Go back / Navigate back
        if re.match(r"^(?:go\s+back|navigate\s+back|back|previous\s+page)$", q_lower):
            return {
                "intent": "browser_navigate_back",
                "category": IntentCategory.APP_AUTOMATION.value,
                "confidence": 1.0,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "browser_navigate_back",
                "arguments": {},
                "requires_context": False,
                "raw_query": raw_q
            }

        # -------------------------------------------------------------
        # 0.5. PLAN PATH: Multi-Step Desktop Task Planning (< 15ms detection)
        # -------------------------------------------------------------
        is_multistep = False
        # A. Explicit compound delimiter with action (colon or semicolon syntax)
        if re.search(r"\bprepare\s+(?:my\s+)?.*workspace\s*:\s*.+", q_lower):
            is_multistep = True
        elif re.search(r"\bprepare\s+(?:my\s+)?(?:today'?s\s+)?(.+?)\s+(?:lecture\s+)?notes?\b", q_lower):
            is_multistep = True
        elif re.search(r"[:;]\s*(?:create|open|launch|make|start|navigate|browse)\b", q_lower):
            is_multistep = True
        # B. Explicit sequencing connectors
        elif re.search(r"\band\s+then\b", q_lower):
            is_multistep = True
        elif re.search(r"\b(?:then|followed\s+by|after\s+that)\s+(?:create|open|launch|make|start|navigate|browse)\b", q_lower):
            is_multistep = True
        # C. Multi-action patterns
        elif re.search(r"\bcreate\s+(?:a\s+)?(?:new\s+)?workspace\s+(?:called|named)\s+.+?\s+and\s+(?:open|view|explore)\s+it\b", q_lower):
            is_multistep = True
        elif re.search(r"\bcreate\s+(?:a\s+)?folder\s+.+?,\s*create\s+.+inside\s+it,\s*and\s+open\s+it\b", q_lower):
            is_multistep = True
        elif re.search(r"\bcreate\s+(?:a\s+)?note\s+.+?\s+and\s+(?:open|show)\s+(?:the\s+)?notes\s+folder\b", q_lower):
            is_multistep = True
        elif re.search(r"\bopen\s+(?:chrome|browser|edge|brave)\s+and\s+(?:open|browse\s+to|navigate\s+to)\s+(?:my\s+)?(?:.+?(?:website|portal|page|canvas|blackboard|moodle))\b", q_lower):
            is_multistep = True
        elif re.search(r"\bopen\s+(?:chrome|browser|edge)\s*,\s*(?:open\s+)?(?:the\s+)?(?:.+?(?:portal|website))\s*,\s*and\s+(?:then\s+)?open\b", q_lower):
            is_multistep = True
        elif re.search(r"\bopen\s+.+?\s+and\s+(?:open|launch|start|explore|view)\s+.+\b", q_lower):
            is_multistep = True
        elif re.search(r"\b(?:create|make)\s+(?:a\s+)?(?:folder|workspace)\b.+?\band\s+(?:create\s+(?:a\s+)?(?:folder|note)|make\s+(?:a\s+)?(?:folder|note)|note\s+[a-zA-Z0-9_\s-]+\s+with)\b", q_lower):
            is_multistep = True
        elif re.search(r"\bcreate\s+(?:a\s+)?folder\s+.+?\s+and\s+(?:open|show|view|explore)\s+(?:it|the\s+folder|that|this)\b", q_lower):
            is_multistep = True
        elif re.search(r"\bopen\s+(?:my\s+)?(?:latest\s+)?(?:lecture\s+)?recording\s+and\s+(?:then\s+)?open\s+(?:my\s+)?(?:.+?\s+notes?)\b", q_lower):
            is_multistep = True
        # Phase 5B: Context-continuation / pronoun-referencing actions
        elif re.search(r"\b(?:put|create|make|add)\s+(?:a\s+)?(?:.+?)\s+(?:inside\s+it|in\s+it|into\s+it)\b", q_lower):
            is_multistep = True
        elif re.search(r"\b(?:create|make|take|write)\s+(?:a\s+)?(?:note|notes)\s+(?:for|about)\s+it\b", q_lower):
            is_multistep = True
        elif re.search(r"\b(?:open|show|explore|view)\s+it\b", q_lower):
            is_multistep = True
        elif re.search(r"\b(?:delete|remove)\s+it\b", q_lower):
            is_multistep = True
        elif re.search(r"\bstart\s+recording\s+(?:and|\&|,)\s+(?:then\s+)?open\s+.+\b", q_lower):
            is_multistep = True
        elif re.search(r"\b(?:open|play)\s+.+?\s+(?:and|\&|,)\s+(?:then\s+)?(?:summarize|explain)\s+(?:it|this|them)\b", q_lower):
            is_multistep = True
        elif re.search(r"\b(?:open|launch|go\s+to|visit)\s+(?:the\s+)?([a-zA-Z0-9_.-]+)[,\s]+(?:and\s+)?(?:then\s+)?(?:search|look\s+up|find)\b", q_lower):
            is_multistep = True
        elif re.search(r"\bstart\s+recording\s+(?:and|,)\s+(?:then\s+)?(?:create|make|write|open|launch|search|type)\b", q_lower):
            is_multistep = True
        elif re.search(r"\bopen\s+" + APP_NAMES_PATTERN + r"\s+and\s+(?:write|type|enter|input)\b", q_lower, re.IGNORECASE):
            is_multistep = True
        elif re.search(r"\bgo\s+to\s+.+?\s+and\s+(?:open|launch|start|go\s+to)\s+.+\b", q_lower):
            is_multistep = True

        if is_multistep:
            return {
                "intent": "task_planning",
                "category": IntentCategory.TASK_PLANNING.value,
                "confidence": 0.98,
                "execution_path": ExecutionPath.PLAN_PATH.value,
                "target_tool": "execute_plan",
                "arguments": {"user_query": q},
                "requires_context": False,
                "raw_query": q
            }

        # -------------------------------------------------------------
        # 1. HYBRID PATH: Qualitative / Analytical System Status
        # -------------------------------------------------------------
        # Performance diagnosis: "Why is SmartScribe slow?"
        if re.search(r"\b(why is smartscribe slow|diagnose why (it's|it is) slow|why is it slow)\b", q_lower):
            return {
                "intent": "diagnose_performance",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 0.96,
                "execution_path": ExecutionPath.HYBRID_PATH.value,
                "target_tool": "diagnose_smartscribe",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }


        # Qualitative desktop activity interpretation: "Am I working on my operating systems project?", "What am I currently working on?"
        if re.search(r"\b(am i working on|what am i (currently )?(working on|doing)|what is my current (task|activity|project)|tell me what i('m| am) working on|what am i doing right now)\b", q_lower):
            return {
                "intent": "interpret_activity",
                "category": IntentCategory.DESKTOP_ACTION.value,
                "confidence": 0.96,
                "execution_path": ExecutionPath.HYBRID_PATH.value,
                "target_tool": "get_active_window",
                "arguments": {},
                "requires_context": True,
                "raw_query": q
            }

        # Workspace Preparation / Setup: "Set up everything I need for today's operating systems lecture"
        m_setup = re.search(r"\b(?:set\s*up|prepare|get\s+ready\s+for)\s+(?:everything\s+(?:i\s+need\s+for\s+)?)?(.+?(?:lecture|meeting|class|project|session))\b|\b(?:set\s*up|prepare)\s+(?:my\s+)?workspace\s+(?:for\s+)?(.+)\b", q_lower)
        if m_setup:
            topic = (m_setup.group(1) or m_setup.group(2) or "workspace").strip()
            return {
                "intent": "plan_workspace_setup",
                "category": IntentCategory.DESKTOP_ACTION.value,
                "confidence": 0.94,
                "execution_path": ExecutionPath.HYBRID_PATH.value,
                "target_tool": "find_desktop_files",
                "arguments": {"query": topic},
                "requires_context": True,
                "raw_query": q
            }

        # -------------------------------------------------------------
        # 2. FAST PATH: Deterministic System Metrics & Utilities (< 50ms)
        # -------------------------------------------------------------

        # A. Deterministic System Health & Hardware Status (< 15ms, zero LLM)
        if not SYSTEM_STATUS_REASONING_PATTERNS.search(q_lower) and SYSTEM_STATUS_QUERY_PATTERNS.search(q_lower):
            return {
                "intent": "get_system_status",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 0.98,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "get_system_status",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # B. Clock / Time / Date
        if re.search(r"\b(what time is it|current time|what('s| is) the time|what day is (it|today)|today('s|s) date|what date is it|tell me the time)\b", q_lower):
            return {
                "intent": "get_current_time",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 0.99,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "get_current_time",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # C. Storage / Disk Space
        if re.search(r"\b(disk space|storage status|how much storage|how much disk space is left|do i have enough disk space|free space|drive space|storage space|check storage|check disk)\b", q_lower):
            return {
                "intent": "get_storage_status",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 0.95,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "get_storage_status",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # D. Process / Background Tasks / Resource Consumers
        if re.search(r"\b(what('s| is) using (most of )?my (ram|memory|cpu)|which (apps|applications|processes) are using the most (ram|memory|cpu)|what is running( in the background| right now)?|running tasks|active jobs|process status|active processes|what are the active jobs|top processes|task list|how much (ram|memory|cpu) is \w+ using|how much (ram|memory) does \w+ use)\b", q_lower):
            return {
                "intent": "get_process_summary",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 0.96,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "get_process_summary",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # D2. SmartScribe Diagnostics (Fast Path)
        if re.search(r"\b(diagnose smartscribe|smartscribe (health|diagnostics|status)|is smartscribe (?:currently )?(healthy|working|transcribing|running)|check smartscribe)\b", q_lower):
            return {
                "intent": "diagnose_smartscribe",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 0.97,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "diagnose_smartscribe",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # E0. Multi-Step Task Planning & Compound Execution
        is_plan_query = (
            bool(re.search(r"\bprepare\s+(?:everything\s+i\s+need\s+for|a\s+workspace\s+for|materials\s+for|everything\s+for)\s+(?:today'?s\s+)?(.+?)(?:\s+lecture|\s+class|\s+session)?\b", q_lower)) or
            bool(re.search(r"\bprepare\s+(?:my\s+)?(?:today'?s\s+)?(.+?)\s+(?:lecture\s+)?notes?\b", q_lower)) or
            bool(re.search(r"\bcreate\s+(?:a\s+)?(?:new\s+)?workspace\s+(?:called|named)\s+['\"]?([^'\"]+?)['\"]?\s+and\s+(?:open|view|explore)\s+it\b", q_lower)) or
            bool(re.search(r"\bcreate\s+(?:a\s+)?folder\s+(?:called|named)\s+['\"]?([^'\"]+?)['\"]?,\s+create\s+(?:a\s+)?(?:notes\s+folder|subfolder)\s+inside\s+it,\s+and\s+open\s+it\b", q_lower)) or
            bool(re.search(r"\bcreate\s+(?:a\s+)?note\s+(?:called|named)\s+['\"]?([^'\"]+?)['\"]?\s+and\s+(?:open|show)\s+(?:the\s+)?notes\s+folder\b", q_lower)) or
            bool(re.search(r"\bopen\s+(?:my\s+)?(latest\s+(?:lecture\s+)?recording)\s+and\s+(?:then\s+)?open\s+(?:my\s+)?(.+?\s+notes?)\b", q_lower)) or
            bool(re.search(r"\bopen\s+(chrome|browser|edge|brave)\s+and\s+(?:open|browse\s+to|navigate\s+to)\s+(?:my\s+)?(.+?(?:website|portal|page|canvas|blackboard|moodle))\b", q_lower)) or
            bool(re.search(r"\bopen\s+(chrome|browser|edge)\s*,\s*(?:open\s+)?(?:the\s+)?(.+?(?:portal|website))\s*,\s*and\s+(?:then\s+)?open\s+(?:my\s+)?(.+?\s+notes?)\b", q_lower)) or
            # Conversational Browser Compound Goals (e.g. "Open YouTube and search for operating systems", "Go to Wikipedia and search operating systems")
            bool(re.search(r"\b(?:open|launch|go\s+to|visit|browse(?:\s+to)?)\s+(?:the\s+)?([a-zA-Z0-9_.-]+)\s+(?:and|,|then)\s+(?:then\s+)?(?:search(?:\s+for)?|look\s+up|find)\b", q_lower)) or
            bool(re.search(r"\b(?:on\s+[a-zA-Z0-9_.-]+\s+search\s+(?:for\s+)?|search\s+(?:for\s+)?.+?\s+on\s+[a-zA-Z0-9_.-]+)\b", q_lower)) or
            bool(re.search(r"\btype\s+.+?\s+into\s+.+?\s+and\s+(?:then\s+)?click\b", q_lower)) or
            bool(re.search(r"\bsearch\s+for\s+.+?\s+and\s+tell\s+me\b", q_lower)) or
            # Ambiguous/vague pronoun commands requiring session context or clarification
            bool(re.match(r"^(?:open|launch|play|show|view|explore)\s+(?:it|that|this)$", q_lower)) or
            bool(re.match(r"^(?:open|go\s+to|visit)\s+(?:that|the)\s+site$", q_lower)) or
            bool(re.match(r"^click\s+(?:that|it|this)$", q_lower)) or
            bool(re.match(r"^search\s+(?:for\s+)?(?:it|that)$", q_lower)) or
            bool(re.match(r"^(?:do\s+something(?:\s+with\s+this\s+page)?|help\s+me\s+with\s+this\s+page)$", q_lower)) or
            # Single browser DOM actions
            bool(re.search(r"\btype\s+.+?\s+into\s+(?:the\s+)?(?:search(?:\s+box|\s+field|\s+input)?|input|field|textbox)\b", q_lower)) or
            bool(re.search(r"\bclick\s+(?:the\s+)?(?:search(?:\s+button|\s+icon)?|first(?:\s+safe)?\s+result|result(?:\s+link)?|button|link)\b", q_lower)) or
            bool(re.search(r"\bselect\s+(?:option\s+)?.+?\s+(?:in|from)\s+(?:the\s+)?(?:dropdown|select)\b", q_lower))
        )
        if is_plan_query:
            return {
                "intent": "task_planning",
                "category": IntentCategory.TASK_PLANNING.value,
                "confidence": 0.96,
                "execution_path": ExecutionPath.PLAN_PATH.value,
                "target_tool": "execute_plan",
                "arguments": {"user_query": q},
                "requires_context": True,
                "raw_query": q
            }

        # E. Recording Control: Start & Stop
        if re.search(r"\b(start recording|begin recording|record audio|record meeting|begin the meeting recording|record this session|start capturing audio)\b", q_lower):
            return {
                "intent": "start_recording",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 0.98,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "start_recording",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        if re.search(r"\b(stop recording|finish recording|end recording|end the recording|stop audio capture|stop the meeting recording)\b", q_lower):
            return {
                "intent": "stop_recording",
                "category": IntentCategory.SYSTEM_CONTROL.value,
                "confidence": 0.98,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "stop_recording",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # E2. Browser Video Controls (Fast Path — deterministic keyboard shortcuts)
        # "Play next video", "next video", "go to next video"
        if re.search(
            r"\b(?:play\s+(?:the\s+)?next(?:\s+video)?|next\s+video|go\s+to\s+(?:the\s+)?next\s+video|skip\s+(?:this\s+)?video)\b",
            q_lower
        ):
            return {
                "intent": "browser_next_video",
                "category": IntentCategory.BROWSER_INTELLIGENCE.value,
                "confidence": 0.97,
                "execution_path": ExecutionPath.PLAN_PATH.value,
                "target_tool": "execute_plan",
                "arguments": {"user_query": q},
                "requires_context": False,
                "raw_query": q
            }

        # "Pause the video", "pause", "stop the video", "play", "resume the video"
        if re.search(
            r"\b(?:pause(?:\s+(?:the\s+)?video)?|stop\s+(?:the\s+)?video|unpause(?:\s+video)?|resume(?:\s+the\s+)?video|play\s+(?:the\s+)?video|toggle\s+(?:video\s+)?playback)\b",
            q_lower
        ):
            return {
                "intent": "browser_pause_video",
                "category": IntentCategory.BROWSER_INTELLIGENCE.value,
                "confidence": 0.96,
                "execution_path": ExecutionPath.PLAN_PATH.value,
                "target_tool": "execute_plan",
                "arguments": {"user_query": q},
                "requires_context": False,
                "raw_query": q
            }

        # F. Safe App Launcher (with Negative / Interrogative Guardrails)
        neg_matched = bool(APP_NEGATIVE.search(q))
        if not neg_matched:
            pos_matched = APP_POSITIVE.search(q)
            if pos_matched:
                raw_app = pos_matched.group(1) or pos_matched.group(2) or pos_matched.group(3)
                raw_clean = re.sub(r"\s+", " ", raw_app.lower().strip())
                mapped_app = APP_MAP.get(raw_clean)
                if mapped_app:
                    return {
                        "intent": "open_application",
                        "category": IntentCategory.APP_AUTOMATION.value,
                        "confidence": 0.95,
                        "execution_path": ExecutionPath.FAST_PATH.value,
                        "target_tool": "open_application",
                        "arguments": {"application": mapped_app},
                        "requires_context": False,
                        "raw_query": q
                    }

        # G1. Known Website Aliases (Fast Path)
        m_alias = re.search(
            r"^(?:(?:could|can)\s+you\s+(?:please\s+)?)?(?:open|launch|go\s+to|take\s+me\s+to|visit|browse(?:\s+to)?)\s+(?:the\s+|my\s+)?([a-zA-Z0-9_-]+)(?:\s+website|\s+site)?(?:\s+for\s+me)?$",
            q_lower
        )
        if m_alias:
            alias_candidate = m_alias.group(1).lower().strip()
            if alias_candidate in KNOWN_WEBSITE_ALIASES:
                target_url = KNOWN_WEBSITE_ALIASES[alias_candidate]
                return {
                    "intent": "open_website",
                    "category": IntentCategory.APP_AUTOMATION.value,
                    "confidence": 0.96,
                    "execution_path": ExecutionPath.FAST_PATH.value,
                    "target_tool": "open_website",
                    "arguments": {"url": target_url},
                    "requires_context": False,
                    "raw_query": q
                }
            elif alias_candidate in ("it", "that", "this", "something", "site", "website"):
                # Pronoun / vague referent -> falls through to PLAN_PATH / clarification
                pass
            elif re.search(r"\b(?:website|site)\b", q_lower):
                # Explicit website requested but alias unknown -> clarify
                return {
                    "intent": "request_clarification",
                    "category": IntentCategory.APP_AUTOMATION.value,
                    "confidence": 0.90,
                    "execution_path": ExecutionPath.PLAN_PATH.value,
                    "target_tool": "execute_plan",
                    "arguments": {"query": q},
                    "requires_context": True,
                    "raw_query": q
                }

        # G2. Open Website by URL / Domain (Fast Path)
        m_web = re.search(r"\b(open|browse|browse to|go to|visit|launch|take me to)\s+(https?://\S+|www\.\S+|[a-zA-Z0-9.-]+\.(?:com|org|io|net|edu|gov|co|ai))\b", q_lower)
        if m_web:
            url_target = m_web.group(2)
            if not url_target.startswith(("http://", "https://")):
                url_target = "https://" + url_target
            return {
                "intent": "open_website",
                "category": IntentCategory.APP_AUTOMATION.value,
                "confidence": 0.95,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "open_website",
                "arguments": {"url": url_target},
                "requires_context": False,
                "raw_query": q
            }

        # H0-A. Create Note (Fast Path)
        m_note = re.search(
            r"\b(?:create|make|write|take)(?:\s+a|\s+new)?\s+note\s+(?:called|named|titled)?\s*[\"']?([a-zA-Z0-9_\s-]+?)[\"']?(?:\s+(?:with\s+content|saying|containing)\s+(.+))?$",
            q_lower
        )
        if not m_note:
            m_note = re.search(
                r"\bnew\s+note\s+(?:called|named|titled)?\s*[\"']?([a-zA-Z0-9_\s-]+?)[\"']?(?:\s+(?:with\s+content|saying|containing)\s+(.+))?$",
                q_lower
            )
        if m_note:
            note_title = m_note.group(1).strip()
            note_content = m_note.group(2).strip() if m_note.group(2) else ""
            return {
                "intent": "create_note",
                "category": IntentCategory.DESKTOP_ACTION.value,
                "confidence": 0.96,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "create_note",
                "arguments": {"title": note_title, "content": note_content},
                "requires_context": False,
                "raw_query": q
            }

        # H0-B. Create Folder (Fast Path)
        m_folder_create = re.search(
            r"\b(?:create|make)(?:\s+a|\s+new)?\s+(?:(workspaces?|notes?)\s+)?folder\s+(?:called|named)?\s*[\"']?([a-zA-Z0-9_\s-]+?)[\"']?(?:\s+in\s+(workspaces?|notes?))?$",
            q_lower
        )
        if not m_folder_create:
            m_folder_create = re.search(
                r"\bnew\s+folder\s+(?:called|named)?\s*[\"']?([a-zA-Z0-9_\s-]+?)[\"']?(?:\s+in\s+(workspaces?|notes?))?$",
                q_lower
            )
        if m_folder_create:
            folder_name = (m_folder_create.group(2) if (m_folder_create.lastindex and m_folder_create.lastindex >= 2 and m_folder_create.group(2)) else m_folder_create.group(1)).strip()
            parent_dir = "notes" if "note" in q_lower else "workspaces"
            return {
                "intent": "create_folder",
                "category": IntentCategory.DESKTOP_ACTION.value,
                "confidence": 0.96,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "create_folder",
                "arguments": {"folder_name": folder_name, "parent": parent_dir},
                "requires_context": False,
                "raw_query": q
            }

        # H0-C. Open Folder (Fast Path)
        m_open_fld = re.search(
            r"\b(?:open|show|view)\s+(?:the\s+|my\s+)?folder\s+(?:called\s+|named\s+)?[\"']?([a-zA-Z0-9_\s-]+?)[\"']?$|"
            r"\b(?:open|show|view)\s+(?:me\s+)?(?:my\s+)?(workspaces?(?:\s+folder)?|notes\s+folder|recordings\s+folder|downloads\s+folder)\b",
            q_lower
        )
        if m_open_fld:
            fld_target = (m_open_fld.group(1) or m_open_fld.group(2) or "").strip()
            return {
                "intent": "open_folder",
                "category": IntentCategory.DESKTOP_ACTION.value,
                "confidence": 0.95,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "open_folder",
                "arguments": {"folder_name": fld_target},
                "requires_context": False,
                "raw_query": q
            }

        # H1. Open File / Structured Target Resolution (Fast Path)
        m_open_fld_alias = re.search(r"\b(?:open|show|play|view)\s+(?:me\s+)?(?:my\s+)?(?:smartscribe\s+)?(downloads(?:\s+folder)?|recordings(?:\s+folder)?|notes(?:\s+folder)?)\b", q_lower)
        if m_open_fld_alias:
            target_key = m_open_fld_alias.group(1).strip()
            file_arg = "notes"
            if "download" in target_key:
                file_arg = "downloads"
            elif "recording" in target_key:
                file_arg = "recordings"

            return {
                "intent": "open_file",
                "category": IntentCategory.DESKTOP_ACTION.value,
                "confidence": 0.95,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "open_file",
                "arguments": {"file_name": file_arg, "query": q},
                "requires_context": False,
                "raw_query": q
            }

        # Structured Target File Queries (e.g. "open my latest lecture PDF", "launch my latest recording", "open lecture.pdf")
        m_open_act = re.search(r"\b(open|launch|play|show|view)\b", q_lower)
        if m_open_act:
            from backend.assistant.target_resolver import FileTargetResolver
            spec = FileTargetResolver.parse_query(q)
            if spec.requested_type or spec.requested_name or spec.temporal_filter:
                return {
                    "intent": "open_file",
                    "category": IntentCategory.DESKTOP_ACTION.value,
                    "confidence": 0.96,
                    "execution_path": ExecutionPath.FAST_PATH.value,
                    "target_tool": "open_file",
                    "arguments": {
                        "file_name": spec.requested_name or q,
                        "query": q,
                        "target_spec": spec.to_dict()
                    },
                    "requires_context": False,
                    "raw_query": q
                }

        m_open_spec = re.search(r"\b(?:open|play)\s+(?:file\s+)?([a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{2,5})\b", q_lower)
        if not m_open_spec:
            m_open_spec = re.search(r"\b(?:open|play)\s+file\s+([a-zA-Z0-9_.-]+)\b", q_lower)
        if m_open_spec:
            file_name_arg = m_open_spec.group(1).strip()
            return {
                "intent": "open_file",
                "category": IntentCategory.DESKTOP_ACTION.value,
                "confidence": 0.94,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "open_file",
                "arguments": {"file_name": file_name_arg, "query": q},
                "requires_context": False,
                "raw_query": q
            }

        # G2. Active Window / Foreground Desktop Context (Fast Path)
        if re.search(r"\b(what window (am i using|is active|is open)|what('s| is) (my )?active window|what('s| is) open|what application is (currently )?active|what app is (currently )?(active|open)|active window|current window|what am i looking at)\b", q_lower):
            if not re.search(r"\b(read|inspect|summarize|explain)\b", q_lower):
                return {
                    "intent": "get_active_window",
                    "category": IntentCategory.DESKTOP_ACTION.value,
                    "confidence": 0.96,
                    "execution_path": ExecutionPath.FAST_PATH.value,
                    "target_tool": "get_active_window",
                    "arguments": {},
                    "requires_context": False,
                    "raw_query": q
                }

        # H. Desktop File Discovery across approved roots (Fast Path)
        if "transcript" not in q_lower:
            m_desk_files = re.search(
                r"\b(?:find|search for|look for|locate)\s+(?:me\s+)?(?:my\s+|the\s+)?(.+?(?:notes?|pdf|recording|recordings|file|files|doc|docx|slides?))\b|"
                r"\bfind\s+(?:my\s+|the\s+)?(latest|recent|newest|yesterday(?:'s)?)\s+(.+)\b|"
                r"\bfind\s+(?:my\s+|the\s+)?([a-zA-Z0-9_\s-]+)\s+(?:notes|pdf|recording)\b|"
                r"\bsearch\s+(?:desktop\s+)?(?:files|recordings|notes)\s+for\s+(.+)\b|"
                r"\b(?:find file|find recording|look for file)\s+(.+)\b",
                q_lower
            )
            if m_desk_files:
                return {
                    "intent": "find_desktop_files",
                    "category": IntentCategory.DESKTOP_ACTION.value,
                    "confidence": 0.95,
                    "execution_path": ExecutionPath.FAST_PATH.value,
                    "target_tool": "find_desktop_files",
                    "arguments": {"query": q},
                    "requires_context": False,
                    "raw_query": q
                }

        # I. Search Transcript Chunks
        m_tr = re.search(r"\b(search transcript for|find in transcript|look up .+ in transcript)\s+(.+)$", q_lower)
        if m_tr:
            search_query = m_tr.group(m_tr.lastindex).strip()
            return {
                "intent": "search_transcript",
                "category": IntentCategory.MEDIA_QUERY.value,
                "confidence": 0.92,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "search_transcript",
                "arguments": {"query": search_query},
                "requires_context": True,
                "raw_query": q
            }

        # J. Summarize Current Session
        if re.search(r"\b(summarize this (meeting|lecture|video|recording|session)|summarize current session|summarize the lecture|give me (a|the) summary)\b", q_lower) and not re.search(r"\b(page|article|webpage)\b", q_lower):
            return {
                "intent": "summarize_current_session",
                "category": IntentCategory.MEDIA_QUERY.value,
                "confidence": 0.94,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "summarize_current_session",
                "arguments": {},
                "requires_context": True,
                "raw_query": q
            }

        # K. Delete Recording / Session (DESTRUCTIVE ACTION, REQUIRES CONFIRMATION)
        if re.search(r"\b(delete (my |this )?(recording|meeting|session)|remove (my |this )?(recording|meeting|session))\b", q_lower):
            return {
                "intent": "delete_recording",
                "category": IntentCategory.MEDIA_QUERY.value,
                "confidence": 0.96,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "delete_recording",
                "arguments": {},
                "requires_context": True,
                "raw_query": q
            }

        # L. Read Current Page / Metadata (< 10ms Fast Path)
        if re.search(r"\b(read (this|current|the) (page|article|webpage)|read what i('m| am) looking at|what (page|article) am i (reading|looking at)|what('s|\s+is)\s+on\s+(this|the|current)\s+(page|article|webpage)|what is on this page|what's on this page|inspect (this|current) page)\b", q_lower):
            return {
                "intent": "read_current_page",
                "category": IntentCategory.BROWSER_INTELLIGENCE.value,
                "confidence": 0.95,
                "execution_path": ExecutionPath.FAST_PATH.value,
                "target_tool": "read_current_page",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # -------------------------------------------------------------
        # 3. AI PATH: Browser Intelligence, Content Q&A, General Knowledge
        # -------------------------------------------------------------

        # Browser Article Summarization
        if re.search(r"\b(what('s|\s+is)\s+(this|the|my\s+current)\s+(page|article|webpage)\s+about|tell\s+me\s+what\s+this\s+page\s+is\s+about|summarize\s+(this|current|the)\s+(page|webpage)|summarize\s+(this|the)\s+article|give\s+me\s+(a\s+|the\s+)?summary\s+of\s+this\s+(page|article|webpage)|summarize\s+what\s+i('m|\s+am)\s+reading|summary\s+of\s+(this|the)\s+(page|article))\b", q_lower):
            return {
                "intent": "summarize_current_page",
                "category": IntentCategory.BROWSER_INTELLIGENCE.value,
                "confidence": 0.95,
                "execution_path": ExecutionPath.AI_PATH.value,
                "target_tool": "summarize_current_page",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # Browser Extract Key Points
        if re.search(r"\b(what\s+are\s+the\s+(key|important|main)\s+(points|takeaways|things)(\s+(on|of|in|from))?\s*(this|the)?\s*(page|article|here)?|important\s+points\s+here|extract\s+key\s+points|key\s+takeaways|bullet\s+points\s+of\s+this\s+(page|article)|give\s+me\s+the\s+key\s+points|list\s+the\s+(main|key)\s+points)\b", q_lower):
            return {
                "intent": "extract_key_points",
                "category": IntentCategory.BROWSER_INTELLIGENCE.value,
                "confidence": 0.93,
                "execution_path": ExecutionPath.AI_PATH.value,
                "target_tool": "extract_key_points",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # Browser Explain Article Simply
        if re.search(r"\b(explain\s+this\s+(page|article|webpage)(\s+simply)?|(can\s+you\s+)?explain\s+what\s+i('m|\s+am)\s+(reading|looking\s+at)|break\s+down\s+this\s+(page|article|webpage)|explain\s+the\s+article\s+simply)\b", q_lower):
            return {
                "intent": "explain_current_page",
                "category": IntentCategory.BROWSER_INTELLIGENCE.value,
                "confidence": 0.92,
                "execution_path": ExecutionPath.AI_PATH.value,
                "target_tool": "explain_current_page",
                "arguments": {},
                "requires_context": False,
                "raw_query": q
            }

        # Browser Question Answering (BM25 + Qwen)
        m_bqa = re.search(r"\b(what does this (page|article) say about|does this (page|article) mention|search this (page|article) for|what is on this (page|article) about)\s+(.+)$", q_lower)
        if m_bqa:
            return {
                "intent": "answer_about_current_page",
                "category": IntentCategory.BROWSER_INTELLIGENCE.value,
                "confidence": 0.94,
                "execution_path": ExecutionPath.AI_PATH.value,
                "target_tool": "answer_about_current_page",
                "arguments": {"query": q},
                "requires_context": False,
                "raw_query": q
            }

        # Media / Lecture content question patterns
        if re.search(r"\b(what did (they|the speaker|the professor|he|she) say|when did they discuss|who mentioned|what was discussed about)\b", q_lower):
            return {
                "intent": "media_question",
                "category": IntentCategory.MEDIA_QUERY.value,
                "confidence": 0.90,
                "execution_path": ExecutionPath.AI_PATH.value,
                "target_tool": None,
                "arguments": {"query": q},
                "requires_context": True,
                "raw_query": q
            }

        # General knowledge / fallback reasoning query
        return {
            "intent": "general_reasoning_query",
            "category": IntentCategory.GENERAL_KNOWLEDGE.value,
            "confidence": 0.85,
            "execution_path": ExecutionPath.AI_PATH.value,
            "target_tool": None,
            "arguments": {"query": q},
            "requires_context": True,
            "raw_query": q
        }
