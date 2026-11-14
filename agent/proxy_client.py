import json 
from contracts.models import ToolCall, Decision, ToolCallResponse 
from guard.guard import check 
from agent import stub_env as env

def execute(tool_call: ToolCall, guard_enabled: bool = True) -> ToolCallResponse:
    if guard_enabled:
        decision = check(tool_call)
    else:
        decision = Decision(decision="allow", reason="guard disabled", score=0.0)

    if decision.decision == "block":
        return ToolCallResponse(decision=decision, executed=False, result=None)

    fn = env.TOOLS.get(tool_call.tool_name)
    if fn is None:
        return ToolCallResponse(decision=decision, executed=False, result=f"unknown tool {tool_call.tool_name}")
    try:
        output = fn(**tool_call.args)
    except TypeError as e:
        return ToolCallResponse(decision=decision, executed=False, result=f"bad args: {e}")

    return ToolCallResponse(decision=decision, executed=True, result=json.dumps(output))