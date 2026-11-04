"""SAGE-v2 m10 round 2, failure class: a replan's suffix that repairs the failed step and drops a goal still to do
(the held object is put down on a surface instead of delivered). Synthetic ENV and sentences."""
from fakes import FakeLLM, FakeRobot, done, run_task, steps
from test_sage_v2_step3_gate import BlockPlanner, Block, understood, DS, DR, CT, loop, robot, places  # noqa: F401


class ForgetfulPlanner(BlockPlanner):
    """Plans the delivery with a stray Find of the container (which fails); its replan forgets the delivery."""

    def generate_plan(self, task, obs, visible_objects, **kw):
        return steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DR}", "Find drawer", f"Open {DR}",
                     f"PutIn {DR}"), {"method": "t"}

    def replan(self, *a, **kw):
        return steps(f"MoveTo {CT}", f"Place {CT}"), {}


def test_replan_suffix_keeps_the_pending_goal(make_assist, env):
    robot = FakeRobot(env, {"apple": DS}, faults=[{"skill": "find", "obj": "drawer", "nth": 1, "kind": "fail"}])
    llm = FakeLLM(understood(("in", "apple", "desk", "drawer")))
    evs = run_task(make_assist(robot, llm, ForgetfulPlanner([])), "Put the apple from the desk in the drawer.")
    assert done(evs)["status"] == "success", done(evs)
    assert robot.obj["apple"] == DR + "/in"


def test_finish_when_the_suffix_never_lets_go(loop, robot, places):
    from test_sage_v2_step3_gate import G, W, run_repair, score_of
    goals = [G("in", "spoon", DS, DR)]
    world = W(holding="spoon", arrived=DS, opened=[DR])
    out, _n, counts = run_repair(loop, robot, places, steps(f"MoveTo {CT}"), world, goals, only=("finish",),
                                 reached=loop._reaches)
    assert counts.get("finish") == 1 and score_of(loop, places, world, goals)(out) == 0
    assert [s["action"] for s in out][-1] == "PutIn"
