from contracts.models import ToolCall, Decision

def check(tool_call: ToolCall) -> Decision:
    return Decision(decision="allow", reason="stub",score=0.0)
