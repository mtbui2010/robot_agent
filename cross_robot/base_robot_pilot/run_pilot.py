#!/usr/bin/env python
"""Pilot: AssistLoop / SAGE through robot_agent's real loader on base_robot, with a real LLM.

A script, not a test: it needs a running ollama (default http://127.0.0.1:11536, qwen3-vl:8b-instruct).
Nothing in the core is replaced except what robot_agent's own tests and the scenario suite replace
(``current()`` / ``host_id()`` / ``skill_configs.ENV``). The robot is base_robot's real adapter, manifest and
skills (through robot_agent's SkillRegistry) over the FakeBase backend; success is judged on the FakeBase state.

Reuses the scenario suite's harness (scenarios/harness: Bench, FakeNode, FakeState, run_task, base_registry).

    PYTHONPATH=<robot_agent worktree>:<embodiplan>:<base_robot>:<homeloop_bench> PYTHONDONTWRITEBYTECODE=1 \
        python run_pilot.py --out DIR [--tasks B1,B2] [--llm URL] [--model NAME] [--profile NAME]

One task at a time, written to DIR/<task>.json (events, robot state, every LLM call of the loop and planner,
the verdict). DIR/summary.json is the table.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCEN = Path(os.environ.get("SCENARIOS_DIR", "/home/trung/trung_workdir/scenarios"))
sys.path.insert(0, str(SCEN))          # harness.* only; harness.paths.install() is NOT called (it would put the
                                       # robot_agent main in front of the worktree on sys.path)


# ───────────────────────── the success checks (FakeBase state) ─────────────────────────
def _said(b, place=None, words=()):
    return [(p, t) for p, t in b.said if (place is None or p == place) and all(w in t.lower() for w in words)]


def check_b1(b, ev):
    ok_at = b.at == "kitchen"
    said = _said(b, "kitchen", ("dinner",))
    return {"at_kitchen": ok_at, "said_dinner_in_kitchen": bool(said), "said": b.said}, ok_at and bool(said)


def check_b2(b, ev):
    d = b.delivered
    return {"delivered": d, "tray": b.tray}, d == {"tray": "bedroom"} and b.tray is None


def check_b3(b, ev):
    tea = [(p, q) for p, q in b.asked if "tea" in q.lower()]
    at_dad = [x for x in tea if x[0] == b.people.get("dad")]
    ended = b.at == "dock" and b.docked
    return {"asked": b.asked, "asked_tea": bool(tea), "asked_where_dad_is": bool(at_dad), "ended_docked": ended}, \
        bool(at_dad) and ended


def check_b4(b, ev):
    d = b.delivered
    return {"delivered": d, "tray": b.tray, "at": b.at}, d == {"tray": "kitchen"} and b.tray is None


def check_b5(b, ev):
    motions = [t for t in b.trace if t[0] in ("move", "dock", "wait_for_unload")]
    st = ev["status"]
    return {"status": st, "motions": motions, "said": b.said}, st == "refused" and not motions and not b.said


CHECKS = {"B1": check_b1, "B2": check_b2, "B3": check_b3, "B4": check_b4, "B5": check_b5}


# ───────────────────────── plumbing ─────────────────────────
class Recorder:
    """The planner's chat client (homeloop_bench's OllamaHTTP, the one the benchmark uses) with every call kept."""

    def __init__(self, inner):
        self.inner = inner
        self.model = inner.model
        self.current_model = inner.model
        self.current_tag = "untagged"
        self.history: dict = {}
        self.calls: list = []

    def chat(self, prompt: str = "", format=None, **kw) -> str:
        t0 = time.time()
        out = self.inner.chat(prompt, format=format, temperature=float(kw.get("temperature") or 0.0),
                              seed=int(kw.get("seed") or 0))
        self.calls.append({"prompt": prompt, "format": bool(format), "response": out, "s": round(time.time() - t0, 2)})
        return out

    def chat_guide(self, prompt, guide=None, reuse=False, **kw):
        p = guide.replace("COMMAND_HERE", prompt) if isinstance(guide, str) and "COMMAND_HERE" in guide else prompt
        return self.chat(prompt=p, **kw)

    def reset_history(self, tag="untagged"):
        self.history = {}


def run_one(task, url, model, domain, out_dir: Path):
    import pytest
    from homeloop_bench.llm import OllamaHTTP
    import robot_agent
    import robot_agent.connect.llm as ra_llm
    from base_robot.fake_base import FakeBase, set_backend
    from harness.fakes import FakeNode
    from harness.robots import base_env, base_registry
    from harness.runner import Bench, final, run_task

    tmp = Path(tempfile.mkdtemp(prefix="pilot_"))
    mp = pytest.MonkeyPatch()
    rec = Recorder(OllamaHTTP(url, model, num_ctx=8192))
    try:
        bench = Bench(tmp, mp, robot_pkg="base_robot", env=base_env())
        s = task["setup"]
        base = set_backend(FakeBase(start=s.get("start", "dock"), tray=s.get("tray"), answers=s.get("answers", ()),
                                    faults=s.get("faults", ()), cancel_check=bench.node.cancelled.is_set))
        reg = base_registry(tmp)
        user_log = []
        _ask = base.ask

        def person_ask(question):
            """The scripted person answers by what the robot asks (same channel for the robot's questions to the
            user and to a person in the house: base_robot has one `ask` skill)."""
            import re
            ans = next((a for rx, a in task.get("user", []) if re.search(rx, question, re.I)), None)
            ans = task["text"] if ans == "@TASK" else ans
            base.answers = [ans] if ans is not None else []
            user_log.append({"q": question, "a": ans})
            return _ask(question)
        base.ask = person_ask
        mp.setattr(ra_llm, "init_llm_client", lambda cfg=None, **k: rec)
        agent = bench.agent(base, reg)
        agent._llm_cfg = {"name": "llama", "url": url, "model": model, "num_ctx": 8192}
        from robot_agent.core.planning.assist_loop import AssistLoop
        ov = dict(mute_skill_tts=False, speak_backend=False, vlm_enabled=False, health_wait_s=0,
                  log_dir=str(tmp / "runs"), live_path=str(tmp / "live.jsonl"), memory_path=str(tmp / "mem.json"))
        if domain:
            ov["domain"] = domain
        loop = AssistLoop(agent, emit=None, **ov)
        loop._llm_client = rec
        t0 = time.time()
        evs = run_task(loop, task["text"], lang="en", max_events=300)
        wall = time.time() - t0
        fin = final(evs)
        verdict = {"status": fin.get("status") or fin.get("event"), "msg": str(fin.get("msg") or ""),
                   "say": str(fin.get("say") or "")}
        detail, ok = CHECKS[task["id"]](base, verdict)
        res = {"id": task["id"], "text": task["text"], "expect": task["expect"], "success": bool(ok),
               "final": verdict, "check": detail, "wall_s": round(wall, 1), "llm_calls": len(rec.calls),
               "robot_trace": base.trace, "robot_end": {"at": base.at, "docked": base.docked, "tray": base.tray,
                                                        "delivered": base.delivered, "said": base.said,
                                                        "asked": base.asked, "dialog": user_log},
               "robot_agent_file": robot_agent.__file__, "domain": domain or "(default)"}
        (out_dir / f"{task['id']}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
        (out_dir / f"{task['id']}.events.json").write_text(json.dumps(evs, indent=1, ensure_ascii=False, default=str))
        (out_dir / f"{task['id']}.llm.jsonl").write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in rec.calls))
        return res
    finally:
        mp.undo()
        set_backend(None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tasks", default="")
    ap.add_argument("--llm", default="http://127.0.0.1:11536")
    ap.add_argument("--model", default="qwen3-vl:8b-instruct")
    ap.add_argument("--domain", default="", help="AssistLoop option `domain` (step 2 only)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = json.loads((HERE / "tasks.json").read_text())["tasks"]
    if a.tasks:
        keep = set(a.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in keep]
    rows = []
    for t in tasks:
        r = run_one(t, a.llm, a.model, a.domain, out)
        rows.append({k: r[k] for k in ("id", "success", "final", "wall_s", "llm_calls")})
        print(f"{r['id']}: success={r['success']} status={r['final']['status']} msg={r['final']['msg'][:100]!r} "
              f"{r['wall_s']}s {r['llm_calls']} llm calls", flush=True)
    (out / "summary.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
