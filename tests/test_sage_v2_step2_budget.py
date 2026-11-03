"""SAGE-v2 round 3, step 2 (planning cost, one unit): one sub-goal per understood goal (F1.2), the planner's
REAL calls counted against the run's budget (F0.2), the cap from the robot's manifest (planner.llm_budget).

Before: SAGE decomposed the whole restated task, the 8B model answered step-level sub-goals (7 blocks for one
goal), every block re-planned the task, and one ``generate_plan`` of 1 + N + R calls took ONE unit of the run's
budget, so the edit reserve believed there was room and the bench's outer cap ended the run inside SAGE.
Synthetic ENV / sentences only (the fakebot sim house).
"""
import json
import re

import pytest

from fakes import FakeLLM, FakeRobot, done, run_task, steps
from robot_agent.contract import Manifest
from robot_agent.core.planning.backends.pyplanner_grace import GraceBackend
from robot_agent.core.planning.budget import DEFAULT_LLM_BUDGET

DS, DT = "desk@study room", "dining table@kitchen"


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


class CountingPlanner:
    """A planner without a budget hook that reports the LLM calls it made."""

    def __init__(self, plan, calls):
        self.plan, self.n, self.seen = plan, calls, []

    def generate_plan(self, task, obs, visible_objects):
        self.seen.append(task)
        return [dict(s) for s in self.plan], {"method": "counting", "llm_calls": self.n}

    def replan(self, *a):
        return [], {"llm_calls": 1}


def test_budget_counts_planner_calls(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table")))
    planner = CountingPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"), 7)
    loop = make_assist(robot, llm, planner)
    evs = run_task(loop, "Put the apple from the desk on the dining table.")
    assert done(evs)["status"] == "success"
    # understand (1) + the planner's 7 real calls
    assert loop._last_budget_used == 1 + 7


# ── SAGE itself behind GraceBackend, with a fake LLM backend ──
class Backend:
    def __init__(self, expand, decompose=None, every_call=None):
        self.expand, self.decompose, self.prompts, self.every = dict(expand), decompose, [], every_call

    def chat(self, messages, temperature=None):
        user = messages[-1]["content"]
        self.prompts.append(user)
        if "Break this into ordered sub-goals" in user:
            return json.dumps({"subgoals": self.decompose or ["MoveTo", DS, "Find", "apple", "Pick", "apple"]}), 0, 0
        m = re.search(r"Current sub-goal: (.*)", user)
        return json.dumps({"steps": self.expand.get(m.group(1).strip() if m else "", [])}), 0, 0


def sage(backend):
    from embodiplan.sage import SAGEPlanner
    p = SAGEPlanner(host="http://127.0.0.1:1", model="scripted", enable_memory=False,
                    capabilities={"hands": 1, "find_reaches": False})
    p._backend = backend
    return GraceBackend.wrap(p)


def test_assist_subgoals_from_goals(make_assist, env):
    sg = "put the apple (it is at desk@study room) on dining table@kitchen"
    be = Backend({sg: steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")})
    robot = FakeRobot(env, {"apple": DS})
    loop = make_assist(robot, FakeLLM(understood(("on", "apple", "desk", "dining table"))), sage(be))
    d = done(run_task(loop, "Put the apple from the desk on the dining table."))
    assert d["status"] == "success", d
    assert not any("Break this into ordered sub-goals" in p for p in be.prompts)   # no decompose call
    assert len(be.prompts) == 1 and "Current sub-goal: " + sg in be.prompts[0]
    assert loop._last_budget_used == 2                                       # understand + one expand


def test_sage_subgoals_model_keeps_the_decomposition(make_assist, env):
    be = Backend({}, decompose=["fetch the apple"])
    be.expand["fetch the apple"] = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    robot = FakeRobot(env, {"apple": DS})
    loop = make_assist(robot, FakeLLM(understood(("on", "apple", "desk", "dining table"))), sage(be),
                       sage_subgoals="model")
    assert done(run_task(loop, "Put the apple from the desk on the dining table."))["status"] == "success"
    assert "Break this into ordered sub-goals" in be.prompts[0]


class Endless(Backend):
    """Every expand answers a plan the gate rejects, every refine / edit too: a planner needing ~25 calls."""

    def chat(self, messages, temperature=None):
        self.prompts.append(messages[-1]["content"])
        return json.dumps({"steps": steps("Pick apple", f"Place {DT}")}), 0, 0


def test_budget_cap20_clean_stop(make_assist, env):
    robot = FakeRobot(env, {"apple": DS})
    llm = FakeLLM(understood(("on", "apple", "desk", "dining table"), ("on", "mug", "desk", "dining table")),
                  default=understood(("on", "apple", "desk", "dining table")))
    be = Endless({})
    loop = make_assist(robot, llm, sage(be), max_replans=50)
    assert loop.cfg["llm_budget"] == DEFAULT_LLM_BUDGET == 20
    d = done(run_task(loop, "Put the apple and the mug from the desk on the dining table.", max_events=3000))
    assert d["status"] == "failed", d
    # either the cap stopped it cleanly, or the gate refused the plan: never a crash, never past the cap
    assert d.get("code") in ("budget_exceeded", None) or "checks" in d.get("msg", "")
    assert len(llm.calls) + len(be.prompts) <= 20


def test_manifest_llm_budget_precedence(make_assist, env, monkeypatch):
    robot = FakeRobot(env, {})
    loop = make_assist(robot, FakeLLM(), CountingPlanner([], 1))
    assert loop.cfg["llm_budget"] == DEFAULT_LLM_BUDGET
    from robot_agent.contract import manifest as M
    orig = M.Manifest.from_dict.__func__

    def with_cap(cls, d, source=None):                                          # the robot pack's cap
        return orig(cls, {**d, "planner": {**(d.get("planner") or {}), "llm_budget": 26}}, source)
    monkeypatch.setattr(M.Manifest, "from_dict", classmethod(with_cap))
    assert make_assist(robot, FakeLLM(), CountingPlanner([], 1)).cfg["llm_budget"] == 26
    monkeypatch.setenv("ROBOT_AGENT_LLM_BUDGET", "12")                           # the env wins over it
    assert make_assist(robot, FakeLLM(), CountingPlanner([], 1)).cfg["llm_budget"] == 12
    assert make_assist(robot, FakeLLM(), CountingPlanner([], 1), llm_budget=9).cfg["llm_budget"] == 9


def test_manifest_planner_keys_validate():
    m = Manifest.from_dict({"robot": "x", "planner": {"llm_budget": 26, "translate_budget": 4,
                                                      "translate_model": "some-model:30b"},
                            "limits": {"payload_kg": 1.5}, "safety": {"hazards": []}, "skills": []})
    assert m.planner["llm_budget"] == 26 and m.limits == {"payload_kg": 1.5} and m.to_dict()["limits"]
    from robot_agent.contract.manifest import ManifestError
    with pytest.raises(ManifestError):
        Manifest.from_dict({"robot": "x", "planner": {"llm_budget": "lots"}, "skills": []})
    with pytest.raises(ManifestError):
        Manifest.from_dict({"robot": "x", "limits": {"payload_kg": -1}, "skills": []})
