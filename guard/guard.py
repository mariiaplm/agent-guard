from contracts.models import ToolCall, Decision
from guard.rules import check_rules
from guard.judge import judge 

def check(tool_call: ToolCall) -> Decision:
    """Rules first (fast, free). Judge only when the rules can't decide."""
    decision = check_rules(tool_call)
    if decision is not None:
        return decision
    return judge(tool_call)
