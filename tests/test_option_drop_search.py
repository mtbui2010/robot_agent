"""Option ``drop_search`` (default off): an object lost on the way to a hand-over, or after a release, is looked for on the floors of the
rooms the robot passed. Made-up flat (a pantry, a den), made-up person and objects; a symbolic robot whose hand-over skill fails with an
empty hand and whose carried object falls on a floor."""

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.assist_loop import Places
from robot_agent.options import drop_search as D

SH, PF, DF, DS, DT, ME = "shelf@pantry", "floor@pantry", "floor@den", "sofa@den", "table@den", "spot by quill"
ENV = {
    SH: {"aliases": ["shelf"], "loc": {"x": 5.0, "y": 1.0}, "surface": True},
    PF: {"aliases": ["pantry floor"], "loc": {"x": 5.0, "y": 2.0}, "surface": False},
    DS: {"aliases": ["sofa"], "loc": {"x": 1.0, "y": 6.0}, "surface": False},
    DT: {"aliases": ["den table"], "loc": {"x": 3.0, "y": 6.0}, "surface": True},
    DF: {"aliases": ["den floor"], "loc": {"x": 1.5, "y": 6.0}, "surface": False},
    ME: {"person": "quill", "speaker": True, "aliases": ["me", "quill"], "loc": {"x": 1.0, "y": 6.2}, "seat": DS, "surface": False},
}
FLOORS = {"planner": {"options": {"drop_search": {"floors": {"head": "floor", "assumed": True}}}}}
GOAL_HAND = {"status": "clear", "reason": "", "question": "",
             "goals": [{"type": "on", "object": "zorb", "from": SH, "to": ME, "hand": True}]}
HAND_PLAN = steps(f"MoveTo {SH}", "Find zorb", "Pick zorb", f"MoveTo {ME}", f"Handover {ME}")


class LossyRobot(FakeRobot):
    """FakeRobot with a hand-over to the person, and a carried object that falls on the floor of ``fall_room`` on the ``fall_on``-th move."""

    def __init__(self, *a, fall_room=None, fall_on=None, **k):
        super().__init__(*a, **k)
        self.fall_room, self.fall_on, self.moves = fall_room, fall_on, 0

    def _exec(self, skill, o, params):
        if skill == "move":
            self.moves += 1
            if self.moves == self.fall_on and self.holding:
                self.obj[self.holding], self.holding = f"floor@{self.fall_room}", None
        if skill == "handover":
            if not self.holding:
                return {"isdone": False, "msg": "nothing in the gripper"}
            self.obj[self.holding], self.holding = "person:quill", None
            return {"isdone": True, "msg": "handed"}
        return super()._exec(skill, o, params)


def _run(make_assist, robot, plan, goals=GOAL_HAND, replans=(), data=FLOORS, task="Bring me the zorb.", **opts):
    planner = ScriptedPlanner(plan, replans=replans)
    loop = make_assist(robot, FakeLLM(goals, default="{}"), planner, search_legs=1, search_rank="prior", ask_help=False,
                       detector_miss_rate=0, **opts)
    if data is not None:
        loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), **data["planner"]}
    return run_task(loop, task, max_events=3000), planner


def _flags(evs):
    return [e for e in evs if e["event"] == "recover" and e.get("kind") == D.FLAG]


@pytest.fixture
def env():
    return dict(ENV)


@pytest.fixture(autouse=True)
def home_pack(agent_state):
    agent_state.robot_pkg = "fakehome"         # the test robot with a Handover skill


# ── pure functions ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, want", [
    ({"isdone": True}, True),
    ({"isdone": False, "msg": "nothing in the gripper"}, False),
    ({"isdone": False}, False),
    ({"isdone": False, "msg": "gripper sensor offline", "code": "hw_fault"}, None),
    ({"isdone": False, "msg": "no answer", "code": "timeout"}, None),
    (None, None),
])
def test_drop_search_reading_never_takes_a_failed_sensor_call_for_an_empty_hand(raw, want):
    assert D.reading(raw) is want


@pytest.mark.parametrize("data, want", [({"head": " Floor ", "assumed": True}, "floor"), ({"head": "ground", "source": "map doc"}, "ground"),
                                        ({"head": "floor"}, None), ({}, None), ({"assumed": True}, None), (None, None)])
def test_drop_search_floor_head_needs_a_label(data, want):
    assert D.floor_head(data) == want


def test_drop_search_floor_order_person_room_then_path_then_pick_place():
    places = Places(ENV)
    done_ = steps(f"MoveTo {SH}", "Find zorb", "Pick zorb", f"MoveTo {ME}")
    assert D.room_of(places, ME, 1.0) == "den"                                   # the person's seat
    assert D.drop_floors(places, "floor", ME, done_, 1.0) == [DF, PF]
    assert D.drop_floors(places, "ground", ME, done_, 1.0) == []                  # no place with that head: nothing to look at
    assert D.drop_floors(places, "floor", "nobody", [], 1.0) == []
    assert D.drop_floors(places, "floor", ME, steps("Pick zorb", f"MoveTo {ME}"), 1.0, start=SH) == [DF, PF]     # picked where it started


# ── through the loop ────────────────────────────────────────────────────────────────────────────────
def test_drop_search_hand_over_with_an_empty_hand_is_not_searched_off(make_assist, env):
    robot = LossyRobot(env, {"zorb": SH}, start=SH, fall_room="pantry", fall_on=1)
    evs, _ = _run(make_assist, robot, HAND_PLAN)
    assert _flags(evs) == [] and done(evs)["status"] == "failed"
    assert ("move", PF, True, f"arrived at {PF}") not in robot.trace


def test_drop_search_no_floor_data_does_nothing_empty(make_assist, env):
    robot = LossyRobot(env, {"zorb": SH}, start=SH, fall_room="pantry", fall_on=1)
    evs, _ = _run(make_assist, robot, HAND_PLAN, data=None, drop_search=1)
    assert _flags(evs) == [] and done(evs)["status"] == "failed"


def test_drop_search_loop_final_hand_over_object_lost_on_the_way_is_found_and_handed(make_assist, env):
    robot = LossyRobot(env, {"zorb": SH}, start=SH, fall_room="pantry", fall_on=1)
    evs, planner = _run(make_assist, robot, HAND_PLAN, drop_search=1)
    assert [e["msg"] for e in _flags(evs)] and done(evs)["status"] == "success"
    assert robot.obj["zorb"] == "person:quill"                                    # the final state: the person has it
    looked = [o for s, o, ok, _m in robot.trace if s == "move"]
    assert looked.index(DF) < looked.index(PF)                                     # the person's room first, then the path


def test_drop_search_loop_final_goal_check_names_the_floor_in_the_replan(make_assist, env):
    goals = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "zorb", "from": SH, "to": DT}]}
    plan = steps(f"MoveTo {SH}", "Find zorb", "Pick zorb", f"MoveTo {DT}", f"Place {DT}")
    fetch = steps(f"MoveTo {DF}", "Find zorb", "Pick zorb", f"MoveTo {DT}", f"Place {DT}")
    robot = LossyRobot(env, {"zorb": SH}, start=SH)
    robot._exec = _fall_on_place(robot, "den")
    evs, planner = _run(make_assist, robot, plan, goals=goals, replans=[fetch], task="Put the zorb on the den table.", drop_search=1, drop_search_goal=1,
                        data={"planner": {**FLOORS["planner"], "action_roles": {"move": "MoveTo"}}})
    reasons = [c[2] for c in planner.calls if c[0] == "replan"]
    assert reasons and DF in reasons[0] and "zorb" in reasons[0]                    # the unmet goal and where the object is
    assert done(evs)["status"] == "success" and robot.obj["zorb"] == DT


def test_drop_search_end_check_does_not_look_at_the_floor_goal_check_off(make_assist, env):
    goals = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "zorb", "from": SH, "to": DT}]}
    plan = steps(f"MoveTo {SH}", "Find zorb", "Pick zorb", f"MoveTo {DT}", f"Place {DT}")
    robot = LossyRobot(env, {"zorb": SH}, start=SH)
    robot._exec = _fall_on_place(robot, "den")
    evs, planner = _run(make_assist, robot, plan, goals=goals, task="Put the zorb on the den table.", drop_search=1,
                        data={"planner": {**FLOORS["planner"], "action_roles": {"move": "MoveTo"}}})
    assert DF not in [o for s, o, ok, _m in robot.trace if s == "move"] and done(evs)["status"] == "failed"


def _fall_on_place(robot, room):
    """The first Place reports success while the object lands on the floor of ``room``."""
    inner, fell = robot._exec, []

    def _exec(skill, o, params):
        if skill == "place" and robot.holding and not fell:
            fell.append(o)
            robot.obj[robot.holding], robot.holding = f"floor@{room}", None
            return {"isdone": True, "msg": "placed"}
        return inner(skill, o, params)
    return _exec
