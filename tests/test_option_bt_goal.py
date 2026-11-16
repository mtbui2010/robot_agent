"""Option ``bt_goal`` (default OFF) and its runner ``core/planning/behavior_tree.py``: a goal run by a behaviour tree declared as data.
Made-up flat (a den and a loft), made-up objects (``gizmo``, ``widget``), a made-up language ``zq`` and a made-up second robot with other action
names. The runner on small trees with a fake host; ``tree_for`` on small tables; the FINAL outcome through the real AssistLoop."""
import json
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import NEXT, REGISTRY
from robot_agent.core.planning import behavior_tree as bt
from robot_agent.core.planning import langdata
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.options import bt_goal as B

SHELF, BENCH, CRATE = "shelf@loft", "bench@den", "crate@den"
ENV = {SHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       BENCH: {"aliases": ["bench"], "loc": {"x": 6.0, "y": 1.0}, "surface": True},
       CRATE: {"aliases": ["crate"], "loc": {"x": 6.5, "y": 2.0}, "surface": True}}
PICK_OK = ["sequence", ["act", "Pick", "{object}"], ["cond", "holding", "{object}"]]
ON = ["fallback", ["cond", "placed", "{object}", "{to}"],
      ["sequence",
       ["fallback", ["cond", "holding", "{object}"],
        ["sequence", ["cond", "hand_free"], ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"],
         ["fallback", ["retry", 2, PICK_OK],
          ["sequence", ["ask", "q_ask_next_pick", "ask_next.retry", "dialogue.yes"], ["act", "Find", "{object}"], PICK_OK]]]],
       ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"], ["reply", "say_bt_goal_done"]]]
TREES = {"on": {"tree": ON, "skip_if": ["hand"], "assumed": True}}
PLAN = steps(f"MoveTo {SHELF}", "Find gizmo", "Pick gizmo", f"MoveTo {BENCH}", f"Place {BENCH}")


@pytest.fixture
def env():
    return dict(ENV)


def drive(gen):
    """Run a generator to its end: (what it yielded, what it returned)."""
    evs = []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as e:
        return evs, e.value


# ── the runner on small trees with a fake host ────────────────────────────────────────────────────────────────────────────────
class Host:
    """Leaves of a made-up robot: ``results`` per action (a list, popped; default True), ``answers`` for ask / confirm, ``facts`` for cond."""

    def __init__(self, results=None, answers=(), facts=()):
        self.results, self.answers, self.facts, self.calls = {k: list(v) for k, v in (results or {}).items()}, list(answers), set(facts), []

    def cond(self, name, *args):
        self.calls.append(("cond", name) + args)
        return name in self.facts

    def act(self, action, *args):
        self.calls.append(("act", action) + args)
        yield {"event": "step", "action": action}
        r = self.results.get(action)
        return r.pop(0) if r else True

    def ask(self, key, *refs):
        self.calls.append(("ask", key))
        yield {"event": "clarify", "key": key}
        return self.answers.pop(0) if self.answers else None

    confirm = ask

    def reply(self, key):
        self.calls.append(("reply", key))
        yield {"event": "status", "key": key}
        return True


def acts(h):
    return [c[1] for c in h.calls if c[0] == "act"]


def test_sequence_stops_at_the_first_failure():
    h = Host(results={"Grab": [False]})
    _evs, out = drive(bt.tick(["sequence", ["act", "Drive", "x"], ["act", "Grab", "y"], ["act", "Drop", "x"]], h))
    assert out == bt.FAILURE and acts(h) == ["Drive", "Grab"]


def test_fallback_tries_the_next_child_after_a_failure_and_stops_at_a_success():
    h = Host(results={"Grab": [False]})
    _evs, out = drive(bt.tick(["fallback", ["act", "Grab", "y"], ["act", "Look", "y"], ["act", "Drop", "x"]], h))
    assert out == bt.SUCCESS and acts(h) == ["Grab", "Look"]


@pytest.mark.parametrize("fails,n,want,tries", [(1, 2, bt.SUCCESS, 2), (2, 2, bt.FAILURE, 2), (0, 3, bt.SUCCESS, 1), (3, 3, bt.FAILURE, 3)])
def test_retry_ticks_its_child_at_most_n_times(fails, n, want, tries):
    h = Host(results={"Grab": [False] * fails})
    assert drive(bt.tick(["retry", n, ["act", "Grab", "y"]], h))[1] == want and acts(h) == ["Grab"] * tries


def test_a_condition_reads_and_never_acts():
    h = Host(facts={"holding"})
    evs, out = drive(bt.tick(["fallback", ["cond", "holding", "y"], ["act", "Grab", "y"]], h))
    assert out == bt.SUCCESS and acts(h) == [] and evs == []


def test_ask_answer_flows_yes_goes_on_no_falls_back_and_a_stop_ends_at_once():
    tree = ["fallback", ["sequence", ["ask", "q_x"], ["act", "Grab", "y"]], ["act", "Drop", "y"]]
    h = Host(answers=[True])
    assert drive(bt.tick(tree, h))[1] == bt.SUCCESS and acts(h) == ["Grab"]
    h = Host(answers=[False])
    assert drive(bt.tick(tree, h))[1] == bt.SUCCESS and acts(h) == ["Drop"]
    h = Host(answers=[None])                                    # the run ended while asking: nothing more is done
    assert drive(bt.tick(tree, h))[1] == bt.STOPPED and acts(h) == []


@pytest.mark.parametrize("tree,ok", [(ON, True), (["retry", 0, ["act", "A", "x"]], False), (["retry", True, ["act", "A", "x"]], False),
                                     (["sequence"], False), (["jump", "x"], False), (["act"], False), (["act", 3], False), ("act", False), ([], False)])
def test_valid_tree_shapes(tree, ok):
    assert bt.valid(tree) is ok


def test_fill_puts_the_goal_in_and_a_missing_slot_gives_no_tree():
    assert bt.fill(["act", "MoveTo", "{to}"], {"to": BENCH}) == ["act", "MoveTo", BENCH]
    assert bt.fill(["sequence", ["act", "MoveTo", "{from}"]], {"from": ""}) is None and bt.fill(["act", "MoveTo", "{to}"], {}) is None


# ── tree_for: which goal gets a tree (pure) ───────────────────────────────────────────────────────────────────────────────────────
ACTS = {"MoveTo", "Find", "Pick", "Place"}
GOAL = {"type": "on", "object": "gizmo", "from": SHELF, "to": BENCH}


def ok(_k):
    return True


def test_tree_for_a_declared_kind_fills_the_goal():
    t = B.tree_for(GOAL, TREES, ACTS, True, ok)
    assert t is not None and ["act", "MoveTo", SHELF] in list(bt.leaves(t)) and ["act", "Place", BENCH] in list(bt.leaves(t))


@pytest.mark.parametrize("goal,trees,actions,can_ask,phrase", [
    (GOAL, TREES, ACTS - {"Place"}, True, ok),                                     # the robot lacks a skill the tree names: not used
    (GOAL, {"on": {"tree": ON}}, ACTS, True, ok),                                   # unlabelled data
    (GOAL, {}, ACTS, True, ok), (GOAL, None, ACTS, True, ok),                       # no data
    ({**GOAL, "type": "in"}, TREES, ACTS, True, ok),                                # no tree for the kind
    ({**GOAL, "from": ""}, TREES, ACTS, True, ok),                                  # a slot the goal does not fill (and no memory)
    ({**GOAL, "hand": True}, TREES, ACTS, True, ok),                                # skip_if
    (GOAL, TREES, ACTS, False, ok),                                                 # no Ask skill for the ask leaf
    (GOAL, TREES, ACTS, True, lambda k: k != "say_bt_goal_done"),                   # no phrase in the run's language
    (GOAL, {"on": {"tree": ["cond", "levitating", "{object}"], "assumed": True}}, ACTS, True, ok),   # an unknown condition
])
def test_tree_for_gives_no_tree_when_anything_is_missing_empty(goal, trees, actions, can_ask, phrase):
    assert B.tree_for(goal, trees, actions, can_ask, phrase) is None


def test_a_goal_without_from_takes_the_place_the_engine_remembers():
    t = B.tree_for({**GOAL, "from": ""}, TREES, ACTS, True, ok, source=CRATE)
    assert ["act", "MoveTo", CRATE] in list(bt.leaves(t)) and ["act", "MoveTo", SHELF] in list(bt.leaves(B.tree_for(GOAL, TREES, ACTS, True, ok, CRATE)))


def test_plan_trees_is_all_or_nothing():
    assert len(B.plan_trees([GOAL, {**GOAL, "object": "widget"}], TREES, ACTS, True, ok)) == 2
    assert B.plan_trees([GOAL, {**GOAL, "type": "open"}], TREES, ACTS, True, ok) is None and B.plan_trees([], TREES, ACTS, True, ok) is None


def test_a_second_made_up_robot_and_language_work_through_data_alone(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", {"dialogue": {"yes": ["vee"]}, "speech": {"q_zq_more": "Ree {object}?", "assumed": True},
                                               "zq_words": {"again": ["ree"], "assumed": True}})
    tree = ["fallback", ["retry", 1, ["act", "Grab", "{object}"]], ["sequence", ["ask", "q_zq_more", "zq_words.again"], ["act", "Grab", "{object}"]]]
    trees = {"on": {"tree": tree, "source": "made-up pack sheet"}}
    t = B.tree_for(GOAL, trees, {"Grab"}, True, lambda k: k == "q_zq_more")
    assert t is not None and B.tree_for(GOAL, trees, ACTS, True, ok) is None          # the first robot has no Grab
    assert B.pack_words("zq", ["zq_words.again", "dialogue.yes", "nope.none"]) == ["ree", "vee"]


@pytest.mark.parametrize("name,args,world,placed,want", [
    ("holding", ("gizmo",), NS(holding="gizmo"), {}, True), ("holding", ("gizmo",), NS(holding=None), {}, False),
    ("hand_free", (), NS(holding=None), {}, True), ("at", (BENCH,), NS(holding=None, arrived=BENCH), {}, True),
    ("placed", ("gizmo", BENCH), NS(holding=None), {"gizmo": BENCH}, True), ("placed", ("gizmo", BENCH), NS(holding="gizmo"), {"gizmo": BENCH}, False),
    ("placed", ("gizmo",), NS(holding=None), {}, False), ("levitating", (), NS(holding=None), {}, False)])
def test_belief_conditions_table(name, args, world, placed, want):
    assert B.holds(name, args, world, placed) is want


# ── through the real AssistLoop ───────────────────────────────────────────────────────────────────────────────────────────────
class Bot(FakeRobot):
    """The made-up flat; a grasp fails ``fails`` times."""

    def __init__(self, *a, fails=0, **k):
        super().__init__(*a, **k)
        self.fails = fails

    def _exec(self, skill, o, params):
        if skill == "pick" and self.fails > 0:
            self.fails -= 1
            return {"isdone": False, "msg": f"grasp failed: {o} slipped"}
        return super()._exec(skill, o, params)


def _run(make_assist, on=True, trees=TREES, answers=(), fails=0, replans=(), goal=GOAL):
    und = {"status": "clear", "reason": "", "question": "", "goals": [dict(goal)]}
    robot = Bot(ENV, {"gizmo": SHELF, "widget": CRATE}, answers=list(answers), start=CRATE, fails=fails)
    planner = ScriptedPlanner(PLAN, replans=[steps(*r) for r in replans])
    loop = make_assist(robot, FakeLLM(default=und), planner, detector_miss_rate=0, search_legs=1, **({"bt_goal": "1"} if on else {}))
    if trees is not None:
        loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"bt_goal": {"trees": trees}}}
    return loop, robot, planner, run_task(loop, "Take the gizmo from the shelf and put it on the bench.", max_events=3000)


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at", "plan_sec")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


def _flags(loop):
    return [e for e in loop.lab_log if e["flag"] == B.FLAG]


def test_registered_and_off_by_default_sage_plans_off(make_assist):
    assert "bt_goal" in ASSIST_OPTIONS and REGISTRY.get("bt_goal").key("enabled").default is False and B.SPEC.enabled == "enabled"
    loop, robot, planner, evs = _run(make_assist, on=False)
    assert planner.calls[0][0] == "generate_plan" and not _flags(loop) and done(evs)["status"] == "success"


@pytest.mark.parametrize("trees", [None, {}, {"on": {"tree": ON}}, {"in": {"tree": ON, "assumed": True}}])
def test_loop_final_on_with_no_usable_data_is_byte_identical_to_off_empty(make_assist, trees):
    def run(**o):
        loop, robot, _p, evs = _run(make_assist, trees=trees, **o)
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, loop.lab_log
    assert run(on=True) == run(on=False)


def test_loop_final_the_tree_reaches_the_same_outcome_as_the_sage_plan_with_no_planner_call(make_assist):
    _l, sage, sp, sage_evs = _run(make_assist, on=False)
    loop, robot, planner, evs = _run(make_assist)
    assert planner.calls == [] and sp.calls[0][0] == "generate_plan"
    assert (done(evs)["status"], robot.obj) == (done(sage_evs)["status"], sage.obj) == ("success", {"gizmo": BENCH, "widget": CRATE})
    moves = [t[:2] for t in robot.trace if t[0] in ("move", "find", "pick", "place")]
    assert moves[:5] == [("move", SHELF), ("find", "gizmo"), ("pick", "gizmo"), ("move", BENCH), ("place", BENCH)]
    assert [f["status"] for f in _flags(loop)] == ["claimed", bt.SUCCESS]
    assert any(e["event"] == "status" and e.get("say") == "I put the gizmo on the bench in the den." for e in evs)


def test_loop_final_pick_fails_twice_then_the_ask_menu_again_and_the_goal_is_reached(make_assist):
    loop, robot, planner, evs = _run(make_assist, answers=["Try again, please."], fails=2)
    assert robot.asked == ["I couldn't pick up the gizmo. Shall I try again here, look somewhere else, or stop?"]
    assert [t[0] for t in robot.trace].count("pick") == 3 and planner.calls == []
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == BENCH


def test_loop_regression_a_tree_that_fails_hands_the_goal_to_the_sage_replan(make_assist):
    """The fail-open class: a tree ends in failure (pick fails twice, the person does not say again). The engine's recovery stayed out
    while the tree ran (no help question of its own), then the plan end gives the goal to the planner's replan from the current belief."""
    loop, robot, planner, evs = _run(make_assist, answers=["no"], fails=2, replans=[(f"MoveTo {SHELF}", "Find gizmo", "Pick gizmo",
                                                                                      f"MoveTo {BENCH}", f"Place {BENCH}")])
    assert len(robot.asked) == 1 and "move it closer" not in robot.asked[0]
    assert [c[0] for c in planner.calls] == ["replan"] and [f["status"] for f in _flags(loop)] == ["claimed", bt.FAILURE]
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == BENCH


def test_on_step_failed_answers_only_while_a_tree_runs():
    opt = B.BtGoal({}, NS())
    run = NS()
    assert drive(opt.on_step_failed(run, {}, None, "", "Pick", "gizmo"))[1] is None
    opt._state(run)["active"] = True
    assert drive(opt.on_step_failed(run, {}, None, "", "Pick", "gizmo"))[1] == NEXT
