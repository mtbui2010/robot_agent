"""Round 15, option ``unapplied_change`` (default OFF), through the real AssistLoop: a correction said after the pick swaps the
object (088 shape). The reading of the correction is not clear and the understand step has a question. With the option the robot
asks it once and applies the answer; if the change still cannot be applied the run ends failed and says so, never a success for
the old goals. Without the option the run is what it was."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager

DS, DT = "desk@study room", "dining table@kitchen"
HOUSE = {"mug": DS, "book": DS}
TASK = "Bring the mug from the desk to the dining table."
PLAN = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
REPLAN = steps(f"Place {DS}", "Find book", "Pick book", f"MoveTo {DT}", f"Place {DT}")
SAY = "no wait, not the mug, bring the other thing"


def understood(obj, status="clear", question=""):
    goals = [{"type": "on", "object": obj, "from": "desk", "to": "dining table"}] if obj else []
    return {"status": status, "reason": "", "question": question, "goals": goals}


def llm(answer_word="paperback"):
    return FakeLLM((answer_word, understood("book")),
                   ("other thing", understood(None, "unclear", "Which one do you mean, the book or the notebook?")),
                   default=understood("mug"))


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    r = run_control.manager().start(TASK, user_id="alice", role="member").run
    run_control.begin_run()
    yield r
    reset_manager()
    run_control._CANCEL.clear()


class SayAfter(FakeRobot):
    def execute(self, skill, params, node=None, log_fn=None):
        out = super().execute(skill, params, node, log_fn)
        if skill == "pick" and not getattr(self, "said", False):
            self.said = True
            run_control.manager().inject(run_control.current_run().run_id, SAY, role="member")
        return out


def go(make_assist, env, answers, **opts):
    robot = SayAfter(env, HOUSE, answers=answers)
    loop = make_assist(robot, llm(), ScriptedPlanner(PLAN, replans=[REPLAN]), **opts)
    return robot, loop, run_task(loop, TASK, max_events=3000)


def test_option_registered_and_off_by_default(make_assist, env):
    from robot_agent.contract.manifest import PLANNER_KEYS
    from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
    assert "unapplied_change" in ASSIST_OPTIONS and "unapplied_change" in PLANNER_KEYS
    mk = lambda **kw: make_assist(FakeRobot(env, HOUSE), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["unapplied_change"] is False and mk(unapplied_change="1").cfg["unapplied_change"] is True


def test_088_shape_the_question_is_asked_once_and_the_answer_applies_the_change(make_assist, env, run):
    robot, loop, evs = go(make_assist, env, ["the paperback"], unapplied_change="1")
    assert done(evs)["status"] == "success", [e for e in evs if e["event"] in ("goal_change", "done")]
    assert robot.obj["book"] == DT and robot.obj["mug"] == DS and robot.holding is None      # the mug went back
    assert len(robot.asked) == 1 and "book or the notebook" in robot.asked[0]
    ch = [e for e in evs if e["event"] == "goal_change"]
    assert ch and ch[0]["applied"]


def test_088_shape_no_answer_means_no_success_claim(make_assist, env, run):
    robot, loop, evs = go(make_assist, env, [], unapplied_change="1")
    d = done(evs)
    assert d["status"] == "failed" and "could not apply your change" in d["msg"]
    assert len(robot.asked) >= 1


def test_088_shape_without_the_option_the_run_claims_success_for_the_old_goal(make_assist, env, run):
    robot, loop, evs = go(make_assist, env, [])
    assert done(evs)["status"] == "success" and not robot.asked          # the false success of round 14
    assert not any(e["event"] == "clarify" and e.get("kind") == "goal_change" for e in evs)
