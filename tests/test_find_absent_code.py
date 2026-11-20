"""Option ``fetch_guard`` part ``absent_find``: a failed ``Find`` that reports no reason at all is ABSENT (the search and then the question run), not
UNKNOWN (a replan of the same plan). Made-up flat (den, loft), made-up object ``widget``. The class: on the real robot every Find that saw nothing
answered {isdone: false} with no text, so five misses in one run each ended in a replan that returned the same plan."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import FailureCode as F
from robot_agent.options.fetch_guard import FetchGuard

SHELF, BENCH = "shelf@loft", "bench@den"
ENV = {SHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 6.0, "y": 1.0}, "surface": True}}


@pytest.fixture
def env():
    return dict(ENV)


class Svc:
    def lab(self, *a, **k):
        pass


def option(**cfg):
    o = FetchGuard.__new__(FetchGuard)
    o.cfg, o.svc = {"absent_find": True, **cfg}, Svc()
    return o


def test_the_service_turns_unknown_into_absent_only_for_a_find():
    o = option()
    assert o.failure_code(None, "Find", F.UNKNOWN) is F.ABSENT
    assert o.failure_code(None, "Pick", F.UNKNOWN) is None                      # the engine's own code stays
    assert o.failure_code(None, "Find", F.PERCEPTION_UNAVAILABLE) is None        # a reason the engine knows is kept
    assert o.failure_code(None, "Find", F.TIMEOUT) is None


def test_the_service_is_off_with_the_key_off_empty():
    assert option(absent_find=False).failure_code(None, "Find", F.UNKNOWN) is None


def _run(make_assist, **opts):
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "widget", "from": SHELF, "to": BENCH}]}
    faults = [{"skill": "find", "obj": "widget", "nth": "all", "kind": "fail", "msg": ""}]
    robot = FakeRobot(ENV, {"widget": SHELF}, faults=faults, answers=[], start=BENCH)
    planner = ScriptedPlanner(steps(f"MoveTo {SHELF}", "Find widget", "Pick widget", f"MoveTo {BENCH}", f"Place {BENCH}"),
                              replans=[steps("Find widget", "Pick widget", f"MoveTo {BENCH}", f"Place {BENCH}")] * 6)
    loop = make_assist(robot, FakeLLM(default=und), planner, detector_miss_rate=0, search_legs=1, health_wait_s=0, search_max=2, **opts)
    evs = run_task(loop, "Take the widget from the shelf and put it on the bench.", max_events=3000)
    return robot, planner, evs


def test_loop_final_a_silent_find_miss_searches_on_and_asks_where_instead_of_replanning_the_same_plan(make_assist):
    robot, planner, evs = _run(make_assist, fetch_guard="1")
    asked = [t[1] for t in robot.trace if t[0] == "ask"]
    assert any("where is it" in q.lower() for q in asked), (asked, done(evs))     # the search ran its course, then the question
    assert sum(1 for c in planner.calls if c[0] == "replan") <= 3


def test_loop_final_with_the_option_off_the_silent_miss_goes_to_replans_as_before(make_assist):
    robot, planner, evs = _run(make_assist)                                       # make_assist pins fetch_guard off
    assert sum(1 for c in planner.calls if c[0] == "replan") > 3
