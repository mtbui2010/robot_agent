"""Option ``bt_goal``, entry key ``needs_openable``: a tree entry used only when the named slots are places the robot declares openable (e.g.
open a closed source before Find), else the next entry of the kind's list (the tree as before). Made-up flat, objects and trees."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.planning import behavior_tree as bt
from robot_agent.options import bt_goal as B

BIN, SHELF, BENCH, ATTIC = "bin@den", "shelf@loft", "bench@den", "attic@loft"
ENV = {BIN: {"aliases": ["bin"], "loc": {"x": 2.0, "y": 1.0}, "openable": True},
       SHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "openable": False},
       BENCH: {"aliases": ["bench"], "loc": {"x": 6.0, "y": 1.0}, "openable": False},
       ATTIC: {"aliases": ["attic"], "loc": {"x": 3.0, "y": 3.0}}}                  # no openable field: unknown
OPEN_FROM = ["fallback", ["cond", "opened", "{from}"], ["sequence", ["act", "Open", "{from}"], ["cond", "opened", "{from}"]]]


def on_tree(*before_find):
    return ["fallback", ["cond", "placed", "{object}", "{to}"],
            ["sequence",
             ["fallback", ["cond", "holding", "{object}"],
              ["sequence", ["cond", "hand_free"], ["act", "MoveTo", "{from}"], *before_find, ["act", "Find", "{object}"],
               ["act", "Pick", "{object}"], ["cond", "holding", "{object}"]]],
             ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"], ["cond", "hand_free"]]]


PLAIN = {"tree": on_tree(), "skip_if": ["hand"], "assumed": True}
OPENING = {"tree": on_tree(OPEN_FROM), "skip_if": ["hand"], "needs_openable": ["from"], "assumed": True}
TREES = {"on": [OPENING, PLAIN]}
ACTS = {"MoveTo", "Find", "Pick", "Place", "Open"}


def goal(src):
    return {"type": "on", "object": "gizmo", "from": src, "to": BENCH}


def rule(key):
    return bool(ENV.get(key, {}).get("openable"))


def openable(key):
    return B.declared_openable(ENV, key, rule)


def acts(tree):
    return [leaf[1] for leaf in bt.leaves(tree) if leaf[0] == "act"]


def plain(src):
    return B.tree_for(goal(src), {"on": PLAIN}, ACTS, True, lambda k: True)


@pytest.mark.parametrize("key,want", [(BIN, True), (SHELF, False), (ATTIC, False), ("cellar@den", False), ("", False), (None, False)])
def test_declared_openable_needs_the_field_and_the_robot_rule(key, want):
    assert B.declared_openable(ENV, key, rule) is want


def test_declared_openable_follows_the_robot_rule_over_the_field():
    assert B.declared_openable(ENV, SHELF, lambda k: True) is True and B.declared_openable(ENV, BIN, lambda k: False) is False


def test_an_openable_source_gets_the_open_step_before_find():
    t = B.tree_for(goal(BIN), TREES, ACTS, True, lambda k: True, openable=openable)
    assert acts(t) == ["MoveTo", "Open", "Find", "Pick", "MoveTo", "Place"] and ["act", "Open", BIN] in list(bt.leaves(t))


@pytest.mark.parametrize("src", [SHELF, ATTIC, "cellar@den"])
def test_a_surface_or_an_unknown_source_keeps_the_tree_as_before(src):
    assert B.tree_for(goal(src), TREES, ACTS, True, lambda k: True, openable=openable) == plain(src)


def test_no_openable_rule_or_no_place_data_keeps_the_tree_as_before_empty():
    assert B.tree_for(goal(BIN), TREES, ACTS, True, lambda k: True) == plain(BIN)
    empty = lambda k: B.declared_openable({}, k, rule)                       # noqa: E731
    assert B.tree_for(goal(BIN), TREES, ACTS, True, lambda k: True, openable=empty) == plain(BIN)


def test_entries_without_the_key_ignore_the_rule_off():
    assert B.tree_for(goal(BIN), {"on": PLAIN}, ACTS, True, lambda k: True, openable=lambda k: True) == plain(BIN)
    assert B.tree_for(goal(SHELF), {"on": PLAIN}, ACTS, True, lambda k: True, openable=lambda k: False) == plain(SHELF)


def test_a_robot_without_open_keeps_the_tree_as_before():
    assert B.tree_for(goal(BIN), TREES, ACTS - {"Open"}, True, lambda k: True, openable=openable) == B.tree_for(
        goal(BIN), {"on": PLAIN}, ACTS - {"Open"}, True, lambda k: True)


def test_a_source_already_open_is_not_opened_again():
    tree = B.tree_for(goal(BIN), TREES, ACTS, True, lambda k: True, openable=openable)

    class Host:
        def __init__(self, opened):
            self.world, self.calls = NS(holding=None, arrived=None, opened=set(opened)), []

        def cond(self, name, *args):
            return B.holds(name, args, self.world, {"gizmo": BENCH} if "Place" in self.calls else {})

        def act(self, action, obj):
            self.calls.append(action)
            self.world.opened |= {obj} if action == "Open" else set()
            self.world.holding = obj if action == "Pick" else None if action == "Place" else self.world.holding
            return True
            yield
    for opened, want in ((set(), ["MoveTo", "Open", "Find"]), ({BIN}, ["MoveTo", "Find"])):
        h = Host(opened)
        list(bt.tick(tree, h))
        assert h.calls[:len(want)] == want


# ── the class, through the real AssistLoop: the object is in a closed container ───────────────────────────────────────────────
@pytest.fixture
def env():
    return dict(ENV)


def _run(make_assist, trees):
    und = {"status": "clear", "reason": "", "question": "", "goals": [goal(BIN)]}
    robot = FakeRobot(ENV, {"gizmo": BIN + "/in"}, start=BENCH)
    plan = steps(f"MoveTo {BIN}", f"Open {BIN}", "Find gizmo", "Pick gizmo", f"MoveTo {BENCH}", f"Place {BENCH}")
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, search_legs=1, health_wait_s=0,
                       bt_goal="1")
    loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"bt_goal": {"trees": trees}}}
    return robot, run_task(loop, "Take the gizmo from the bin and put it on the bench.", max_events=3000)


def test_loop_regression_a_closed_source_is_opened_before_the_first_find_no_wasted_look(make_assist):
    """The class: the fetch looked into a closed container first (a Find that sees nothing, one attempt used) and opened it only after."""
    robot, evs = _run(make_assist, {"on": [OPENING, PLAIN]})
    skills = [t[:3] for t in robot.trace if t[0] in ("move", "open_drawer", "find")]
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == BENCH
    assert skills[:3] == [("move", BIN, True), ("open_drawer", BIN, True), ("find", "gizmo", True)]
    assert [t for t in robot.trace if t[0] == "find" and not t[2]] == []
