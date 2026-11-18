"""Option ``cond_goal``, key ``open_closed`` (layer cond_answer, default OFF): the place it opens before the look counts as opened just now
for the run (service ``opened_now``), as after the engine's own Open, so a later miss of another object inside is not doubted as a stale
open belief (the doubt cleared the fresh open and the search had no way left). A really stale open belief is still doubted.
Made-up cases (a gizmo, a widget, a bin, a desk, a bench), on stubs and through the real AssistLoop."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning.option_services import LoopServices
from test_option_cond_answer import BENCH, BIN, DESK, ENV, LAYER, Q, drain, stub, when

GIZMO_NOT_IN_BIN = {"type": "on", "object": "widget", "from": "", "to": "bench", "when": when("bin", False)}


def _opened_stub(opts, open_ok=True):
    opt, run, log = stub(DESK, opts=opts, open_ok=open_ok)
    log.opened = []
    opt.svc.opened_now = lambda r, k: log.opened.append(k)
    run.goals = [dict(GIZMO_NOT_IN_BIN)]
    drain(opt.on_goals(run))
    return log


def test_open_closed_tells_the_engine_the_place_was_opened_just_now():
    assert _opened_stub({"cond_goal.open_closed": "1"}).opened == [BIN]


def test_cond_goal_open_closed_off_or_a_failed_open_tells_nothing():
    assert _opened_stub({}).opened == [] and _opened_stub({"cond_goal.open_closed": "1"}, open_ok=False).opened == []


def test_cond_goal_open_closed_empty_a_host_without_the_service_opens_as_before():
    opt, run, log = stub(DESK, opts={"cond_goal.open_closed": "1"})
    run.goals = [dict(GIZMO_NOT_IN_BIN)]
    drain(opt.on_goals(run))
    assert log.steps == [("MoveTo", BIN), ("Open", BIN)]


def test_service_opened_now_keeps_the_place_for_the_execution():
    run = NS()
    LoopServices(NS()).opened_now(run, BIN)
    assert run.pre_doubted == {BIN}


@pytest.fixture
def env():
    return {k: dict(v) for k, v in ENV.items()}


# ── through the loop: the class (a miss of another object inside the place the condition opened) ──────────────────────────
def _loop(make_assist, opts, world=None):
    und = {"status": "clear", "reason": "", "question": "", "goals": [dict(GIZMO_NOT_IN_BIN)]}
    robot = FakeRobot(ENV, {"gizmo": DESK, "widget": DESK}, start=BENCH)
    plan = steps(f"MoveTo {BIN}", "Find widget", "Pick widget", f"MoveTo {BENCH}", f"Place {BENCH}")
    elsewhere = steps(f"MoveTo {DESK}", "Find widget", "Pick widget", f"MoveTo {BENCH}", f"Place {BENCH}")
    planner = ScriptedPlanner(plan, replans=[elsewhere])
    loop = make_assist(robot, FakeLLM(default=und), planner, world=world, detector_miss_rate=0, search_legs=1, health_wait_s=0,
                       **{"cond_goal": "1", "cond_goal.prompt": "A condition is a goal's when.", "cond_goal.q_unsure": Q,
                          "cond_goal.assumed": "1", **opts})
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find", "open": "Open"}
    evs = run_task(loop, "If the gizmo is not in the bin, put the widget on the bench.", max_events=3000)
    return robot, planner, evs


def test_loop_regression_a_miss_inside_the_place_the_condition_opened_is_no_stale_open(make_assist):
    robot, planner, evs = _loop(make_assist, LAYER)
    assert not any("is not open" in c[2] for c in planner.calls if c[0] == "replan")            # not doubted as a stale open
    legs = [e for e in evs if e["event"] == "recover" and e.get("kind") == "search_leg"]
    assert legs and DESK in legs[0]["msg"]                                                       # the bin counts as searched: on to the desk
    assert [t[0] for t in robot.trace].count("open_drawer") == 1 and BIN in robot.opened
    assert done(evs)["status"] == "success" and robot.obj["widget"] == BENCH


def test_loop_a_really_stale_open_belief_is_still_doubted(make_assist):
    robot, planner, evs = _loop(make_assist, LAYER, world={"opened": [BIN]})        # believed open, closed in fact: no Open by the layer
    reasons = [c[2] for c in planner.calls if c[0] == "replan"]
    assert reasons and f"precondition: {BIN} is not open" in reasons[0]
    assert "open_drawer" not in [t[0] for t in robot.trace]
