"""Option ``accept_goals`` (env ROBOT_AGENT_ACCEPT_GOALS, manifest planner.accept_goals; default OFF): the caller hands
run_blocking its goals and the understand step is skipped. Through the real AssistLoop.run_blocking against the
symbolic FakeRobot, a scripted planner and an LLM that fails the test when it is asked anything."""
import json

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, steps
from robot_agent.contract.manifest import PLANNER_KEYS

DS, DT = "desk@study room", "dining table@kitchen"
HOUSE = {"apple": DS, "mug": DS}
TASK = "Bring the apple from the desk to the dining table."
PLAN = lambda: steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")   # noqa: E731
GIVEN = [{"type": "on", "object": "apple", "from": DS, "to": DT}]
UNDERSTOOD = {"status": "clear", "reason": "", "question": "", "goals": [
    {"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}


def scrub(x):
    """The events without wall-clock values (ts, t, run_id, elapsed_s) at any depth, and without the run-start
    reconcile (the shared fake state keeps the first run's pose for the second one)."""
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


def drain(loop, task=TASK, **kw):
    evs = [{k: v for k, v in ev.items() if k not in ("world", "say_audio")} for ev in loop.run_blocking(task, **kw)]
    return evs


def test_given_goals_skip_the_understand_step(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM()                                    # any call raises: the loop may not ask the model
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN()), accept_goals="1")
    evs = drain(loop, goals=GIVEN)
    assert done(evs)["status"] == "success", done(evs)
    assert llm.calls == [] and robot.asked == []
    u = next(e for e in evs if e["event"] == "understand")
    assert u["status"] == "clear" and u["reason"] == "goals given by the caller" and u["question"] is None
    assert [(g["type"], g["object"], g["from"], g["to"]) for g in u["goals"]] == [("on", "apple", DS, DT)]
    assert robot.obj["apple"] == DT


def test_given_goals_are_grounded_not_filtered(make_assist, env):
    """Goals the request's words never mention are kept (the understand step's own filters do not run on the
    caller's goals); a source the robot has no memory of is left empty, so it searches (no invented location)."""
    robot = FakeRobot(env, HOUSE)
    loop = make_assist(robot, FakeLLM(), ScriptedPlanner(PLAN()), accept_goals="1")
    evs = drain(loop, "Do the usual thing.", goals=GIVEN)
    u = next(e for e in evs if e["event"] == "understand")
    assert [(g["object"], g["from"], g["to"]) for g in u["goals"]] == [("apple", "", DT)]
    assert done(evs)["status"] == "success"


def test_option_off_ignores_given_goals_byte_identical(make_assist, env):
    """Off (the default): goals passed in are ignored, the run is the usual one with the usual LLM call."""
    def run(**kw):
        robot = FakeRobot(env, HOUSE)
        llm = FakeLLM(UNDERSTOOD)
        evs = drain(make_assist(robot, llm, ScriptedPlanner(PLAN())), **kw)
        return json.dumps(scrub(evs), sort_keys=True, default=str), len(llm.calls), robot.trace
    plain, with_goals = run(), run(goals=GIVEN)
    assert with_goals == plain and plain[1] == 1


def test_option_on_without_goals_understands_as_usual(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM(UNDERSTOOD)
    evs = drain(make_assist(robot, llm, ScriptedPlanner(PLAN()), accept_goals="1"))
    assert done(evs)["status"] == "success" and len(llm.calls) == 1
    evs = drain(make_assist(FakeRobot(env, HOUSE), FakeLLM(UNDERSTOOD), ScriptedPlanner(PLAN()), accept_goals="1"), goals=[])
    assert done(evs)["status"] == "success"


def test_flag_default_off_option_env_manifest(make_assist, monkeypatch, env):
    monkeypatch.delenv("ROBOT_AGENT_ACCEPT_GOALS", raising=False)
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["accept_goals"] is False
    assert mk(accept_goals="1").cfg["accept_goals"] is True
    monkeypatch.setenv("ROBOT_AGENT_ACCEPT_GOALS", "1")
    assert mk().cfg["accept_goals"] is True
    assert mk(accept_goals="0").cfg["accept_goals"] is False
    monkeypatch.delenv("ROBOT_AGENT_ACCEPT_GOALS")
    lp = mk()
    lp.robot.manifest.planner = dict(lp.robot.manifest.planner or {}, accept_goals=1)
    assert lp._v2_cfg({})["accept_goals"] is True
    assert "accept_goals" in PLANNER_KEYS

