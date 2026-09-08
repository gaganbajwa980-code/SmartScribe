import re
import threading
from typing import Optional, Dict, Any, List

class SessionMemory:
    """
    Lightweight, privacy-safe task-session memory for SmartScribe.
    Maintains short-term task state (completed steps, verified outputs, created artifacts)
    across multi-turn user requests to resolve follow-up continuation commands.
    
    Strictly task-scoped: zero user surveillance, zero persistent personal profiling.
    """
    _instance = None
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(SessionMemory, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        with self._lock:
            self.prior_plan_id: Optional[str] = None
            self.last_plan: Optional[Any] = None
            self.completed_steps: List[Dict[str, Any]] = []
            self.verified_outputs: Dict[str, Any] = {}
            self.created_artifacts: List[Dict[str, Any]] = []
            self.last_target: Optional[Dict[str, Any]] = None
            self.cancellations: List[str] = []
            self._initialized = True

    def clear(self):
        """Clears all session memory (useful between test runs or sessions)."""
        with self._lock:
            self.prior_plan_id = None
            self.last_plan = None
            self.completed_steps.clear()
            self.verified_outputs.clear()
            self.created_artifacts.clear()
            self.last_target = None
            self.cancellations.clear()

    def record_artifact(self, artifact_type: str, name: str, path: Optional[str] = None):
        """Records an explicitly created or verified artifact."""
        with self._lock:
            art = {
                "type": artifact_type,
                "name": name,
                "path": path or name
            }
            self.created_artifacts.append(art)
            self.last_target = art

    def record_plan_execution(self, plan: Any, context: Optional[Any] = None):
        """
        Records the results of an executed TaskPlan into session memory.
        Updates completed steps, verified outputs, and the most recent target.
        """
        with self._lock:
            if not plan:
                return

            self.prior_plan_id = getattr(plan, "plan_id", None)
            self.last_plan = plan

            # Record cancellation if applicable
            plan_status = getattr(plan, "status", None)
            if hasattr(plan_status, "value"):
                plan_status_val = plan_status.value
            else:
                plan_status_val = str(plan_status)

            if plan_status_val == "cancelled" and self.prior_plan_id:
                self.cancellations.append(self.prior_plan_id)

            steps = getattr(plan, "steps", []) or []
            for s in steps:
                s_dict = s.to_dict() if hasattr(s, "to_dict") else dict(s)
                if s_dict.get("verified") or s_dict.get("status") in ("succeeded", "SUCCEEDED"):
                    self.completed_steps.append(s_dict)
                    tool = s_dict.get("tool", "")
                    args = s_dict.get("arguments", {})
                    res = s_dict.get("result") or {}
                    data = res.get("data") if isinstance(res, dict) else {}

                    # Target extraction by tool
                    if tool in ("create_folder", "open_folder"):
                        f_name = args.get("folder_name") or data.get("folder_name")
                        f_path = res.get("path") or data.get("path") or f_name
                        if f_name:
                            self.record_artifact("folder", f_name, f_path)
                            self.verified_outputs[f"{s_dict.get('step_id', '')}.folder_name"] = f_name
                            self.verified_outputs["folder_name"] = f_name

                    elif tool == "create_note":
                        n_title = args.get("title") or data.get("title")
                        n_path = res.get("file_path") or res.get("path") or data.get("path")
                        if n_title:
                            self.record_artifact("note", n_title, n_path)
                            self.verified_outputs[f"{s_dict.get('step_id', '')}.title"] = n_title
                            self.verified_outputs["note_title"] = n_title

                    elif tool == "open_file":
                        fn = args.get("file_name") or args.get("query") or res.get("resolved_target")
                        target_p = res.get("resolved_target") or res.get("path")
                        if fn:
                            self.record_artifact("file", str(fn), str(target_p) if target_p else str(fn))
                            self.verified_outputs[f"{s_dict.get('step_id', '')}.file_name"] = str(fn)

            if context and hasattr(context, "verified_outputs"):
                self.verified_outputs.update(context.verified_outputs)

    def get_last_target(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent verified target artifact from the session."""
        with self._lock:
            return dict(self.last_target) if self.last_target else None

    def resolve_pronoun_reference(self, query: str, target_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Resolves continuation pronouns ('it', 'inside it', 'notes for it')
        against the verified recent target in session memory.
        
        Returns:
            - dict with {"resolved": True, "target": ...} if resolved
            - dict with {"resolved": False, "is_ambiguous": True, "reason": ...} if pronoun detected without prior target
            - None if no pronoun reference detected in query
        """
        q = (query or "").strip().lower()
        if not q:
            return None

        # Check for container/folder pronoun patterns
        m_inside = re.search(r"\b(?:inside|in|into)\s+(?:it|the\s+folder|that|this)\b", q)
        # Check for topic/file pronoun patterns
        m_for = re.search(r"\b(?:for|about|from|of)\s+(?:it|that|this)\b", q)
        # Check for direct object pronoun patterns
        m_direct = re.search(r"\b(?:open|view|show|delete|remove|explore)\s+(?:it|that|this)\b", q)

        has_pronoun = bool(m_inside or m_for or m_direct)
        if not has_pronoun:
            # Also check generic standalone "it" as an object
            if re.search(r"\b(?:put|add|move|copy)\s+.*?\s+(?:inside|in)\s+it\b", q):
                has_pronoun = True

        if not has_pronoun:
            return None

        with self._lock:
            # 1. Container requests ("inside it") expect a folder
            if m_inside or "inside it" in q or "in it" in q:
                if self.last_target and self.last_target.get("type") == "folder":
                    return {
                        "resolved": True,
                        "target": dict(self.last_target),
                        "pronoun": "inside it",
                        "resolved_value": self.last_target.get("name")
                    }
                # Look back in created_artifacts for recent folder
                for art in reversed(self.created_artifacts):
                    if art.get("type") == "folder":
                        return {
                            "resolved": True,
                            "target": dict(art),
                            "pronoun": "inside it",
                            "resolved_value": art.get("name")
                        }
                # If no folder in memory, pronoun is ambiguous
                return {
                    "resolved": False,
                    "is_ambiguous": True,
                    "pronoun": "inside it",
                    "reason": "Referent 'inside it' cannot be resolved: no target folder found in session memory."
                }

            # 2. Topic/file requests ("notes for it")
            if m_for:
                if self.last_target:
                    return {
                        "resolved": True,
                        "target": dict(self.last_target),
                        "pronoun": "for it",
                        "resolved_value": self.last_target.get("name")
                    }
                return {
                    "resolved": False,
                    "is_ambiguous": True,
                    "pronoun": "for it",
                    "reason": "Referent 'for it' cannot be resolved: no prior target found in session memory."
                }

            # 3. Direct action requests ("open it", "delete it")
            if m_direct or "it" in q.split():
                if self.last_target:
                    return {
                        "resolved": True,
                        "target": dict(self.last_target),
                        "pronoun": "it",
                        "resolved_value": self.last_target.get("name")
                    }
                return {
                    "resolved": False,
                    "is_ambiguous": True,
                    "pronoun": "it",
                    "reason": "Referent 'it' cannot be resolved: no prior target found in session memory."
                }

        return None

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "prior_plan_id": self.prior_plan_id,
                "completed_step_count": len(self.completed_steps),
                "created_artifact_count": len(self.created_artifacts),
                "verified_outputs": dict(self.verified_outputs),
                "last_target": dict(self.last_target) if self.last_target else None,
                "cancellations": list(self.cancellations)
            }
