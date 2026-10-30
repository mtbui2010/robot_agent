"""Fix stream `understand`: LLM / planner errors end the run with a clear message (D9, D10), a non-JSON
understand answer is repaired by the LLM before the person is asked (D11), and the run's LLM calls are capped."""

import types

import pytest

from fakes import FakeLLM, FakeRobot, FakeSkillRegistry, ScriptedPlanner, done, run_task, steps

DS, DT, CT = "desk@study room", "dining table@kitchen", "counter@kitchen"
HOUSE = {"apple": DS, "mug": DS}
DOWN = "Cannot reach Ollama: connection refused"
APPLE = {"status": "clear", "reason": "", "question": "",
         "goals": [{"type": "on", "object": "apple", "from": "desk", "to": "dining table"}]}
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
TASK = "Bring the apple from the desk to the dining table."


class RaisingLLM(FakeLLM):
    """FakeLLM whose understand calls raise `exc` (translations / other prompts never come here)."""

    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def chat(self, prompt="", format=None, **kw):
        self.calls.append((prompt, format))
        raise self.exc


class DownPlanner(ScriptedPlanner):
    def __init__(self, plan=(), down_at="generate"):
        super().__init__(plan)
        self.down_at = down_at

    def generate_plan(self, task, obs, visible_objects):
        if self.down_at == "generate":
            self.calls.append(("generate_plan", task))
            raise ConnectionError(DOWN)
        return super().generate_plan(task, obs, visible_objects)

    def replan(self, *a, **k):
        self.calls.append(("replan",))
        raise ConnectionError(DOWN)


@pytest.mark.parametrize("exc, code", [(ConnectionError(DOWN), "hw_fault"),
                                       (TimeoutError("Read timed out. (read timeout=60)"), "timeout")])
def test_llm_down_in_understand_ends_with_a_spoken_reason(make_assist, env, exc, code):
    robot = FakeRobot(env, HOUSE)
    evs = run_task(make_assist(robot, RaisingLLM(exc), ScriptedPlanner(PLAN)), TASK)
    d = evs[-1]
    assert d["event"] == "done" and d["status"] == "failed" and d["code"] == code
    assert "language model is not reachable" in d["msg"] and "language model" in d["say"]
    assert robot.trace == [] and not any(e["event"] == "error" for e in evs)


def test_planner_down_is_not_reported_as_no_plan(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    d = done(run_task(make_assist(robot, FakeLLM(default=APPLE), DownPlanner()), TASK))
    assert d["status"] == "failed" and d["msg"].startswith("the planner is not reachable") and d["code"] == "hw_fault"
    assert "no plan" not in d["msg"] and robot.trace == []


def test_planner_down_at_replan_stops_without_the_old_future(make_assist, env):
    robot = FakeRobot(env, dict(HOUSE, apple=CT))            # not on the desk: the Find fails, a replan is needed
    loop = make_assist(robot, FakeLLM(default=APPLE), DownPlanner(PLAN, down_at="replan"))
    d = done(run_task(loop, TASK))
    assert d["status"] == "failed" and "planner is not reachable" in d["msg"], d
    assert [s for s, *_ in robot.trace] == ["move", "find"]   # nothing after the failed Find


def test_non_json_understand_is_repaired_before_asking(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM("I think the user wants the apple moved.", default=APPLE)
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner(PLAN)), TASK))
    assert d["status"] == "success" and robot.asked == []
    assert "was not valid JSON" in llm.calls[1][0] and "I think the user wants" in llm.calls[1][0]


def test_non_json_twice_asks_the_person_to_rephrase(make_assist, env):
    robot = FakeRobot(env, HOUSE)                             # no answers: the run ends on the silence
    llm = FakeLLM(default="no json here")
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner(PLAN)), TASK))
    assert d["status"] == "failed" and robot.trace[0][0] == "ask"
    assert "say the task in other words" in robot.asked[0]


def test_llm_budget_caps_the_understand_calls(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    llm = FakeLLM(default="no json here")
    d = done(run_task(make_assist(robot, llm, ScriptedPlanner(PLAN), llm_budget=1), TASK))
    assert d["status"] == "failed" and "budget" in d["msg"] and len(llm.calls) == 1


def test_closed_loop_planner_down_says_why(agent_state, tmp_path, monkeypatch, env):
    import robot_agent.skill_configs as sc
    import robot_agent.core.planning.loop as LP
    monkeypatch.setattr(sc, "ENV", env)
    monkeypatch.setattr(LP, "reconcile_world", lambda node, world, threshold=0.8: world)
    bot = FakeRobot(env, HOUSE)
    agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(bot), device_manager=types.SimpleNamespace(_ros_node=None),
                                  _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
    loop = LP.ClosedLoop(agent, log_dir=str(tmp_path / "runs"), live_path=str(tmp_path / "l.jsonl"),
                         mute_skill_tts=False, speak_backend=False, vlm_enabled=False)
    loop._make_planner = lambda: DownPlanner()
    evs = run_task(loop, "go to the kitchen")
    d = evs[-1]
    assert d["event"] == "done" and d["status"] == "failed" and d["code"] == "hw_fault"
    assert "planner is not reachable" in d["msg"] and d["say"] and bot.trace == []


def test_error_classification():
    from robot_agent.contract import FailureCode
    from robot_agent.core.planning.understand import LLMUnavailable, ask_json, LLMBadOutput, LLMBudget, LLMBudgetExceeded
    assert LLMUnavailable(TimeoutError("x")).code is FailureCode.TIMEOUT
    assert LLMUnavailable(RuntimeError("HTTPConnectionPool: Read timed out")).code is FailureCode.TIMEOUT
    assert LLMUnavailable(OSError("Connection refused")).code is FailureCode.HW_FAULT
    bug = LLMUnavailable(KeyError("message"))
    assert bug.code is FailureCode.SOFTWARE and bug.message("planner").startswith("the planner failed")
    with pytest.raises(LLMBadOutput):
        ask_json(FakeLLM(default="prose"), "p", {}, retries=1)
    b = LLMBudget(2)
    assert ask_json(FakeLLM("```json\n{\"a\": 1}\n```"), "p", {}, budget=b) == {"a": 1} and b.used == 1
    with pytest.raises(LLMBudgetExceeded):
        ask_json(FakeLLM(default="prose"), "p", {}, budget=b, retries=3)
