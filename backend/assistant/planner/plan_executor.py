import time
from typing import Dict, Any, Optional

from backend.assistant.planner.plan_schema import TaskPlan, PlanStep, PlanStatus, StepStatus
from backend.assistant.planner.plan_context import PlanContext
from backend.assistant.planner.plan_validator import PlanValidator
from backend.assistant.execution_engine import ExecutionEngine
from backend.assistant.permission_manager import PermissionManager, RiskLevel
from backend.assistant.action_verifier import ActionStatus

class PlanExecutor:
    """
    Sequential execution engine for SmartScribe TaskPlans.
    Executes steps through ExecutionEngine, strictly verifies outputs via ActionVerifier,
    handles permissions & cancellations, and produces truthful outcomes.
    """
    def __init__(self):
        self.validator = PlanValidator()
        self.executor = ExecutionEngine()
        self.perm_mgr = PermissionManager()

    def execute_plan(
        self,
        plan: TaskPlan,
        context: Optional[PlanContext] = None,
        confirmation_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a TaskPlan step-by-step.
        """
        t0 = time.perf_counter()
        ctx = context or PlanContext(user_query=plan.user_query)

        # 1. Immediate cancellation check
        if ctx.is_cancelled():
            plan.status = PlanStatus.CANCELLED
            plan.error_message = "Execution cancelled by user."
            return self._build_report(plan, t0, "Plan cancelled before execution.")

        # 2. Validation check if in draft
        if plan.status == PlanStatus.DRAFT:
            is_valid, errors = self.validator.validate_plan(plan, ctx)
            if not is_valid:
                return self._build_report(plan, t0, f"Plan validation failed: {'; '.join(errors)}")

        # 3. Plan-level approval check
        if plan.status == PlanStatus.AWAITING_APPROVAL and not any(s.status == StepStatus.AWAITING_APPROVAL for s in plan.steps):
            # Check confirmation token if provided
            token_valid = False
            if confirmation_token:
                token_valid, _, perm_msg = self.perm_mgr.validate_confirmation_token(confirmation_token)

            if not token_valid:
                req_data = self.perm_mgr.create_confirmation_request(
                    tool_name="execute_plan",
                    arguments={"plan_id": plan.plan_id, "goal": plan.goal, "query": plan.user_query},
                    risk_level=RiskLevel.SENSITIVE,
                    summary=f"Plan '{plan.goal}' requires user approval before execution."
                )
                plan.approval_token = req_data["token"]
                return {
                    "success": False,
                    "status": PlanStatus.AWAITING_APPROVAL.value,
                    "plan": plan.to_dict(),
                    "confirmation_token": req_data["token"],
                    "confirmation_prompt": req_data["summary"],
                    "message": f"Plan '{plan.goal}' requires approval to proceed.",
                    "response": f"I have prepared a plan: {plan.goal}. Do you want me to proceed?",
                    "spoken_text": f"I have prepared a plan to {plan.goal}. Shall I proceed?",
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 2)
                }
            else:
                plan.approval_token = None
                plan.status = PlanStatus.EXECUTING

        plan.status = PlanStatus.EXECUTING
        any_failed = False
        succeeded_count = 0

        # 4. Sequential Step Execution Loop
        for idx, step in enumerate(plan.steps):
            plan.current_step_index = idx

            # Check cancellation between steps
            if ctx.is_cancelled():
                step.status = StepStatus.CANCELLED
                step.error = "Cancelled by user."
                for remaining in plan.steps[idx+1:]:
                    remaining.status = StepStatus.SKIPPED
                plan.status = PlanStatus.CANCELLED
                break

            # Check step dependencies
            dep_failed = False
            for dep_id in step.dependencies:
                dep_step = plan.get_step(dep_id)
                if not dep_step or dep_step.status != StepStatus.SUCCEEDED:
                    dep_failed = True
                    break

            if dep_failed:
                step.status = StepStatus.SKIPPED
                step.error = f"Prerequisite dependency step failed or skipped."
                any_failed = True
                continue

            # Resolve dynamic arguments from previous step outputs
            resolved_args = ctx.resolve_arguments(step.arguments)
            step.arguments = resolved_args

            # Execute step via ExecutionEngine
            step.status = StepStatus.EXECUTING
            step_t0 = time.perf_counter()
            intent_payload = {
                "target_tool": step.tool,
                "tool": step.tool,
                "arguments": resolved_args,
                "intent": step.tool
            }

            step_res = self.executor.execute_intent(
                intent_payload,
                context={"session_id": ctx.session_id},
                confirmation_token=confirmation_token
            )
            step_dur = (time.perf_counter() - step_t0) * 1000
            step.duration_ms = step_dur
            step.result = step_res
            step.verified = step_res.get("verified", False)

            # Check if execution engine returned waiting_for_confirmation
            if step_res.get("status") == "waiting_for_confirmation":
                step.status = StepStatus.AWAITING_APPROVAL
                plan.status = PlanStatus.AWAITING_APPROVAL
                return {
                    "success": False,
                    "status": PlanStatus.AWAITING_APPROVAL.value,
                    "plan": plan.to_dict(),
                    "confirmation_token": step_res.get("confirmation_token"),
                    "confirmation_prompt": step_res.get("confirmation_prompt"),
                    "message": f"Step '{step.step_id}' ({step.tool}) requires immediate confirmation.",
                    "response": f"Step {step.step_id} requires confirmation: {step_res.get('confirmation_prompt')}",
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 2)
                }

            if step_res.get("status") == "verification_failed" or step_res.get("error_code") == "VERIFICATION_FAILED" or (step_res.get("success") and not step.verified):
                step.status = StepStatus.VERIFICATION_FAILED
                step.error = f"Step executed, but verification failed: {step_res.get('message')}"
                any_failed = True
                # Halt dependent steps on verification failure
                self._skip_remaining_dependents(plan, step.step_id)
                break
            elif step_res.get("success") and step.verified:
                step.status = StepStatus.SUCCEEDED
                succeeded_count += 1
                ctx.record_step_result(step.step_id, step_res)
            else:
                step.status = StepStatus.FAILED
                step.error = step_res.get("message") or "Tool execution returned failure."
                any_failed = True
                # Halt dependent steps on execution failure
                self._skip_remaining_dependents(plan, step.step_id)
                break

        plan.completed_at = time.time()

        # Final plan status synthesis
        if plan.status == PlanStatus.CANCELLED:
            final_status = PlanStatus.CANCELLED
            success = False
        elif succeeded_count == len(plan.steps):
            final_status = PlanStatus.SUCCEEDED
            success = True
        elif succeeded_count > 0 and any_failed:
            final_status = PlanStatus.PARTIAL_SUCCESS
            success = True
        else:
            final_status = PlanStatus.FAILED
            success = False

        plan.status = final_status
        return self._build_report(plan, t0, None, success=success)

    def _skip_remaining_dependents(self, plan: TaskPlan, failed_step_id: str):
        """Marks any future steps that depend on the failed step as SKIPPED."""
        for s in plan.steps:
            if s.status == StepStatus.PENDING and failed_step_id in s.dependencies:
                s.status = StepStatus.SKIPPED
                s.error = f"Skipped because dependency '{failed_step_id}' failed."

    def _build_report(
        self,
        plan: TaskPlan,
        t0: float,
        override_message: Optional[str] = None,
        success: bool = False
    ) -> Dict[str, Any]:
        dur = round((time.perf_counter() - t0) * 1000, 2)
        total = len(plan.steps)
        succeeded = sum(1 for s in plan.steps if s.status == StepStatus.SUCCEEDED)
        failed = sum(1 for s in plan.steps if s.status in (StepStatus.FAILED, StepStatus.VERIFICATION_FAILED))
        skipped = sum(1 for s in plan.steps if s.status == StepStatus.SKIPPED)

        if override_message:
            msg = override_message
        elif plan.status == PlanStatus.SUCCEEDED:
            msg = f"Task completed successfully: {plan.goal} ({succeeded}/{total} steps verified)."
        elif plan.status == PlanStatus.PARTIAL_SUCCESS:
            failed_step = next((s for s in plan.steps if s.status in (StepStatus.FAILED, StepStatus.VERIFICATION_FAILED)), None)
            f_name = failed_step.tool if failed_step else "a step"
            msg = f"Partially completed ({succeeded}/{total} steps). Halted at '{f_name}': {failed_step.error if failed_step else ''}"
        elif plan.status == PlanStatus.CANCELLED:
            msg = f"Plan execution was cancelled by user ({succeeded}/{total} completed)."
        else:
            failed_step = next((s for s in plan.steps if s.status in (StepStatus.FAILED, StepStatus.VERIFICATION_FAILED)), None)
            msg = f"Plan failed: {failed_step.error if failed_step else plan.error_message or 'Execution failed.'}"

        # Generate concise spoken response
        if plan.status == PlanStatus.SUCCEEDED:
            spoken = f"Completed all steps for {plan.goal}."
        elif plan.status == PlanStatus.PARTIAL_SUCCESS:
            spoken = f"Partially completed {plan.goal}. {succeeded} steps succeeded before encountering an issue."
        elif plan.status == PlanStatus.CANCELLED:
            spoken = f"Cancelled {plan.goal}."
        else:
            spoken = f"Could not complete {plan.goal}."

        return {
            "success": success or (plan.status == PlanStatus.SUCCEEDED),
            "status": plan.status.value,
            "plan": plan.to_dict(),
            "message": msg,
            "response": msg,
            "spoken_text": spoken,
            "duration_ms": dur,
            "steps_summary": {
                "total": total,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped
            }
        }
