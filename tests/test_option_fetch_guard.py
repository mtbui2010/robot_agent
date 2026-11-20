"""Option ``fetch_guard`` (default ON): an object with no source is never looked for at its destination. Pure parts on made-up tables, the
service and the hook on stubs, and runs through the real AssistLoop (make-believe flat: a den and a loft; objects gizmo and widget)."""
from types import SimpleNamespace as NS

import pytest

from embodiplan.action_model import SymbolicState
from fakes import FakeLLM, FakeRobot, done, run_task, steps
from robot_agent.core import run_control
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.search import SearchBelief
from robot_agent.core.planning.search_hooks import SearchHooks
from robot_agent.interact.run_manager import reset_manager
from robot_agent.options.fetch_guard import SPEC, FetchGuard, fetch_findings, unknown_sources, visits

RACK, BENCH, CHEST = "rack@den", "bench@loft", "chest@loft"
ENV = {RACK: {"aliases": ["rack"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 4.0, "y": 1.0}, "surface": True},
       CHEST: {"aliases": ["chest"], "loc": {"x": 5.0, "y": 2.0}, "surface": True}}
GOAL = {"type": "on", "object": "gizmo", "from": "", "to": BENCH}
AT_DEST = steps(f"MoveTo {BENCH}", "Find gizmo", "Pick gizmo", f"Place {BENCH}")
GOOD = steps(f"MoveTo {RACK}", "Find gizmo", "Pick gizmo", f"MoveTo {BENCH}", f"Place {BENCH}")
TASK = "Put the gizmo on the bench."


@pytest.fixture
def env():
    return dict(ENV)


def grounded(plan):
    return [dict(s, grounded=True) for s in plan]


def option(overrides=None, remembered=None):
    labs = []
    svc = NS(lab=lambda *a, **k: labs.append((a, k)), remembered=lambda run, o: (remembered or {}).get(o))
    return FetchGuard(resolve(SPEC, dict(overrides or {}), {}, {}), svc), labs


def run_of(goals, at=None):
    return NS(goals=[dict(g) for g in goals], world=SymbolicState(arrived=at))


# ── pure parts ──
def test_unknown_sources_keeps_only_fetch_goals_with_no_source_and_no_trusted_sighting():
    goals = [GOAL, dict(GOAL, object="widget", **{"from": RACK}), dict(GOAL, object="cog", from_any=[RACK]),
             dict(GOAL, object="nut"), {"type": "go", "object": "", "to": BENCH}]
    assert unknown_sources(goals, lambda o: o == "nut") == {"gizmo": BENCH}


def test_fetch_findings_flags_the_first_look_at_the_destination_reached_by_a_move():
    got = fetch_findings({"gizmo": BENCH}, [(0, "MoveTo", BENCH, None), (1, "Find", "gizmo", BENCH), (2, "Pick", "gizmo", BENCH)])
    assert len(got) == 1 and got[0].startswith("step 2 'Find gizmo'") and "must end up" in got[0]


def test_fetch_findings_ignores_a_look_elsewhere_a_look_where_the_robot_starts_and_other_objects():
    assert fetch_findings({"gizmo": BENCH}, [(0, "Find", "gizmo", RACK), (1, "Find", "gizmo", BENCH)]) == []   # the first look decides
    assert fetch_findings({"gizmo": BENCH}, [(0, "Find", "gizmo", None)]) == []                               # no plan MoveTo: a look here
    assert fetch_findings({"gizmo": BENCH}, [(0, "Find", "widget", BENCH)]) == []


def test_visits_tracks_the_place_of_the_plans_own_moves():
    got = visits(SymbolicState(arrived=RACK), grounded(AT_DEST))
    assert [v[3] for v in got] == [None, BENCH, BENCH, BENCH]


# ── off and empty ──
def test_fetch_guard_off():
    """Off: neither the service nor the hook answers."""
    opt, labs = option({"fetch_guard": "0", "fetch_guard.rank_unknown": "0", "fetch_guard.plan_finding": "0"})
    assert opt.rank_first(run_of([GOAL]), "gizmo") is None
    assert opt.on_plan_findings([], run_of([GOAL]), grounded(AT_DEST)) is None and labs == []


def test_fetch_guard_empty():
    """No goals, or no plan: nothing is added."""
    opt, labs = option()
    assert opt.on_plan_findings([], run_of([]), grounded(AT_DEST)) is None
    assert opt.on_plan_findings(["x"], run_of([GOAL]), []) is None and labs == []


def test_fetch_guard_on_by_default_with_all_three_parts():
    assert [k.default for k in SPEC.keys] == [True, True, True, True]


# ── the service and the hook ──
def test_rank_first_asks_for_an_object_the_memory_does_not_trust():
    opt, labs = option()
    assert opt.rank_first(run_of([GOAL]), "gizmo") is True and labs[0][1]["part"] == "rank_unknown"
    assert option(remembered={"gizmo": RACK})[0].rank_first(run_of([GOAL]), "gizmo") is None
    assert option({"fetch_guard.rank_unknown": "0"})[0].rank_first(run_of([GOAL]), "gizmo") is None


def test_plan_findings_adds_the_finding_to_the_engines_own_and_logs_it_once():
    opt, labs = option()
    run = run_of([GOAL])
    got = opt.on_plan_findings(["engine finding"], run, grounded(AT_DEST))
    assert got[0] == "engine finding" and "must end up" in got[1]
    opt.on_plan_findings([], run, grounded(AT_DEST))
    assert len(labs) == 1 and labs[0][1]["part"] == "plan_finding"
    assert opt.on_plan_findings([], run, grounded(GOOD)) is None
    assert option({"fetch_guard.plan_finding": "0"})[0].on_plan_findings([], run, grounded(AT_DEST)) is None


def test_first_source_skips_the_destination_the_ranking_put_first():
    s = SearchBelief([BENCH, RACK, CHEST])
    s.ranked["gizmo"] = [BENCH, RACK]
    assert s.first_place("gizmo") == BENCH                       # the engine's own first place is the destination
    assert SearchHooks._first_source(s, "gizmo", BENCH, None) == RACK
    assert SearchHooks._first_source(SearchBelief([BENCH, RACK]), "gizmo", BENCH, None) is None   # nothing known: no guess


# ── through the loop ──
class Planner:
    """Returns the plans in order (the last one again when they run out); records each observation it was given."""

    def __init__(self, *plans):
        self.plans, self.obs = [list(p) for p in plans], []

    def generate_plan(self, task, obs, visible_objects):
        self.obs.append(obs)
        return [dict(s) for s in (self.plans.pop(0) if len(self.plans) > 1 else self.plans[0])], {"method": "scripted"}

    def replan(self, task, completed, failed, error, obs, visible_objects):
        return [], {"method": "scripted"}


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": [dict(g) for g in goals] or
            [{"type": "on", "object": "gizmo", "from": "", "to": "bench"}]}


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    r = run_control.manager().start(TASK, user_id="alice", role="member").run
    run_control.begin_run()
    yield r
    reset_manager()
    run_control._CANCEL.clear()


def go(make_assist, env, planner, llm=None, **opts):
    robot = FakeRobot(env, {"gizmo": RACK}, start=CHEST)
    loop = make_assist(robot, llm or FakeLLM(default=understood()), planner, **opts)
    return run_task(loop, TASK, max_events=3000)


def plans(evs):
    return [[f"{s['action']} {s['object']}" for s in e["steps"]] for e in evs if e.get("event") == "plan"]


def test_through_the_loop_a_destination_first_plan_is_refined_with_the_finding(run, make_assist, env):
    planner = Planner(AT_DEST, GOOD)
    evs = go(make_assist, env, planner, fetch_guard="1", **{"fetch_guard.rank_unknown": "0"})
    assert len(planner.obs) == 2 and "must end up" in planner.obs[1]        # the refine was told why
    assert plans(evs)[-1][0] == f"MoveTo {RACK}" and done(evs)["status"] == "success"


def test_through_the_loop_destination_first_plan_runs_as_written_off(run, make_assist, env):
    planner = Planner(AT_DEST, GOOD)
    evs = go(make_assist, env, planner, fetch_guard="0")
    assert len(planner.obs) == 1 and plans(evs)[0][0] == f"MoveTo {BENCH}"


def test_regression_no_source_takes_the_ranked_first_place_that_is_not_the_destination(run, make_assist, env):
    """The failure class: a source the person's answer did not give stays empty; the planner looked at the destination. Now the
    ranking (destination first, as a model may answer) gives the source, and the planner reads it."""
    llm = FakeLLM((r"looking for the gizmo", {"places": [BENCH, RACK]}), default=understood())
    planner = Planner(GOOD)
    evs = go(make_assist, env, planner, llm=llm, fetch_guard="1")
    assert f"from {RACK}" in planner.obs[0] and done(evs)["status"] == "success"
    off = Planner(GOOD)
    go(make_assist, env, off, llm=FakeLLM((r"looking for the gizmo", {"places": [BENCH, RACK]}), default=understood()), fetch_guard="0")
    assert f"from {RACK}" not in off.obs[0]
