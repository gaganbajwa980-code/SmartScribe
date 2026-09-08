import re
from typing import Dict, Any, Optional
from backend.assistant.desktop_context import DesktopContext, collect_desktop_context

class PlanContext:
    """
    Encapsulates runtime context for multi-step task planning and execution.
    Manages step output propagation, variable substitution, and cancellation.
    """
    def __init__(
        self,
        user_query: str = "",
        session_id: Optional[int] = None,
        desktop_context: Optional[DesktopContext] = None,
        cancel_event: Optional[Any] = None,
        context_snapshot: Optional[Any] = None,
        session_memory: Optional[Any] = None
    ):
        self.user_query = user_query.strip() if user_query else ""
        self.session_id = session_id
        self.desktop_context = desktop_context
        self.cancel_event = cancel_event
        self.context_snapshot = context_snapshot
        self.session_memory = session_memory
        self.step_results: Dict[str, Dict[str, Any]] = {}
        self.verified_outputs: Dict[str, Any] = {}

    def is_cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    def record_step_result(self, step_id: str, result: Dict[str, Any]):
        """Records execution output and verified data from a completed step."""
        self.step_results[step_id] = result
        if isinstance(result, dict):
            # Record key output paths or names
            data = result.get("data") or {}
            if not isinstance(data, dict):
                data = {}
            for k in ["path", "file_name", "folder_name", "application", "title"]:
                val = result.get(k) or data.get(k)
                if val:
                    self.verified_outputs[f"{step_id}.{k}"] = val
                    if not step_id.startswith("step_"):
                        self.verified_outputs[f"step_{step_id}.{k}"] = val
                    if step_id.startswith("s") and step_id[1:].isdigit():
                        self.verified_outputs[f"step_{step_id[1:]}.{k}"] = val

    def resolve_arguments(self, raw_args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolves dynamic reference tokens from previous steps.
        E.g. '{step_1.folder_name}' -> 'Operating Systems'
        """
        clean = {}
        for k, v in raw_args.items():
            if isinstance(v, str):
                resolved_v = v
                # Look for {step_X.field} tokens
                matches = re.findall(r"\{(\w+\.[\w_]+)\}", v)
                for m in matches:
                    if m in self.verified_outputs:
                        val_str = str(self.verified_outputs[m])
                        resolved_v = resolved_v.replace(f"{{{m}}}", val_str)
                    elif m.split(".")[0] in self.step_results:
                        step_id, prop = m.split(".", 1)
                        step_res = self.step_results[step_id]
                        val = step_res.get(prop)
                        if val is None and isinstance(step_res.get("data"), dict):
                            val = step_res["data"].get(prop)
                        if val is not None:
                            resolved_v = resolved_v.replace(f"{{{m}}}", str(val))
                clean[k] = resolved_v
            elif isinstance(v, dict):
                clean[k] = self.resolve_arguments(v)
            else:
                clean[k] = v
        return clean

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_query": self.user_query,
            "session_id": self.session_id,
            "desktop_context": self.desktop_context.to_dict() if self.desktop_context else None,
            "context_snapshot": self.context_snapshot.to_dict() if hasattr(self.context_snapshot, "to_dict") else None,
            "session_memory": self.session_memory.to_dict() if hasattr(self.session_memory, "to_dict") else None,
            "completed_step_count": len(self.step_results),
            "verified_outputs": self.verified_outputs
        }
