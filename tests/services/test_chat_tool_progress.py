from app.services.chat_tool_progress import tool_event_status


def test_tool_event_status_distinguishes_progress_failure_and_completion() -> None:
    assert tool_event_status("execute_bash_started", {}) == "running"
    assert tool_event_status("execute_bash_progress", {}) == "running"
    assert tool_event_status("execute_bash", {"exit_code": 2}) == "failed"
    assert tool_event_status("edit_file_failed", {}) == "failed"
    assert tool_event_status("read_file", {}) == "completed"
