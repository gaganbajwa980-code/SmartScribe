import re
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

from backend.assistant.planner.plan_schema import TaskPlan, PlanStep, PlanStatus, StepStatus, UserGoal
from backend.assistant.planner.plan_context import PlanContext
from backend.assistant.planner.plan_validator import PlanValidator
from backend.assistant.permission_manager import RiskLevel
from backend.assistant.tool_registry import ToolRegistry
from backend.resource_manager import ResourceManager
from backend.assistant.planner.context_snapshot import ContextSnapshot, ContextDecision, collect_context_snapshot
from backend.assistant.planner.session_memory import SessionMemory
from backend.assistant.tools.desktop_tools import NOTES_DIR
from backend.assistant.intent_router import KNOWN_WEBSITE_ALIASES, APP_MAP, APP_NAMES_PATTERN

class TaskPlanner:
    """
    Intelligent Desktop Planner for SmartScribe.
    Converts compound natural language instructions into deterministic,
    strongly-typed, validated TaskPlans using curated safe tools.
    Supports context-aware artifact reuse, duplicate avoidance, and task continuation.
    """
    def __init__(self):
        self.validator = PlanValidator()
        self.registry = ToolRegistry()
        self.rm = ResourceManager()
        self.session_memory = SessionMemory()

    def plan(self, query: str, context: Optional[PlanContext] = None) -> TaskPlan:
        """
        Main entry point for generating a TaskPlan from a user query.
        Attempts deterministic fast-path planning first (< 15ms).
        Falls back to local Qwen interpretation if ambiguous.
        """
        q = (query or "").strip()
        ctx = context or PlanContext(user_query=q)

        # Ensure session memory is populated
        if ctx.session_memory is None:
            ctx.session_memory = self.session_memory

        # 0. Check for explicit ambiguous / underspecified requests (Ask Rather Than Guess)
        q_norm = re.sub(r"^(?:could|can)\s+you\s+(?:please\s+)?", "", q.lower().strip())
        q_norm = re.sub(r"\s+please$", "", q_norm).strip()
        q_norm = re.sub(r"[?!.,;]+$", "", q_norm).strip()

        if re.match(r"^click\s+(?:that|it|this)$", q_norm):
            return TaskPlan(
                user_query=q,
                goal="Clarification required for ambiguous click",
                steps=[],
                status=PlanStatus.FAILED,
                error_message="Which element should I click? Multiple elements or no active target found. Please specify the button or link name."
            )

        if re.match(r"^(?:open|go\s+to|visit)\s+(?:that|the)\s+site$", q_norm):
            return TaskPlan(
                user_query=q,
                goal="Clarification required for ambiguous website",
                steps=[],
                status=PlanStatus.FAILED,
                error_message="Which website should I open? Please specify the website name or URL."
            )

        if re.match(r"^search\s+(?:for\s+)?(?:it|that)$", q_norm):
            return TaskPlan(
                user_query=q,
                goal="Clarification required for ambiguous search query",
                steps=[],
                status=PlanStatus.FAILED,
                error_message="What should I search for? Please specify the search topic."
            )

        if re.match(r"^(?:do\s+something(?:\s+with\s+this\s+page)?|help\s+me\s+with\s+this\s+page)$", q_norm):
            return TaskPlan(
                user_query=q,
                goal="Clarification required for vague page action",
                steps=[],
                status=PlanStatus.FAILED,
                error_message="What would you like me to do with this page? I can summarize it, extract key points, or answer questions."
            )

        # Check for continuation pronouns ("inside it", "notes for it", "open it")
        # Only check session memory if the query does NOT define its own antecedent
        has_internal_antecedent = bool(
            re.search(r"\b(?:create|make)\b.*?\b(?:workspace|folder|note)\b", q, re.IGNORECASE)
            and re.search(r"\b(?:and\s+)?(?:then\s+)?(?:open|show|explore|view)\s+it\b", q, re.IGNORECASE)
        )
        if not has_internal_antecedent:
            pronoun_res = ctx.session_memory.resolve_pronoun_reference(q)
            if pronoun_res:
                if not pronoun_res.get("resolved") and pronoun_res.get("is_ambiguous"):
                    # Genuinely ambiguous referent -> request clarification
                    ambiguous_plan = TaskPlan(
                        user_query=q,
                        goal="Clarification required for ambiguous referent",
                        steps=[],
                        status=PlanStatus.FAILED,
                        error_message=f"Ambiguous request: {pronoun_res.get('reason', 'Could not resolve referent').rstrip('.')}. Please clarify the target name."
                    )
                    ambiguous_plan.context_decisions.append(ContextDecision(
                        observation=f"Pronoun '{pronoun_res.get('pronoun')}' detected without clear referent in session memory.",
                        confidence="HIGH",
                        effect_on_plan="request_clarification",
                        source="SessionMemory"
                    ).to_dict())
                    return ambiguous_plan
                elif pronoun_res.get("resolved"):
                    # Pronoun resolved! Plan follow-up action with resolved target
                    resolved_plan = self._plan_followup(q, pronoun_res, ctx)
                    if resolved_plan:
                        self.validator.validate_plan(resolved_plan, ctx)
                        return resolved_plan

        # 1. Deterministic Fast-Path Planner (< 15ms, zero LLM overhead)
        plan = self._plan_deterministic(q, ctx)
        if plan:
            plan = self._adapt_plan_with_context(plan, ctx)
            self.validator.validate_plan(plan, ctx)
            return plan

        # 2. Qwen-assisted Planning (Structured interpretation under safety constraints)
        plan = self._plan_with_qwen(q, ctx)
        if plan:
            plan = self._adapt_plan_with_context(plan, ctx)
            self.validator.validate_plan(plan, ctx)
            return plan

        # 3. Fallback: Empty/Failed Plan
        failed_plan = TaskPlan(
            user_query=q,
            goal="Process multi-step command",
            steps=[],
            status=PlanStatus.FAILED,
            error_message="Could not synthesize a safe multi-step plan for this request."
        )
        return failed_plan

    @staticmethod
    def mask_quoted_spans(text: str):
        """
        Masks single- or double-quoted substrings with placeholder tokens
        so delimiters like 'and', commas, colons inside quotes are never split.
        """
        quote_map = {}
        def replacer(match):
            token = f"__SMARTSCRIBE_QUOTE_{len(quote_map)}__"
            quote_map[token] = match.group(0)
            return token
        masked = re.sub(r'("[^"]*"|\'[^\']*\')', replacer, text)
        return masked, quote_map

    @staticmethod
    def unmask_text(text: str, quote_map: dict) -> str:
        """Restores original quoted strings from placeholder tokens."""
        res = text
        for token, original in quote_map.items():
            res = res.replace(token, original)
        return res

    @classmethod
    def decompose_clauses(cls, q: str) -> List[str]:
        """
        Decomposes a compound query into distinct action clauses while preserving
        semantic 'and' within names, filenames, folder names, and quoted strings.

        Rules:
        1. Quoted spans ('...' and "...") are masked with tokens to prevent any splitting.
        2. Explicit action connectors ("and then", "then", "followed by", "after that",
           semicolons, colons followed by action patterns) are treated as step boundaries.
        3. Commas followed by action verbs, action phrases, or connectors are boundaries.
        4. The word "and" is treated as an action boundary IF AND ONLY IF followed by:
           - an action verb: open, launch, start, explore, view, browse, navigate, create, make
           - or the action pattern: note <title> with <content>
        5. Quoted spans are restored in each extracted clause.
        """
        masked, quote_map = cls.mask_quoted_spans(q)
        ACTION_VERBS = r"(?:open|launch|start|explore|view|browse|navigate|create|make|search|look\s+up|find|type|write|enter|input|click|select|play|summarize|explain|record|tell\s+me)"
        split_pattern = (
            r"\s+(?:and\s+)?then\s+|"
            r"\s+(?:followed\s+by|after\s+that)\s+|"
            r"\s*;\s*|"
            r"\s*:\s*(?=" + ACTION_VERBS + r"\b|note\s+[^,;:]+?\s+with\b)|"
            r",\s*(?:(?:and\s+)?then\s+|followed\s+by\s+|after\s+that\s+)?(?=" + ACTION_VERBS + r"\b|note\s+[^,;:]+?\s+with\b)|"
            r",\s+(?=(?:and\s+)?then\b)|"
            r"\s+and\s+(?=" + ACTION_VERBS + r"\b|note\s+[^,;:]+?\s+with\b)"
        )
        raw_clauses = re.split(split_pattern, masked, flags=re.IGNORECASE)
        clauses = []
        for c in raw_clauses:
            clean = cls.unmask_text(c.strip(), quote_map)
            clean = re.sub(r"^(?:(?:and\s+)?then|followed\s+by|after\s+that|and)\s+", "", clean, flags=re.IGNORECASE).strip()
            clean = clean.rstrip(".,;").strip()
            if re.match(r"^prepare\s+(?:my\s+)?.*workspace$", clean, re.IGNORECASE):
                continue
            if clean:
                clauses.append(clean)
        return clauses

    def _plan_deterministic(self, q: str, ctx: PlanContext) -> Optional[TaskPlan]:
        """
        Deterministic planning rules for common student, lecture, browser, and workflow tasks.
        """
        q_lower = q.lower().strip()
        q_norm = re.sub(r"^(?:could|can)\s+you\s+(?:please\s+)?", "", q_lower)
        q_norm = re.sub(r"\s+please$", "", q_norm).strip()
        q_norm = re.sub(r"[?!.,;]+$", "", q_norm).strip()
        q_lower = q_norm

        # -------------------------------------------------------------
        # 0. AMBIGUITY & CONFIDENCE POLICIES (ASK RATHER THAN GUESS)
        # -------------------------------------------------------------
        if re.match(r"^(?:open|launch|play|show|view|explore)\s+(?:it|that|this)$", q_lower):
            pronoun_res = ctx.session_memory.resolve_pronoun_reference(q) if ctx.session_memory else None
            if not pronoun_res or not pronoun_res.get("resolved"):
                return TaskPlan(
                    user_query=q,
                    goal="Clarification required for ambiguous referent",
                    steps=[],
                    status=PlanStatus.FAILED,
                    error_message="Ambiguous request: Referent 'it' cannot be resolved: no prior target found in session memory. Please clarify the target name."
                )
            else:
                target_data = pronoun_res.get("target", {})
                t_type = target_data.get("type", "file")
                t_name = pronoun_res.get("resolved_value") or target_data.get("name")
                if t_type == "folder":
                    return TaskPlan(
                        user_query=q,
                        goal=f"Open folder '{t_name}'",
                        steps=[
                            PlanStep(
                                step_id="step_1",
                                tool="open_folder",
                                arguments={"folder_name": t_name},
                                reason=f"Open referenced folder '{t_name}'",
                                risk_level=RiskLevel.READ_ONLY
                            )
                        ],
                        status=PlanStatus.DRAFT
                    )
                else:
                    return TaskPlan(
                        user_query=q,
                        goal=f"Open file '{t_name}'",
                        steps=[
                            PlanStep(
                                step_id="step_1",
                                tool="open_file",
                                arguments={"target": t_name, "requested_target": t_name},
                                reason=f"Open referenced file '{t_name}'",
                                risk_level=RiskLevel.LOW_RISK_ACTION
                            )
                        ],
                        status=PlanStatus.DRAFT
                    )

        if re.match(r"^click\s+(?:that|it|this)$", q_lower):
            return TaskPlan(
                user_query=q,
                goal="Clarification required for ambiguous click",
                steps=[],
                status=PlanStatus.FAILED,
                error_message="Which element should I click? Multiple elements or no active target found. Please specify the button or link name."
            )

        if re.match(r"^(?:open|go\s+to|visit)\s+(?:that|the)\s+site$", q_lower):
            return TaskPlan(
                user_query=q,
                goal="Clarification required for ambiguous website",
                steps=[],
                status=PlanStatus.FAILED,
                error_message="Which website should I open? Please specify the website name or URL."
            )

        if re.match(r"^search\s+(?:for\s+)?(?:it|that)$", q_lower):
            return TaskPlan(
                user_query=q,
                goal="Clarification required for ambiguous search query",
                steps=[],
                status=PlanStatus.FAILED,
                error_message="What should I search for? Please specify the search topic."
            )

        if re.match(r"^(?:do\s+something(?:\s+with\s+this\s+page)?|help\s+me\s+with\s+this\s+page)$", q_lower):
            return TaskPlan(
                user_query=q,
                goal="Clarification required for vague page action",
                steps=[],
                status=PlanStatus.FAILED,
                error_message="What would you like me to do with this page? I can summarize it, extract key points, or answer questions."
            )

        # -------------------------------------------------------------------
        # 0.5. BROWSER VIDEO CONTROLS
        # -------------------------------------------------------------------
        # Next video
        if re.search(
            r"\b(?:play\s+(?:the\s+)?next(?:\s+video)?|next\s+video|go\s+to\s+(?:the\s+)?next\s+video|skip\s+(?:this\s+)?video)\b",
            q_lower
        ):
            return TaskPlan(
                user_query=q,
                goal="Play the next video",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool="click_browser_element",
                        arguments={
                            "target_binding": {
                                "tab_id": -1,
                                "window_id": -1,
                                "origin": "",
                                "url_path": "/",
                                "frame_id": 0,
                                "dom_selector": ".ytp-next-button, button[aria-label*='Next']",
                                "element_fingerprint": "",
                                "element_tag": "BUTTON",
                                "element_role": "button",
                                "is_visible": True,
                                "is_enabled": True,
                                "is_sensitive": False
                            }
                        },
                        reason="Click Next Video button in browser",
                        risk_level=RiskLevel.LOW_RISK_ACTION,
                        dependencies=[],
                        expected_result="Next video started playing"
                    )
                ]
            )

        # Pause / resume video
        if re.search(
            r"\b(?:pause(?:\s+(?:the\s+)?video)?|stop\s+(?:the\s+)?video|unpause(?:\s+video)?|resume(?:\s+the\s+)?video|play\s+(?:the\s+)?video|toggle\s+(?:video\s+)?playback)\b",
            q_lower
        ):
            return TaskPlan(
                user_query=q,
                goal="Toggle video playback (pause or resume)",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool="click_browser_element",
                        arguments={
                            "target_binding": {
                                "tab_id": -1,
                                "window_id": -1,
                                "origin": "",
                                "url_path": "/",
                                "frame_id": 0,
                                "dom_selector": ".ytp-play-button, button[aria-label*='Pause'], button[aria-label*='Play']",
                                "element_fingerprint": "",
                                "element_tag": "BUTTON",
                                "element_role": "button",
                                "is_visible": True,
                                "is_enabled": True,
                                "is_sensitive": False
                            }
                        },
                        reason="Click Pause/Play button in browser",
                        risk_level=RiskLevel.LOW_RISK_ACTION,
                        dependencies=[],
                        expected_result="Video playback toggled"
                    )
                ]
            )

        # -------------------------------------------------------------
        # 1. BROWSER COMPOUND WORKFLOW DECOMPOSITION
        # -------------------------------------------------------------
        # Case A: "Open {site} and search for {topic}" / "Go to {site} and look up {topic}"
        m_site_search = re.search(
            r"^(?:(?:could|can)\s+you\s+(?:please\s+)?)?(?:open|launch|go\s+to|visit|browse(?:\s+to)?)\s+(?:the\s+)?([a-zA-Z0-9_.-]+)[,\s]+(?:and\s+)?(?:then\s+)?(?:search(?:\s+for)?|look\s+up|find)\s+(.+)$",
            q_lower
        )
        if not m_site_search:
            m_site_search = re.search(
                r"^(?:(?:could|can)\s+you\s+(?:please\s+)?)?(?:on\s+([a-zA-Z0-9_.-]+)\s+search(?:\s+for)?\s+(.+)|search(?:\s+for)?\s+(.+?)\s+on\s+([a-zA-Z0-9_.-]+))(?:\s+and\s+.+)?$",
                q_lower
            )
        if not m_site_search:
            m_site_search = re.search(
                r"^(?:(?:could|can)\s+you\s+(?:please\s+)?)?open\s+(chrome|browser|edge)\s*,\s*search\s+(?:for\s+)?(.+?)(?:,\s*and\s+(?:tell\s+me\s+what\s+you\s+find|report\s+status|verify))?$",
                q_lower
            )

        if m_site_search:
            if m_site_search.lastindex and m_site_search.lastindex >= 4 and m_site_search.group(3) and m_site_search.group(4):
                topic = m_site_search.group(3).strip().rstrip(".,?!")
                site_raw = m_site_search.group(4).strip()
            else:
                site_raw = m_site_search.group(1).strip()
                topic = m_site_search.group(2).strip().rstrip(".,?!")

            # Check if topic contains a 3rd clause (e.g. ", and explain the result", "and explain what you find", "and summarize it")
            third_tool = None
            m_followup = re.search(r"(?:,\s*and\s+|\s+and\s+)(?:then\s+)?(explain|summarize|tell\s+me)(?:\s+(?:the\s+result|results?|what\s+you\s+find|it|them|about\s+it))?$", topic, re.IGNORECASE)
            if m_followup:
                verb = m_followup.group(1).lower()
                topic = topic[:m_followup.start()].strip().rstrip(".,?!")
                if "summarize" in verb:
                    third_tool = "summarize_current_page"
                else:
                    third_tool = "explain_current_page"

            site_clean = site_raw.lower()
            if site_clean in KNOWN_WEBSITE_ALIASES:
                target_url = KNOWN_WEBSITE_ALIASES[site_clean]
                origin = target_url
                display_site = site_clean.title()
            elif "." in site_clean:
                target_url = site_clean if site_clean.startswith("http") else f"https://{site_clean}"
                origin = target_url
                display_site = site_clean
            elif site_clean in ("chrome", "browser", "edge", "brave"):
                target_url = "https://www.google.com"
                origin = "https://www.google.com"
                display_site = "Google"
            else:
                return TaskPlan(
                    user_query=q,
                    goal="Clarification required for unknown website",
                    steps=[],
                    status=PlanStatus.FAILED,
                    error_message=f"Which website should I open? '{site_raw}' is not recognized. Please specify a full domain."
                )

            goal = f"Open {display_site} and search for '{topic}'"
            steps = [
                PlanStep(
                    step_id="step_1",
                    tool="open_website",
                    arguments={"url": target_url},
                    reason=f"Open {display_site} in default browser",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    expected_result=f"{display_site} opened in browser",
                    verification_requirement="url_opened"
                ),
                PlanStep(
                    step_id="step_2",
                    tool="read_current_page",
                    arguments={},
                    reason=f"Observe active browser page and discover DOM targets on {display_site}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_1"],
                    expected_result="Active page observed and DOM targets discovered",
                    verification_requirement="page_observed"
                ),
                PlanStep(
                    step_id="step_3",
                    tool="type_browser_field",
                    arguments={
                        "target_binding": {
                            "tab_id": 1,
                            "window_id": 1,
                            "origin": origin,
                            "url_path": "/",
                            "frame_id": 0,
                            "dom_selector": "#search, input[name='search_query'], input[type='search'], #search-input, input[name='search'], input[name='q']",
                            "element_fingerprint": f"sha256:{site_clean}_search_input",
                            "element_tag": "INPUT",
                            "element_role": "searchbox",
                            "is_visible": True,
                            "is_enabled": True,
                            "is_sensitive": False
                        },
                        "text": topic
                    },
                    reason=f"Type search topic '{topic}' into search box",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_2"],
                    expected_result=f"Topic '{topic}' entered and verified",
                    verification_requirement="read_back_verified"
                ),
                PlanStep(
                    step_id="step_4",
                    tool="click_browser_element",
                    arguments={
                        "target_binding": {
                            "tab_id": 1,
                            "window_id": 1,
                            "origin": origin,
                            "url_path": "/",
                            "frame_id": 0,
                            "dom_selector": "#search-icon-legacy, button[type='submit'], #search-btn, button[aria-label='Search'], input[type='submit']",
                            "element_fingerprint": f"sha256:{site_clean}_search_button",
                            "element_tag": "BUTTON",
                            "element_role": "button",
                            "is_visible": True,
                            "is_enabled": True,
                            "is_sensitive": False
                        }
                    },
                    reason="Click search button to execute search",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_3"],
                    expected_result="Search button clicked and search executed",
                    verification_requirement="action_verified"
                ),
                PlanStep(
                    step_id="step_5",
                    tool="read_current_page",
                    arguments={},
                    reason=f"Verify resulting search results for '{topic}'",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_4"],
                    expected_result=f"Search results for '{topic}' verified",
                    verification_requirement="results_verified"
                )
            ]

            if third_tool:
                verb_display = "explain" if third_tool == "explain_current_page" else "summarize"
                goal = f"Open {display_site}, search for '{topic}', and {verb_display} results"
                steps.append(
                    PlanStep(
                        step_id="step_6",
                        tool=third_tool,
                        arguments={},
                        reason=f"{verb_display.title()} resulting search page for '{topic}'",
                        risk_level=RiskLevel.LOW_RISK_ACTION,
                        dependencies=["step_5"],
                        expected_result=f"Explanation/summary for '{topic}' generated",
                        verification_requirement="action_verified"
                    )
                )

            return TaskPlan(user_query=q, goal=goal, steps=steps)

        # Case B: "Type {topic} into search box and click search" / "Search for {topic} and tell me the resulting status"
        m_type_click = re.search(
            r"\btype\s+(?:['\"]([^'\"]+)['\"]|(.+?))\s+into\s+(?:the\s+)?(?:search(?:\s+box|\s+field|\s+input)?|input|field|textbox)\s+and\s+(?:then\s+)?click\s+(?:the\s+)?(?:search(?:\s+button|\s+icon)?|button)\b",
            q_lower
        )
        if not m_type_click:
            m_type_click = re.search(
                r"\bsearch\s+for\s+(?:['\"]([^'\"]+)['\"]|(.+?))\s+and\s+tell\s+me\s+(?:the\s+resulting\s+status|what\s+happened|results?)\b",
                q_lower
            )
        if m_type_click:
            topic = (m_type_click.group(1) or m_type_click.group(2)).strip().rstrip(".,?!")
            goal = f"Enter '{topic}' into search box and execute search"
            steps = [
                PlanStep(
                    step_id="step_1",
                    tool="read_current_page",
                    arguments={},
                    reason="Observe active browser page and discover DOM targets",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    expected_result="Active page observed and DOM targets discovered",
                    verification_requirement="page_observed"
                ),
                PlanStep(
                    step_id="step_2",
                    tool="type_browser_field",
                    arguments={
                        "target_binding": {
                            "tab_id": 1,
                            "window_id": 1,
                            "origin": "https://active.browser.local",
                            "url_path": "/",
                            "frame_id": 0,
                            "dom_selector": "#search, input[name='search_query'], input[type='search'], #search-input",
                            "element_fingerprint": "sha256:active_page_search_input",
                            "element_tag": "INPUT",
                            "element_role": "searchbox",
                            "is_visible": True,
                            "is_enabled": True,
                            "is_sensitive": False
                        },
                        "text": topic
                    },
                    reason=f"Type '{topic}' into search input",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_1"],
                    expected_result=f"Topic '{topic}' entered and verified",
                    verification_requirement="read_back_verified"
                ),
                PlanStep(
                    step_id="step_3",
                    tool="click_browser_element",
                    arguments={
                        "target_binding": {
                            "tab_id": 1,
                            "window_id": 1,
                            "origin": "https://active.browser.local",
                            "url_path": "/",
                            "frame_id": 0,
                            "dom_selector": "#search-icon-legacy, button[type='submit'], #search-btn, button[aria-label='Search']",
                            "element_fingerprint": "sha256:active_page_search_button",
                            "element_tag": "BUTTON",
                            "element_role": "button",
                            "is_visible": True,
                            "is_enabled": True,
                            "is_sensitive": False
                        }
                    },
                    reason="Click search button to submit query",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_2"],
                    expected_result="Search button clicked",
                    verification_requirement="action_verified"
                ),
                PlanStep(
                    step_id="step_4",
                    tool="read_current_page",
                    arguments={},
                    reason=f"Verify resulting status for '{topic}'",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_3"],
                    expected_result=f"Search outcome verified",
                    verification_requirement="results_verified"
                )
            ]
            return TaskPlan(user_query=q, goal=goal, steps=steps)

        # Case C: "Type {topic} into the search box"
        m_single_type = re.search(
            r"\btype\s+(?:['\"]([^'\"]+)['\"]|(.+?))\s+into\s+(?:the\s+)?(?:search(?:\s+box|\s+field|\s+input)?|input|field|textbox)$",
            q_lower
        )
        if m_single_type:
            text_to_type = (m_single_type.group(1) or m_single_type.group(2)).strip().rstrip(".,?!")
            goal = f"Type '{text_to_type}' into search input"
            steps = [
                PlanStep(
                    step_id="step_1",
                    tool="read_current_page",
                    arguments={},
                    reason="Observe active browser page and discover DOM targets",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    expected_result="Active page observed",
                    verification_requirement="page_observed"
                ),
                PlanStep(
                    step_id="step_2",
                    tool="type_browser_field",
                    arguments={
                        "target_binding": {
                            "tab_id": 1,
                            "window_id": 1,
                            "origin": "https://active.browser.local",
                            "url_path": "/",
                            "frame_id": 0,
                            "dom_selector": "#search, input[name='search_query'], input[type='search'], #search-input",
                            "element_fingerprint": "sha256:active_search_input",
                            "element_tag": "INPUT",
                            "element_role": "searchbox",
                            "is_visible": True,
                            "is_enabled": True,
                            "is_sensitive": False
                        },
                        "text": text_to_type
                    },
                    reason=f"Type '{text_to_type}' into search field",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_1"],
                    expected_result=f"Text '{text_to_type}' typed and verified",
                    verification_requirement="read_back_verified"
                )
            ]
            return TaskPlan(user_query=q, goal=goal, steps=steps)

        # Case D: "Click the search button" / "Click the first safe result"
        m_single_click = re.search(
            r"\bclick\s+(?:the\s+)?(?:(search(?:\s+button|\s+icon)?)|(first(?:\s+safe)?\s+result|result(?:\s+link)?)|button|link)\b",
            q_lower
        )
        if m_single_click:
            is_search_btn = bool(m_single_click.group(1) or "search" in q_lower)
            is_first_result = bool(m_single_click.group(2) or "result" in q_lower)
            if is_search_btn:
                sel = "#search-icon-legacy, button[type='submit'], #search-btn, button[aria-label='Search']"
                tag = "BUTTON"
                role = "button"
                desc = "search button"
            elif is_first_result:
                sel = "a.safe-result, #result-link-1, .result-item a, #results a"
                tag = "A"
                role = "link"
                desc = "first safe result link"
            else:
                sel = "button, a"
                tag = "BUTTON"
                role = "button"
                desc = "element"

            goal = f"Click {desc}"
            steps = [
                PlanStep(
                    step_id="step_1",
                    tool="read_current_page",
                    arguments={},
                    reason="Observe active browser page and discover DOM targets",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    expected_result="Active page observed",
                    verification_requirement="page_observed"
                ),
                PlanStep(
                    step_id="step_2",
                    tool="click_browser_element",
                    arguments={
                        "target_binding": {
                            "tab_id": 1,
                            "window_id": 1,
                            "origin": "https://active.browser.local",
                            "url_path": "/",
                            "frame_id": 0,
                            "dom_selector": sel,
                            "element_fingerprint": f"sha256:active_{tag.lower()}_binding",
                            "element_tag": tag,
                            "element_role": role,
                            "is_visible": True,
                            "is_enabled": True,
                            "is_sensitive": False
                        }
                    },
                    reason=f"Click {desc}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_1"],
                    expected_result=f"{desc.title()} clicked and verified",
                    verification_requirement="action_verified"
                )
            ]
            return TaskPlan(user_query=q, goal=goal, steps=steps)

        # Case E: "Select option A in the dropdown"
        m_single_select = re.search(
            r"\bselect\s+(?:option\s+)?(?:['\"]([^'\"]+)['\"]|(.+?))\s+(?:in|from)\s+(?:the\s+)?(?:dropdown|select)\b|\bselect\s+option\s+([a-zA-Z0-9_\s-]+)$",
            q_lower
        )
        if m_single_select:
            opt_val = (m_single_select.group(1) or m_single_select.group(2) or m_single_select.group(3)).strip().rstrip(".,?!")
            goal = f"Select '{opt_val}' in dropdown"
            steps = [
                PlanStep(
                    step_id="step_1",
                    tool="read_current_page",
                    arguments={},
                    reason="Observe active browser page and discover DOM targets",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    expected_result="Active page observed",
                    verification_requirement="page_observed"
                ),
                PlanStep(
                    step_id="step_2",
                    tool="select_browser_option",
                    arguments={
                        "target_binding": {
                            "tab_id": 1,
                            "window_id": 1,
                            "origin": "https://active.browser.local",
                            "url_path": "/",
                            "frame_id": 0,
                            "dom_selector": "select#topic-select, select",
                            "element_fingerprint": "sha256:active_select_binding",
                            "element_tag": "SELECT",
                            "element_role": "combobox",
                            "is_visible": True,
                            "is_enabled": True,
                            "is_sensitive": False
                        },
                        "option_value": opt_val
                    },
                    reason=f"Select option '{opt_val}' in dropdown",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_1"],
                    expected_result=f"Option '{opt_val}' selected and verified",
                    verification_requirement="read_back_verified"
                )
            ]
            return TaskPlan(user_query=q, goal=goal, steps=steps)

        # -------------------------------------------------------------
        # Pattern E: "Prepare a workspace for today's {topic} lecture." /
        #            "Prepare everything I need for today's {topic} lecture."
        # -------------------------------------------------------------
        m_prep = re.search(
            r"\bprepare\s+(?:everything\s+i\s+need\s+for|a\s+workspace\s+for|materials\s+for|everything\s+for)\s+(?:today'?s\s+)?(.+?)(?:\s+(?:lecture|class|session))?\s*[\.\?]?$",
            q_lower
        )
        if m_prep:
            raw_topic = m_prep.group(1).strip()
            # Clean topic title
            topic = re.sub(r"\b(today'?s|lecture|class|notes?|the|my)\b", "", raw_topic, flags=re.IGNORECASE).strip()
            topic = topic.title() or "Operating Systems"
            
            goal = f"Prepare workspace and lecture notes for {topic}"
            steps = [
                PlanStep(
                    step_id="step_1",
                    tool="create_folder",
                    arguments={"folder_name": f"{topic} Lecture", "parent": "workspaces"},
                    reason=f"Create dedicated workspace directory for {topic}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    expected_result=f"Folder '{topic} Lecture' created",
                    verification_requirement="folder_exists"
                ),
                PlanStep(
                    step_id="step_2",
                    tool="create_note",
                    arguments={
                        "title": f"{topic} Notes",
                        "content": f"# {topic} Lecture Notes\nDate: {time.strftime('%Y-%m-%d')}\nTopic: {topic}\n\n## Key Concepts\n- \n\n## Questions\n- \n",
                        "extension": "md"
                    },
                    reason=f"Initialize structured lecture notes template for {topic}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_1"],
                    expected_result=f"Note '{topic} Notes.md' created",
                    verification_requirement="file_persistence"
                ),
                PlanStep(
                    step_id="step_3",
                    tool="open_folder",
                    arguments={"folder_name": f"{topic} Lecture"},
                    reason=f"Open the newly prepared workspace in Explorer",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=["step_1"],
                    expected_result=f"Folder '{topic} Lecture' opened",
                    verification_requirement="folder_open"
                )
            ]
            return TaskPlan(user_query=q, goal=goal, steps=steps)

        # -------------------------------------------------------------
        # Pattern E2: "Prepare my {topic} lecture notes." / "Prepare {topic} notes."
        # -------------------------------------------------------------
        m_prep_notes = re.search(
            r"\bprepare\s+(?:my\s+)?(?:today'?s\s+)?(.+?)\s+(?:lecture\s+)?notes?\s*[\.\?]?$",
            q_lower
        )
        if m_prep_notes:
            raw_topic = m_prep_notes.group(1).strip()
            topic = re.sub(r"\b(today'?s|the|my)\b", "", raw_topic, flags=re.IGNORECASE).strip()
            topic = topic.title() or "Operating Systems"
            goal = f"Prepare lecture notes for {topic}"
            steps = [
                PlanStep(
                    step_id="step_1",
                    tool="create_note",
                    arguments={
                        "title": f"{topic} Notes",
                        "content": f"# {topic} Lecture Notes\nDate: {time.strftime('%Y-%m-%d')}\nTopic: {topic}\n\n## Key Concepts\n- \n\n## Questions\n- \n",
                        "extension": "md"
                    },
                    reason=f"Prepare lecture notes for {topic}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    expected_result=f"Note '{topic} Notes.md' prepared",
                    verification_requirement="file_persistence"
                )
            ]
            return TaskPlan(user_query=q, goal=goal, steps=steps)

        # -------------------------------------------------------------
        # Pattern A: "Create a workspace called {name} and open it."
        # -------------------------------------------------------------
        m_ws = re.search(
            r"\bcreate\s+(?:a\s+)?(?:new\s+)?(?:workspace\s+(?:called|named)\s+['\"]?([^'\"]+?)['\"]?|['\"]?([^'\"]+?)['\"]?\s+workspace)\s+and\s+(?:open|view|explore)\s+it\b",
            q_lower
        )
        if m_ws:
            ws_name = (m_ws.group(1) or m_ws.group(2)).strip()
            return TaskPlan(
                user_query=q,
                goal=f"Create and open workspace '{ws_name}'",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool="create_folder",
                        arguments={"folder_name": ws_name, "parent": "workspaces"},
                        reason=f"Create workspace directory '{ws_name}'",
                        expected_result=f"Folder '{ws_name}' created",
                        verification_requirement="folder_exists"
                    ),
                    PlanStep(
                        step_id="step_2",
                        tool="open_folder",
                        arguments={"folder_name": ws_name},
                        reason=f"Open workspace '{ws_name}' in Explorer",
                        dependencies=["step_1"],
                        expected_result=f"Folder '{ws_name}' opened",
                        verification_requirement="folder_open"
                    )
                ]
            )

        # -------------------------------------------------------------
        # Pattern F: "Create a folder called {fld1}, create a notes folder inside it, and open it."
        # -------------------------------------------------------------
        m_nested = re.search(
            r"\bcreate\s+(?:a\s+)?folder\s+(?:called|named)\s+['\"]?([^'\"]+?)['\"]?,\s+create\s+(?:a\s+)?(?:notes\s+folder|subfolder)\s+inside\s+it,\s+and\s+open\s+it\b",
            q_lower
        )
        if m_nested:
            parent_fld = m_nested.group(1).strip()
            return TaskPlan(
                user_query=q,
                goal=f"Create '{parent_fld}' with notes subfolder and open it",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool="create_folder",
                        arguments={"folder_name": parent_fld, "parent": "workspaces"},
                        reason=f"Create parent folder '{parent_fld}'",
                        expected_result=f"Folder '{parent_fld}' created"
                    ),
                    PlanStep(
                        step_id="step_2",
                        tool="create_folder",
                        arguments={"folder_name": f"{parent_fld}/notes"},
                        reason=f"Create 'notes' subfolder inside '{parent_fld}'",
                        dependencies=["step_1"],
                        expected_result=f"Subfolder 'notes' created"
                    ),
                    PlanStep(
                        step_id="step_3",
                        tool="open_folder",
                        arguments={"folder_name": parent_fld},
                        reason=f"Open folder '{parent_fld}' in Explorer",
                        dependencies=["step_1"],
                        expected_result=f"Folder '{parent_fld}' opened"
                    )
                ]
            )

        # -------------------------------------------------------------
        # Pattern B: "Create a note called {name} and open the notes folder."
        # -------------------------------------------------------------
        m_note = re.search(
            r"\bcreate\s+(?:a\s+)?note\s+(?:called|named)\s+['\"]?([^'\"]+?)['\"]?\s+and\s+(?:open|show)\s+(?:the\s+)?notes\s+folder\b",
            q_lower
        )
        if m_note:
            note_title = m_note.group(1).strip()
            return TaskPlan(
                user_query=q,
                goal=f"Create note '{note_title}' and open notes folder",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool="create_note",
                        arguments={
                            "title": note_title,
                            "content": f"# {note_title.title()}\n\n",
                            "extension": "md"
                        },
                        reason=f"Create note '{note_title}.md'",
                        expected_result=f"Note '{note_title}.md' created"
                    ),
                    PlanStep(
                        step_id="step_2",
                        tool="open_folder",
                        arguments={"folder_name": "notes"},
                        reason="Open SmartScribe notes directory in Explorer",
                        dependencies=["step_1"],
                        expected_result="Notes folder opened"
                    )
                ]
            )

        # -------------------------------------------------------------
        # Pattern C: "Open my latest lecture recording and then open my lecture notes."
        # -------------------------------------------------------------
        m_rec_notes = re.search(
            r"\bopen\s+(?:my\s+)?(latest\s+(?:lecture\s+)?recording)\s+and\s+(?:then\s+)?(?:open\s+)?(?:my\s+)?(.+?\s+notes?)\b",
            q_lower
        )
        if m_rec_notes:
            rec_desc = m_rec_notes.group(1).strip()
            notes_desc = m_rec_notes.group(2).strip()
            return TaskPlan(
                user_query=q,
                goal=f"Open {rec_desc} and {notes_desc}",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool="open_file",
                        arguments={"query": rec_desc},
                        reason=f"Resolve and open {rec_desc}",
                        expected_result="Audio recording opened in player"
                    ),
                    PlanStep(
                        step_id="step_2",
                        tool="open_file",
                        arguments={"query": notes_desc},
                        reason=f"Resolve and open {notes_desc}",
                        dependencies=["step_1"],
                        expected_result="Lecture notes opened in editor"
                    )
                ]
            )

        # -------------------------------------------------------------
        # Pattern D: "Open Chrome and open my university website/portal."
        # -------------------------------------------------------------
        m_app_web = re.search(
            r"\bopen\s+(chrome|browser|edge|brave)\s+and\s+(?:open|browse\s+to|navigate\s+to)\s+(?:my\s+)?(.+?(?:website|portal|page|canvas|blackboard|moodle))\b",
            q_lower
        )
        if m_app_web:
            app_name = m_app_web.group(1).strip()
            portal_desc = m_app_web.group(2).strip()
            portal_url = "https://portal.university.edu" if "portal" in portal_desc else "https://canvas.instructure.com"
            return TaskPlan(
                user_query=q,
                goal=f"Launch {app_name.title()} and open {portal_desc}",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool="open_application",
                        arguments={"application": app_name},
                        reason=f"Launch approved web browser {app_name.title()}",
                        expected_result=f"{app_name.title()} running"
                    ),
                    PlanStep(
                        step_id="step_2",
                        tool="open_website",
                        arguments={"url": portal_url},
                        reason=f"Open {portal_desc} URL in browser",
                        dependencies=["step_1"],
                        expected_result=f"Webpage opened: {portal_url}"
                    )
                ]
            )

        # -------------------------------------------------------------
        # Pattern G: "Open Chrome, open the university portal, and then open my latest lecture notes."
        # -------------------------------------------------------------
        m_three_step = re.search(
            r"\bopen\s+(chrome|browser|edge)\s*,\s*(?:open\s+)?(?:the\s+)?(.+?(?:portal|website))\s*,\s*and\s+(?:then\s+)?open\s+(?:my\s+)?(.+?\s+notes?)\b",
            q_lower
        )
        if m_three_step:
            app_n = m_three_step.group(1).strip()
            port_n = m_three_step.group(2).strip()
            note_n = m_three_step.group(3).strip()
            return TaskPlan(
                user_query=q,
                goal=f"Launch browser, open {port_n}, and open {note_n}",
                steps=[
                    PlanStep(
                        step_id="step_1",
                        tool="open_application",
                        arguments={"application": app_n},
                        reason=f"Launch browser {app_n}"
                    ),
                    PlanStep(
                        step_id="step_2",
                        tool="open_website",
                        arguments={"url": "https://portal.university.edu"},
                        reason=f"Open {port_n}",
                        dependencies=["step_1"]
                    ),
                    PlanStep(
                        step_id="step_3",
                        tool="open_file",
                        arguments={"query": note_n},
                        reason=f"Open {note_n}",
                        dependencies=["step_1"]
                    )
                ]
            )

        # -------------------------------------------------------------
        # Pattern G: General Multi-Action Sequence Decomposition (Hardened)
        # Uses decompose_clauses() to respect content 'and' vs action 'and'
        # -------------------------------------------------------------
        # Pre-compile app clause pattern using shared APP_NAMES_PATTERN
        _APP_CLAUSE_RE = re.compile(
            r"\b(?:open|launch|start|go\s+to|switch\s+to|take\s+me\s+to)\s+(?:the\s+)?(?:application\s+|app\s+)?("
            + APP_NAMES_PATTERN + r")\b",
            re.IGNORECASE
        )
        # 4c. Browser action signals - prevent misclassification as open_file
        _BROWSER_SIGNAL_RE = re.compile(
            r"\b(first\s+(?:result|video|link)|top\s+result|click\s+(?:the\s+)?(?:first|top)|play\s+(?:the\s+)?(?:first|top)\s+(?:result|video|link)|next\s+video|pause\s+(?:the\s+)?video|search\s+result)\b",
            re.IGNORECASE
        )

        clauses = self.decompose_clauses(q)
        extracted_steps = []
        last_created_folder = None
        step_idx = 1

        for c in clauses:
            c_clean = c.strip()
            if not c_clean:
                continue

            # 0a. start recording
            m_rec_start = re.search(r"\b(?:start|begin)\s+(?:the\s+)?(?:audio\s+|microphone\s+)?recording\b", c_clean, re.IGNORECASE)
            if m_rec_start:
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="start_recording",
                    arguments={},
                    reason="Start microphone recording",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=dep,
                    expected_result="Microphone recording started"
                ))
                step_idx += 1
                continue

            # 0b. stop recording
            m_rec_stop = re.search(r"\b(?:stop|finish|end)\s+(?:the\s+)?(?:audio\s+|microphone\s+)?recording\b", c_clean, re.IGNORECASE)
            if m_rec_stop:
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="stop_recording",
                    arguments={},
                    reason="Stop microphone recording",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=dep,
                    expected_result="Microphone recording stopped and saved"
                ))
                step_idx += 1
                continue

            # 0c. summarize or explain action
            m_sum = re.search(r"\b(?:summarize|explain)\s+(?:it|this|that|the\s+result|results?|the\s+page|the\s+lecture|the\s+session|what\s+you\s+find)?\b", c_clean, re.IGNORECASE)
            if m_sum:
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                prev_tool = extracted_steps[-1].tool if extracted_steps else ""
                if prev_tool in ("open_website", "read_current_page"):
                    tool_to_use = "explain_current_page" if "explain" in c_clean.lower() else "summarize_current_page"
                else:
                    tool_to_use = "summarize_current_session"
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool=tool_to_use,
                    arguments={},
                    reason="Summarize or explain referenced target",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=dep,
                    expected_result="Summary or explanation generated"
                ))
                step_idx += 1
                continue

            # Check if this clause is an open/launch action vs create action
            is_open_clause = bool(re.match(r"^(?:open|launch|start|explore|view|browse|navigate|play)\b", c_clean, re.IGNORECASE))

            # 1. create folder
            if not is_open_clause:
                m_f = re.search(r"\b(?:create|make)\s+(?:a\s+|the\s+|another\s+)?(?:new\s+)?(?:folder|workspace)\s+(?:called\s+|named\s+)?(?:['\"]([^'\"]+)['\"]|(.+))", c_clean, re.IGNORECASE)
                if m_f:
                    raw_name = (m_f.group(1) or m_f.group(2)).strip()
                    raw_name = re.sub(r"\s+in\s+(workspaces?|notes?)$", "", raw_name, flags=re.IGNORECASE).strip().strip("'\"")
                    f_name = raw_name.rstrip(".,;").strip()
                    last_created_folder = f_name
                    extracted_steps.append(PlanStep(
                        step_id=f"step_{step_idx}",
                        tool="create_folder",
                        arguments={"folder_name": f_name, "parent": "workspaces"},
                        reason=f"Create folder {f_name}",
                        risk_level=RiskLevel.LOW_RISK_ACTION,
                        expected_result=f"Folder '{f_name}' created",
                        verification_requirement="folder_exists"
                    ))
                    step_idx += 1
                    continue

            # 2. create note (guarded against open actions)
            if not is_open_clause:
                m_n = re.search(r"\b(?:create\s+(?:a\s+|the\s+|another\s+)?(?:new\s+)?)?note\s+(?:called\s+|named\s+|titled\s+)?(.+)", c_clean, re.IGNORECASE)
                if m_n:
                    note_body = m_n.group(1).strip()
                    m_content = re.search(r"\s+(?:with\s+(?:content\s+)?|saying\s+|containing\s+)(.+)$", note_body, re.IGNORECASE)
                    if m_content:
                        n_title = note_body[:m_content.start()].strip().strip("'\"").rstrip(".,;")
                        n_content = m_content.group(1).strip().strip("'\"")
                    else:
                        n_title = note_body.strip().strip("'\"").rstrip(".,;")
                        n_content = f"# {n_title.title()}\n\n"
                    dep = [extracted_steps[-1].step_id] if extracted_steps else []
                    extracted_steps.append(PlanStep(
                        step_id=f"step_{step_idx}",
                        tool="create_note",
                        arguments={"title": n_title, "content": n_content, "extension": "txt"},
                        reason=f"Create note {n_title}",
                        risk_level=RiskLevel.LOW_RISK_ACTION,
                        dependencies=dep,
                        expected_result=f"Note '{n_title}.txt' created",
                        verification_requirement="file_persistence"
                    ))
                    step_idx += 1
                    continue

            # 3. open folder / open it
            m_of_it = re.search(r"\b(?:open|show|view|explore)\s+(?:it|the\s+folder|that|this)\b", c_clean, re.IGNORECASE)
            if m_of_it:
                of_name = last_created_folder or "workspaces"
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="open_folder",
                    arguments={"folder_name": of_name},
                    reason=f"Open folder {of_name}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=dep,
                    expected_result=f"Folder '{of_name}' opened",
                    verification_requirement="folder_open"
                ))
                step_idx += 1
                continue

            m_of = re.search(r"\b(?:open|show|view|explore)\s+(?:the\s+|my\s+)?folder\s+(?:called\s+|named\s+)?(?:['\"]([^'\"]+)['\"]|(.+))", c_clean, re.IGNORECASE)
            if m_of:
                raw_of = (m_of.group(1) or m_of.group(2)).strip().strip("'\"").rstrip(".,;")
                if raw_of.lower() in ("it", "the folder", "folder", "that", "this"):
                    of_name = last_created_folder or "workspaces"
                else:
                    of_name = raw_of
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="open_folder",
                    arguments={"folder_name": of_name},
                    reason=f"Open folder {of_name}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=dep,
                    expected_result=f"Folder '{of_name}' opened",
                    verification_requirement="folder_open"
                ))
                step_idx += 1
                continue

            # 4. open application - uses shared APP_NAMES_PATTERN for consistency
            m_app = _APP_CLAUSE_RE.search(c_clean)
            if m_app:
                raw_app_name = m_app.group(1).lower().strip()
                app_n = APP_MAP.get(raw_app_name, raw_app_name)
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="open_application",
                    arguments={"application": app_n},
                    reason=f"Open application {app_n}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    expected_result=f"Application '{app_n}' launched"
                ))
                step_idx += 1
                continue

            # 4d. type/write text into foreground application
            m_type = re.search(
                r"\b(?:write|type|enter|input)\s+(?:['\"]([^'\"]+)['\"]|(.+))",
                c_clean, re.IGNORECASE
            )
            if m_type:
                text_to_type = (m_type.group(1) or m_type.group(2)).strip().rstrip('.,;')
                # Determine target window from prior open_application step
                target_win = ""
                for prev_step in reversed(extracted_steps):
                    if prev_step.tool == "open_application":
                        target_win = prev_step.arguments.get("application", "").title()
                        break
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="type_text",
                    arguments={
                        "text": text_to_type,
                        "target_window_title": target_win
                    },
                    reason=f"Type '{text_to_type}' into {target_win or 'foreground window'}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=dep,
                    expected_result=f"Text '{text_to_type}' typed into {target_win or 'foreground window'}"
                ))
                step_idx += 1
                continue

            # 4b. open website (alias or domain/URL)
            KNOWN_TLDS = (".com", ".org", ".net", ".edu", ".gov", ".io", ".co", ".ai", ".dev", ".app", ".info", ".me")
            FILE_EXTS = (".md", ".txt", ".pdf", ".doc", ".docx", ".wav", ".mp3", ".mp4", ".py", ".json", ".csv", ".xlsx", ".pptx", ".log")
            m_site_clause = re.search(r"\b(?:open|launch|go\s+to|visit|browse(?:\s+to)?)\s+(?:the\s+)?([a-zA-Z0-9_.-]+)(?:\s+website|\s+site)?$", c_clean, re.IGNORECASE)
            if m_site_clause:
                site_k = m_site_clause.group(1).lower().strip()
                if site_k in KNOWN_WEBSITE_ALIASES:
                    url_t = KNOWN_WEBSITE_ALIASES[site_k]
                    dep = [extracted_steps[-1].step_id] if extracted_steps else []
                    extracted_steps.append(PlanStep(
                        step_id=f"step_{step_idx}",
                        tool="open_website",
                        arguments={"url": url_t},
                        reason=f"Open website {site_k.title()}",
                        risk_level=RiskLevel.LOW_RISK_ACTION,
                        dependencies=dep,
                        expected_result=f"Website '{site_k.title()}' opened",
                        verification_requirement="url_opened"
                    ))
                    step_idx += 1
                    continue
                elif (site_k.startswith(("http://", "https://")) or any(site_k.endswith(tld) for tld in KNOWN_TLDS)) and not any(site_k.endswith(ext) for ext in FILE_EXTS):
                    url_t = site_k if site_k.startswith("http") else f"https://{site_k}"
                    dep = [extracted_steps[-1].step_id] if extracted_steps else []
                    extracted_steps.append(PlanStep(
                        step_id=f"step_{step_idx}",
                        tool="open_website",
                        arguments={"url": url_t},
                        reason=f"Open website {site_k}",
                        risk_level=RiskLevel.LOW_RISK_ACTION,
                        dependencies=dep,
                        expected_result=f"Website '{site_k}' opened",
                        verification_requirement="url_opened"
                    ))
                    step_idx += 1
                    continue
            if _BROWSER_SIGNAL_RE.search(c_clean):
                # Browser action signal detected — use CSS selector for live DOM resolution
                # This binding does NOT use a hardcoded fingerprint; the BrowserDriver
                # performs live DOM lookup using dom_selector as the resolution key.
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                # Determine selector from signal type
                if re.search(r"\b(pause|stop)\s+(?:the\s+)?video\b", c_clean, re.IGNORECASE):
                    css_sel = ".ytp-play-button, [data-title-no-tooltip='Pause'], button[aria-label*='Pause']"
                    action_desc = "pause video"
                elif re.search(r"\b(next\s+video|play\s+(?:the\s+)?next)\b", c_clean, re.IGNORECASE):
                    css_sel = ".ytp-next-button, [aria-label*='Next']"
                    action_desc = "next video"
                else:
                    # first result / first video
                    css_sel = "ytd-video-renderer a#video-title, a.ytd-video-renderer, #results ytd-video-renderer a"
                    action_desc = "first search result"
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="click_browser_element",
                    arguments={
                        "target_binding": {
                            "tab_id": -1,            # -1 = active tab
                            "window_id": -1,
                            "origin": "",            # resolved at runtime
                            "url_path": "/",
                            "frame_id": 0,
                            "dom_selector": css_sel,
                            "element_fingerprint": "",   # empty = live DOM resolution required
                            "element_tag": "A",
                            "element_role": "link",
                            "is_visible": True,
                            "is_enabled": True,
                            "is_sensitive": False
                        }
                    },
                    reason=f"Browser action: {action_desc} via CSS selector",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=dep,
                    expected_result=f"Browser {action_desc} action completed"
                ))
                step_idx += 1
                continue

            # 5. open file
            m_file = re.search(r"\b(?:open|launch|play|show|view)\s+(?:the\s+)?(?:file\s+|recording\s+|pdf\s+|audio\s+|document\s+)?(?:called\s+|named\s+)?(?:['\"]([^'\"]+)['\"]|(.+))", c_clean, re.IGNORECASE)
            if m_file:
                fn = (m_file.group(1) or m_file.group(2)).strip().strip("'\"").rstrip(".,;")
                if fn.lower() in ("it", "that", "this", "something", "site", "website"):
                    continue
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="open_file",
                    arguments={"query": fn, "file_name": fn, "target": fn, "requested_target": fn},
                    reason=f"Open file {fn}",
                    risk_level=RiskLevel.LOW_RISK_ACTION,
                    dependencies=dep,
                    expected_result=f"File '{fn}' opened"
                ))
                step_idx += 1
                continue

        if len(extracted_steps) >= 1:
            return TaskPlan(
                user_query=q,
                goal=f"Execute task ({len(extracted_steps)} step{'s' if len(extracted_steps) > 1 else ''})",
                steps=extracted_steps
            )

        return None

    def _plan_with_qwen(self, q: str, ctx: PlanContext) -> Optional[TaskPlan]:
        """
        Uses local Qwen2.5-3B to interpret non-deterministic compound requests.
        Enforces strict JSON schema output and validates every single step.
        """
        # Guard resource manager
        can_run, reason = self.rm.can_start_llm()
        if not can_run:
            return None

        try:
            from backend.ai_engine import AIEngine
            ai = AIEngine()
            
            allowed_tools = [
                "create_folder", "create_note", "open_folder", "open_file",
                "open_application", "open_website", "get_system_status",
                "get_process_summary", "get_active_window"
            ]

            prompt = f"""<|im_start|>system
You are the SmartScribe Task Planner. Convert the user's request into a structured JSON plan using ONLY these allowed tools:
{json.dumps(allowed_tools)}

RULES:
1. Output JSON ONLY. Zero conversational filler.
2. Every step MUST use one of the allowed tools.
3. Every step has step_id ("step_1", "step_2"), tool, arguments, reason, dependencies (list of prior step_ids).
4. Do NOT hallucinate shell, powershell, or unapproved tools.

SCHEMA:
{{
  "goal": "brief description of plan",
  "steps": [
    {{
      "step_id": "step_1",
      "tool": "tool_name",
      "arguments": {{}},
      "reason": "why this step is needed",
      "dependencies": []
    }}
  ]
}}
<|im_end|>
<|im_start|>user
{q}
<|im_end|>
<|im_start|>assistant
"""
            raw_out = ai.generate(prompt, max_tokens=350, temperature=0.1)
            if not raw_out:
                return None

            # Extract JSON block
            m_json = re.search(r"\{[\s\S]*\}", raw_out)
            if not m_json:
                return None

            parsed = json.loads(m_json.group(0))
            raw_steps = parsed.get("steps", [])
            if not raw_steps:
                return None

            plan_steps = []
            for s in raw_steps:
                tool_name = s.get("tool")
                if tool_name not in allowed_tools:
                    return None  # Reject plan with unapproved tool immediately
                plan_steps.append(PlanStep(
                    step_id=s.get("step_id", f"step_{len(plan_steps)+1}"),
                    tool=tool_name,
                    arguments=s.get("arguments", {}),
                    reason=s.get("reason", ""),
                    dependencies=s.get("dependencies", [])
                ))

            return TaskPlan(
                user_query=q,
                goal=parsed.get("goal", "Multi-step plan"),
                steps=plan_steps
            )
        except Exception:
            return None

    def create_single_step_plan(self, tool_name: str, arguments: Dict[str, Any], query: str) -> TaskPlan:
        """Wraps a single tool intent into a valid TaskPlan."""
        tool_def = self.registry.get(tool_name)
        risk = tool_def.risk_level if tool_def else RiskLevel.LOW_RISK_ACTION
        step = PlanStep(
            step_id="step_1",
            tool=tool_name,
            arguments=arguments,
            reason=f"Execute {tool_name}",
            risk_level=risk,
            expected_result=f"Action '{tool_name}' completed"
        )
        return TaskPlan(
            user_query=query,
            goal=f"Execute {tool_name}",
            steps=[step],
            requires_approval=(risk in (RiskLevel.SENSITIVE, RiskLevel.DESTRUCTIVE))
        )

    def _plan_followup(self, q: str, pronoun_res: Dict[str, Any], ctx: PlanContext) -> Optional[TaskPlan]:
        """
        Synthesizes a continuation TaskPlan by resolving referent pronouns
        against short-term task session memory.
        """
        if ctx.context_snapshot is None:
            ctx.context_snapshot = collect_context_snapshot(session_memory=ctx.session_memory)

        q_lower = q.lower()
        target_name = pronoun_res.get("resolved_value") or ""
        pronoun = pronoun_res.get("pronoun") or "it"

        # 1. "Put a notes folder inside it" / "Create a notes folder inside it"
        m_inside = re.search(
            r"\b(?:create|make|put|add)\s+(?:a\s+|the\s+)?(?:new\s+)?(?:folder\s+(?:called\s+|named\s+)?['\"]?([^'\"]+?)['\"]?|(notes\s+folder)|subfolder)\s+(?:inside|in)\s+it\b",
            q_lower
        )
        if m_inside or "inside it" in q_lower or "in it" in q_lower:
            subfolder = "notes"
            if m_inside and m_inside.group(1):
                subfolder = m_inside.group(1).strip()
            full_fld = f"{target_name}/{subfolder}"
            goal = f"Create '{subfolder}' folder inside '{target_name}'"
            step = PlanStep(
                step_id="step_1",
                tool="create_folder",
                arguments={"folder_name": full_fld, "parent": "workspaces"},
                reason=f"Create '{subfolder}' subfolder inside '{target_name}'",
                expected_result=f"Subfolder '{full_fld}' created",
                verification_requirement="folder_exists"
            )
            plan = TaskPlan(
                user_query=q,
                goal=goal,
                steps=[step],
                context_used=[f"Target folder '{target_name}' resolved from prior task"],
                context_decisions=[
                    ContextDecision(
                        observation=f"Resolved pronoun '{pronoun}' to prior folder '{target_name}'.",
                        confidence="HIGH",
                        effect_on_plan="resolve_target_from_session",
                        source="SessionMemory",
                        details={"target_name": target_name, "subfolder": subfolder}
                    ).to_dict()
                ],
                context_snapshot=ctx.context_snapshot.to_dict() if ctx.context_snapshot else None
            )
            return plan

        # 2. "Create notes for it" / "Now create notes for it"
        m_notes_for = re.search(r"\b(?:create|make|write|take)\s+(?:a\s+)?notes?\s+(?:for|about)\s+it\b", q_lower)
        if m_notes_for or "notes for it" in q_lower or "note for it" in q_lower:
            topic = target_name.replace(".wav", "").replace(".mp4", "").replace(".pdf", "").replace(".txt", "").replace(".md", "")
            topic = re.sub(r"^(?:recording_?|lecture_?)", "", topic, flags=re.IGNORECASE).strip()
            topic = topic.replace("_", " ").title() or "Lecture"
            title = f"{topic} Notes"
            step = PlanStep(
                step_id="step_1",
                tool="create_note",
                arguments={"title": title, "content": f"# {title}\n\n", "extension": "md"},
                reason=f"Create note '{title}' referencing '{target_name}'",
                expected_result=f"Note '{title}.md' created",
                verification_requirement="file_persistence"
            )
            plan = TaskPlan(
                user_query=q,
                goal=f"Create notes for '{target_name}'",
                steps=[step],
                context_used=[f"Target '{target_name}' resolved from prior task"],
                context_decisions=[
                    ContextDecision(
                        observation=f"Resolved pronoun '{pronoun}' to prior target '{target_name}'.",
                        confidence="HIGH",
                        effect_on_plan="resolve_target_from_session",
                        source="SessionMemory",
                        details={"target_name": target_name, "title": title}
                    ).to_dict()
                ],
                context_snapshot=ctx.context_snapshot.to_dict() if ctx.context_snapshot else None
            )
            return plan

        # 3. "Open it" / "Show it"
        m_open_it = re.search(r"\b(?:open|show|view|explore)\s+it\b", q_lower)
        if m_open_it:
            art_type = pronoun_res.get("target", {}).get("type", "folder")
            tool_name = "open_folder" if art_type == "folder" else "open_file"
            if art_type == "folder":
                step_args = {"folder_name": target_name}
            else:
                step_args = {"target": target_name, "file_name": target_name, "requested_target": target_name}
            step = PlanStep(
                step_id="step_1",
                tool=tool_name,
                arguments=step_args,
                reason=f"Open {art_type} '{target_name}'",
                expected_result=f"{art_type.title()} '{target_name}' opened",
                verification_requirement="folder_open" if art_type == "folder" else "exact_target_match_check"
            )
            plan = TaskPlan(
                user_query=q,
                goal=f"Open '{target_name}'",
                steps=[step],
                context_used=[f"Target '{target_name}' resolved from prior task"],
                context_decisions=[
                    ContextDecision(
                        observation=f"Resolved pronoun '{pronoun}' to prior {art_type} '{target_name}'.",
                        confidence="HIGH",
                        effect_on_plan="resolve_target_from_session",
                        source="SessionMemory",
                        details={"target_name": target_name, "type": art_type}
                    ).to_dict()
                ],
                context_snapshot=ctx.context_snapshot.to_dict() if ctx.context_snapshot else None
            )
            return plan

        return None

    def _adapt_plan_with_context(self, plan: TaskPlan, ctx: PlanContext) -> TaskPlan:
        """
        Inspects context snapshot and adapts plan to reuse existing artifacts,
        avoid redundant launches, and provide structured explanations for plan diffs.
        """
        if not plan or not plan.steps:
            return plan

        if ctx.context_snapshot is None and any(s.tool in ("create_folder", "create_note", "open_file") for s in plan.steps):
            ctx.context_snapshot = collect_context_snapshot(session_memory=ctx.session_memory)

        snapshot: Optional[ContextSnapshot] = ctx.context_snapshot
        if not snapshot:
            return plan

        q_lower = plan.user_query.lower()

        # Rule 1: Check explicit "new" / "another"
        has_explicit_new_folder = bool(re.search(r"\b(?:create|make)\s+(?:another|a\s+new)\s+folder\b", q_lower))
        has_explicit_new_note = bool(re.search(r"\b(?:create|make|write)\s+(?:another|a\s+new|new)\s+note\b", q_lower))
        has_explicit_another_note = bool(re.search(r"\b(?:create|make|write)\s+another\s+note\b", q_lower))

        # Check existing workspaces to adapt folder creation
        if not has_explicit_new_folder:
            adapted_steps = []
            steps_changed = False
            original_steps_dict = [s.to_dict() for s in plan.steps]

            for s in plan.steps:
                if s.tool == "create_folder":
                    f_name = s.arguments.get("folder_name", "")
                    # Match exact or case-insensitive or base stem
                    existing_match = None
                    norm_fn = f_name.lower().strip()
                    norm_fn_base = re.sub(r"\s+(?:lecture|class|workspace|project|session)$", "", norm_fn)
                    for ew in snapshot.existing_workspaces:
                        norm_ew = ew.lower().strip()
                        norm_ew_base = re.sub(r"\s+(?:lecture|class|workspace|project|session)$", "", norm_ew)
                        if norm_ew == norm_fn or norm_ew_base == norm_fn_base or norm_ew == norm_fn_base or norm_ew_base == norm_fn:
                            existing_match = ew
                            break

                    if existing_match:
                        steps_changed = True
                        obs = f"Workspace '{existing_match}' already exists."
                        dec = ContextDecision(
                            observation=obs,
                            confidence="HIGH",
                            effect_on_plan="reuse_existing_workspace",
                            source="DesktopContext.filesystem",
                            details={"existing_workspace": existing_match}
                        )
                        plan.context_decisions.append(dec.to_dict())
                        plan.context_used.append(f"{existing_match} workspace exists")

                        if len(plan.steps) > 2:
                            # 3+ step workflow (e.g. create_folder, create_note, open_folder)
                            # Omit create_folder entirely; subsequent steps proceed
                            continue
                        elif len(plan.steps) == 2:
                            # 2 step workflow (create_folder + open_folder)
                            # Convert into single step: open existing workspace with reuse flag
                            continue
                        else:
                            # Single step create_folder -> convert to open_folder with reused=True
                            s.tool = "open_folder"
                            s.arguments = {"folder_name": existing_match}
                            s.reason = f"Workspace '{existing_match}' already exists; opening existing workspace."
                            s.description = f"Open existing workspace '{existing_match}'"
                            s.reused = True
                            s.context_observation = obs
                            adapted_steps.append(s)
                            continue

                elif s.tool == "open_folder":
                    # If this open_folder was following an omitted create_folder, update target if needed
                    f_arg = s.arguments.get("folder_name", "")
                    norm_fa = f_arg.lower().strip()
                    norm_fa_base = re.sub(r"\s+(?:lecture|class|workspace|project|session)$", "", norm_fa)
                    for ew in snapshot.existing_workspaces:
                        norm_ew = ew.lower().strip()
                        norm_ew_base = re.sub(r"\s+(?:lecture|class|workspace|project|session)$", "", norm_ew)
                        if norm_ew == norm_fa or norm_ew_base == norm_fa_base or norm_ew == norm_fa_base or norm_ew_base == norm_fa:
                            s.arguments["folder_name"] = ew
                            s.reused = True
                            s.context_observation = f"Reusing existing workspace '{ew}'."
                            break
                    s.dependencies = [d for d in s.dependencies if any(st.step_id == d for st in adapted_steps)]
                    adapted_steps.append(s)

                elif s.tool == "create_note":
                    s.dependencies = [d for d in s.dependencies if any(st.step_id == d for st in adapted_steps)]
                    n_title = s.arguments.get("title", "")
                    if not (has_explicit_new_note or has_explicit_another_note):
                        # Helper for normalizing note titles consistent with TargetResolver philosophy
                        def _norm_note(name: str) -> str:
                            t = (name or "").lower().strip()
                            t = re.sub(r"\.(?:md|txt|docx)$", "", t)
                            t = re.sub(r"\s+(?:lecture\s+)?notes?$", "", t).strip()
                            return t

                        target_norm = _norm_note(n_title)
                        target_tokens = [w for w in target_norm.split() if len(w) > 1 and w not in ("the", "my", "a", "an", "today", "todays")]

                        exact_matches = []
                        partial_candidates = []

                        for en in snapshot.existing_notes:
                            en_lower = en.lower().strip()
                            en_norm = _norm_note(en)

                            if en_lower == n_title.lower().strip() or en_norm == target_norm:
                                exact_matches.append(en)
                            else:
                                if target_tokens and all(k in en_norm for k in target_tokens):
                                    partial_candidates.append(en)

                        # Decision resolution
                        if len(exact_matches) == 1:
                            note_match = exact_matches[0]
                            steps_changed = True
                            obs = f"Note '{note_match}' already exists."
                            dec = ContextDecision(
                                observation=obs,
                                confidence="HIGH",
                                effect_on_plan="reuse_note",
                                source="DesktopContext.filesystem",
                                details={"existing_note": note_match, "target_title": n_title}
                            )
                            plan.context_decisions.append(dec.to_dict())
                            plan.context_used.append(f"{note_match} note exists")
                            # Convert create_note to open_file
                            note_file_name = f"{note_match}.md"
                            try:
                                if (NOTES_DIR / f"{note_match}.txt").exists():
                                    note_file_name = f"{note_match}.txt"
                                elif (NOTES_DIR / f"{note_match}.md").exists():
                                    note_file_name = f"{note_match}.md"
                            except Exception:
                                pass
                            s.tool = "open_file"
                            s.arguments = {"query": note_file_name, "file_name": note_file_name}
                            s.reason = f"Note '{note_match}' already exists; opening existing note."
                            s.description = f"Open existing note '{note_match}'"
                            s.reused = True
                            s.context_observation = obs
                            adapted_steps.append(s)
                            continue

                        elif len(exact_matches) > 1 or (not exact_matches and len(partial_candidates) > 1):
                            # Ambiguous match: multiple existing notes match the topic!
                            ambig_candidates = exact_matches if len(exact_matches) > 1 else partial_candidates
                            obs = f"Ambiguous note request: Found multiple candidates matching '{n_title}': {', '.join(ambig_candidates)}."
                            dec = ContextDecision(
                                observation=obs,
                                confidence="MEDIUM",
                                effect_on_plan="request_clarification",
                                source="DesktopContext.filesystem",
                                details={"candidates": ambig_candidates, "target_title": n_title}
                            )
                            plan.context_decisions.append(dec.to_dict())
                            plan.context_used.append(f"Ambiguous note candidates: {', '.join(ambig_candidates)}")
                            plan.status = PlanStatus.FAILED
                            plan.steps = []
                            plan.goal = "Clarification required for ambiguous note request"
                            plan.error_message = f"Ambiguous note request: Multiple candidate notes found ({', '.join(ambig_candidates)}). Please clarify which note you want to open or create."
                            plan.is_adapted = True
                            plan.adaptation_reason = "Halted plan: Ambiguous note candidates found in context."
                            return plan

                        elif len(exact_matches) == 0 and len(partial_candidates) == 1:
                            note_match = partial_candidates[0]
                            steps_changed = True
                            obs = f"Found matching existing note '{note_match}' for topic '{n_title}'."
                            dec = ContextDecision(
                                observation=obs,
                                confidence="HIGH",
                                effect_on_plan="reuse_note",
                                source="DesktopContext.filesystem",
                                details={"existing_note": note_match, "target_title": n_title}
                            )
                            plan.context_decisions.append(dec.to_dict())
                            plan.context_used.append(f"{note_match} note exists")
                            note_file_name = f"{note_match}.md"
                            try:
                                if (NOTES_DIR / f"{note_match}.txt").exists():
                                    note_file_name = f"{note_match}.txt"
                                elif (NOTES_DIR / f"{note_match}.md").exists():
                                    note_file_name = f"{note_match}.md"
                            except Exception:
                                pass
                            s.tool = "open_file"
                            s.arguments = {"query": note_file_name, "file_name": note_file_name}
                            s.reason = f"Matching note '{note_match}' found; opening existing note."
                            s.description = f"Open existing note '{note_match}'"
                            s.reused = True
                            s.context_observation = obs
                            adapted_steps.append(s)
                            continue

                    adapted_steps.append(s)

                else:
                    s.dependencies = [d for d in s.dependencies if any(st.step_id == d for st in adapted_steps)]
                    adapted_steps.append(s)

            if steps_changed and adapted_steps:
                # Renumber steps
                for idx, stp in enumerate(adapted_steps, 1):
                    old_id = stp.step_id
                    new_id = f"step_{idx}"
                    stp.step_id = new_id
                    for future_s in adapted_steps[idx:]:
                        future_s.dependencies = [new_id if d == old_id else d for d in future_s.dependencies]

                plan.steps = adapted_steps
                plan.is_adapted = True
                plan.original_steps = original_steps_dict
                plan.adaptation_reason = "Adapted plan based on existing filesystem artifacts."

        # Rule 2: Avoid duplicate media/recording or application launches (Target-Aware)
        active_app = (snapshot.active_application or "").lower()
        active_title = (snapshot.active_window_title or "").lower()
        active_media = snapshot.active_media_item

        rec_step_idx = -1
        target_rec_name = None
        target_rec_stem = None

        for idx, s in enumerate(plan.steps):
            if s.tool == "open_file":
                q_arg = (s.arguments.get("query") or s.arguments.get("file_name") or "").lower()
                if "recording" in q_arg or "audio" in q_arg or ".wav" in q_arg or ".mp3" in q_arg or ".m4a" in q_arg:
                    rec_step_idx = idx
                    # Determine target recording filename
                    if s.arguments.get("file_name") and s.arguments.get("file_name") not in ("recordings", "notes", "downloads"):
                        target_rec_name = Path(s.arguments.get("file_name")).name
                    elif snapshot.existing_recordings:
                        target_rec_name = snapshot.existing_recordings[-1]
                    else:
                        target_rec_name = s.arguments.get("query", "recording.wav")
                    target_rec_stem = Path(target_rec_name).stem.lower()
                    break

        if rec_step_idx >= 0 and target_rec_name:
            # Deterministic evidence check: Is THAT specific target recording already open?
            is_target_open = False
            evidence_desc = ""
            evidence_source = "DesktopContext.active_window"

            # 1. Active media item match
            if active_media:
                media_str = str(active_media).lower()
                if target_rec_stem in media_str or target_rec_name.lower() in media_str:
                    is_target_open = True
                    evidence_source = "DesktopContext.active_media"
                    evidence_desc = f"Target recording '{target_rec_name}' is currently playing/active in media context."

            # 2. Active media player application window containing target filename/stem
            is_media_app = any(m in active_app for m in ("media player", "groove", "vlc", "wmplayer"))
            if not is_target_open and is_media_app:
                if target_rec_stem in active_title or target_rec_name.lower() in active_title:
                    is_target_open = True
                    evidence_source = "DesktopContext.active_window"
                    evidence_desc = f"Target recording '{target_rec_name}' is already open in {snapshot.active_application} ('{snapshot.active_window_title}')."

            # A window titled "Recording Settings" or generic "recording" in a non-media app does NOT count!
            if is_target_open:
                if len(plan.steps) > 1:
                    if not plan.original_steps:
                        plan.original_steps = [st.to_dict() for st in plan.steps]
                    removed_step = plan.steps.pop(rec_step_idx)
                    for idx, stp in enumerate(plan.steps, 1):
                        stp.step_id = f"step_{idx}"
                        stp.dependencies = []

                    dec = ContextDecision(
                        observation=evidence_desc,
                        confidence="HIGH",
                        effect_on_plan="avoid_duplicate_launch",
                        source=evidence_source,
                        details={"target_recording": target_rec_name, "active_window": snapshot.active_window_title, "active_app": snapshot.active_application}
                    )
                    plan.context_decisions.append(dec.to_dict())
                    plan.context_used.append(f"Recording already active: {target_rec_name}")
                    plan.is_adapted = True
                    plan.adaptation_reason = f"Target recording '{target_rec_name}' is already active in media player; skipped duplicate launch."
                else:
                    plan.steps[0].reused = True
                    plan.steps[0].context_observation = evidence_desc
                    dec = ContextDecision(
                        observation=evidence_desc,
                        confidence="HIGH",
                        effect_on_plan="avoid_duplicate_launch",
                        source=evidence_source,
                        details={"target_recording": target_rec_name, "active_window": snapshot.active_window_title, "active_app": snapshot.active_application}
                    )
                    plan.context_decisions.append(dec.to_dict())
                    plan.context_used.append(f"Recording already active: {target_rec_name}")
                    plan.is_adapted = True
                    plan.adaptation_reason = f"Target recording '{target_rec_name}' is already active; skipped duplicate launch."

        # Attach snapshot dict to plan
        plan.context_snapshot = snapshot.to_dict()
        return plan

    @classmethod
    def build_user_goal(cls, query: str, plan: Optional[TaskPlan] = None) -> UserGoal:
        """
        Synthesizes a structured UserGoal intermediate representation for a user query.
        Captures intent, action, target, parameters, constraints, dependencies, confidence,
        and clarification requirements.
        """
        q = (query or "").strip()
        q_lower = q.lower()

        # Check clarification requirement
        if plan and plan.status == PlanStatus.FAILED and ("clarif" in (plan.error_message or "").lower() or "ambiguous" in (plan.error_message or "").lower()):
            return UserGoal(
                intent="clarification_required",
                action="clarify",
                target=None,
                parameters={"query": q},
                constraints=["confidence_insufficient"],
                dependencies=[],
                confidence=0.4,
                requires_clarification=True,
                clarification_prompt=plan.error_message
            )

        # Compound browser search
        m_bs = re.search(r"\b(?:open|launch|go\s+to|visit)\s+(?:the\s+)?([a-zA-Z0-9_.-]+)\s+(?:and|,|then)\s+(?:then\s+)?(?:search(?:\s+for)?|look\s+up|find)\s+(.+)$", q_lower)
        if m_bs:
            return UserGoal(
                intent="browser_search_workflow",
                action="search",
                target=m_bs.group(1).strip(),
                parameters={"site": m_bs.group(1).strip(), "query": m_bs.group(2).strip()},
                constraints=["safe_dom_only", "no_cdp", "verified_action"],
                dependencies=["open_website", "dom_discovery", "type_field", "click_element"],
                confidence=0.95,
                requires_clarification=False
            )

        # Website opening
        for alias, url in KNOWN_WEBSITE_ALIASES.items():
            if re.search(rf"\b(?:open|launch|go\s+to|visit)\s+(?:the\s+)?{alias}\b", q_lower):
                return UserGoal(
                    intent="open_website",
                    action="open",
                    target=alias,
                    parameters={"url": url},
                    constraints=["safe_origin_only"],
                    dependencies=[],
                    confidence=0.96,
                    requires_clarification=False
                )

        # Page understanding
        if re.search(r"\b(?:what\s+is\s+this\s+page\s+about|summarize\s+this\s+page)\b", q_lower):
            return UserGoal(
                intent="summarize_current_page",
                action="summarize",
                target="current_page",
                parameters={},
                constraints=["read_only"],
                dependencies=["active_tab_context"],
                confidence=0.95,
                requires_clarification=False
            )

        # Generic default UserGoal
        action = plan.steps[0].tool if (plan and plan.steps) else "execute"
        target = plan.steps[0].arguments.get("file_name") or plan.steps[0].arguments.get("application") or plan.steps[0].arguments.get("url") if (plan and plan.steps) else None
        return UserGoal(
            intent=plan.goal if plan else "process_request",
            action=action,
            target=target,
            parameters={"query": q},
            constraints=["policy_gated"],
            dependencies=[s.step_id for s in plan.steps] if plan else [],
            confidence=0.90 if plan else 0.5,
            requires_clarification=False
        )

