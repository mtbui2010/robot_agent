"""Option ``reach_check`` (default OFF): a Pick of an object a Find saw beyond the declared side reach gets the pack's base shift first.
Pure-function cases of ``decide``, hook-level tests on a made-up robot (shift skill ``glide``, param ``metres``, pick role ``Grab``) and
loop-level tests of the FINAL outcome on the test house with a made-up object. Nothing here is a real robot's skill or a real task."""
import math
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.types import SkillSpec
from robot_agent.core.options import REGISTRY
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.options.reach_check import SPEC, ReachCheck, decide, shift_config

SHIFT = {"skill": "glide", "param": "metres", "margin_m": 0.05, "max_shift_m": 0.3, "source": "made-up pack"}


def drive(gen):
    """Run a flow hook: (events it yielded, its return value)."""
    if not hasattr(gen, "send"):
        return [], gen
    evs = []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as e:
        return evs, e.value


def make(shift=SHIFT, reach=0.8, skills=("glide",), pick="Grab"):
    labs = []
    planner = {"options": {"reach_check": {"shift": shift}}} if shift is not None else {}
    geometry = {"reach_m": reach} if reach is not None else {}
    robot = NS(manifest=NS(planner=planner, geometry=geometry), knows_skill=lambda n: n in skills)
    svc = NS(robot=robot, role=lambda r: pick if r == "pick" else None, lab=lambda flag, **i: labs.append((flag, i)))
    return ReachCheck({}, svc), labs


def run_of(point, found="gizmo", plan=("Grab gizmo",)):
    return NS(world=NS(found=found), seen_at={"gizmo": point} if point is not None else {}, steps=steps(*plan), i=0,
              ev=lambda event, **f: {"event": event, **f})


# ── decide: the pure rule ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_decide_inside_reach_needs_no_shift():
    assert decide(0.5, 0.8, 0.05, 0.3) is None and decide(0.8, 0.8, 0.05, 0.3) is None


def test_decide_just_outside_shifts_the_missing_distance_plus_margin():
    assert decide(0.85, 0.8, 0.05, 0.3) == pytest.approx(0.1)


def test_decide_too_far_even_after_the_max_shift_is_none():
    assert decide(1.2, 0.8, 0.05, 0.3) is None


def test_decide_exact_margin_at_the_max_shift_is_allowed():
    assert decide(0.95, 0.8, 0.05, 0.2) == pytest.approx(0.2) and decide(0.96, 0.8, 0.05, 0.2) is None
    assert decide(0.9, 0.8, 0.0, 0.3) == pytest.approx(0.1)                  # no margin declared as 0: just the gap


@pytest.mark.parametrize("args", [(-0.9, 0.8, 0.05, 0.3), (0.9, -0.8, 0.05, 0.3), (0.9, 0.8, -0.05, 0.3), (0.9, 0.8, 0.05, -0.3),
                                  (0.9, 0.0, 0.05, 0.3), (0.9, 0.8, 0.05, 0.0), (None, 0.8, 0.05, 0.3), (0.9, "0.8", 0.05, 0.3),
                                  (0.9, 0.8, True, 0.3), (math.nan, 0.8, 0.05, 0.3), (0.9, math.inf, 0.05, 0.3)])
def test_decide_negative_or_odd_values_are_none(args):
    assert decide(*args) is None


def test_shift_config_needs_a_label_and_both_names():
    assert shift_config(SHIFT)["skill"] == "glide"
    assert shift_config({**SHIFT, "source": ""}) is None and shift_config({k: v for k, v in SHIFT.items() if k != "source"}) is None
    assert shift_config({**SHIFT, "param": " "}) is None and shift_config(None) is None
    assert shift_config({"skill": "glide", "param": "metres", "assumed": True})["margin_m"] is None   # numbers missing: decide says None


# ── the hook on a made-up robot ───────────────────────────────────────────────────────────────────────────────────────────
def test_made_up_robot_gets_its_own_shift_skill_before_the_pick_signed_toward_the_object():
    opt, labs = make()
    run = run_of([0.2, -0.95, 0.7])
    evs, step = drive(opt.on_step(run, run.steps[0]))
    assert step == run.steps[0] and step["skill"] == "glide" and step["action"] == ""
    assert step["params"] == {"metres": pytest.approx(-0.2)}                  # the object is on -y: shift that way
    assert [s.get("action") for s in run.steps] == ["", "Grab"]
    assert labs == [("reach_check", {"object": "gizmo", "dist_m": 0.95, "reach_m": 0.8, "shift_m": 0.2})] and evs[0]["kind"] == "reach_check"


def test_once_per_sighting_and_never_for_a_point_from_another_place():
    opt, labs = make()
    run = run_of([0.2, 0.95, 0.7])
    drive(opt.on_step(run, run.steps[0]))
    assert drive(opt.on_step(run, run.steps[1]))[1] is None and len(run.steps) == 2   # the Pick after the shift: same sighting, no second shift
    moved = run_of([0.2, 0.95, 0.7], found=None)                              # a MoveTo since the Find: found is cleared
    assert drive(make()[0].on_step(moved, moved.steps[0]))[1] is None and len(moved.steps) == 1


def test_too_far_after_the_max_shift_or_inside_reach_leaves_the_plan():
    for point in ([0.2, 1.4, 0.7], [0.2, 0.5, 0.7]):
        opt, labs = make()
        run = run_of(point)
        assert drive(opt.on_step(run, run.steps[0]))[1] is None and len(run.steps) == 1 and labs == []


def test_reach_check_empty():
    for kw in ({"shift": None}, {"reach": None}, {"skills": ()}, {"pick": None}, {"shift": {**SHIFT, "source": None}}):
        opt, labs = make(**kw)
        run = run_of([0.2, 0.95, 0.7])
        assert drive(opt.on_step(run, run.steps[0]))[1] is None and len(run.steps) == 1 and labs == [], kw
    opt, labs = make()
    for point in (None, [0.2], ["a", "b"]):                                   # no point, or one without a side
        run = run_of(point)
        assert drive(opt.on_step(run, run.steps[0]))[1] is None and labs == []


# ── through the real AssistLoop: the failure class (an object seen deeper than the arm reaches) ───────────────────────────
class SideBot(FakeRobot):
    """The test house; Find sees the object ``depth`` m to the side; a Pick beyond ``arm`` m fails as out of reach; ``glide`` moves the
    base ``metres`` toward it (made-up skill)."""

    def __init__(self, *a, depth=0.95, arm=0.8, **k):
        super().__init__(*a, **k)
        self.depth, self.arm = depth, arm

    def _exec(self, skill, o, params):
        if skill == "glide":
            self.depth -= abs(float(params["metres"]))
            return {"isdone": True, "msg": "glided"}
        if skill == "pick" and self.depth > self.arm:
            return {"isdone": False, "msg": f"pick: {o} is {self.depth:.2f} m deep, beyond the arm reach {self.arm:.2f} m"}
        r = super()._exec(skill, o, params)
        if skill == "find" and r.get("isdone"):
            for v in r["ins"].values():
                v["pose_3d"] = [0.2, self.depth, 0.7]
        return r


GOALS = [{"type": "on", "object": "gizmo", "from": "", "to": "side table"}]
PLAN = ("MoveTo desk@study room", "Find gizmo", "Pick gizmo", "MoveTo side table@living room", "Place side table@living room")


def _loop_run(make_assist, env, on, shift=SHIFT):
    robot = SideBot(env, {"gizmo": "desk@study room"}, start="entrance")
    und = {"status": "clear", "reason": "", "question": "", "goals": GOALS}
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(steps(*PLAN)), detector_miss_rate=0, **({"reach_check": "1"} if on else {}))
    m = loop.robot.manifest
    m.geometry["reach_m"] = 0.8                                               # the made-up robot's declared side reach
    m.skills.append(SkillSpec(name="glide", action="", params={"type": "object", "properties": {"metres": {"type": "number"}}}))
    m.planner["action_roles"] = {"pick": "Pick"}
    if shift is not None:
        m.planner["options"] = {"reach_check": {"shift": shift}}
    evs = run_task(loop, "Put the gizmo on the side table.", max_events=3000)
    return loop, robot, evs


def test_registered_and_off_by_default_the_pick_fails_as_today_off(make_assist, env):
    assert "reach_check" in ASSIST_OPTIONS and REGISTRY.get("reach_check").key("enabled").default is False and SPEC.enabled == "enabled"
    loop, robot, evs = _loop_run(make_assist, env, on=False)
    assert not [e for e in loop.lab_log if e["flag"] == "reach_check"] and "glide" not in [t[0] for t in robot.trace]
    assert done(evs)["status"] == "failed" and robot.obj == {"gizmo": "desk@study room"}
    assert [t[:3] for t in robot.trace][:3] == [("move", "desk@study room", True), ("find", "gizmo", True), ("pick", "gizmo", False)]
    assert robot.asked and "too far" in robot.asked[0]                        # as today: the robot asks the person to move it


def test_loop_final_reposition_before_the_pick_and_the_goal_is_reached(make_assist, env):
    loop, robot, evs = _loop_run(make_assist, env, on=True)
    names = [t[0] for t in robot.trace]
    assert names.index("glide") == names.index("pick") - 1 and names.count("glide") == 1
    assert [e["shift_m"] for e in loop.lab_log if e["flag"] == "reach_check"] == [pytest.approx(0.2)]
    assert done(evs)["status"] == "success" and robot.obj == {"gizmo": "side table@living room"} and robot.holding is None


def test_loop_final_on_without_data_or_too_far_ends_as_off_empty(make_assist, env):
    for kw in ({"shift": None}, {"shift": {**SHIFT, "max_shift_m": 0.1}}):     # no data; 0.95 m needs 0.2 m, more than 0.1
        loop, robot, evs = _loop_run(make_assist, env, on=True, **kw)
        assert "glide" not in [t[0] for t in robot.trace] and not [e for e in loop.lab_log if e["flag"] == "reach_check"], kw
        assert done(evs)["status"] != "success" and robot.obj == {"gizmo": "desk@study room"}, kw
