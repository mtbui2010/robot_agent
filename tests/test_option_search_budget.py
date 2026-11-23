"""Option ``search_budget`` (default OFF), through the real AssistLoop and the belief: a budget per object (pack data), a stop on the remaining
share of the belief, one where-question when the search is over, then a report and a stop; "I don't know" does not repeat the question and does
not end a search that still has looks. Made-up numbers (assumed); the apple, the towel and the places are the symbolic FakeRobot's own."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core import run_control
from robot_agent.core.options import REGISTRY
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.core.planning.search import SearchBelief
from robot_agent.interact.run_manager import reset_manager
from robot_agent.options.search_budget import SearchBudget, asks, looks_of, remaining, stop_of

DT, SF, CT, DS, SD = "dining table@kitchen", "shelf@kitchen", "counter@kitchen", "desk@study room", "side table@living room"
ONE = {"type": "on", "object": "apple", "from": "", "to": "dining table"}
TWO = {"type": "on", "object": "towel", "from": "counter", "to": "dining table"}
PLAN_1 = steps(f"MoveTo {SF}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
PLAN_2 = steps(f"MoveTo {SF}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}",
               f"MoveTo {CT}", "Find towel", "Pick towel", f"MoveTo {DT}", f"Place {DT}")


@pytest.fixture
def run(make_assist):
    reset_manager()
    run_control._CANCEL.clear()
    r = run_control.manager().start("t", user_id="alice", role="member").run
    run_control.begin_run()
    yield r
    reset_manager()
    run_control._CANCEL.clear()


def go(make_assist, env, budget=None, where=SD, answers=(), two=False, on=True, **opts):
    """The apple is at ``where`` (the 4th place the ranking names); the towel (two=True) too."""
    robot = FakeRobot(env, {"apple": where, "towel": where}, answers=list(answers))
    goals = [ONE, TWO] if two else [ONE]
    llm = FakeLLM({"status": "clear", "reason": "", "question": "", "goals": goals}, default={"ranked": [SF, CT, DS, SD]})
    base = dict(search_legs=1, search_rank="prior", search_max=9, search_total=4, ask_help=False, detector_miss_rate=0)
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN_2 if two else PLAN_1), **{**base, **({"search_budget": "1"} if on else {}), **opts})
    if budget is not None:
        loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, options={"search_budget": {"budget": budget}})
    evs = run_task(loop, "Put the apple on the dining table.", max_events=4000)
    return robot, evs, loop


def qs(evs):
    return [str(e.get("question")) for e in evs if e.get("event") == "clarify"]


def finds(robot):
    """The Finds of the apple that saw nothing (the looks that missed)."""
    return [o for s, o, ok, *_ in robot.trace if s == "find" and o == "apple" and not ok]


def test_registered_and_off_by_default_the_data_is_ignored_off(run, make_assist, env):
    assert "search_budget" in ASSIST_OPTIONS and REGISTRY.get("search_budget").key("enabled").default is False
    robot, evs, loop = go(make_assist, env, {"looks_per_object": 1, "assumed": True}, on=False, search_total=8)
    assert done(evs)["status"] == "success" and len(finds(robot)) == 2          # the engine's own budget of 8 looks
    assert not [e for e in loop.lab_log if e["flag"] == "search_budget"]


BAD = (None, {}, {"assumed": True}, {"looks_per_object": 3}, {"looks_per_object": 3, "source": ""}, {"looks_per_object": 0}, {"looks_per_object": "3"}, {"stop_probability": 1.5}, {"ask": "once"})


@pytest.mark.parametrize("bad", BAD)
def test_an_empty_or_foreign_pack_keeps_the_engine_budget_empty(run, make_assist, env, bad):
    robot, evs, loop = go(make_assist, env, bad, search_total=8)
    assert done(evs)["status"] == "success" and len(finds(robot)) == 2, bad
    assert not [e for e in loop.lab_log if e["flag"] == "search_budget"], bad


def test_without_search_legs_the_run_still_ends_normally(run, make_assist, env):
    robot, evs, loop = go(make_assist, env, {"looks_per_object": 1, "ask": "never", "assumed": True}, search_total=8, search_legs=0)
    assert done(evs)["status"] in ("success", "failed")           # the stop rule of the legs-less search stays the engine's (search_max)


def test_each_object_has_its_own_budget_per_object(run, make_assist, env):
    robot, evs, _ = go(make_assist, env, {"looks_per_object": 4, "assumed": True}, two=True)
    assert robot.obj["apple"] == DT and robot.obj["towel"] == DT and done(evs)["status"] == "success", done(evs)
    robot, evs, _ = go(make_assist, env, None, two=True)                         # no data: one count for the run, the second search runs dry
    assert done(evs)["status"] == "failed"


def test_the_declared_looks_replace_the_engine_budget_and_the_run_reports_where_it_looked(run, make_assist, env):
    robot, evs, loop = go(make_assist, env, {"looks_per_object": 2, "ask": "never", "assumed": True}, search_total=8)
    d = done(evs)
    assert d["status"] == "failed" and len(finds(robot)) == 2 and "could not find the apple" in str(d), d
    assert [e for e in loop.lab_log if e["flag"] == "search_budget" and e["why"] == "looks" and e["object"] == "apple"]


def test_the_search_stops_when_the_remaining_probability_is_low(run, make_assist, env):
    robot, evs, loop = go(make_assist, env, {"stop_probability": 0.99, "ask": "never", "assumed": True}, search_total=8)
    assert done(evs)["status"] == "failed" and len(finds(robot)) < 2
    assert [e for e in loop.lab_log if e["flag"] == "search_budget" and e["why"] == "probability"]
    robot, evs, _ = go(make_assist, env, {"stop_probability": 0.0001, "assumed": True}, search_total=8)      # a floor nothing falls under
    assert done(evs)["status"] == "success"


def test_when_the_search_is_over_the_person_is_asked_once_then_the_run_reports_and_stops(run, make_assist, env):
    robot, evs, _ = go(make_assist, env, {"looks_per_object": 2, "assumed": True}, answers=["I do not know"], ask_help=True)
    asked = [q for q in qs(evs) if "Where is" in q or "couldn't find" in q]
    assert len(asked) == 1 and done(evs)["status"] == "failed" and len(finds(robot)) == 2
    robot, evs, _ = go(make_assist, env, {"looks_per_object": 2, "assumed": True}, answers=["on the side table in the living room"], ask_help=True)
    assert len([q for q in qs(evs) if "Where is" in q]) == 1 and [e for e in evs if e["event"] == "replan"]      # the answer is used (a replan), not asked again


def test_ask_never_ends_the_run_without_the_question(run, make_assist, env):
    robot, evs, _ = go(make_assist, env, {"looks_per_object": 2, "ask": "never", "assumed": True}, ask_help=True)
    assert done(evs)["status"] == "failed" and not [q for q in qs(evs) if "Where is" in q]


def test_i_do_not_know_asked_earlier_goes_on_alone_while_there_are_looks(run, make_assist, env):
    robot, evs, _ = go(make_assist, env, {"looks_per_object": 6, "assumed": True}, answers=["I do not know"], ask_help=True, search_max=1)
    assert done(evs)["status"] == "success" and len([q for q in qs(evs) if "Where is" in q]) == 1


def test_readers_and_the_remaining_share_pure():
    assert looks_of({"looks_per_object": 3}) == 3 and all(looks_of(b) is None for b in (None, {}, {"looks_per_object": 0}, {"looks_per_object": True}))
    assert stop_of({"stop_probability": 0.1}) == 0.1 and all(stop_of(b) is None for b in (None, {"stop_probability": 0}, {"stop_probability": 1}, {"stop_probability": "x"}))
    assert asks(None) and asks({"ask": "once"}) and not asks({"ask": "never"})
    bel = SearchBelief(["a", "b", "c", "d"])
    assert remaining(bel, "x") == 1.0
    bel.see("x", "a")
    seen = []
    for p in ("b", "c", "d"):
        bel.missed("x", p, ())
        seen.append(remaining(bel, "x"))
    assert seen[0] > seen[1] > seen[2] > 0                                          # falls as the places are used up
    bel.missed("x", "a", ())
    assert remaining(bel, "x") == 0.0


def test_no_belief_or_no_data_answers_nothing_empty():
    from types import SimpleNamespace as NS
    from robot_agent.core.options.config import resolve
    from robot_agent.options.search_budget import SPEC
    svc = NS(robot=NS(manifest=NS(planner={"options": {"search_budget": {"budget": {"looks_per_object": 2, "assumed": True}}}})), lab=lambda *a, **k: None)
    opt = SearchBudget(resolve(SPEC, {"search_budget": "1"}, {}, None), svc)
    assert opt.search_over(NS(), "x") is None and opt.looks_made(NS(), "x") is None and opt.search_limit(NS(), "x") == 2
    svc.robot.manifest.planner = {}
    assert opt.search_limit(NS(), "x") is None and opt.search_over(NS(search=SearchBelief(["a"])), "x") is None


