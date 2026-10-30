"""Fix stream `plan`, ClosedLoop: no success without doing the task (D20), no motion for a plan
the robot cannot bind (D21)."""

import types

import pytest

from fakes import FakeRobot, FakeSkillRegistry, ScriptedPlanner, done, run_task, steps

DS, DT = "desk@study room", "dining table@kitchen"
HOUSE = {"apple": DT}


@pytest.fixture
def closed(agent_state, tmp_path, monkeypatch, env):
    import robot_agent.skill_configs as sc
    import robot_agent.core.planning.loop as LP
    from robot_agent.core.run_control import begin_run
    monkeypatch.setattr(sc, "ENV", env)
    monkeypatch.setattr(LP, "reconcile_world", lambda node, world, threshold=0.8: world)
    begin_run()

    def make(planner, faults=(), start="entrance"):
        bot = FakeRobot(env, HOUSE, faults=faults, start=start)
        agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(bot),
                                      device_manager=types.SimpleNamespace(_ros_node=None),
                                      _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
        loop = LP.ClosedLoop(agent, log_dir=str(tmp_path / "runs"), live_path=str(tmp_path / "l.jsonl"),
                             mute_skill_tts=False, speak_backend=False, vlm_enabled=False)
        loop._make_planner = lambda: planner
        return loop, bot
    return make


def _motions(bot):
    return [t for t in bot.trace if t[0] in ("move", "pick", "place", "open_drawer", "close_drawer")]


# ── D20 ──
def test_empty_plan_is_a_failure_with_a_reason(closed):
    loop, bot = closed(ScriptedPlanner([]))
    d = done(run_task(loop, "go to the kitchen"))
    assert d["status"] == "failed" and "no plan" in d["msg"]


def test_empty_replan_after_a_failure_is_not_success(closed):
    fault = [{"skill": "move", "kind": "fail", "obj": None, "nth": "all", "msg": "navigation failed: path blocked"}]
    loop, bot = closed(ScriptedPlanner(steps("MoveTo DiningTable"), replans=[[]]), faults=fault)
    d = done(run_task(loop, "go to the dining table"))
    assert d["status"] == "failed"
    assert "no new plan" in d["msg"] and "path blocked" in d["msg"]


def test_a_full_run_is_still_success(closed):
    loop, bot = closed(ScriptedPlanner(steps("Find apple", "Pick apple", "Wait")), start=DT)
    evs = run_task(loop, "bring the apple")
    assert done(evs)["status"] == "success"
    assert bot.holding == "apple"


# ── D21 ──
def test_unbindable_step_refused_before_any_motion(closed):
    planner = ScriptedPlanner(steps("MoveTo DiningTable", "Find apple", "Pick apple", "TurnOn lamp"))
    loop, bot = closed(planner)
    evs = run_task(loop, "turn on the lamp")
    d = done(evs)
    assert d["status"] == "failed" and "TurnOn" in d["msg"]
    assert _motions(bot) == [], "drove off before reaching the step it cannot do"
    assert [c[0] for c in planner.calls] == ["generate_plan", "replan"], "asked the planner once for a plan without it"
    assert any(e["event"] == "gate" for e in evs)


def test_unbindable_plan_refined_once_then_runs(closed):
    good = steps("Find apple", "Pick apple")
    planner = ScriptedPlanner(steps("Find apple", "TurnOn apple"), replans=[good])
    loop, bot = closed(planner, start=DT)
    evs = run_task(loop, "pick up the apple")
    assert done(evs)["status"] == "success" and bot.holding == "apple"
    assert [t[0] for t in bot.trace if t[0] != "grasp_succeed"][:2] == ["find", "pick"]


def test_refined_plan_still_unbindable_fails(closed):
    planner = ScriptedPlanner(steps("TurnOn lamp"), replans=[steps("TurnOff lamp")])
    loop, bot = closed(planner)
    d = done(run_task(loop, "turn on the lamp"))
    assert d["status"] == "failed" and bot.trace == []
