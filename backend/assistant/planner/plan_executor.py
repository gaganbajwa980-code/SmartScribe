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
    def __init__(self, executor: Optional[ExecutionEngine] = None):
        self.validator = PlanValidator()
        self.executor = executor or ExecutionEngine()
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

        # 1b. Immediate check for failed or empty plan (e.g. ambiguity clarification)
        if plan.status == PlanStatus.FAILED:
            return self._build_report(plan, t0, plan.error_message or "Plan generation failed.", success=False)
        if not plan.steps:
            plan.status = PlanStatus.FAILED
            return self._build_report(plan, t0, plan.error_message or "Plan contains no steps.", success=False)

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

            # Check unconfirmed execution (verification unavailable)
            step_status = step_res.get("status")
            is_unconf = (
                step_status in ("unconfirmed", "verification_unavailable") or
                step_res.get("data", {}).get("postcondition_status") == "verification_unavailable" or
                (step_res.get("success") and not step.verified and step_res.get("verification", {}).get("method") == "browser_postcondition_unavailable")
            )
            if is_unconf:
                step.status = StepStatus.UNCONFIRMED
                if getattr(step, "allow_unconfirmed", False):
                    succeeded_count += 1
                    ctx.record_step_result(step.step_id, step_res)
                else:
                    # Plan continuation policy: Default: HALT plan and report to user (STOP -> REPORT -> DO NOT GUESS)
                    plan.status = PlanStatus.AWAITING_APPROVAL
                    self._skip_remaining_dependents(plan, step.step_id)
                    return {
                        "success": False,
                        "status": "unconfirmed_halt",
                        "plan": plan.to_dict(),
                        "step_id": step.step_id,
                        "message": f"Step '{step.step_id}' ({step.tool}) executed, but result could not be confirmed (verification unavailable). Proceed with next step?",
                        "response": "Action executed but result could not be confirmed. Proceed with next step?",
                        "duration_ms": round((time.perf_counter() - t0) * 1000, 2)
                    }
            elif step_res.get("status") == "verification_failed" or step_res.get("error_code") == "VERIFICATION_FAILED" or (step_res.get("success") and not step.verified):
                step.status = StepStatus.VERIFICATION_FAILED
                step.error = f"Step executed, but verification failed: {step_res.get('message')}"
                any_failed = True
                print(f"[SMARTSCRIBE EXECUTION] Step {step.step_id} ({step.tool}) -> VERIFICATION_FAILED: {step.error}")
                # Halt dependent steps on verification failure
                self._skip_remaining_dependents(plan, step.step_id)
                break
            elif step_res.get("success") and step.verified:
                step.status = StepStatus.SUCCEEDED
                succeeded_count += 1
                ctx.record_step_result(step.step_id, step_res)
                print(f"[SMARTSCRIBE EXECUTION] Step {step.step_id} ({step.tool}) -> SUCCEEDED (verified=True): {step_res.get('message')}")
            else:
                step.status = StepStatus.FAILED
                step.error = step_res.get("message") or "Tool execution returned failure."
                any_failed = True
                print(f"[SMARTSCRIBE EXECUTION] Step {step.step_id} ({step.tool}) -> FAILED: {step.error}")
                # Halt dependent steps on execution failure
                self._skip_remaining_dependents(plan, step.step_id)
                break

            # Window readiness wait: after open_application succeeds, poll before type_text
            if (step.status == StepStatus.SUCCEEDED
                    and step.tool == "open_application"
                    and idx + 1 < len(plan.steps)
                    and plan.steps[idx + 1].tool == "type_text"):
                expected_app = step.arguments.get("application", "").lower()
                res_data = step_res.get("data", {}) if isinstance(step_res, dict) else {}
                launched_pid = res_data.get("pid") or (step_res.get("pid") if isinstance(step_res, dict) else None)
                proc_name = f"{expected_app}.exe" if not expected_app.endswith(".exe") else expected_app
                step1_hwnd = res_data.get("hwnd") or (step_res.get("hwnd") if isinstance(step_res, dict) else None) or "N/A"
                print(f"[SMARTSCRIBE STEP_1 RESULT] Application: '{expected_app}', PID: {launched_pid}, Process Name: '{proc_name}', HWND: {step1_hwnd}")

                _wait_start = time.perf_counter()
                _ready = False
                _last_winfo = {}
                while (time.perf_counter() - _wait_start) < 3.0:
                    try:
                        from backend.assistant.desktop_context import get_active_window_info
                        _last_winfo = get_active_window_info()
                        _fg_hwnd = _last_winfo.get("hwnd", 0)
                        _fg_pid = _last_winfo.get("pid", 0)
                        _fg_title = (_last_winfo.get("window_title") or "").lower()
                        _fg_proc = (_last_winfo.get("process_name") or "").lower()
                        _fg_app = (_last_winfo.get("application") or "").lower()

                        if expected_app and (expected_app in _fg_title or expected_app in _fg_proc or expected_app in _fg_app):
                            _ready = True
                            print(f"[SMARTSCRIBE WINDOW READINESS] Succeeded: Application '{expected_app}' is active in foreground (took {round(time.perf_counter() - _wait_start, 2)}s, HWND={_fg_hwnd}, PID={_fg_pid}, Proc='{_last_winfo.get('process_name')}', Title='{_last_winfo.get('window_title')}')")
                            break

                        # Target app not in foreground yet (e.g. browser still has focus)
                        # Bring target app window to front
                        act_ok = self._activate_application_window(expected_app, launched_pid=launched_pid)
                        print(f"[SMARTSCRIBE WINDOW READINESS] Polling readiness: Expected='{expected_app}', Foreground: HWND={_fg_hwnd}, PID={_fg_pid}, Proc='{_fg_proc}', Title='{_last_winfo.get('window_title')}', Activation attempted={act_ok}")
                    except Exception as e:
                        print(f"[SMARTSCRIBE WINDOW READINESS] Check warning: {e}")
                    time.sleep(0.2)
                if not _ready:
                    _next_step = plan.steps[idx + 1]
                    _next_step.status = StepStatus.SKIPPED
                    _next_step.error = (
                        f"Application '{step.arguments.get('application', '')}' did not become "
                        f"the foreground window within 3.0 seconds (foreground: '{_last_winfo.get('window_title', 'Desktop')}', HWND={_last_winfo.get('hwnd', 0)}, PID={_last_winfo.get('pid', 0)}, proc: '{_last_winfo.get('process_name', 'explorer.exe')}'). Typing step skipped safely."
                    )
                    print(f"[SMARTSCRIBE WINDOW READINESS] FAILED: {_next_step.error}")
                    any_failed = True
                    self._skip_remaining_dependents(plan, _next_step.step_id)
                    break

        plan.completed_at = time.time()

        # Final plan status synthesis
        if plan.status == PlanStatus.CANCELLED:
            final_status = PlanStatus.CANCELLED
            success = False
        elif plan.status == PlanStatus.FAILED or not plan.steps:
            final_status = PlanStatus.FAILED
            success = False
        elif succeeded_count == len(plan.steps):
            final_status = PlanStatus.SUCCEEDED
            success = True
        elif succeeded_count > 0 and any_failed:
            final_status = PlanStatus.PARTIAL_SUCCESS
            success = False
        else:
            final_status = PlanStatus.FAILED
            success = False

        plan.status = final_status
        return self._build_report(plan, t0, None, success=success)

    def _skip_remaining_dependents(self, plan: TaskPlan, failed_step_id: str):
        """Marks any future steps that transitively depend on the failed step as SKIPPED."""
        to_skip = {failed_step_id}
        changed = True
        while changed:
            changed = False
            for s in plan.steps:
                if s.status == StepStatus.PENDING and any(dep in to_skip for dep in s.dependencies):
                    s.status = StepStatus.SKIPPED
                    failed_dep = next(dep for dep in s.dependencies if dep in to_skip)
                    s.error = f"Skipped because dependency '{failed_dep}' failed or was skipped."
                    to_skip.add(s.step_id)
                    changed = True
        for s in plan.steps:
            if s.status == StepStatus.PENDING:
                s.status = StepStatus.SKIPPED
                s.error = f"Skipped because plan execution was halted after step '{failed_step_id}' failed."

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

        spoken = None
        if override_message:
            msg = override_message
            spoken = override_message
        elif plan.status == PlanStatus.SUCCEEDED:
            msg = f"Task completed successfully: {plan.goal} ({succeeded}/{total} steps verified)."
            spoken = f"Completed all steps for {plan.goal}."
        elif plan.status == PlanStatus.PARTIAL_SUCCESS:
            failed_step = next((s for s in plan.steps if s.status in (StepStatus.FAILED, StepStatus.VERIFICATION_FAILED)), None)
            raw_err = failed_step.error if failed_step else ''
            if "wasn't ready yet" in raw_err or "browser_not_ready" in raw_err:
                msg = "I opened the website, but the browser connection wasn't ready yet, so I stopped safely."
                spoken = msg
            elif "Live DOM target resolution failed" in raw_err:
                msg = f"Partially completed ({succeeded}/{total} steps). Could not locate the target element on the webpage."
                spoken = f"Partially completed {plan.goal}. Could not locate the target element on the webpage."
            else:
                f_name = failed_step.tool if failed_step else "a step"
                msg = f"Partially completed ({succeeded}/{total} steps). Halted at '{f_name}': {raw_err}"
                spoken = f"Partially completed {plan.goal}. {succeeded} steps succeeded before encountering an issue."
        elif plan.status == PlanStatus.CANCELLED:
            msg = f"Plan execution was cancelled by user ({succeeded}/{total} completed)."
            spoken = f"Cancelled {plan.goal}."
        else:
            failed_step = next((s for s in plan.steps if s.status in (StepStatus.FAILED, StepStatus.VERIFICATION_FAILED)), None)
            if failed_step:
                raw_err = failed_step.error or "Action failed."
                if "wasn't ready yet" in raw_err or "browser_not_ready" in raw_err:
                    msg = "I opened the website, but the browser connection wasn't ready yet, so I stopped safely."
                    spoken = msg
                elif "Live DOM target resolution failed" in raw_err:
                    msg = "Could not locate the target element on the webpage."
                    spoken = msg
                else:
                    msg = f"Plan could not be completed: {raw_err}"
                    spoken = f"Could not complete {plan.goal}."
            elif plan.error_message:
                msg = plan.error_message
                spoken = plan.error_message
            else:
                msg = "Execution failed."
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

    def _activate_application_window(self, app_name: str, launched_pid: Optional[int] = None) -> bool:
        """
        Attempts to locate and bring a launched application's window to the foreground on Windows.
        All Win32/UIA operations run on a fresh thread attached to the interactive 'default' desktop
        via run_on_interactive_desktop(), bypassing the Flask/Antigravity non-interactive desktop context.
        """
        if not app_name:
            return False

        def _do_activate():
            try:
                import ctypes
                from ctypes import wintypes
                import os

                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                app_lower = app_name.lower().strip()

                WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
                user32.EnumWindows.restype = wintypes.BOOL

                candidates = []
                IGNORED_CLASSES = {
                    "UAC_InputIndicatorOverlayWnd", "UAC Input Indicator",
                    "Touch Tooltip Window", "CiceroUIWndFrame", "Default IME",
                    "MSCTFIME UI", "Tooltip", "GDI+ Hook Window Class"
                }

                class RECT(ctypes.Structure):
                    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long), ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

                def enum_cb(hwnd, lparam):
                    if not user32.IsWindowVisible(hwnd):
                        return True

                    c_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, c_buf, 256)
                    cls_name = c_buf.value
                    if cls_name in IGNORED_CLASSES:
                        return True

                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    exe_name = ""
                    if pid.value > 0:
                        hProc = kernel32.OpenProcess(0x1000, False, pid.value)
                        if hProc:
                            buf = ctypes.create_unicode_buffer(260)
                            size = wintypes.DWORD(260)
                            if kernel32.QueryFullProcessImageNameW(hProc, 0, buf, ctypes.byref(size)):
                                exe_name = os.path.basename(buf.value).lower()
                            kernel32.CloseHandle(hProc)

                    t_len = user32.GetWindowTextLengthW(hwnd)
                    t_buf = ctypes.create_unicode_buffer(t_len + 1)
                    user32.GetWindowTextW(hwnd, t_buf, t_len + 1)
                    title = t_buf.value

                    r = RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(r))
                    w = r.right - r.left
                    h = r.bottom - r.top

                    matches = False
                    if launched_pid and pid.value == launched_pid:
                        matches = True
                    elif (app_lower in exe_name) or (app_lower in title.lower()) or (app_lower == cls_name.lower()):
                        matches = True

                    if matches:
                        score = 0
                        if len(title) > 0:
                            score += 10
                        if w > 100 and h > 100:
                            score += 5
                        if app_lower in title.lower():
                            score += 5
                        if launched_pid and pid.value == launched_pid:
                            score += 3
                        candidates.append((score, hwnd, pid.value, exe_name, title, cls_name))
                    return True

                cb_ref = WNDENUMPROC(enum_cb)
                user32.EnumWindows(cb_ref, 0)
                print(f"[SMARTSCRIBE WINDOW ACTIVATION] EnumWindows found {len(candidates)} candidate(s) for '{app_name}' (launched_pid={launched_pid})")

                if not candidates:
                    return False

                candidates.sort(key=lambda c: c[0], reverse=True)
                best_cand = candidates[0]
                target_hwnd = best_cand[1]
                print(f"[SMARTSCRIBE WINDOW ACTIVATION] Best candidate: HWND={target_hwnd}, PID={best_cand[2]}, exe='{best_cand[3]}', title='{best_cand[4]}', score={best_cand[0]}")

                user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
                user32.BringWindowToTop(target_hwnd)

                fore_hwnd = user32.GetForegroundWindow()
                fore_thread = user32.GetWindowThreadProcessId(fore_hwnd, None)
                target_thread = user32.GetWindowThreadProcessId(target_hwnd, None)
                cur_thread = kernel32.GetCurrentThreadId()

                if fore_thread != cur_thread:
                    user32.AttachThreadInput(cur_thread, fore_thread, True)
                if target_thread != cur_thread:
                    user32.AttachThreadInput(cur_thread, target_thread, True)

                # Alt key simulation to bypass foreground lockout
                user32.keybd_event(0x12, 0, 0, 0)
                user32.SetForegroundWindow(target_hwnd)
                user32.keybd_event(0x12, 0, 2, 0)

                if target_thread != cur_thread:
                    user32.AttachThreadInput(cur_thread, target_thread, False)
                if fore_thread != cur_thread:
                    user32.AttachThreadInput(cur_thread, fore_thread, False)

                user32.SwitchToThisWindow(target_hwnd, True)

                # UIAutomation SetFocus
                try:
                    import comtypes.client
                    try:
                        import comtypes.gen.UIAutomationClient as UIA
                    except Exception:
                        comtypes.client.GetModule("UIAutomationCore.dll")
                        import comtypes.gen.UIAutomationClient as UIA
                    uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
                    elem = uia.ElementFromHandle(target_hwnd)
                    if elem:
                        elem.SetFocus()
                except Exception:
                    pass

                return True
            except Exception as e:
                print(f"[SMARTSCRIBE WINDOW ACTIVATION] Warning: {e}")
                return False

        try:
            from backend.assistant.desktop_context import run_on_interactive_desktop
            result = run_on_interactive_desktop(_do_activate, timeout=4.0)
            return bool(result)
        except Exception as e:
            print(f"[SMARTSCRIBE WINDOW ACTIVATION] run_on_interactive_desktop failed: {e}")
            return False

