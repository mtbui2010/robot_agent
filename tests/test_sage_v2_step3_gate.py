"""SAGE-v2 round 3, step 3 (gate convergence without LLM edits): F1.1 the goal kind from its destination, F1.3
the symbolic repair operators (plan_repair.py), block-aware, each kept only when the critical findings drop.

Classes (gate30 round 1): C1 the gate did not converge (EN 004 006 008 017 021, KO 003 008 021 023), C1b open /
close with the hand full, C1c an ``on`` goal for a container, C1e a room as a MoveTo target. Synthetic ENVs and
sentences written for the test.
"""
import pytest

from fakes import FakeLLM, FakeRobot, done, run_task, steps
from robot_agent.core.planning import goal_fix, plan_repair
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from robot_agent.core.planning.base import WorldState

DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


@pytest.fixture
def robot():
    from robot_agent.core.planning.robot import RobotView
    return RobotView.load("fakebot")


@pytest.fixture
def places(env, robot):
    return Places(env, openable=robot.openable)


@pytest.fixture
def loop(robot):
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = robot
    lp._namemap = robot.adapter.namemap
    return lp


# ── F1.1 ──
@pytest.mark.parametrize("text", ["Put the spoon in the drawer.", "Please put the spoon into the kitchen drawer",
                                  "숟가락을 부엌 서랍에 넣어 주세요", "숟가락 부엌 서랍 안에 둬요"])
def test_on_to_container_becomes_in(places, text):
    out, notes = goal_fix.kind_from_destination([G("on", "spoon", "", DR)], text, places)
    assert out[0]["type"] == "in" and notes


@pytest.mark.parametrize("text", ["Put the spoon on top of the drawer.", "숟가락을 부엌 서랍 위에 올려 주세요",
                                  "Put the spoon on the drawer."])
def test_on_top_of_kept(places, text):
    assert goal_fix.kind_from_destination([G("on", "spoon", "", DR)], text, places)[0][0]["type"] == "on"


def test_put_away_on_shelf_kept_on():
    env = {"shelf unit@hall": {"aliases": ["shelf unit"], "openable": True}, "desk@hall": {"aliases": ["desk"]}}
    ps = Places(env)
    out, _ = goal_fix.kind_from_destination([G("on", "mug", "", "shelf unit@hall")],
                                            "Put the mug away on the shelf unit.", ps)
    assert out[0]["type"] == "on"


def test_in_to_surface_becomes_on(places):
    out, _ = goal_fix.kind_from_destination([G("in", "cup", "", DT)], "put the cup on the dining table", places)
    assert out[0]["type"] == "on"


def test_empty_object_goal_dropped():
    keep, dropped = goal_fix.drop_empty([G("on", "", "", DT), G("on", "cup", "", DT)])
    assert keep == [G("on", "cup", "", DT)] and dropped == [G("on", "", "", DT)]


def test_goal_object_is_place_dropped(places):
    out, dropped = goal_fix.drop_invented([G("on", "drawer", "", "counter@kitchen")], "open the drawer", places)
    assert dropped and out == [{"type": "open", "object": DR, "from": "", "to": DR}]


def test_unsaid_go_dropped(places):
    goals = [G("on", "cup", "", DT), G("go", "", "", "sofa@living room")]
    out, dropped = goal_fix.drop_invented(goals, "put the cup on the dining table", places)
    assert out == goals[:1] and dropped == goals[1:]
    # a go the words do name (its room) stays
    goals2 = [G("on", "cup", "", DT), G("go", "", "", "sofa@living room")]
    assert goal_fix.drop_invented(goals2, "put the cup on the dining table, then wait in the living room",
                                  places)[0] == goals2


# ── F1.3 operators ──
def score_of(loop, places, world, goals):
    return lambda s: len([p for p in loop._check(s, world, places, goals) if p[2]])


def run_repair(loop, robot, places, plan, world, goals, **kw):
    caps = robot.model_capabilities()
    return plan_repair.repair(plan, score_of(loop, places, world, goals), world, places, goals, caps=caps,
                              actions=robot.plan_actions, **kw)


def test_repair_hoist_open_before_pick(loop, robot, places):
    goals = [G("in", "spoon", DS, DR)]
    plan = steps(f"MoveTo {DS}", "Find spoon", "Pick spoon", f"MoveTo {DR}", f"Open {DR}", f"PutIn {DR}")
    out, notes, counts = run_repair(loop, robot, places, plan, W(), goals)
    assert counts.get("hoist") == 1 and score_of(loop, places, W(), goals)(out) == 0
    assert [(s["action"], s["object"]) for s in out][:2] == [("MoveTo", DR), ("Open", DR)]


def test_repair_close_while_holding_goes_after_release(loop, robot, places):
    goals = [G("on", "spoon", DR, DT), G("close", DR, "", DR)]
    plan = steps(f"MoveTo {DR}", f"Open {DR}", "Find spoon", "Pick spoon", f"Close {DR}", f"MoveTo {DT}",
                 f"Place {DT}")
    out, _n, counts = run_repair(loop, robot, places, plan, W(), goals)
    assert counts.get("hoist") == 1 and score_of(loop, places, W(), goals)(out) == 0
    assert [s["action"] for s in out][-2:] == ["MoveTo", "Close"]


def test_repair_dedupe_replaced_goal(loop, robot, places):
    goals = [G("on", "apple", DS, DT)]
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}",
                 f"MoveTo {DT}", "Find apple", "Pick apple", f"Place {DT}")
    out, _n, counts = run_repair(loop, robot, places, plan, W(), goals)
    assert counts.get("dedupe") == 1 and out == plan[:5]


def test_repair_insert_moveto_and_find(loop, robot, places):
    goals = [G("on", "apple", DS, DT)]
    plan = steps(f"MoveTo {DS}", "Pick apple", f"Place {DT}")
    out, _n, counts = run_repair(loop, robot, places, plan, W(), goals)
    assert counts.get("insert") == 2
    assert [(s["action"], s["object"]) for s in out] == [(s["action"], s["object"]) for s in steps(
        f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")]


def test_repair_room_target(loop, robot, places):
    goals = [G("on", "apple", DS, DT)]
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", "MoveTo @kitchen", f"Place {DT}")
    plan[3]["grounded"] = False
    out, _n, counts = run_repair(loop, robot, places, plan, W(), goals)
    assert counts.get("room") == 1 and out[3] == {"action": "MoveTo", "object": DT, "grounded": True}


def test_repair_place_to_putin(loop, robot, places):
    goals = [G("in", "spoon", DS, DR)]
    plan = steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DS}", "Find spoon", "Pick spoon", f"MoveTo {DR}",
                 f"Place {DR}")
    out, _n, counts = run_repair(loop, robot, places, plan, W(), goals)
    assert counts.get("action") == 1 and out[-1]["action"] == "PutIn"


def test_repair_complete_missing_goal_only_when_on(loop, robot, places):
    goals = [G("on", "apple", DS, DT), G("on", "mug", DS, CT)]
    plan = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    out, _n, counts = run_repair(loop, robot, places, plan, W(), goals)
    assert out == plan and counts["completed_by_gate"] == 0                       # off by default
    out, _n, counts = run_repair(loop, robot, places, plan, W(), goals, complete=True, reached=loop._reaches)
    assert counts["completed_by_gate"] == 1 and score_of(loop, places, W(), goals)(out) == 0


def test_repair_never_increases_findings(loop, robot, places):
    goals = [G("on", "apple", DS, DT)]
    for plan in (steps("Pick apple"), steps(f"MoveTo {CT}", f"Place {CT}"), steps(f"Open {DR}", "Find apple")):
        before = score_of(loop, places, W(), goals)(plan)
        out, _n, _c = run_repair(loop, robot, places, plan, W(), goals)
        assert score_of(loop, places, W(), goals)(out) <= before


class Block:
    def __init__(self, name, steps_):
        self.name, self.steps = name, steps_


class BlockPlanner:
    """A planner with sub-goal blocks; repair_block answers from ``fixes`` (and records the calls)."""

    def __init__(self, blocks, fixes=()):
        self._blocks, self.fixes, self.edits = blocks, list(fixes), []

    def generate_plan(self, task, obs, visible_objects):
        return [dict(s) for b in self._blocks for s in b.steps], {"method": "blocks"}

    def replan(self, *a):
        return [], {}

    def blocks(self):
        return self._blocks

    def join_blocks(self, blocks):
        return [dict(s) for b in blocks for s in b.steps]

    def repair_block(self, block, feedback, *, task, state=None, obs=""):
        self.edits.append((block.name, feedback))
        return self.fixes.pop(0) if self.fixes else block.steps


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


def test_hoist_across_blocks_updates_both(loop, robot, places):
    goals = [G("in", "spoon", DS, DR)]
    b1, b2 = Block("fetch", steps(f"MoveTo {DS}", "Find spoon", "Pick spoon")), \
        Block("stash", steps(f"MoveTo {DR}", f"Open {DR}", f"PutIn {DR}"))
    blocks = [b1, b2]
    join = lambda bs: [dict(s) for b in bs for s in b.steps]                       # noqa: E731
    out, _n, counts = run_repair(loop, robot, places, join(blocks), W(), goals, blocks=blocks, join=join,
                                 ground=lambda s: s)
    assert counts.get("hoist") == 1
    assert [s["action"] for s in b1.steps] == ["MoveTo", "Open", "MoveTo", "Find", "Pick"]
    assert [s["action"] for s in b2.steps] == ["MoveTo", "PutIn"]
    assert join(blocks) == out


def test_gate_no_llm_edit_when_symbolic_fixes(make_assist, env):
    blocks = [Block("fetch", steps(f"MoveTo {DS}", "Find spoon", "Pick spoon")),
              Block("stash", steps(f"MoveTo {DR}", f"Open {DR}", f"PutIn {DR}"))]
    planner = BlockPlanner(blocks)
    robot = FakeRobot(env, {"spoon": DS})
    evs = run_task(make_assist(robot, FakeLLM(understood(("in", "spoon", "desk", "drawer"))), planner),
                   "Put the spoon from the desk in the drawer.")
    assert done(evs)["status"] == "success", done(evs)
    assert planner.edits == []                                                     # no LLM edit call
    assert robot.obj["spoon"] == DR + "/in"


def test_operator_fix_survives_block_edit(make_assist, env):
    """Finding 1 (Open with a full hand) is fixed by the hoist; finding 2 (the apple put on the wrong place) goes
    to the LLM block edit, which starts from the repaired blocks: the hoist is still in the final plan."""
    b1 = Block("fetch spoon", steps(f"MoveTo {DS}", "Find spoon", "Pick spoon"))
    b2 = Block("stash spoon", steps(f"MoveTo {DR}", f"Open {DR}", f"PutIn {DR}"))
    b3 = Block("apple", steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {CT}", f"Place {CT}"))
    fixed_b3 = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    planner = BlockPlanner([b1, b2, b3], fixes=[fixed_b3])
    robot = FakeRobot(env, {"spoon": DS, "apple": DS})
    llm = FakeLLM(understood(("in", "spoon", "desk", "drawer"), ("on", "apple", "desk", "dining table")))
    evs = run_task(make_assist(robot, llm, planner), "Put the spoon from the desk in the drawer and the apple from "
                                                      "the desk on the dining table.")
    assert done(evs)["status"] == "success", done(evs)
    assert [name for name, _fb in planner.edits] == ["apple"]
    plan = next(e for e in evs if e["event"] == "plan")["plan_meta"]
    assert plan["repaired_by_op"].get("hoist") == 1 and plan["completed_by_gate"] == 0
    assert [s["action"] for s in b1.steps][:2] == ["MoveTo", "Open"]


def test_gate_complete_off_by_default(make_assist, env):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), BlockPlanner([]))
    assert loop.cfg["gate_complete"] == "0"


def test_repair_find_where_the_person_said(loop, robot, places):
    goals = [G("on", "apple", DS, DT)]
    plan = steps(f"MoveTo {CT}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    out, _n, counts = run_repair(loop, robot, places, plan, W(), goals)
    assert counts.get("insert") == 1 and out[0]["object"] == DS
