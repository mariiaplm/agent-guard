from typing import Literal, Optional
from pydantic import BaseModel

ATTACKER_EMAIL = "attacker@evil-mail.com"
ATTACKER_ACCOUNT = "ACCT-6666"

Provenance = Literal["user", "untrusted_content"]
ToolName = Literal["read_email", "send_email", "transfer_money"]


class Email(BaseModel):
    id: str
    sender: str
    subject: str
    body: str


class ToolCall(BaseModel):
    run_id: str
    step: int                     # order of calls within one run
    user_request: str             # what the user originally asked
    tool_name: ToolName
    args: dict
    provenance: Provenance        # where the instruction behind this call came from
    source_text: Optional[str] = None  # the email/doc text that triggered it, if any


class Decision(BaseModel):
    decision: Literal["allow", "block"]
    reason: str
    score: float = 0.0            # 0 = safe, 1 = malicious


class ToolCallLog(BaseModel):
    """One row per tool call. Feeds the dashboard's live log table."""
    run_id: str
    step: int
    tool_name: ToolName
    args: dict
    provenance: Provenance
    decision: Literal["allow", "block"]
    reason: str
    score: float
    executed: bool                # False if blocked
    result: Optional[str] = None  # what the tool returned (or error)
    timestamp: str


class RunRecord(BaseModel):
    """One row per run (one full agent attempt at one task). Feeds the before/after chart."""
    run_id: str
    task_id: str                       # which benign task the user asked for
    attack_id: Optional[str] = None    # None for benign-only runs
    guard_enabled: bool
    attack_succeeded: bool = False     # set by env canary (ground truth)
    task_succeeded: bool = False       # did the legit task still get done
    timestamp: str


# API shapes
#
#   POST /runs                  StartRunRequest  -> StartRunResponse
#   POST /tool_call             ToolCall         -> ToolCallResponse
#   POST /runs/{run_id}/finish  (no body)        -> RunRecord
#   GET  /stats?experiment=heldout               -> numbers for one experiment only
# The guard function the backend imports:
#   guard/guard.py:  def check(tool_call: ToolCall) -> Decision
class StartRunRequest(BaseModel):
    task_id: str
    attack_id: Optional[str] = None
    guard_enabled: bool
    extra_emails: list[Email] = []     # poisoned emails injected into this run's inbox


class StartRunResponse(BaseModel):
    run_id: str
    user_request: str                  # the task prompt the agent should act on
    experiment: Optional[str] = None  

class ToolCallResponse(BaseModel):
    decision: Decision
    executed: bool
    result: Optional[str] = None       # tool output if executed, else None