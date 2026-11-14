"""Option ``effect_check`` (default OFF): after a failed release the declared hand sensor decides what the belief holds; a sensor that cannot
be read is no "empty"; an object this run released and the goal check misses is looked for on that room's floor first. Hook-level tests on
made-up packs (sensors palm_probe / grip_eye, releases Gift / Drop / Stash, floor names ground / floor) and loop-level tests of the FINAL state on
a made-up flat (rooms den / attic, a person pal) with a hand-over robot. Nothing here is a real robot's skill, map or task."""
import copy
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import FailureCode
from robot_agent.core.options import NEXT, REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.effect_check import SPEC, EffectCheck, effects_of, reading

PACK = {"sensor": {"skill": "palm_probe", "unknown": ["hw_fault"]}, "releases": ["Gift", "Drop"], "floor": "ground", "source": "made-up pack"}
YARD = {"bench@yard": {}, "ground@yard": {}, "crate@shed": {}, "ground@shed": {}, "rug@loft": {}}


def make(data=PACK, holding="gizmo", reads=False, sensor="palm_probe", env=YARD, placed=None):
    calls = {"let_go": [], "prefer": []}
    svc = NS(labs=[], hand_holds=lambda run: reads, let_go=lambda run, st: calls["let_go"].append(st["action"]) or True,
             prefer_now=lambda run, obj, keys: calls["prefer"].append((obj, list(keys))),
             robot=NS(manifest=NS(planner={}), holding_sensor=lambda: sensor))
    svc.lab = lambda flag, **k: svc.labs.append(k)
    cfg = resolve(SPEC, {"effect_check": "1"}, {}, {"options": {"effect_check": {"effects": data}}} if data is not None else None)
    run = NS(world=NS(holding=holding), i=3, places=Places(env), placed=dict(placed or {}), ev=lambda e, **k: {"event": e, **k})
    o = EffectCheck(cfg, svc)
    o.on_start(run)
    return o, run, svc, calls


def fail(o, run, a, obj, code=FailureCode.GRASP_FAILED):
    """Drive the flow hook: (events it yielded, what it returned)."""
    gen, evs = o.on_step_failed(run, {"action": a, "object": obj}, code, f"{a} failed", a, obj), []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as stop:
        return evs, stop.value


# ── hook level ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_sensor_reads_empty_after_a_failed_release_the_belief_lets_go_and_the_refind_runs():
    o, run, svc, calls = make(reads=False)
    evs, out = fail(o, run, "Gift", "pal@den")
    assert out is NEXT and calls["let_go"] == ["Gift"] and evs[0]["kind"] == "effect_check" and evs[0]["reading"] == "empty"
    assert svc.labs == [{"part": "release", "object": "gizmo", "step": "Gift pal@den", "reading": "empty"}]


def test_sensor_reading_is_three_valued_and_only_for_the_declared_holding_sensor():
    unknown = effects_of(PACK)["unknown"]
    assert reading({"isdone": True}, unknown) is True and reading({"isdone": False, "msg": ""}, unknown) is False
    assert reading({"isdone": False, "code": "hw_fault", "msg": "probe offline"}, unknown) is None
    assert reading({"isdone": False, "code": "absent"}, unknown) is False and reading("odd", unknown) is None
    o, run, svc, _ = make()
    assert o.hand_reading("palm_probe", {"isdone": False, "code": "hw_fault"}, True) is True       # cannot read: the caller's default
    assert o.hand_reading("palm_probe", {"isdone": False, "code": "hw_fault"}, False) is False
    assert o.hand_reading("palm_probe", {"isdone": False}, True) is False and o.hand_reading("palm_probe", {"isdone": True}, False) is True
    assert o.hand_reading("other_probe", {"isdone": False, "code": "hw_fault"}, True) is None         # another skill: the engine's own reading
    assert [lab["part"] for lab in svc.labs] == ["unreadable"]
    o2, run2, _, calls2 = make(sensor="grip_eye")                                                  # declared skill is not the robot's holding sensor
    assert fail(o2, run2, "Gift", "pal@den") == ([], None) and calls2["let_go"] == []


def test_no_grasp_inference_a_failed_hand_over_with_the_object_in_hand_or_an_unread_sensor_keeps_holding():
    for reads in (True, None):
        o, run, svc, calls = make(reads=reads)
        assert fail(o, run, "Gift", "pal@den", FailureCode.GRASP_FAILED) == ([], None)
        assert run.world.holding == "gizmo" and calls["let_go"] == []
        assert svc.labs[0]["reading"] == {True: "holding", None: "unknown"}[reads]
    o, run, svc, calls = make(reads=False)
    assert fail(o, run, "Stash", "crate@shed", FailureCode.DROPPED) == ([], None) and calls["let_go"] == []    # not a declared release


def test_no_heard_by_a_speech_result_with_listeners_is_never_read():
    o, run, svc, calls = make(reads=True)
    assert fail(o, run, "Chirp", "pal@den", FailureCode.TIMEOUT) == ([], None) and not svc.labs     # a speech step: not a release
    assert o.hand_reading("palm_probe", {"isdone": True, "heard_by": []}, False) is True              # only isdone / code of the sensor count
    assert o.hand_reading("chirp", {"isdone": False, "heard_by": []}, True) is None
    src = open(__import__("robot_agent.options.effect_check", fromlist=["x"]).__file__, encoding="utf-8").read()
    assert "heard_by" not in src


def test_floor_of_the_room_where_the_run_released_the_object_comes_first_in_its_search():
    o, run, svc, calls = make(placed={"widget": "bench@yard", "bolt": "rug@loft"})
    assert o.on_goal_check(run, "widget") is None and calls["prefer"] == [("widget", ["ground@yard"])]
    assert o.on_goal_check(run, "bolt") is None and o.on_goal_check(run, "cog") is None             # no floor in the loft; not released by the run
    assert calls["prefer"] == [("widget", ["ground@yard"])] and svc.labs == [{"part": "floor", "object": "widget", "place": "bench@yard", "floor": "ground@yard"}]
    two = {**YARD, "ground@yard#2": {}, "Ground@yard": {}}
    o2, run2, _, calls2 = make(env=two, placed={"widget": "bench@yard"})
    assert o2.on_goal_check(run2, "widget") is None and calls2["prefer"] == []                         # two floor places: no guess


def test_empty_or_unlabelled_or_foreign_pack_data_does_nothing_empty():
    for data in (None, {}, {**PACK, "source": ""}, {k: v for k, v in PACK.items() if k != "source"}, {"assumed": True}, {"foreign": 1, "assumed": True}):
        o, run, svc, calls = make(data=data, reads=False, placed={"widget": "bench@yard"})
        assert fail(o, run, "Gift", "pal@den") == ([], None)
        assert o.hand_reading("palm_probe", {"isdone": False, "code": "hw_fault"}, True) is None
        assert o.on_goal_check(run, "widget") is None
        assert calls == {"let_go": [], "prefer": []} and not svc.labs and run.world.holding == "gizmo"
    o, run, svc, calls = make(holding=None, reads=False)                                              # the belief holds nothing: nothing to sync
    assert fail(o, run, "Gift", "pal@den") == ([], None) and calls["let_go"] == []


def test_engine_services_default_path_unchanged_off(make_assist, env):
    """``_holding`` with no option answering ``hand_reading`` reads ``isdone`` as before (a fault reads as empty); ``hand_holds`` is three-valued."""
    robot = ProbeRobot(env, {}, probe={"isdone": False, "code": "hw_fault", "msg": "probe offline"})
    loop = make_assist(robot, FakeLLM(default={}), ScriptedPlanner([]))
    assert loop._holding(None, default=True) is False and loop.options.option("effect_check") is None
    run = NS(node=None)
    from robot_agent.core.planning.option_services import LoopServices
    assert LoopServices(loop).hand_holds(run) is False
    robot.probe = {"isdone": True}
    assert loop._holding(None, default=False) is True and LoopServices(loop).hand_holds(run) is True


# ── through the real AssistLoop ──────────────────────────────────────────────────────────────────────────────────────
PAL = "pal@den"
FLAT = {"shelf@den": {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
        "floor@den": {"aliases": ["den floor"], "loc": {"x": 1.5, "y": 1.0}},
        "chest@attic": {"aliases": ["chest"], "loc": {"x": 5.0, "y": 5.0}, "surface": True},
        PAL: {"person": "pal", "speaker": True, "aliases": ["me", "pal"], "loc": {"x": 2.0, "y": 1.0}, "spot": "shelf@den", "surface": False}}
LOOP_PACK = {"sensor": {"skill": "grasp_succeed", "unknown": ["hw_fault"]}, "releases": ["Place", "Handover"], "floor": "floor", "source": "made-up pack"}


class ProbeRobot(FakeRobot):
    """A FakeRobot whose holding sensor answers ``probe`` when set, a hand-over to the person, and a carry that drops the object on the floor of
    the room it walks into (``slip``: the move target's room) once."""

    def __init__(self, *a, probe=None, slip=None, **k):
        super().__init__(*a, **k)
        self.probe, self.slip = probe, slip

    def _exec(self, skill, o, params):
        if skill == "grasp_succeed" and self.probe is not None:
            return dict(self.probe)
        if skill == "move" and self.slip and self.holding and o == self.slip:
            self.obj[self.holding], self.holding, self.slip = "floor@" + o.split("@")[-1], None, None
        if skill == "handover":
            if not self.holding:
                return {"isdone": False, "msg": "grasp failed: nothing was taken", "code": "grasp_failed"}
            self.obj[self.holding], self.holding = "person:pal", None
            return {"isdone": True, "msg": "handed"}
        return super()._exec(skill, o, params)


@pytest.fixture
def env():
    return copy.deepcopy(FLAT)


@pytest.fixture(autouse=True)
def hand_pack(agent_state):
    agent_state.robot_pkg = "fakehome"                 # the test pack with a Handover skill


def _hand_over(make_assist, env, on):
    robot = ProbeRobot(env, {"gizmo": "shelf@den"}, start="shelf@den", slip=PAL)
    und = {"status": "clear", "reason": "", "reason_type": "", "question": "",
           "goals": [{"type": "on", "object": "gizmo", "from": "shelf@den", "to": "me"}]}
    plan = steps("MoveTo shelf@den", "Find gizmo", "Pick gizmo", f"MoveTo {PAL}", f"Handover {PAL}")
    loop = make_assist(robot, FakeLLM(und, default="{}"), ScriptedPlanner(plan), search_legs=1, search_rank="prior", ask_help=False,
                       detector_miss_rate=0, **({"effect_check": "1"} if on else {}))
    loop.robot.manifest.planner.setdefault("options", {})["effect_check"] = {"effects": LOOP_PACK}
    return loop, robot, run_task(loop, "Bring me the gizmo.", max_events=3000)


def test_registered_default_off_a_hand_over_with_an_empty_hand_is_retried_as_is_off(make_assist, env):
    assert "effect_check" in ASSIST_OPTIONS and REGISTRY.get("effect_check").key("enabled").default is False
    loop, robot, evs = _hand_over(make_assist, env, on=False)
    assert not [e for e in loop.lab_log if e["flag"] == "effect_check"]
    assert done(evs)["status"] != "success" and robot.obj["gizmo"] == "floor@den"            # the default path: never looked at the floor
    assert not any(t[0] == "move" and t[1] == "floor@den" for t in robot.trace)


def test_loop_final_hand_over_after_the_sensor_read_empty_the_gizmo_is_found_on_the_floor_and_handed(make_assist, env):
    loop, robot, evs = _hand_over(make_assist, env, on=True)
    labs = [e for e in loop.lab_log if e["flag"] == "effect_check" and e.get("part") == "release"]
    assert labs and labs[0]["reading"] == "empty" and labs[0]["object"] == "gizmo"
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == "person:pal" and robot.holding is None
    assert [t[:2] for t in robot.trace if t[0] in ("move", "pick", "handover")][-4:] == [
        ("move", "floor@den"), ("pick", "gizmo"), ("move", PAL), ("handover", PAL)]
    assert sum(1 for t in robot.trace if t[0] == "handover" and not t[2]) == 1                    # one failed hand-over, not three


class FallRobot(ProbeRobot):
    """A put-down at ``fall`` reports success and leaves the object on the floor of that room (once): a silent drop."""

    def _exec(self, skill, o, params):
        if skill == "place" and self.holding and getattr(self, "fall", None) == o:
            self.obj[self.holding], self.holding, self.fall = "floor@" + o.split("@")[-1], None, None
            return {"isdone": True, "msg": "placed"}
        return super()._exec(skill, o, params)


def _silent_drop(make_assist, env, on):
    robot = FallRobot(env, {"widget": "chest@attic"}, start="chest@attic")
    robot.fall = "shelf@den"
    und = {"status": "clear", "reason": "", "reason_type": "", "question": "",
           "goals": [{"type": "on", "object": "widget", "from": "chest@attic", "to": "shelf@den"}]}
    again = steps("Find widget", "Pick widget", "MoveTo shelf@den", "Place shelf@den")
    loop = make_assist(robot, FakeLLM(und, default="{}"),
                       ScriptedPlanner(steps("MoveTo chest@attic", "Find widget", "Pick widget", "MoveTo shelf@den", "Place shelf@den"), replans=[again, again]),
                       search_legs=1, search_rank="prior", ask_help=False, detector_miss_rate=0, **({"effect_check": "1"} if on else {}))
    loop.robot.manifest.planner.setdefault("options", {})["effect_check"] = {"effects": LOOP_PACK}
    evs = run_task(loop, "Put the widget on the shelf.", max_events=3000)
    looks = [t[1] for t in robot.trace if t[0] == "move"]
    return loop, robot, evs, looks[looks.index("shelf@den") + 1:]            # where the robot went after the put-down


def test_loop_final_silent_drop_the_floor_of_the_release_room_is_the_first_look_and_the_widget_ends_on_the_shelf(make_assist, env):
    loop, robot, evs, after = _silent_drop(make_assist, env, on=True)
    assert after[0] == "floor@den" and "chest@attic" not in after                               # the floor first, no other place
    assert done(evs)["status"] == "success" and robot.obj["widget"] == "shelf@den"
    assert [e["part"] for e in loop.lab_log if e["flag"] == "effect_check"] == ["floor"]


def test_silent_drop_without_the_option_another_place_is_searched_first_off(make_assist, env):
    loop, robot, evs, after = _silent_drop(make_assist, env, on=False)
    assert after[0] != "floor@den" and not [e for e in loop.lab_log if e["flag"] == "effect_check"]


def test_sensor_fault_through_the_engine_reading_keeps_the_callers_default_and_hand_holds_is_unknown(make_assist, env):
    """grasp_misread: with the option on, ``_holding`` reads a declared "cannot read" code as the caller's default, not as "empty"."""
    from robot_agent.core.planning.option_services import LoopServices
    robot = ProbeRobot(env, {}, probe={"isdone": False, "code": "hw_fault", "msg": "probe offline"})
    loop = make_assist(robot, FakeLLM(default={}), ScriptedPlanner([]), effect_check="1")
    loop.robot.manifest.planner.setdefault("options", {})["effect_check"] = {"effects": LOOP_PACK}
    run = NS(node=None)
    assert loop._holding(None, default=True) is True and loop._holding(None, default=False) is False
    assert LoopServices(loop).hand_holds(run) is None
    robot.probe = {"isdone": False, "msg": ""}
    assert loop._holding(None, default=True) is False and LoopServices(loop).hand_holds(run) is False
    assert [e["part"] for e in loop.lab_log if e["flag"] == "effect_check"] == ["unreadable"]
