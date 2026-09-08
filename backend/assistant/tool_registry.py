import threading
from dataclasses import dataclass
from typing import Dict, Any, Optional, Callable, List, Tuple
from backend.assistant.permission_manager import RiskLevel

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters_schema: Dict[str, Any]
    risk_level: RiskLevel
    requires_confirmation: bool
    execution_fn: Callable[..., Any]
    result_schema: Dict[str, Any]
    allowed_values: Optional[Dict[str, List[Any]]] = None

class ToolRegistry:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ToolRegistry, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, auto_load_defaults: bool = True):
        with self._lock:
            if getattr(self, "_initialized", False):
                return
            self._tools: Dict[str, ToolDefinition] = {}
            self._initialized = True
            if auto_load_defaults:
                self._load_default_tools()

    def _load_default_tools(self):
        """Loads canonical production tools from tools module to ensure registry is never empty."""
        try:
            from backend.assistant.tools import register_all_safe_tools
            register_all_safe_tools()
        except ImportError:
            pass

    def register(self, tool_def: ToolDefinition):
        with self._lock:
            self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters_schema": t.parameters_schema,
                "risk_level": t.risk_level.value if isinstance(t.risk_level, RiskLevel) else str(t.risk_level),
                "requires_confirmation": t.requires_confirmation
            }
            for t in self._tools.values()
        ]

    def validate_args(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        tool = self.get(tool_name)
        if not tool:
            return False, f"Tool '{tool_name}' not found.", {}

        clean_args = {}
        schema = tool.parameters_schema.get("properties", {})
        required = tool.parameters_schema.get("required", [])

        # Normalize known canonical argument aliases before checking requirements
        normalized_args = dict(args or {})
        aliases = {
            "create_note": {"name": "title"},
            "create_folder": {"name": "folder_name"},
            "open_folder": {"name": "folder_name"},
            "open_file": {"file_path": "file_name", "query": "file_name"},
        }.get(tool_name, {})

        for alias_key, canonical_key in aliases.items():
            if alias_key in normalized_args and (canonical_key not in normalized_args or normalized_args[canonical_key] is None):
                normalized_args[canonical_key] = normalized_args[alias_key]

        for req_prop in required:
            if req_prop not in normalized_args or normalized_args[req_prop] is None:
                return False, f"Missing required parameter '{req_prop}' for tool '{tool_name}'.", {}

        for prop_name, prop_spec in schema.items():
            if prop_name in normalized_args and normalized_args[prop_name] is not None:
                val = normalized_args[prop_name]
                prop_type = prop_spec.get("type")

                # Type checks
                if prop_type == "string" and not isinstance(val, str):
                    val = str(val)
                elif prop_type == "integer" and not isinstance(val, int):
                    try:
                        val = int(val)
                    except ValueError:
                        return False, f"Parameter '{prop_name}' must be an integer.", {}
                elif prop_type == "boolean" and not isinstance(val, bool):
                    val = str(val).lower() in ["true", "1", "yes"]

                # Allowlist validation
                if tool.allowed_values and prop_name in tool.allowed_values:
                    allowed = [str(x).lower() for x in tool.allowed_values[prop_name]]
                    if str(val).lower() not in allowed:
                        return False, f"Value '{val}' for '{prop_name}' not allowed. Must be one of {allowed}.", {}

                clean_args[prop_name] = val
            elif "default" in prop_spec:
                clean_args[prop_name] = prop_spec["default"]

        # Preserve extra execution parameters passed to tools like open_file (e.g. query, target_spec, file_path)
        for k, v in normalized_args.items():
            if k not in clean_args and v is not None:
                clean_args[k] = v

        return True, "Valid", clean_args
