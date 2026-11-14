"""AgentGuard proxy.
CONTAINS ALL ENDPOINTS FOR AGENT AND DASHBOARD
Run from the repo root:
    uvicorn backend.main:app --reload --port 8000
Docs (try endpoints in the browser):  http://localhost:8000/docs
"""
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend import db
from contracts.models import (
    Decision,
    RunRecord,
    StartRunRequest,
    StartRunResponse,
    ToolCall,
    ToolCallResponse,
)
from env import state as env_state
from env.tasks import TASKS, check_task
from env.tools import execute

# Guard: use the real one if it exists, otherwise a stub that allows everything.
try:
    from guard.guard import check as guard_check
    GUARD_SOURCE = "guard.guard"
except ImportError:
    def guard_check(tool_call: ToolCall) -> Decision:
        return Decision(decision="allow", reason="stub guard (guard/guard.py not found)")
    GUARD_SOURCE = "stub"


def run_guard(tc: ToolCall) -> Decision:
    """Call the guard. If it crashes, fail closed."""
    try:
        d = guard_check(tc)
        return d if isinstance(d, Decision) else Decision(**d)
    except Exception as e:
        return Decision(decision="block", reason=f"guard error: {e!r}", score=1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    print(f"[agentguard] DB at {db.DB_PATH}, guard = {GUARD_SOURCE}")
    yield


app = FastAPI(title="AgentGuard", lifespan=lifespan)
app.add_middleware(  # so the dashboard can call the API from the browser
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# agent-facing
@app.post("/runs", response_model=StartRunResponse)
def start_run(req: StartRunRequest):
    if req.task_id not in TASKS:
        raise HTTPException(404, f"Unknown task_id {req.task_id!r}. Options: {list(TASKS)}")
    run_id = uuid.uuid4().hex[:12]
    env_state.create_run(run_id, req.task_id, req.extra_emails)
    db.insert_run(run_id, req.task_id, req.attack_id, req.guard_enabled, req.experiment)
    return StartRunResponse(run_id=run_id, user_request=TASKS[req.task_id].user_request)


@app.post("/tool_call", response_model=ToolCallResponse)
def tool_call(tc: ToolCall):
    run = db.get_run(tc.run_id)
    if run is None:
        raise HTTPException(404, f"Unknown run_id {tc.run_id!r}. Call POST /runs first.")
    if run["status"] == "finished":
        raise HTTPException(409, "Run already finished.")
    try:
        state = env_state.get_run(tc.run_id)
    except KeyError:
        raise HTTPException(410, "Run state lost (server restarted?). Start a new run.")

    if run["guard_enabled"]:
        decision = run_guard(tc)
    else:
        decision = Decision(decision="allow", reason="guard disabled", score=0.0)

    executed = decision.decision == "allow"
    result = execute(state, tc.tool_name, tc.args) if executed else None

    db.insert_tool_call({
        "run_id": tc.run_id, "step": tc.step, "tool_name": tc.tool_name, "args": tc.args,
        "provenance": tc.provenance, "source_text": tc.source_text,
        "decision": decision.decision, "reason": decision.reason, "score": decision.score,
        "executed": executed, "result": result, "timestamp": db.now(),
    })

    if not executed:
        result_for_agent = f"BLOCKED by AgentGuard: {decision.reason}"
    else:
        result_for_agent = result
    return ToolCallResponse(decision=decision, executed=executed, result=result_for_agent)


@app.post("/runs/{run_id}/finish", response_model=RunRecord)
def finish(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Unknown run_id {run_id!r}.")
    try:
        state = env_state.get_run(run_id)
    except KeyError:
        raise HTTPException(410, "Run state lost (server restarted?).")

    attack_succeeded = state.compromised
    task_succeeded = check_task(state)
    ts = db.finish_run(run_id, attack_succeeded, task_succeeded, state.compromise_reason)
    return RunRecord(
        run_id=run_id, task_id=run["task_id"], attack_id=run["attack_id"],
        guard_enabled=run["guard_enabled"], experiment=run.get("experiment"),
        attack_succeeded=attack_succeeded,
        task_succeeded=task_succeeded, timestamp=ts,
    )


# dashboard-facing
@app.get("/health")
def health():
    return {"ok": True, "guard": GUARD_SOURCE}


@app.get("/tasks")
def list_tasks():
    return {tid: t.user_request for tid, t in TASKS.items()}

@app.get("/experiments")
def experiments():
    return db.list_experiments()

@app.get("/runs")
def runs(limit: int = 100, experiment: Optional[str] = None):
    return db.list_runs(limit, experiment)


@app.get("/runs/{run_id}")
def run_detail(run_id: str):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Unknown run_id {run_id!r}.")
    return {"run": run, "tool_calls": db.list_tool_calls(run_id=run_id, limit=1000)}


@app.get("/logs")
def logs(run_id: Optional[str] = None, since_id: int = 0, limit: int = 200):
    return db.list_tool_calls(run_id=run_id, since_id=since_id, limit=limit)


@app.get("/stats")
def get_stats(experiment: Optional[str] = None):
    return db.stats()