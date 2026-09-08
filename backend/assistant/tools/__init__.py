from backend.assistant.tool_registry import ToolRegistry, ToolDefinition
from backend.assistant.permission_manager import RiskLevel
from backend.assistant.tools.system_tools import (
    tool_get_system_status, tool_get_current_time, tool_get_disk_status,
    tool_get_process_summary, tool_diagnose_smartscribe, tool_get_storage_status
)
from backend.assistant.tools.media_tools import (
    tool_start_recording, tool_stop_recording, tool_search_transcript, tool_search_local_files,
    tool_open_file, tool_summarize_current_session, tool_delete_recording
)
from backend.assistant.tools.app_tools import (
    tool_open_application, tool_open_website, ALLOWED_APPLICATIONS, APP_ALIAS_MAP
)
from backend.assistant.tools.browser_tools import (
    tool_read_current_page,
    tool_summarize_current_page,
    tool_extract_key_points,
    tool_answer_about_current_page,
    tool_explain_current_page
)
from backend.assistant.tools.desktop_tools import (
    tool_get_active_window,
    tool_find_desktop_files,
    tool_create_note,
    tool_create_folder,
    tool_open_folder
)

def register_all_safe_tools():
    reg = ToolRegistry()

    # 1. get_system_status
    reg.register(ToolDefinition(
        name="get_system_status",
        description="Returns real-time RAM, CPU, GPU, VRAM, and AI job states.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_get_system_status,
        result_schema={"type": "object"}
    ))

    # 2. get_current_time
    reg.register(ToolDefinition(
        name="get_current_time",
        description="Returns current local time, date, day of week, and timezone.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_get_current_time,
        result_schema={"type": "object"}
    ))

    # 3. get_disk_status
    reg.register(ToolDefinition(
        name="get_disk_status",
        description="Returns drive storage usage and SmartScribe recordings disk space.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_get_disk_status,
        result_schema={"type": "object"}
    ))

    # 3b. get_storage_status
    reg.register(ToolDefinition(
        name="get_storage_status",
        description="Returns drive storage usage, free/total space, and SmartScribe file sizes.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_get_storage_status,
        result_schema={"type": "object"}
    ))

    # 4. get_process_summary
    reg.register(ToolDefinition(
        name="get_process_summary",
        description="Returns summary of SmartScribe active tasks and resource governance.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_get_process_summary,
        result_schema={"type": "object"}
    ))

    # 5. search_transcript
    reg.register(ToolDefinition(
        name="search_transcript",
        description="Performs Okapi BM25 search over transcript chunks.",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "session_id": {"type": "integer"}
            },
            "required": ["query"]
        },
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_search_transcript,
        result_schema={"type": "object"}
    ))

    # 6. search_local_files
    reg.register(ToolDefinition(
        name="search_local_files",
        description="Searches media files in SmartScribe recordings directory.",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}}
        },
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_search_local_files,
        result_schema={"type": "object"}
    ))

    # 7. open_application
    reg.register(ToolDefinition(
        name="open_application",
        description="Launches an approved desktop application from the curated allowlist.",
        parameters_schema={
            "type": "object",
            "properties": {"application": {"type": "string"}},
            "required": ["application"]
        },
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_open_application,
        result_schema={"type": "object"},
        allowed_values={"application": list(ALLOWED_APPLICATIONS.keys()) + list(APP_ALIAS_MAP.keys())}
    ))

    # 8. open_website
    reg.register(ToolDefinition(
        name="open_website",
        description="Opens a validated web URL in default browser.",
        parameters_schema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"]
        },
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_open_website,
        result_schema={"type": "object"}
    ))

    # 9. open_file
    reg.register(ToolDefinition(
        name="open_file",
        description="Opens a recording or approved workspace file in its default player/viewer.",
        parameters_schema={
            "type": "object",
            "properties": {"file_name": {"type": "string"}},
            "required": []
        },
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_open_file,
        result_schema={"type": "object"}
    ))

    # 10. start_recording
    reg.register(ToolDefinition(
        name="start_recording",
        description="Starts microphone audio recording.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_start_recording,
        result_schema={"type": "object"}
    ))

    # 11. stop_recording
    reg.register(ToolDefinition(
        name="stop_recording",
        description="Stops active microphone recording and saves file.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_stop_recording,
        result_schema={"type": "object"}
    ))

    # 12. summarize_current_session
    reg.register(ToolDefinition(
        name="summarize_current_session",
        description="Summarizes the active workspace media session.",
        parameters_schema={
            "type": "object",
            "properties": {"session_id": {"type": "integer"}}
        },
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_summarize_current_session,
        result_schema={"type": "object"}
    ))

    # 13. delete_recording (DESTRUCTIVE_ACTION, REQUIRES EXPLICIT CONFIRMATION)
    reg.register(ToolDefinition(
        name="delete_recording",
        description="Permanently deletes a recording session and associated media.",
        parameters_schema={
            "type": "object",
            "properties": {"session_id": {"type": "integer"}}
        },
        risk_level=RiskLevel.DESTRUCTIVE_ACTION,
        requires_confirmation=True,
        execution_fn=tool_delete_recording,
        result_schema={"type": "object"}
    ))

    # 14. read_current_page (READ_ONLY, no confirmation)
    reg.register(ToolDefinition(
        name="read_current_page",
        description="Reads metadata and structural overview of the active browser webpage.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_read_current_page,
        result_schema={"type": "object"}
    ))

    # 15. summarize_current_page (READ_ONLY, no confirmation)
    reg.register(ToolDefinition(
        name="summarize_current_page",
        description="Synthesizes an executive summary of the active webpage using AI.",
        parameters_schema={
            "type": "object",
            "properties": {"detail_level": {"type": "string"}}
        },
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_summarize_current_page,
        result_schema={"type": "object"}
    ))

    # 16. extract_key_points (READ_ONLY, no confirmation)
    reg.register(ToolDefinition(
        name="extract_key_points",
        description="Extracts 3-5 core bullet points from the active browser article.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_extract_key_points,
        result_schema={"type": "object"}
    ))

    # 17. answer_about_current_page (READ_ONLY, no confirmation)
    reg.register(ToolDefinition(
        name="answer_about_current_page",
        description="Answers questions about the active webpage using BM25 chunk retrieval.",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"]
        },
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_answer_about_current_page,
        result_schema={"type": "object"}
    ))

    # 18. explain_current_page (READ_ONLY, no confirmation)
    reg.register(ToolDefinition(
        name="explain_current_page",
        description="Explains the active webpage article in plain, beginner-friendly terms.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_explain_current_page,
        result_schema={"type": "object"}
    ))

    # 19. diagnose_smartscribe (READ_ONLY, no confirmation)
    reg.register(ToolDefinition(
        name="diagnose_smartscribe",
        description="Performs read-only component health diagnosis of Qwen, Whisper, GPU, and RAM.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_diagnose_smartscribe,
        result_schema={"type": "object"}
    ))

    # 20. get_active_window (READ_ONLY, no confirmation)
    reg.register(ToolDefinition(
        name="get_active_window",
        description="Returns the user's currently active foreground window title, process, and application name.",
        parameters_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_get_active_window,
        result_schema={"type": "object"}
    ))

    # 21. find_desktop_files (READ_ONLY, no confirmation)
    reg.register(ToolDefinition(
        name="find_desktop_files",
        description="Safely searches documents and media files across approved workspace roots.",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "file_type": {"type": "string"},
                "limit": {"type": "integer"}
            }
        },
        risk_level=RiskLevel.READ_ONLY,
        requires_confirmation=False,
        execution_fn=tool_find_desktop_files,
        result_schema={"type": "object"}
    ))

    # 22. create_note (LOW_RISK_ACTION, no confirmation)
    reg.register(ToolDefinition(
        name="create_note",
        description="Creates a new markdown or text note inside the approved notes directory with persistence verification.",
        parameters_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "extension": {"type": "string"}
            },
            "required": ["title"]
        },
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_create_note,
        result_schema={"type": "object"}
    ))

    # 23. create_folder (LOW_RISK_ACTION, no confirmation)
    reg.register(ToolDefinition(
        name="create_folder",
        description="Creates a new directory inside approved workspace or notes folders with disk verification.",
        parameters_schema={
            "type": "object",
            "properties": {
                "folder_name": {"type": "string"},
                "parent": {"type": "string"}
            },
            "required": ["folder_name"]
        },
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_create_folder,
        result_schema={"type": "object"}
    ))

    # 24. open_folder (LOW_RISK_ACTION, no confirmation)
    reg.register(ToolDefinition(
        name="open_folder",
        description="Safely opens an approved workspace directory (notes, recordings, workspaces, Downloads) in Explorer.",
        parameters_schema={
            "type": "object",
            "properties": {
                "folder_name": {"type": "string"}
            },
            "required": ["folder_name"]
        },
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=tool_open_folder,
        result_schema={"type": "object"}
    ))

    # 25. execute_plan (LOW_RISK_ACTION wrapper for multi-step task execution)
    def _tool_execute_plan(query: str, plan_id: str = None, **kwargs):
        from backend.assistant.planner import TaskPlanner, PlanExecutor, PlanContext, PlanStatus
        planner = TaskPlanner()
        ctx = PlanContext(user_query=query)
        plan = planner.plan(query, context=ctx)
        if kwargs.get("force_approve") or kwargs.get("already_validated"):
            plan.status = PlanStatus.APPROVED
        executor = PlanExecutor()
        return executor.execute_plan(plan, context=ctx, confirmation_token=kwargs.get("confirmation_token"))

    reg.register(ToolDefinition(
        name="execute_plan",
        description="Plans and sequentially executes a multi-step desktop task with verified outcomes.",
        parameters_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "plan_id": {"type": "string"}
            },
            "required": ["query"]
        },
        risk_level=RiskLevel.LOW_RISK_ACTION,
        requires_confirmation=False,
        execution_fn=_tool_execute_plan,
        result_schema={"type": "object"}
    ))

    return reg
