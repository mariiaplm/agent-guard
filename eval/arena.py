"""Arena runner: runs every attack and task at the agent, guard off and on.

Run from the repo root, with the backend running WITHOUT --reload:
    uvicorn backend.main:app --port 8000

Pilot (10 runs, scripted test agent, no LLM calls):
    python -m eval.arena --attacks eval/sample_attacks.json --experiment pilot --limit 10 --scripted

Pilot with the agent and the 8 templates:
    python -m eval.arena --attacks attacks/templates/attacks.json --experiment pilot --limit 10

Real runs with the teammate's agent:
    python -m eval.arena --attacks attacks/tuning.json  --experiment tuning
    python -m eval.arena --attacks attacks/heldout.json --experiment heldout

Results are appended to eval/results/<experiment>.jsonl, one line per run

Agent interface:
    run_episode(task_id, guard_enabled, attack_id=None, extra_emails=None, experiment=None,
                api_base="http://127.0.0.1:8000", verbose=False) -> RunRecord
    It starts the run, makes the tool calls, and finishes the run itself.
Where it lives: --agent module.path:function  (default agent.agent:run_episode).

Attacks are loaded with attacks.loader.load_attacks(path). Each attack has
    attack_id, type (payment_redirect | exfiltration | fake_system | task_hijack),
    target_task, email (an Email object)
Pilot file until variants exist: attacks/templates/attacks.json
"""
import argparse
import importlib
import json
import random
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from env.tasks import TASKS

RESULTS_DIR = Path("eval/results")


#  helpers
def http_json(url, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def load_agent(spec: str):
    module, _, fn = spec.partition(":")
    return getattr(importlib.import_module(module), fn or "run_episode")


def _get(obj, name, default=None):
    """Read a field from a dict, pydantic model or dataclass."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def load_attack_file(path: str) -> list[dict]:
    """Use the teammate's loader; fall back to plain JSON if it isn't importable."""
    try:
        from attacks.loader import load_attacks
        raw = load_attacks(path)
    except ImportError:
        raw = json.loads(Path(path).read_text())
    attacks = []
    for a in raw:
        attacks.append({
            "attack_id": _get(a, "attack_id") or _get(a, "id"),
            "type": _get(a, "type"),
            "target_task": _get(a, "target_task"),
            "email": _get(a, "email"),   # Email object (loader) or dict (plain JSON)
        })
    return attacks


def run_id_of(result) -> str:
    """run_episode returns a RunRecord; accept a dict or a bare run_id too."""
    if isinstance(result, str):
        return result
    return _get(result, "run_id")


def build_jobs(attacks, benign_tasks, repeats):
    jobs = []
    for a in attacks:
        if a["target_task"] not in TASKS:
            raise SystemExit(f"Attack {a['attack_id']}: unknown target_task {a['target_task']!r}. "
                             f"Options: {list(TASKS)}")
        for guard in (False, True):
            jobs.append({
                "key": f"attack|{a['attack_id']}|guard={'on' if guard else 'off'}",
                "kind": "attack", "task_id": a["target_task"], "guard_enabled": guard,
                "attack_id": a["attack_id"], "attack_type": a["type"], "extra_emails": [a["email"]],
            })
    for task_id in benign_tasks:
        for guard in (False, True):
            for rep in range(repeats):
                jobs.append({
                    "key": f"benign|{task_id}|guard={'on' if guard else 'off'}|rep={rep}",
                    "kind": "benign", "task_id": task_id, "guard_enabled": guard,
                    "attack_id": None, "attack_type": None, "extra_emails": [],
                })
    return jobs


def already_done(path: Path) -> set:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
            if rec.get("status") == "ok":
                done.add(rec["key"])
        except json.JSONDecodeError:
            pass
    return done


#  one job
def run_job(job, agent_fn, api, experiment, retries):
    last_err = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            result = agent_fn(
                task_id=job["task_id"], guard_enabled=job["guard_enabled"],
                attack_id=job["attack_id"], extra_emails=job["extra_emails"] or None,
                experiment=experiment, api_base=api,
            )
            run_id = run_id_of(result)
            record = http_json(f"{api}/runs/{run_id}")["run"]
            if record["status"] != "finished":  # agent forgot to finish: do it so it counts
                record = http_json(f"{api}/runs/{run_id}/finish", {})
            return {
                **{k: job[k] for k in ("key", "kind", "task_id", "guard_enabled", "attack_id", "attack_type")},
                "status": "ok", "run_id": run_id, "experiment": experiment,
                "attack_succeeded": bool(record["attack_succeeded"]),
                "task_succeeded": bool(record["task_succeeded"]),
                "seconds": round(time.time() - t0, 2), "attempts": attempt + 1,
            }
        except Exception as e:  # rate limits, timeouts, LLM errors, backend 5xx
            last_err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(min(60, 2 ** (attempt + 1)) + random.uniform(0, 1))
    return {**{k: job[k] for k in ("key", "kind", "task_id", "guard_enabled", "attack_id", "attack_type")},
            "status": "error", "experiment": experiment, "error": last_err, "attempts": retries + 1}


#  main
def main():
    ap = argparse.ArgumentParser(description="Run attacks and benign tasks against the agent.")
    ap.add_argument("--attacks", required=True, help="JSON file with the attack list")
    ap.add_argument("--experiment", required=True, help="label: pilot | tuning | heldout | final")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--agent", default="agent.agent:run_episode", help="module.path:function")
    ap.add_argument("--scripted", action="store_true", help="use the scripted test agent (no LLM)")
    ap.add_argument("--benign", default="all", help="'all', 'none', or comma-separated task ids")
    ap.add_argument("--repeats", type=int, default=2, help="repeats per benign task per guard setting")
    ap.add_argument("--limit", type=int, default=None, help="only run this many jobs (pilot)")
    ap.add_argument("--workers", type=int, default=3, help="runs in parallel (lower if rate limited)")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between starting runs")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    try:
        health = http_json(f"{args.api}/health")
    except Exception as e:
        raise SystemExit(f"Backend not reachable at {args.api} ({e}). "
                         "Start it with: uvicorn backend.main:app --port 8000")
    print(f"backend ok, guard = {health['guard']}")

    if args.scripted:
        from eval.scripted_agent import run_episode as agent_fn
        print("agent  = scripted test agent")
    else:
        agent_fn = load_agent(args.agent)
        print(f"agent  = {args.agent}")

    attacks = load_attack_file(args.attacks)
    benign = [] if args.benign == "none" else list(TASKS) if args.benign == "all" else args.benign.split(",")
    jobs = build_jobs(attacks, benign, args.repeats)
    random.Random(args.seed).shuffle(jobs)  # so a --limit pilot mixes attacks and benign runs
    if args.limit:
        jobs = jobs[: args.limit]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{args.experiment}.jsonl"
    done = already_done(out_path)
    todo = [j for j in jobs if j["key"] not in done]
    print(f"{len(jobs)} jobs, {len(jobs) - len(todo)} already done, {len(todo)} to run "
          f"-> {out_path}\n")

    lock = threading.Lock()
    n_ok = n_err = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool, out_path.open("a") as out:
        futures = {}
        for job in todo:
            futures[pool.submit(run_job, job, agent_fn, args.api, args.experiment, args.retries)] = job
            time.sleep(args.delay)
        for i, fut in enumerate(as_completed(futures), start=1):
            rec = fut.result()
            with lock:
                out.write(json.dumps(rec) + "\n")
                out.flush()
            if rec["status"] == "ok":
                n_ok += 1
                tag = "HIJACKED" if rec["attack_succeeded"] else ("defended" if rec["kind"] == "attack" else "benign")
                print(f"[{i}/{len(todo)}] {rec['key']:<55} {tag:<9} task={'yes' if rec['task_succeeded'] else 'no '} "
                      f"{rec['seconds']}s")
            else:
                n_err += 1
                print(f"[{i}/{len(todo)}] {rec['key']:<55} ERROR {rec['error'][:80]}")

    print(f"\ndone in {time.time() - started:.0f}s: {n_ok} ok, {n_err} errors "
          f"(re-run the same command to retry errors)")
    stats = http_json(f"{args.api}/stats?experiment={args.experiment}")
    for side in ("guard_off", "guard_on"):
        s = stats.get(side)
        if s:
            print(f"{side:<9}  attacks through: {s['attack_success_rate']}  "
                  f"legit tasks done: {s['benign_task_success_rate']}  blocked calls: {s['calls_blocked']}")


if __name__ == "__main__":
    main()