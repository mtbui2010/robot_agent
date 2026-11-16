"""The own ladder of a failed Pick / MoveTo comes from the declared recovery table (contract.recovery STEP_LADDER +
step_rung), not from numbers in the loop: the pure rung table, the defaults equal to the old numbers, and loop runs
with a made-up robot whose recovery_overrides change the ladder."""

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import (DEFAULT_RECOVERY, STEP_LADDER, FailureCode, Recovery, RecoveryPolicy, RecoveryRule,
                                  step_rung)

R = Recovery
DS, DT = "desk@study room", "dining table@kitchen"
GOALS = {"status": "clear", "reason": "", "question": "",
         "goals": [{"type": "on", "object": "zibble", "from": "desk", "to": "dining table"}]}
PLAN = steps(f"MoveTo {DS}", "Find zibble", "Pick zibble", f"MoveTo {DT}", f"Place {DT}")
TASK = "Put the zibble from the desk on the dining table."


# ── the pure rung table ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ladder, failed, tried, asked, kw, want", [
    (RecoveryRule(R.RETRY, retries=2), None, 1, False, {}, R.RETRY),
    (RecoveryRule(R.RETRY, retries=2), None, 2, False, {}, R.RETRY),
    (RecoveryRule(R.RETRY, retries=2), None, 3, False, {}, R.ASK_HELP),
    (RecoveryRule(R.RETRY, retries=2), None, 1, False, {"can_retry": False}, R.ASK_HELP),
    (RecoveryRule(R.RETRY, retries=2), None, 1, False, {"retries": 0}, R.ASK_HELP),
    (RecoveryRule(R.ASK_HELP, retries=1), None, 1, True, {}, R.ASK_HELP),          # the rule's own action is the rung
    (RecoveryRule(R.RETRY), RecoveryRule(R.RETRY, then=R.REPLAN), 2, True, {"replan_after": True}, R.REPLAN),
    (RecoveryRule(R.RETRY), RecoveryRule(R.RETRY, then=R.REPLAN), 2, True, {}, R.STOP),
    (RecoveryRule(R.RETRY), RecoveryRule(R.RETRY, then=R.ASK_HELP), 2, True, {"replan_after": True}, R.STOP),
    (RecoveryRule(R.RETRY), None, 2, True, {"replan_after": True}, R.STOP),
])
def test_step_rung_table(ladder, failed, tried, asked, kw, want):
    assert step_rung(ladder, failed, tried, asked, **kw) is want


def test_step_rung_default_ladders_equal_the_old_numbers():
    pick, move = (DEFAULT_RECOVERY[STEP_LADDER[a][0]] for a in ("Pick", "MoveTo"))
    assert (pick.action, pick.retries, STEP_LADDER["Pick"][1]) == (R.RETRY, 1, True)       # pick_retries 1, then replan
    assert (move.action, move.retries, STEP_LADDER["MoveTo"][1]) == (R.RETRY, 1, False)    # move_tries <= 1, never replan
    assert DEFAULT_RECOVERY[FailureCode.ABSENT].retries == 3                                # search_max 3


def test_step_ladder_empty_overrides_keep_the_default_table():
    assert RecoveryPolicy({}).table == RecoveryPolicy(None).table == dict(DEFAULT_RECOVERY)


# ── loop runs with a made-up robot ──────────────────────────────────────────────────────────────────
def _policy(monkeypatch, overrides):
    import robot_agent.core.planning.robot as robot_mod
    monkeypatch.setattr(robot_mod, "policy_for", lambda adapter=None: RecoveryPolicy(overrides))


def _kinds(evs):
    return [e["kind"] for e in evs if e["event"] == "recover"]


def _pick_fails(nth):
    return [{"skill": "pick", "kind": "fail", "obj": "zibble", "nth": nth, "msg": "nothing in the gripper"}]


def test_step_ladder_off_default_robot_retries_a_pick_once_then_asks(make_assist, env):
    robot = FakeRobot(env, {"zibble": DS}, faults=_pick_fails([1, 2]), answers=["yes"])
    loop = make_assist(robot, FakeLLM(GOALS), ScriptedPlanner(PLAN))
    assert (loop.cfg["pick_retries"], loop.cfg["search_max"]) == (1, 3)                  # the table's defaults
    evs = run_task(loop, TASK)
    assert done(evs)["status"] == "success" and _kinds(evs) == ["retry", "asked"]


def test_step_ladder_loop_final_declared_retries_replace_the_question(make_assist, env, monkeypatch):
    _policy(monkeypatch, {"grasp_failed": {"action": "retry", "retries": 2, "then": "replan"}})
    robot = FakeRobot(env, {"zibble": DS}, faults=_pick_fails([1, 2]))
    loop = make_assist(robot, FakeLLM(GOALS), ScriptedPlanner(PLAN))
    evs = run_task(loop, TASK)
    assert loop.cfg["pick_retries"] == 2
    assert done(evs)["status"] == "success" and _kinds(evs) == ["retry", "retry"]
    assert robot.asked == [] and robot.obj.get("zibble") == DT                         # nobody asked, the goal holds


def test_step_ladder_loop_final_move_asks_at_once_when_the_rule_has_no_retry(make_assist, env, monkeypatch):
    _policy(monkeypatch, {"blocked": {"action": "retry", "retries": 0, "then": "ask_help"}})
    robot = FakeRobot(env, {"zibble": DS}, answers=["yes"],
                      faults=[{"skill": "move", "kind": "fail", "obj": "dining", "nth": 1, "msg": "path blocked"}])
    evs = run_task(make_assist(robot, FakeLLM(GOALS), ScriptedPlanner(PLAN)), TASK)
    assert done(evs)["status"] == "success" and _kinds(evs) == ["asked"]


def test_step_ladder_ask_help_off_ends_the_pick_ladder_in_the_failed_codes_then(make_assist, env):
    robot = FakeRobot(env, {"zibble": DS}, faults=_pick_fails("all"))
    planner = ScriptedPlanner(PLAN)
    evs = run_task(make_assist(robot, FakeLLM(GOALS), planner, ask_help=False, max_replans=1), TASK)
    assert done(evs)["status"] == "failed" and robot.asked == []
    assert any(c[0] == "replan" for c in planner.calls)                                # GRASP_FAILED then=REPLAN


def test_step_ladder_loop_final_help_question_keeps_its_wording_in_speech(make_assist, env):
    # regression: the ladders' move out of assist_loop.py took the help sentences out of the modules a bench's question
    # parser anchors on (symbolic_robot test_dialog_structured_ra); they live once, in speech.py, and the question is unchanged
    import inspect

    from robot_agent.core.planning import speech, step_ladder
    robot = FakeRobot(env, {"zibble": DS}, faults=_pick_fails([1, 2]), answers=["yes"])
    evs = run_task(make_assist(robot, FakeLLM(GOALS), ScriptedPlanner(PLAN)), TASK)
    assert done(evs)["status"] == "success"
    assert robot.asked == ["I can't pick up the zibble. Could you move it closer to the edge, then say yes?"]
    src = inspect.getsource(step_ladder)
    assert speech.HELP_PICK not in src and speech.HELP_MOVE.endswith("Please clear the way and say yes.") and speech.HELP_MOVE not in src
