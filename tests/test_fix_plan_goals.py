"""Fix stream `plan`: typed goals (core/planning/goals.py). The goal kinds a robot can be given come from
its manifest actions (D23: go / say / deliver beside on / in / open / close), a goal the robot has no
skill for is refused before planning or motion (D24), conflicting goals are a question (D14)."""

import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import Manifest, Messages, Unbindable
from robot_agent.core.planning import goals as G
from robot_agent.core.planning.robot import RobotView

DT, DS, KITCHEN = "dining table@kitchen", "desk@study room", "counter@kitchen"


def understood(*goals, status="clear"):
    return {"status": status, "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


# ── goals.py, pure ──
def test_kinds_follow_the_actions():
    arm = {"MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close"}
    assert G.kinds_for(arm) == ["on", "in", "open", "close", "go"]
    assert G.kinds_for({"MoveTo", "Say", "WaitForUnload"}) == ["on", "in", "open", "close", "go", "say", "deliver"]
    assert G.missing_actions({"type": "on"}, {"MoveTo", "Say"}) == ["Pick", "Place"]
    assert G.missing_actions({"type": "in"}, {"Pick", "Place"}) == []          # Place may realise PutIn
    assert G.missing_actions({"type": "say", "to": "kitchen"}, {"Say"}) == ["MoveTo"]
    assert G.missing_actions({"type": "throw"}, arm) == ["throw"]


def test_validator_two_destinations_is_a_question_open_close_keeps_the_last():
    gs = [{"type": "on", "object": "apple", "to": DS}, {"type": "on", "object": "apple", "to": DT}]
    kept, q = G.conflicts(gs)
    assert q and "apple" in q and "desk in the study room" in q and kept == gs
    gs = [{"type": "open", "object": "drawer@kitchen"}, {"type": "in", "object": "orange", "to": "drawer@kitchen"},
          {"type": "close", "object": "drawer@kitchen"}]
    kept, q = G.conflicts(gs)
    assert q is None and [g["type"] for g in kept] == ["in", "close"]


def test_compile_and_reach_template_goals():
    gs = [{"type": "go", "to": "kitchen"}, {"type": "say", "object": "Dinner is ready.", "to": ""},
          {"type": "deliver", "object": "parcel", "to": "bedroom"}]
    plan = G.compile_steps(gs, "dock", delivery_text="Please take it.", can_say=True)
    assert [(s["action"], s["object"]) for s in plan] == [
        ("MoveTo", "kitchen"), ("Say", "Dinner is ready."), ("MoveTo", "bedroom"), ("Say", "Please take it."),
        ("WaitForUnload", "")]
    assert all(G.reached(g, plan, "dock") for g in gs)
    assert not G.reached(gs[2], plan[:3], "dock")                  # never waited for the hand-over
    assert G.reached({"type": "go", "to": "dock"}, [], "dock")     # already there


# ── a base-only robot (no arm) through AssistLoop ──
TRAYBOT = {
    "robot": "traybot", "version": "1.0", "language": "en",
    "capabilities": {"base": True, "arms": 0, "hands": [], "speaker": True, "tray": True},
    "geometry": {"arrive_tol_m": 0.3},
    "skills": [
        {"name": "move", "action": "MoveTo", "params": {"type": "object", "properties": {"inputs": {"type": "string"}},
                                                         "required": ["inputs"]}},
        {"name": "say", "action": "Say", "params": {"type": "object", "properties": {"inputs": {"type": "string"}},
                                                     "required": ["inputs"]}},
        {"name": "ask", "action": "Ask", "params": {"type": "object", "properties": {"inputs": {"type": "string"}},
                                                     "required": ["inputs"]}},
        {"name": "wait_for_unload", "action": "WaitForUnload"},
    ]}


class TrayBot:
    """Adapter + skills of a base-only robot: drives between ENV places, speaks, waits for the tray to empty."""

    def __init__(self, env):
        self.manifest = Manifest.from_dict(TRAYBOT)
        self.i18n = Messages(language="en", phrases={"cannot_do": {"en": "I have no arm."}})
        self.env, self.at, self.tray = env, "entrance", "parcel"
        self.trace, self.said, self.asked = [], [], []

    def bind(self, step, world=None):
        a, o = step.get("action"), step.get("object") or ""
        spec = self.manifest.skill_for(a)
        if spec is None:
            raise Unbindable(f"no skill for {a}")
        return spec.name, ({"inputs": o} if o else {})

    def observe(self, node=None):
        from robot_agent.contract import Observation
        return Observation(text=f"at {self.at}")

    def reconcile(self, world, node=None):
        return world

    def health(self, node=None):
        return {"ok": True}

    def execute(self, skill, params, node=None, log_fn=None):
        o = (params or {}).get("inputs", "")
        self.trace.append((skill, o))
        if skill == "move":
            if o not in self.env:
                return {"isdone": False, "msg": f"unknown location {o}"}
            self.at = o
        elif skill == "say":
            self.said.append((self.at, o))
        elif skill == "ask":
            self.asked.append(o)
            return {"isdone": False, "text": ""}
        elif skill == "wait_for_unload":
            self.tray = None
        return {"isdone": True, "msg": "ok"}


@pytest.fixture
def traybot_loop(make_assist, env):
    def make(llm, planner=None):
        bot = TrayBot(env)
        planner = planner or ScriptedPlanner([])
        loop = make_assist(bot, llm, planner)
        loop.robot = RobotView(bot)
        loop.sr = bot
        return loop, bot, planner
    return make


def test_armless_robot_refuses_pick_up_before_anything(traybot_loop):
    loop, bot, planner = traybot_loop(FakeLLM(default=understood(("on", "cup", "", "dining table"))))
    evs = run_task(loop, "Put the cup on the dining table.")
    d = done(evs)
    assert d["status"] == "refused" and "Pick" in d["msg"] and d["say"] == "I have no arm."
    assert bot.trace == [] and planner.calls == [], "planned or moved for a goal it has no skill for"


def test_go_and_say_goals_run_without_a_planner(traybot_loop):
    llm = FakeLLM(default=understood(("go", "", "", "dining table"), ("say", "Mom, dinner is ready.", "", "mom")))
    loop, bot, planner = traybot_loop(llm)
    evs = run_task(loop, "Go to the dining table and tell mom dinner is ready.")
    assert done(evs)["status"] == "success"
    assert bot.said == [(DT, "Mom, dinner is ready.")] and planner.calls == []
    prompt, fmt = llm.calls[0]
    assert '"say" =' in prompt and '"deliver" =' in prompt
    assert fmt["properties"]["goals"]["items"]["properties"]["type"]["enum"][-3:] == ["go", "say", "deliver"]


def test_deliver_goal_needs_the_hand_over(traybot_loop):
    loop, bot, planner = traybot_loop(FakeLLM(default=understood(("deliver", "parcel", "", "counter"))))
    evs = run_task(loop, "Deliver this to the counter.")
    assert done(evs)["status"] == "success" and bot.tray is None and bot.at == KITCHEN
    assert [t[0] for t in bot.trace] == ["move", "say", "wait_for_unload"]


def test_manipulator_prompt_has_go_but_not_say(make_assist, env):
    llm = FakeLLM(default=understood(("on", "apple", "desk", "dining table")))
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(FakeRobot(env, {"apple": DS}), llm, planner), "Bring the apple to the dining table.")
    assert done(evs)["status"] == "success"
    prompt, fmt = llm.calls[0]
    assert '"go" =' in prompt and '"say" =' not in prompt
    assert fmt["properties"]["goals"]["items"]["properties"]["type"]["enum"] == ["on", "in", "open", "close", "go"]


def test_conflicting_destinations_are_asked_not_executed(make_assist, env):
    both = understood(("on", "apple", "", "desk"), ("on", "apple", "", "dining table"))
    robot = FakeRobot(env, {"apple": DS})
    planner = ScriptedPlanner(steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    evs = run_task(make_assist(robot, FakeLLM(default=both), planner), "Put the apple on the desk and on the table.")
    assert robot.asked and "apple" in robot.asked[0]
    assert [t for t in robot.trace if t[0] == "move"] == [] and done(evs)["status"] != "success"
