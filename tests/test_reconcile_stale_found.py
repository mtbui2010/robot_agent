"""A ``found`` object kept from an earlier run is dropped at run start when the base has moved since the sighting (reconcile.drop_stale_found):
the plan then looks (or asks) again instead of trusting a sighting that no longer holds. Made-up object ``widget``, made-up poses."""
import pytest

from fakes import FakeLLM, FakeRobot, done, run_task, steps
from robot_agent.core.planning import reconcile as R
from robot_agent.core.planning.base import WorldState

SHELF, BENCH = "shelf@loft", "bench@den"
ENV = {SHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 6.0, "y": 1.0}, "surface": True}}


@pytest.fixture
def env():
    return dict(ENV)


def seen(robot_xy):
    return {"pose_3d": [0.3, 0.5, 0.7], "ts": 1.0, "robot_pose": [robot_xy[0], robot_xy[1], 0.0]}


def world(found="widget", robot_xy=(1.0, 1.0)):
    return WorldState(found=found, found_pose=seen(robot_xy) if robot_xy else None)


def test_a_sighting_from_another_place_is_dropped_and_reported():
    w = world(robot_xy=(1.0, 1.0))
    assert R.drop_stale_found(w, [6.0, 1.0]) == "widget"
    assert (w.found, w.found_pose) == (None, None)


def test_a_sighting_from_where_the_robot_still_stands_is_kept():
    w = world(robot_xy=(1.0, 1.0))
    assert R.drop_stale_found(w, [1.1, 1.0]) is None and w.found == "widget" and w.found_pose


def test_what_cannot_be_proved_stale_is_left_as_it_is_empty():
    assert R.drop_stale_found(world(robot_xy=None), [6.0, 1.0]) is None          # no base pose with the sighting
    w = world(robot_xy=(1.0, 1.0))
    assert R.drop_stale_found(w, None) is None and w.found == "widget"            # the pose cannot be read now
    assert R.drop_stale_found(WorldState(), [6.0, 1.0]) is None                   # nothing found


def test_loop_final_the_plan_is_not_told_the_old_sighting_still_holds(make_assist, env):
    """The class: a run on the real robot started with ``found=plastic spoon`` from a find made 10 minutes and 3 m earlier; the planner skipped the Find."""
    class Rec:
        obs = []

        def generate_plan(self, task, obs, visible_objects):
            Rec.obs.append(obs)
            return [dict(s) for s in steps(f"MoveTo {SHELF}", "Find widget", "Pick widget", f"MoveTo {BENCH}", f"Place {BENCH}")], {"method": "scripted"}

        def replan(self, *a, **k):
            return [], {"method": "scripted"}
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "widget", "from": SHELF, "to": BENCH}]}
    robot = FakeRobot(ENV, {"widget": SHELF}, start=BENCH)
    # sighted at the shelf (1, 1); the robot now stands at the bench (6, 1)
    loop = make_assist(robot, FakeLLM(default=und), Rec(), world={"found": "widget", "found_pose": seen((1.0, 1.0))},
                       detector_miss_rate=0, search_legs=1, health_wait_s=0)
    evs = run_task(loop, "Take the widget from the shelf and put it on the bench.", max_events=3000)
    assert Rec.obs and "found=widget" not in Rec.obs[0]
    assert done(evs)["status"] == "success"
