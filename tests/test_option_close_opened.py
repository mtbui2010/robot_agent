"""Option ``close_opened`` (default OFF): a container the plan opens is closed again once the robot is done with it, unless a goal wants it open.
Hook-level tests on made-up places (crate, hutch, bench, pal), objects (gizmo, widget, bolt) and a made-up hand-over action (Offer), and loop-level
tests of the FINAL state on a made-up flat with a hand-over pack. Nothing here is a real robot's skill or a real task."""
import copy
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import action_model
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.core.planning.base import WorldState
from robot_agent.options.close_opened import SPEC, CloseOpened

ROLES = {"move": "MoveTo", "open": "Open", "close": "Close"}
ENV = {"crate@yard": {"openable": True}, "hutch@loft": {"openable": True}, "bench@yard": {}, "pal@den": {"person": "pal"}}
HAND = {"type": "on", "object": "gizmo", "from": "crate@yard", "to": "pal@den", "hand": True}
FETCH = ("MoveTo crate@yard", "Open crate@yard", "Find gizmo", "Pick gizmo")


def make(roles=ROLES, hands=1, goals=(HAND,), hold=None, at=None, opened=()):
    caps = action_model.Capabilities(hands=hands, find_reaches=False, release_actions=frozenset({"Offer"}))
    svc = NS(role=lambda r: (roles or {}).get(r), lab=lambda *a, **k: svc.labs.append(k), labs=[],
             robot=NS(manifest=NS(planner={}), model_capabilities=lambda: caps))
    run = NS(world=WorldState(arrived=at, holding=hold, opened=set(opened)), places=Places(ENV), goals=list(goals))
    opt = CloseOpened(resolve(SPEC, {SPEC.name: "1"}, {}, None), svc)
    opt.on_start(run)
    return opt, run, svc


def acts(plan):
    return [f'{s["action"]} {s["object"]}' for s in plan]


def test_close_while_holding_goes_after_the_hand_over_at_the_container():
    opt, run, svc = make()
    got = acts(opt.on_plan(steps(*FETCH, "Close crate@yard", "MoveTo pal@den", "Offer pal@den"), run))
    assert got == [*FETCH, "MoveTo pal@den", "Offer pal@den", "MoveTo crate@yard", "Close crate@yard"]
    assert svc.labs == [{"container": "crate@yard", "fix": "moved"}]


def test_close_added_regression_a_container_the_plan_left_open_is_closed_after_the_release_before_the_rest():
    # the class: the plan (or the planner's edit of it) has no Close; the robot would end with the hutch open
    opt, run, svc = make(goals=[{"type": "on", "object": "widget", "from": "hutch@loft", "to": "bench@yard"}])
    plan = steps("MoveTo hutch@loft", "Open hutch@loft", "Find widget", "Pick widget", "MoveTo bench@yard", "Place bench@yard", "MoveTo pal@den")
    got = acts(opt.on_plan(plan, run))
    assert got == ["MoveTo hutch@loft", "Open hutch@loft", "Find widget", "Pick widget", "MoveTo bench@yard", "Place bench@yard",
                   "MoveTo hutch@loft", "Close hutch@loft", "MoveTo pal@den"]
    assert svc.labs == [{"container": "hutch@loft", "fix": "added"}]
    assert opt.on_plan(steps(*got), run) is None                      # a second pass changes nothing


def test_close_right_where_the_robot_is_after_a_put_in_and_before_it_leaves():
    opt, run, _ = make(goals=[{"type": "in", "object": "bolt", "from": "", "to": "crate@yard"}], hold="bolt", at="bench@yard")
    got = acts(opt.on_plan(steps("MoveTo crate@yard", "Open crate@yard", "PutIn crate@yard", "MoveTo bench@yard"), run))
    assert got == ["MoveTo crate@yard", "Open crate@yard", "PutIn crate@yard", "Close crate@yard", "MoveTo bench@yard"]


def test_close_waits_for_the_next_leg_so_the_steps_after_the_release_keep_their_place():
    # two containers: the gizmo goes from the crate into the hutch; the plan closes the hutch itself, then walks on. Closing the crate right after the
    # put-in would leave the robot at the crate for the hutch's Close (a step the gate refuses): it goes before the next MoveTo instead.
    opt, run, _ = make(goals=[{"type": "in", "object": "gizmo", "from": "crate@yard", "to": "hutch@loft"}])
    plan = steps("MoveTo hutch@loft", "Open hutch@loft", *FETCH, "MoveTo hutch@loft", "PutIn hutch@loft", "Close hutch@loft", "MoveTo bench@yard")
    got = acts(opt.on_plan(plan, run))
    assert got == ["MoveTo hutch@loft", "Open hutch@loft", *FETCH, "MoveTo hutch@loft", "PutIn hutch@loft", "Close hutch@loft",
                   "MoveTo crate@yard", "Close crate@yard", "MoveTo bench@yard"]
    w = run.world.copy()
    for st in steps(*got):
        assert not [v for v in action_model.check(st, w, capabilities=opt.svc.robot.model_capabilities()) if v.is_error], st   # each runs where it stands
        action_model.apply(st, w, inplace=True)
    assert not w.opened


def test_container_a_goal_wants_open_is_left_open_by_key_or_by_its_name():
    for name in ("crate@yard", "crate"):
        opt, run, svc = make(goals=[HAND, {"type": "open", "object": name}])
        assert opt.on_plan(steps(*FETCH, "MoveTo pal@den", "Offer pal@den"), run) is None, name
        assert not svc.labs


def test_container_closed_by_a_valid_close_or_open_before_the_plan_is_left_alone():
    opt, run, _ = make()
    assert opt.on_plan(steps(*FETCH, "MoveTo pal@den", "Offer pal@den", "MoveTo crate@yard", "Close crate@yard"), run) is None
    opt, run, _ = make(opened={"crate@yard"})                       # not opened by this plan: not its to close
    assert opt.on_plan(steps("MoveTo crate@yard", "Find gizmo", "Pick gizmo", "MoveTo pal@den", "Offer pal@den"), run) is None


def test_close_while_holding_with_no_release_after_it_or_two_hands_is_unchanged():
    opt, run, svc = make()
    assert opt.on_plan(steps(*FETCH, "Close crate@yard", "MoveTo pal@den"), run) is None    # the hand never empties: no guess
    assert not svc.labs
    opt, run, _ = make(hands=2)
    assert opt.on_plan(steps(*FETCH, "Close crate@yard", "MoveTo pal@den", "Offer pal@den"), run) is None


def test_no_move_role_closes_only_where_the_robot_already_is():
    opt, run, _ = make(roles={"open": "Open", "close": "Close"})
    assert opt.on_plan(steps(*FETCH, "MoveTo pal@den", "Offer pal@den"), run) is None
    opt, run, _ = make(roles={"open": "Open", "close": "Close"}, hold="bolt")
    assert acts(opt.on_plan(steps("MoveTo hutch@loft", "Open hutch@loft", "PutIn hutch@loft"), run))[-1] == "Close hutch@loft"


def test_no_roles_foreign_roles_or_no_plan_do_nothing_empty():
    plan = steps(*FETCH, "MoveTo pal@den", "Offer pal@den")
    for roles in (None, {}, {"open": "Open"}, {"close": "Close"}, {"open": "Ajar", "close": "Shut", "move": "Walk"}):
        opt, run, svc = make(roles=roles)
        assert opt.on_plan(plan, run) is None and not svc.labs, roles
    opt, run, _ = make()
    assert opt.on_plan([], run) is None


def test_lab_event_once_per_distinct_edit_in_a_run():
    opt, run, svc = make()
    for _ in range(4):
        opt.on_plan(steps(*FETCH, "MoveTo pal@den", "Offer pal@den"), run)
    assert svc.labs == [{"container": "crate@yard", "fix": "added"}]
    opt.on_start(run)
    opt.on_plan(steps(*FETCH, "MoveTo pal@den", "Offer pal@den"), run)
    assert len(svc.labs) == 2                                         # a new run logs again


# ── the real loop: a hand delivery out of a locker, on a made-up flat with a hand-over pack ─────────────────────────────────────────────────
PAL = "pal@den"
FLAT = {"locker@den": {"aliases": ["locker"], "loc": {"x": 1.0, "y": 1.0}, "openable": True},
        "stool@den": {"aliases": ["stool"], "loc": {"x": 3.0, "y": 1.0}, "surface": True},
        PAL: {"person": "pal", "speaker": True, "aliases": ["me", "pal"], "loc": {"x": 2.0, "y": 1.0}, "spot": "stool@den", "surface": False}}
PLAN = ("MoveTo locker@den", "Open locker@den", "Find gizmo", "Pick gizmo", f"MoveTo {PAL}", f"Handover {PAL}")


class HandRobot(FakeRobot):
    """A FakeRobot with a hand-over to the person."""

    def _exec(self, skill, o, params):
        if skill == "handover":
            if not self.holding:
                return {"isdone": False, "msg": "nothing to hand over"}
            self.obj[self.holding], self.holding = "person:pal", None
            return {"isdone": True, "msg": "handed"}
        return super()._exec(skill, o, params)


@pytest.fixture
def env():
    return copy.deepcopy(FLAT)


@pytest.fixture(autouse=True)
def hand_pack(agent_state):
    agent_state.robot_pkg = "fakehome"                 # the test pack with a Handover skill and the open / close roles


def _hand_run(make_assist, env, on, extra_goals=(), words="Bring me the gizmo from the locker."):
    robot = HandRobot(env, {"gizmo": "locker@den/in"}, start="stool@den")
    goals = [{"type": "on", "object": "gizmo", "from": "locker@den", "to": "me", "hand": True}, *extra_goals]
    und = {"status": "clear", "reason": "", "reason_type": "", "question": "", "goals": goals}
    loop = make_assist(robot, FakeLLM(und, default="{}"), ScriptedPlanner(steps(*PLAN)), detector_miss_rate=0, ask_help=False,
                       **({"close_opened": "1"} if on else {}))
    return loop, robot, run_task(loop, words, max_events=3000)


def test_registered_and_off_by_default_the_locker_is_left_open_off(make_assist, env):
    assert "close_opened" in ASSIST_OPTIONS and REGISTRY.get("close_opened").key("enabled").default is False
    loop, robot, evs = _hand_run(make_assist, env, on=False)
    assert not [e for e in loop.lab_log if e["flag"] == "close_opened"]
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == "person:pal" and "locker@den" in robot.opened   # the class


def test_loop_final_the_locker_is_closed_after_the_hand_over(make_assist, env):
    loop, robot, evs = _hand_run(make_assist, env, on=True)
    assert [(e["container"], e["fix"]) for e in loop.lab_log if e["flag"] == "close_opened"] == [("locker@den", "added")]
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == "person:pal" and robot.holding is None
    assert "locker@den" not in robot.opened
    assert [t[:2] for t in robot.trace][-3:] == [("handover", PAL), ("move", "locker@den"), ("close_drawer", "locker@den")]


def test_loop_final_a_goal_to_leave_the_locker_open_is_kept(make_assist, env):
    keep = ({"type": "open", "object": "locker@den"},)
    loop, robot, evs = _hand_run(make_assist, env, on=True, extra_goals=keep, words="Bring me the gizmo from the locker and leave the locker open.")
    assert not [e for e in loop.lab_log if e["flag"] == "close_opened"]
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == "person:pal" and "locker@den" in robot.opened
