from typing import Tuple, List, Dict, Any
from pathlib import Path

from backend.assistant.planner.plan_schema import TaskPlan, PlanStep, PlanStatus, StepStatus
from backend.assistant.planner.plan_context import PlanContext
from backend.assistant.tool_registry import ToolRegistry
from backend.assistant.permission_manager import PermissionManager, RiskLevel
from backend.assistant.tools.desktop_tools import (
    get_approved_roots, PROHIBITED_EXTENSIONS, ALLOWED_DOCUMENT_EXTENSIONS
)
from backend.resource_manager import ResourceManager

class PlanValidator:
    """
    Strict pre-execution validator for SmartScribe TaskPlans.
    Guarantees tool allowlisting, argument schemas, security path jails,
    acyclic dependency graphs, and resource safety.
    """
    def __init__(self):
        self.registry = ToolRegistry()
        self.perm_mgr = PermissionManager()
        self.rm = ResourceManager()

    def validate_plan(self, plan: TaskPlan, context: Optional[PlanContext] = None) -> Tuple[bool, List[str]]:
        """
        Validates the entire plan before any execution begins.
        Returns (is_valid, list_of_errors).
        """
        errors = []

        if not plan.steps:
            errors.append("Plan contains zero steps.")
            plan.status = PlanStatus.FAILED
            plan.error_message = "; ".join(errors)
            return False, errors

        step_ids_seen = set()
        approved_roots = get_approved_roots()

        for idx, step in enumerate(plan.steps):
            # 1. Step ID uniqueness
            if not step.step_id:
                errors.append(f"Step at index {idx} has an empty step_id.")
            elif step.step_id in step_ids_seen:
                errors.append(f"Duplicate step_id '{step.step_id}' found at index {idx}.")
            step_ids_seen.add(step.step_id)

            # 2. Tool allowlist check
            tool_def = self.registry.get(step.tool)
            if not tool_def:
                errors.append(f"Step '{step.step_id}' references unknown or unapproved tool '{step.tool}'.")
                continue

            # Update step risk level to match registry
            step.risk_level = tool_def.risk_level

            # 3. Argument schema & security checks
            args = step.arguments or {}

            # Path traversal / prohibited extension checks in arguments
            for arg_key, arg_val in args.items():
                if isinstance(arg_val, str):
                    val_lower = arg_val.lower()

                    # Check traversal
                    if ".." in arg_val:
                        errors.append(f"Step '{step.step_id}' argument '{arg_key}' contains path traversal ('..').")

                    # Check prohibited extensions
                    for p_ext in PROHIBITED_EXTENSIONS:
                        if arg_val.endswith(p_ext) or f"{p_ext}." in val_lower:
                            errors.append(f"Step '{step.step_id}' argument '{arg_key}' contains prohibited file type '{p_ext}'.")

                    # Check absolute path jail if an absolute path is provided
                    if ":" in arg_val or arg_val.startswith(("/", "\\")):
                        try:
                            p_cand = Path(arg_val).resolve()
                            is_safe = False
                            for root in approved_roots:
                                try:
                                    if p_cand == root or root in p_cand.parents:
                                        is_safe = True
                                        break
                                except Exception:
                                    pass
                            if not is_safe and not arg_val.startswith("{"):  # allow dynamic template
                                errors.append(f"Step '{step.step_id}' path '{arg_val}' is outside approved workspace folders.")
                        except Exception:
                            pass

            # Schema argument validation: fill dynamic template variables with dummy placeholder strings
            placeholder_args = {}
            for k, v in args.items():
                if isinstance(v, str) and "{" in v and "}" in v:
                    placeholder_args[k] = "placeholder_dynamic_token"
                else:
                    placeholder_args[k] = v

            is_valid_args, val_err, _ = self.registry.validate_args(step.tool, placeholder_args)
            if not is_valid_args:
                errors.append(f"Step '{step.step_id}' failed argument validation: {val_err}")

            # 4. Dependency ordering & acyclicity
            for dep in step.dependencies:
                if dep not in step_ids_seen or dep == step.step_id:
                    errors.append(f"Step '{step.step_id}' has forward, self, or non-existent dependency '{dep}'.")

            # 5. High-risk flag detection
            if step.risk_level in (RiskLevel.SENSITIVE, RiskLevel.DESTRUCTIVE) or tool_def.requires_confirmation:
                plan.requires_approval = True

        # 6. Resource manager check
        ram_info = self.rm.get_ram_info()
        ram_pct = ram_info.get("percent", 0.0)
        if ram_pct > 95.0:
            errors.append(f"System memory is critically constrained ({ram_pct}% RAM used). Execution halted.")

        if errors:
            plan.status = PlanStatus.FAILED
            plan.error_message = "; ".join(errors)
            return False, errors

        plan.status = PlanStatus.AWAITING_APPROVAL if plan.requires_approval else PlanStatus.VALIDATED
        return True, []
