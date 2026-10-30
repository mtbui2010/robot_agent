"""v2 memory: SAGE's live memory learns only runs whose goals were seen to hold
(core/planning/episode_memory.py), and GraceBackend hands the robot's seed pool to SAGE."""

import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import episode_memory as em

DT, DS = "dining table@kitchen", "desk@study room"
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
TASK = "Bring the apple from the desk to the dining table."


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


class Remembering(ScriptedPlanner):
    def __init__(self, plan, replans=()):
        super().__init__(plan, replans)
        self.recorded = []

    def record_episode(self, task, steps, meta=None):
        self.recorded.append((task, [dict(s) for s in steps], meta))
        return True


class OldStyle(ScriptedPlanner):                     # a planner whose record_episode takes 2 args
    def __init__(self, plan):
        super().__init__(plan)
        self.recorded = []

    def record_episode(self, task, steps):
        self.recorded.append(task)


# ── the policy ────────────────────────────────────────────────────────
G_ON = {"type": "on", "object": "apple", "from": "", "to": DT}
G_OPEN = {"type": "open", "object": "drawer@kitchen", "from": "", "to": "drawer@kitchen"}


def chk(g, ok):
    return {"goal": em.goal_key(g), "ok": ok, "evidence": "e"}


def test_goals_verified_rules():
    assert em.goals_verified([G_ON], [chk(G_ON, True)]) == (True, "")
    assert not em.goals_verified([G_ON], [])[0]                                  # never looked at
    assert not em.goals_verified([G_ON], [chk(G_ON, None)])[0]                   # could not see it
    assert "seen not to hold" in em.goals_verified([G_ON], [chk(G_ON, False)])[1]
    assert em.goals_verified([G_ON], [chk(G_ON, False), chk(G_ON, True)])[0]     # the latest check counts
    assert em.goals_verified([G_OPEN], [chk(G_OPEN, None)])[0]                   # no sensor: the skill's verify
    assert not em.goals_verified([G_OPEN], [chk(G_OPEN, False)])[0]
    assert not em.goals_verified([], [])[0]


def test_record_mode(monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_MEMORY_RECORD", raising=False)
    assert em.record_mode() == "observed" and em.record_mode("belief") == "belief"
    monkeypatch.setenv("ROBOT_AGENT_MEMORY_RECORD", "off")
    assert em.record_mode() == "off" and em.record_mode("nonsense") == "observed"


def test_record_handles_both_signatures_and_errors():
    p = OldStyle(PLAN)
    assert em.record(p, "t", PLAN, {"evidence": "x"}) and p.recorded == ["t"]

    class Broken:
        def record_episode(self, task, steps, meta=None):
            raise OSError("disk full")
    assert em.record(Broken(), "t", PLAN) is False
    assert em.record(object(), "t", PLAN) is False


# ── end to end through the loops ──────────────────────────────────────
def test_assist_records_after_the_goals_are_seen(make_assist, env, monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_MEMORY_RECORD", raising=False)
    planner = Remembering(PLAN)
    llm = FakeLLM(default=understood(("on", "apple", "desk", "dining table")))
    evs = run_task(make_assist(FakeRobot(env, {"apple": DS}), llm, planner), TASK)
    assert done(evs)["status"] == "success"
    assert [e for e in evs if e["event"] == "goal_check" and e["ok"] is True]
    (task, plan, meta), = planner.recorded
    assert plan[-1]["action"] == "Place" and meta["evidence"] == "goal_observation" and meta["run_id"]


def test_assist_false_success_is_not_recorded(make_assist, env):
    # Place reports success but the apple falls: the goal check sees it missing, the replan has nothing
    planner = Remembering(PLAN)
    robot = FakeRobot(env, {"apple": DS}, faults=[{"skill": "place", "obj": DT, "nth": 1, "kind": "place_fall"}])
    llm = FakeLLM(default=understood(("on", "apple", "desk", "dining table")))
    evs = run_task(make_assist(robot, llm, planner), TASK)
    assert done(evs)["status"] == "failed" and robot.obj["apple"] == "floor"
    assert planner.recorded == []


def test_assist_belief_only_is_not_recorded(make_assist, env):
    planner = Remembering(PLAN)
    llm = FakeLLM(default=understood(("on", "apple", "desk", "dining table")))
    evs = run_task(make_assist(FakeRobot(env, {"apple": DS}), llm, planner, goal_check="belief"), TASK)
    assert done(evs)["status"] == "success" and planner.recorded == []
    planner = Remembering(PLAN)
    evs = run_task(make_assist(FakeRobot(env, {"apple": DS}), llm, planner, goal_check="belief",
                               memory_record="belief"), TASK)
    assert done(evs)["status"] == "success" and planner.recorded[0][2]["evidence"] == "belief"


def _closed(tmp_path, env, monkeypatch, agent_state, planner, **ov):
    import types
    import robot_agent.skill_configs as sc
    import robot_agent.core.planning.loop as LP
    from fakes import FakeSkillRegistry
    from robot_agent.core.run_control import begin_run
    monkeypatch.setattr(sc, "ENV", env)
    monkeypatch.setattr(LP, "reconcile_world", lambda node, world, threshold=0.8: world)
    begin_run()
    robot = FakeRobot(env, {"apple": DT}, start=DT)
    agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(robot),
                                  device_manager=types.SimpleNamespace(_ros_node=None),
                                  _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
    loop = LP.ClosedLoop(agent, log_dir=str(tmp_path / "runs"), live_path=str(tmp_path / "l.jsonl"),
                         mute_skill_tts=False, speak_backend=False, vlm_enabled=False, **ov)
    loop._make_planner = lambda: planner
    return loop


CL_PLAN = steps("Find apple", "Pick apple", "Wait")


def test_closed_loop_success_is_a_belief(tmp_path, env, monkeypatch, agent_state):
    monkeypatch.delenv("ROBOT_AGENT_MEMORY_RECORD", raising=False)
    planner = Remembering(CL_PLAN)
    evs = run_task(_closed(tmp_path, env, monkeypatch, agent_state, planner), TASK)
    assert done(evs)["status"] == "success" and planner.recorded == []
    monkeypatch.setenv("ROBOT_AGENT_MEMORY_RECORD", "belief")
    planner = Remembering(CL_PLAN)
    evs = run_task(_closed(tmp_path, env, monkeypatch, agent_state, planner), TASK)
    assert done(evs)["status"] == "success" and len(planner.recorded) == 1


# ── GraceBackend: seed pool and meta ──────────────────────────────────
def _seed(tmp_path):
    p = tmp_path / "seed.jsonl"
    p.write_text(json.dumps({"task": "Open the kitchen drawer.", "reasoning": "seed",
                             "plan_text": json.dumps({"steps": [{"action": "MoveTo", "object": "drawer@kitchen"},
                                                                {"action": "Open", "object": "drawer@kitchen"}]})}) + "\n")
    return p


def test_grace_backend_seed_and_verified_record(tmp_path, monkeypatch):
    pytest.importorskip("embodiplan")
    from embodiplan.sage import SAGEPlanner
    from robot_agent.core.planning.backends.pyplanner_grace import GraceBackend
    prompts = []

    def chat(self, messages, temperature=0.2):
        prompts.append(messages[-1]["content"])
        return '{"subgoals": []}', 0, 0
    monkeypatch.setattr(SAGEPlanner, "_chat", chat)
    live = tmp_path / "live.jsonl"
    b = GraceBackend(host="http://127.0.0.1:1", model="x", live_path=str(live), seed_memory=str(_seed(tmp_path)))
    b.generate_plan("open the drawer in the kitchen", "", [])
    assert any("Open the kitchen drawer." in p and "(GT," in p for p in prompts)
    plan = [{"action": "MoveTo", "object": "drawer@kitchen"}, {"action": "Open", "object": "drawer@kitchen"}]
    assert b.record_episode("open the drawer in the kitchen", plan, meta={"evidence": "goal_observation"})
    rec = json.loads(live.read_text().splitlines()[-1])
    assert rec["verified"] is True and rec["evidence"] == "goal_observation"
    assert b.record_episode("bad", [{"action": "Open", "object": "drawer@kitchen"}]) is False


def test_grace_backend_without_seed(tmp_path, caplog):
    pytest.importorskip("embodiplan")
    from robot_agent.core.planning.backends.pyplanner_grace import _seed_gt_path
    assert _seed_gt_path(None) == "" and _seed_gt_path(str(tmp_path / "missing.jsonl")) == ""
    assert _seed_gt_path(str(_seed(tmp_path))).endswith("seed.jsonl")
