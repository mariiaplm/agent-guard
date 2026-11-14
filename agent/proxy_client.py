import requests
from contracts.models import (ToolCall, ToolCallResponse, RunRecord,
                              StartRunRequest, StartRunResponse)

BASE_URL = "http://127.0.0.1:8000"


def start_run(req: StartRunRequest) -> StartRunResponse:
    r = requests.post(f"{BASE_URL}/runs", json=req.model_dump(mode="json"), timeout=30)
    r.raise_for_status()
    return StartRunResponse(**r.json())


def execute(tool_call: ToolCall) -> ToolCallResponse:
    r = requests.post(f"{BASE_URL}/tool_call", json=tool_call.model_dump(mode="json"), timeout=30)
    r.raise_for_status()
    return ToolCallResponse(**r.json())


def finish_run(run_id: str) -> RunRecord:
    r = requests.post(f"{BASE_URL}/runs/{run_id}/finish", timeout=30)
    r.raise_for_status()
    return RunRecord(**r.json())