"""D15 / D16 / D19 in the real AssistLoop: pending MODIFY / ADD / DROP / NEW applied at step boundaries,
typed answers, a stop during the understand LLM call (fake robot, scripted LLM and planner)."""

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager

DS, DT, CT, SF = "desk@study room", "dining table@kitchen", "counter@kitchen", "sofa@living room"
HOUSE = {"apple": DS, "mug": DS, "banana": DT}
TASK = "Bring the apple from the desk to the dining table."
PLAN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")


def understood(*goals, status="clear"):
    return {"status": status, "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


APPLE = understood(("on", "apple", "desk", "dining table"))


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    mgr = run_control.manager()
    r = mgr.start(TASK, user_id="alice", role="member").run
    run_control.begin_run()                         # this test thread is the run's executive
    yield r
    reset_manager()
    run_control._CANCEL.clear()


class SayAfter(FakeRobot):
    """Says ``text`` into the run right after the ``n``-th call of ``skill`` (the way a person
    speaks while the robot works); the loop sees it at the next step boundary."""

    def __init__(self, *a, say=(), **k):
        super().__init__(*a, **k)
        self.say = list(say)                         # (skill, text, role)

    def execute(self, skill, params, node=None, log_fn=None):
        out = super().execute(skill, params, node, log_fn)
        if self.say and self.say[0][0] == skill:
            _s, text, role = self.say.pop(0)
            self.injected = run_control.manager().inject(run_control.current_run().run_id, text, role=role)
        return out


def changes(evs):
    return [e for e in evs if e["event"] == "goal_change"]


def test_modify_changes_the_goal_and_repairs_the_plan(make_assist, env, run):
    robot = SayAfter(env, HOUSE, say=[("move", "actually, put it on the counter instead", "member")])
    planner = ScriptedPlanner(PLAN, replans=[steps("Find apple", "Pick apple", f"MoveTo {CT}", f"Place {CT}")])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner), TASK)
    assert robot.injected.action == "queued" and done(evs)["status"] == "success"
    assert robot.obj["apple"] == CT
    ch = changes(evs)[0]
    assert ch["applied"] and ch["intent"] == "modify" and ch["goals"][0]["to"] == CT and "counter" in ch["say"]
    rp = [c for c in planner.calls if c[0] == "replan"][0]
    assert rp[1]["action"] == "Change" and "changed the goals" in rp[2]
    assert run.pending == []


def test_drop_of_the_held_object_puts_it_back(make_assist, env, run):
    robot = SayAfter(env, HOUSE, say=[("pick", "forget the apple", "member")])
    planner = ScriptedPlanner(PLAN, replans=[steps(f"Place {DS}")])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner), TASK)
    assert done(evs)["status"] == "success" and robot.obj["apple"] == DS and robot.holding is None
    ch = changes(evs)[0]
    assert ch["intent"] == "drop" and ch["removed"][0]["object"] == "apple"
    assert ch["goals"] == [{"type": "on", "object": "apple", "from": "", "to": DS}]   # put back where it was


def test_add_merges_into_the_goals(make_assist, env, run):
    robot = SayAfter(env, HOUSE, say=[("move", "also bring the mug to the dining table", "member")])
    planner = ScriptedPlanner(PLAN, replans=[steps("Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}",
                                                   f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}",
                                                   f"Place {DT}")])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner), TASK)
    assert done(evs)["status"] == "success" and robot.obj["apple"] == DT and robot.obj["mug"] == DT
    assert [g["object"] for g in changes(evs)[0]["goals"]] == ["apple", "mug"]


def test_unreadable_change_is_told_not_lost(make_assist, env, run):
    from robot_agent.interact.intent_router import Intent, IntentDecision
    robot = FakeRobot(env, HOUSE)
    loop = make_assist(robot, FakeLLM(APPLE, default=APPLE), ScriptedPlanner(PLAN))
    run_control.manager().inject(run.run_id, "hmm, maybe differently",
                                 decision=IntentDecision(Intent.MODIFY, "hmm, maybe differently", "test"))
    evs = run_task(loop, TASK)
    ch = changes(evs)[0]
    assert not ch["applied"] and "could not apply" in ch["say"] and done(evs)["status"] == "success"


def test_new_command_asks_and_defaults_to_after(make_assist, env, run):
    robot = SayAfter(env, HOUSE, say=[("move", "bring the banana to the sofa", "member")])
    planner = ScriptedPlanner(PLAN, replans=[steps("Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}",
                                                   "Find banana", "Pick banana", f"MoveTo {SF}", f"Place {SF}")])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner), TASK)
    assert robot.injected.decision.intent.value == "new"
    q = [e for e in evs if e["event"] == "clarify" and e.get("kind") == "new_task"]
    assert q and "banana" in q[0]["question"] and robot.asked == [q[0]["question"]]
    assert done(evs)["status"] == "success" and robot.obj["apple"] == DT and robot.obj["banana"] == SF


def test_new_command_from_a_higher_role_suspends_the_task(make_assist, env, run):
    robot = SayAfter(env, HOUSE, say=[("pick", "bring the banana to the sofa", "owner")])
    planner = ScriptedPlanner(PLAN, replans=[
        steps(f"Place {DS}", f"MoveTo {DT}", "Find banana", "Pick banana", f"MoveTo {SF}", f"Place {SF}"),
        steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")])
    evs = run_task(make_assist(robot, FakeLLM(APPLE), planner), TASK)
    kinds = [e["event"] for e in evs]
    assert robot.asked == []                                       # the owner outranks the member: no question
    assert done(evs)["status"] == "success" and robot.obj["banana"] == SF and robot.obj["apple"] == DT
    sus = [c for c in changes(evs) if c.get("suspended")][0]
    assert sus["goals"][0] == {"type": "on", "object": "apple", "from": "", "to": DS}   # put down first
    assert sus["suspended"][0]["from"] == DS and "task_resumed" in kinds
    assert [e["event"] for e in run.events if e["event"].startswith("task_")] == ["task_suspended", "task_resumed"]
    assert run_control.manager().task_stack.frames(run.robot_id) == []


def test_closed_loop_says_a_change_is_not_applied(run):
    from types import SimpleNamespace
    from robot_agent.core.planning.loop import ClosedLoop
    run_control.manager().inject(run.run_id, "also bring the mug")
    r = SimpleNamespace(ev=lambda kind, **kw: dict(kw, event=kind))
    evs = list(ClosedLoop._cl_pending(None, r))
    assert evs[0]["event"] == "goal_change" and not evs[0]["applied"] and "also bring the mug" in evs[0]["say"]
    assert run.pending == []
