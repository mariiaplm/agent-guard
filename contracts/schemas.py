from typing import Literal, Optional
from pydantic import BaseModel

Provenance = Literal["user", "untrusted_content"]


class ToolCall(BaseModel):
    run_id: str
    step: int
    user_request: str            # what the user originally asked
    tool_name: str               # read_email | send_email | transfer_money
    args: dict
    provenance: Provenance       # where the instruction behind this call came from
    source_text: Optional[str] = None  # the email/doc text that triggered it, if any


class Decision(BaseModel):
    decision: Literal["allow", "block"]
    reason: str
    score: float = 0.0           # 0 = safe, 1 = definitely malicious


class RunRecord(BaseModel):
    run_id: str
    attack_id: Optional[str] = None   # None for benign tasks
    guard_enabled: bool
    attack_succeeded: bool = False    # set by env canary (ground truth)
    task_succeeded: bool = False      # did the legit task still get done
    timestamp: str