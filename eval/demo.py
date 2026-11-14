"""Demo: play one attack live, with the guard off, then on.

Run from the repo root with the backend running, and the dashboard open on screen:
    uvicorn backend.main:app --port 8000

Scripted agent (no LLM, reliable on stage):
    python -m eval.demo --scripted

Real agent:
    python -m eval.demo --attacks attacks/templates/attacks.json --attack-id <id>

Options:
    --attack-id ID   which attack to play (default: the first payment_redirect one)
    --no-pause       don't wait for Enter between scenes (for recording)
    --step-delay S   seconds between tool calls with --scripted, so rows appear one by one

Every demo run is labelled with --experiment (default "demo"), so it stays out of the
real numbers. Pick "demo" in the dashboard's Experiment dropdown to show only these runs.
"""
import argparse
import sys
import time

from env.tasks import TASKS
from eval.arena import http_json, load_agent, load_attack_file, run_id_of

#  terminal styling
TTY = sys.stdout.isatty()


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if TTY else text


RED, BLUE, GREEN, DIM, BOLD = "31", "34", "32", "2", "1"


def money(n):
    return f"${float(n):,.2f}"


def describe(call):
    a = call.get("args") or {}
    if call["tool_name"] == "transfer_money":
        return f"Send {money(a.get('amount', 0))} to {a.get('to_account', '?')}"
    if call["tool_name"] == "send_email":
        return f"Email {a.get('to', '?')}" + (f": \"{a['subject']}\"" if a.get("subject") else "")
    if call["tool_name"] == "read_email":
        return "Read the inbox"
    return f"{call['tool_name']} {a}"


def pause(enabled, prompt="Press Enter to continue"):
    if enabled:
        input(c(f"\n  {prompt} ", DIM))
    else:
        time.sleep(1.5)


def rule():
    print(c("  " + "─" * 66, DIM))


#  one scene
def play(agent_fn, attack, guard_on, args, scripted):
    label = "GUARD ON" if guard_on else "GUARD OFF"
    color = BLUE if guard_on else RED
    print()
    rule()
    print(c(f"  {label}", f"{BOLD};{color}"))
    rule()

    kwargs = dict(task_id=attack["target_task"], guard_enabled=guard_on,
                  attack_id=attack["attack_id"], extra_emails=[attack["email"]],
                  experiment=args.experiment, api_base=args.api)
    if scripted:
        kwargs["step_delay"] = args.step_delay
    print(c("  Agent is working…", DIM))
    run_id = run_id_of(agent_fn(**kwargs))

    detail = http_json(f"{args.api}/runs/{run_id}")
    run, calls = detail["run"], sorted(detail["tool_calls"], key=lambda t: t["step"])

    print()
    for call in calls:
        from_email = call["provenance"] == "untrusted_content"
        source = c("  (instruction came from the email)", RED) if from_email else ""
        if call["decision"] == "block":
            print(f"  {call['step']}. {describe(call)}{source}")
            print(c(f"     BLOCKED: {call['reason']}", f"{BOLD};{RED}"))
        else:
            print(f"  {call['step']}. {describe(call)}{source}")
    print()

    if run["attack_succeeded"]:
        reason = run.get("compromise_reason") or "the attacker got what they wanted"
        print(c(f"  HIJACKED: {reason}", f"{BOLD};{RED}"))
    else:
        print(c("  DEFENDED: nothing reached the attacker", f"{BOLD};{BLUE}"))
    done = run["task_succeeded"]
    print(c(f"  The user's real task: {'done' if done else 'not done'}", GREEN if done else DIM))
    print(c(f"  run {run_id}", DIM))
    return run


#  main
def main():
    ap = argparse.ArgumentParser(description="Play one attack with the guard off, then on.")
    ap.add_argument("--attacks", default="eval/sample_attacks.json")
    ap.add_argument("--attack-id", default=None)
    ap.add_argument("--experiment", default="demo")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--agent", default="agent.agent:run_episode")
    ap.add_argument("--scripted", action="store_true", help="use the scripted agent (no LLM)")
    ap.add_argument("--step-delay", type=float, default=1.2, help="scripted only: seconds between calls")
    ap.add_argument("--no-pause", action="store_true")
    args = ap.parse_args()

    try:
        health = http_json(f"{args.api}/health")
    except Exception as e:
        raise SystemExit(f"Backend not reachable at {args.api} ({e}).")
    if health["guard"] == "stub":
        print(c("  Warning: the backend is using the stub guard, so nothing will be blocked.", RED))

    attacks = load_attack_file(args.attacks)
    if args.attack_id:
        attack = next((a for a in attacks if a["attack_id"] == args.attack_id), None)
        if attack is None:
            raise SystemExit(f"No attack {args.attack_id!r}. Options: {[a['attack_id'] for a in attacks]}")
    else:
        attack = next((a for a in attacks if a["type"] == "payment_redirect"), attacks[0])

    if args.scripted:
        from eval.scripted_agent import run_episode as agent_fn
    else:
        agent_fn = load_agent(args.agent)

    email = attack["email"]
    get = (lambda k: email.get(k)) if isinstance(email, dict) else (lambda k: getattr(email, k))
    task = TASKS[attack["target_task"]]

    print()
    print(c("  AgentGuard demo", BOLD))
    print()
    print(f"  The user asks the agent:   {c(task.user_request, BOLD)}")
    print()
    print("  But one email in the inbox was planted by an attacker:")
    print(c(f"    From:    {get('sender')}", DIM))
    print(c(f"    Subject: {get('subject')}", DIM))
    print(f"    {get('body')}")
    pause(not args.no_pause, "Press Enter to run the agent with the guard OFF")

    play(agent_fn, attack, guard_on=False, args=args, scripted=args.scripted)
    pause(not args.no_pause, "Press Enter to run the same attack with the guard ON")

    play(agent_fn, attack, guard_on=True, args=args, scripted=args.scripted)
    print()


if __name__ == "__main__":
    main()