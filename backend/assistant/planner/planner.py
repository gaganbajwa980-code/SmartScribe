import re
import json
import time
from typing import Optional, Dict, Any, List

from backend.assistant.planner.plan_schema import TaskPlan, PlanStep, PlanStatus, StepStatus
from backend.assistant.planner.plan_context import PlanContext
from backend.assistant.planner.plan_validator import PlanValidator
from backend.assistant.permission_manager import RiskLevel
from backend.assistant.tool_registry import ToolRegistry
from backend.resource_manager import ResourceManager
from backend.assistant.planner.context_snapshot import ContextSnapshot, ContextDecision, collect_context_snapshot
from backend.assistant.planner.session_memory import SessionMemory

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

        # Ensure session memory and context snapshot are populated
        if ctx.session_memory is None:
            ctx.session_memory = self.session_memory
        if ctx.context_snapshot is None:
            ctx.context_snapshot = collect_context_snapshot(session_memory=ctx.session_memory)

        # 0. Check for continuation pronouns ("inside it", "notes for it", "open it")
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
                        error_message=f"Ambiguous request: {pronoun_res.get('reason', 'Could not resolve referent')}. Please clarify the target name."
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
        ACTION_VERBS = r"(?:open|launch|start|explore|view|browse|navigate|create|make)"
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
        Deterministic planning rules for common student, lecture, and workflow tasks.
        """
        q_lower = q.lower()

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
        clauses = self.decompose_clauses(q)
        extracted_steps = []
        last_created_folder = None
        step_idx = 1

        for c in clauses:
            c_clean = c.strip()
            if not c_clean:
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

            # 4. open application
            m_app = re.search(r"\b(?:open|launch|start)\s+(?:the\s+)?(?:application\s+|app\s+)?(chrome|vscode|calculator|notepad|explorer)\b", c_clean, re.IGNORECASE)
            if m_app:
                app_n = m_app.group(1).lower()
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

            # 5. open file
            m_file = re.search(r"\b(?:open|launch|play|show|view)\s+(?:the\s+)?(?:file\s+|recording\s+|pdf\s+|audio\s+|document\s+)?(?:called\s+|named\s+)?(?:['\"]([^'\"]+)['\"]|(.+))", c_clean, re.IGNORECASE)
            if m_file:
                fn = (m_file.group(1) or m_file.group(2)).strip().strip("'\"").rstrip(".,;")
                dep = [extracted_steps[-1].step_id] if extracted_steps else []
                extracted_steps.append(PlanStep(
                    step_id=f"step_{step_idx}",
                    tool="open_file",
                    arguments={"query": fn},
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
            arg_key = "folder_name" if art_type == "folder" else "file_name"
            step = PlanStep(
                step_id="step_1",
                tool=tool_name,
                arguments={arg_key: target_name},
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
        snapshot: Optional[ContextSnapshot] = ctx.context_snapshot
        if not snapshot or not plan or not plan.steps:
            return plan

        q_lower = plan.user_query.lower()

        # Rule 1: Check explicit "new" / "another"
        has_explicit_new_folder = bool(re.search(r"\b(?:create|make)\s+(?:another|a\s+new)\s+folder\b", q_lower))
        has_explicit_new_note = bool(re.search(r"\b(?:create|make)\s+(?:another|a\s+new)\s+note\b", q_lower))

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
                    for ew in snapshot.existing_workspaces:
                        if (
                            ew.lower() == f_name.lower() or
                            ew.lower() == f_name.replace(" Lecture", "").lower() or
                            f_name.lower().startswith(ew.lower()) or
                            ew.lower().startswith(f_name.lower())
                        ):
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
                    for ew in snapshot.existing_workspaces:
                        if (
                            ew.lower() == f_arg.lower() or
                            ew.lower() == f_arg.replace(" Lecture", "").lower() or
                            f_arg.lower().startswith(ew.lower()) or
                            ew.lower().startswith(f_arg.lower())
                        ):
                            s.arguments["folder_name"] = ew
                            s.reused = True
                            s.context_observation = f"Reusing existing workspace '{ew}'."
                            break
                    s.dependencies = [d for d in s.dependencies if any(st.step_id == d for st in adapted_steps)]
                    adapted_steps.append(s)

                elif s.tool == "create_note":
                    s.dependencies = [d for d in s.dependencies if any(st.step_id == d for st in adapted_steps)]
                    n_title = s.arguments.get("title", "")
                    if not has_explicit_new_note:
                        note_match = None
                        for en in snapshot.existing_notes:
                            if en.lower() == n_title.lower() or en.lower() == n_title.replace(" Notes", "").lower():
                                note_match = en
                                break
                        if note_match:
                            steps_changed = True
                            obs = f"Note '{note_match}' already exists."
                            dec = ContextDecision(
                                observation=obs,
                                confidence="HIGH",
                                effect_on_plan="reuse_note",
                                source="DesktopContext.filesystem",
                                details={"existing_note": note_match}
                            )
                            plan.context_decisions.append(dec.to_dict())
                            plan.context_used.append(f"{note_match} note exists")
                            # Convert create_note to open_file
                            s.tool = "open_file"
                            s.arguments = {"query": f"{note_match}.md"}
                            s.reason = f"Note '{note_match}' already exists; opening existing note."
                            s.description = f"Open existing note '{note_match}'"
                            s.reused = True
                            s.context_observation = obs
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

        # Rule 2: Avoid duplicate media/recording or application launches
        active_app = (snapshot.active_application or "").lower()
        active_title = (snapshot.active_window_title or "").lower()
        active_media = snapshot.active_media_item

        recording_already_open = (
            active_media is not None or
            "recording" in active_title or
            ("media player" in active_app and ("recording" in active_title or ".wav" in active_title)) or
            "recording" in active_app
        )

        if recording_already_open:
            rec_step_idx = -1
            for idx, s in enumerate(plan.steps):
                if s.tool == "open_file":
                    q_arg = (s.arguments.get("query") or s.arguments.get("file_name") or "").lower()
                    if "recording" in q_arg or "audio" in q_arg or ".wav" in q_arg:
                        rec_step_idx = idx
                        break

            if rec_step_idx >= 0 and len(plan.steps) > 1:
                if not plan.original_steps:
                    plan.original_steps = [st.to_dict() for st in plan.steps]
                removed_step = plan.steps.pop(rec_step_idx)
                for idx, stp in enumerate(plan.steps, 1):
                    stp.step_id = f"step_{idx}"
                    stp.dependencies = []

                obs = "Latest lecture recording is already open/active in the media player."
                dec = ContextDecision(
                    observation=obs,
                    confidence="HIGH",
                    effect_on_plan="avoid_duplicate_launch",
                    source="DesktopContext.active_window"
                )
                plan.context_decisions.append(dec.to_dict())
                plan.context_used.append("Latest lecture recording already active")
                plan.is_adapted = True
                plan.adaptation_reason = "Latest lecture recording is already active; skipped duplicate launch."

        # Attach snapshot dict to plan
        plan.context_snapshot = snapshot.to_dict()
        return plan
