"""Option ``bt_goal`` (default OFF) and its runner ``core/planning/behavior_tree.py``: a goal run by a behaviour tree declared as data.
Made-up flat (a den and a loft), made-up objects (``gizmo``, ``widget``), a made-up language ``zq`` and a made-up second robot with other action
names. The runner on small trees with a fake host; ``tree_for`` on small tables; the FINAL outcome through the real AssistLoop."""
import json
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract.recovery import RecoveryPolicy
from robot_agent.contract.types import FailureCode
from robot_agent.core.options import REGISTRY
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


@pytest.mark.parametrize("name,args,opened,want", [("opened", ("crate@den",), {"crate@den"}, True), ("opened", ("crate@den",), set(), False),
                                                  ("closed", ("crate@den",), set(), True), ("closed", ("crate@den",), {"crate@den"}, False)])
def test_container_conditions_table(name, args, opened, want):
    assert B.holds(name, args, NS(holding=None, opened=opened), {}) is want


def test_the_runner_notes_every_node_with_its_place_in_the_tree():
    class Noting(Host):
        def note(self, path, node, status):
            self.calls.append(("note", path, node[0], status))
    h = Noting(results={"Grab": [False]})
    drive(bt.tick(["fallback", ["act", "Grab", "y"], ["sequence", ["cond", "holding", "y"]]], h))
    assert [c[1:] for c in h.calls if c[0] == "note"] == [("0.0", "act", bt.FAILURE), ("0.1.0", "cond", bt.FAILURE),
                                                         ("0.1", "sequence", bt.FAILURE), ("0", "fallback", bt.FAILURE)]


# ── the failure-code policy (pure): what follows when a tree has no way left ───────────────────────────────────────────────────
NO_RETRY = (FailureCode.ESTOP, FailureCode.CANCELLED, FailureCode.SOFTWARE, FailureCode.BUDGET_EXCEEDED)
PERSON = (FailureCode.ABSENT, FailureCode.UNREACHABLE, FailureCode.BLOCKED, FailureCode.GRASP_FAILED, FailureCode.DROPPED, FailureCode.UNKNOWN)
HEALTH = (FailureCode.HW_FAULT, FailureCode.TIMEOUT, FailureCode.PERCEPTION_UNAVAILABLE)


@pytest.mark.parametrize("policy", B.POLICIES)
@pytest.mark.parametrize("asked", [False, True])
def test_the_no_retry_codes_always_stop(policy, asked):
    for code in NO_RETRY:
        assert B.exhausted_action(RecoveryPolicy().rule_for(code), policy, asked) == "stop", code


@pytest.mark.parametrize("codes,asked,a,b", [(PERSON, False, "ask", "ask"), (PERSON, True, "stop", "sage"),
                                            (HEALTH, False, "stop", "sage"), (HEALTH, True, "stop", "sage")])
def test_a_person_is_asked_once_for_a_code_they_can_help_with_then_stop_or_sage(codes, asked, a, b):
    for code in codes:
        rule = RecoveryPolicy().rule_for(code)
        assert (B.exhausted_action(rule, "ask_or_stop", asked), B.exhausted_action(rule, "sage_once", asked)) == (a, b), code


def test_no_failed_step_and_made_up_codes_and_a_made_up_robot_table():
    assert [B.exhausted_action(None, p, False) for p in B.POLICIES] == ["stop", "sage"]          # a condition failed: no question
    assert B.exhausted_action(RecoveryPolicy().rule_for("levitation_fault"), "ask_or_stop", False) == "ask"   # a made-up code: UNKNOWN's rule
    robot2 = RecoveryPolicy({"absent": {"action": "wait_health"}, "blocked": {"action": "stop", "retry_allowed": False}})
    assert B.exhausted_action(robot2.rule_for("absent"), "ask_or_stop", False) == "stop"               # that robot's table says: no person
    assert [B.exhausted_action(robot2.rule_for("blocked"), p, False) for p in B.POLICIES] == ["stop", "stop"]


ZQ = {"stop": ["vrak"], "retry": ["ree", "vee"]}


@pytest.mark.parametrize("answer,place,want", [("", None, "none"), ("vrak ree", None, "stop"), ("ree", None, "retry"), ("glub", "crate@den", "place"),
                                               ("vrak", "crate@den", "stop"), ("glub", None, "none"), ("vee!", None, "retry")])
def test_the_menu_answer_is_read_with_the_pack_words_only(answer, place, want):
    assert B.read_menu(answer, ZQ, place, "zq") == want


def test_the_menu_words_come_from_a_made_up_language_pack_empty(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", {"ask_next": {"retry": ["ree"], "assumed": True}, "dialogue": {"stop": ["vrak"]}})
    assert B.pack_words("zq", ["ask_next.retry", "dialogue.stop", "nope.none"]) == ["ree", "vrak"] and B.pack_words("zq", None) == []


def test_no_replan_and_on_step_failed_answer_only_while_a_tree_runs_off():
    opt, run = B.BtGoal({}, NS()), NS()
    assert opt.no_replan(run, {"action": "Pick", "object": "gizmo"}) is None
    assert drive(opt.on_step_failed(run, {}, FailureCode.BLOCKED, "x", "MoveTo", BENCH))[1] is None and opt._state(run)["failed"] is None
    opt._state(run)["active"] = True
    assert drive(opt.on_step_failed(run, {"action": "MoveTo"}, FailureCode.BLOCKED, "x", "MoveTo", BENCH))[1] is None
    assert opt.no_replan(run, {"action": "MoveTo", "object": BENCH}) is True
    assert opt._state(run)["failed"] == {"code": FailureCode.BLOCKED, "step": {"action": "MoveTo", "object": BENCH}, "reason": "x", "rung": "planner"}


# ── through the real AssistLoop: the engine's own ladder inside the tree, the exhausted policy ─────────────────────────────────
LOOP_ON = ["fallback", ["cond", "placed", "{object}", "{to}"],
           ["sequence",
            ["fallback", ["cond", "holding", "{object}"],
             ["sequence", ["cond", "hand_free"], ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], ["act", "Pick", "{object}"],
              ["cond", "holding", "{object}"]]],
            ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"], ["cond", "hand_free"]]]
LOOP_TREES = {"on": {"tree": LOOP_ON, "skip_if": ["hand"], "assumed": True},
              "go": {"tree": ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]], "assumed": True}}
MENU = {"phrases": {"Find": "q_ask_next_find", "Pick": "q_ask_next_pick", "MoveTo": "q_ask_next_move"},
        "retry": ["ask_next.retry", "ask_next.elsewhere", "dialogue.yes"], "stop": ["ask_next.stop", "dialogue.stop"], "assumed": True}
FIND_MENU = "I couldn't find the gizmo. Shall I look again here, look somewhere else, or stop?"


def _run(make_assist, on=True, trees=LOOP_TREES, menu=MENU, policy=None, answers=(), faults=(), objects=None, replans=(), goal=GOAL,
         search_legs=1, plan=PLAN, task="Take the gizmo from the shelf and put it on the bench.", monkeypatch=None, health=None, **extra):
    und = {"status": "clear", "reason": "", "question": "", "goals": [dict(goal)]}
    robot = FakeRobot(ENV, objects or {"gizmo": SHELF, "widget": CRATE}, answers=list(answers), faults=list(faults), start=CRATE)
    planner = ScriptedPlanner(plan, replans=[steps(*r) for r in replans])
    if health is not None:
        from robot_agent.core.planning.robot import RobotView
        monkeypatch.setattr(RobotView, "health", lambda self, node=None: {"ok": health})
    pack = extra.pop("pack", {})
    opts = {**({"bt_goal": "1"} if on else {}), **({"bt_goal.exhausted": policy} if policy else {}), **extra}
    loop = make_assist(robot, FakeLLM(default=und), planner, detector_miss_rate=0, search_legs=search_legs, health_wait_s=0, **opts)
    data = {k: v for k, v in (("trees", trees), ("menu", menu)) if v is not None}
    loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {**pack, "bt_goal": data}}
    return loop, robot, planner, run_task(loop, task, max_events=3000)


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at", "plan_sec")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


def _flags(loop, node=False):
    return [e for e in loop.lab_log if e["flag"] == B.FLAG and ("node" in e) == node]


def _exh(loop):
    return [e.get("action") for e in _flags(loop) if e.get("status") == "exhausted"]


def _skills(robot):
    return [t[:2] for t in robot.trace if t[0] in ("move", "find", "pick", "place")]


def test_registered_on_by_default_and_pinned_off_here_sage_plans_off(make_assist):
    assert "bt_goal" in ASSIST_OPTIONS and REGISTRY.get("bt_goal").key("enabled").default is True and B.SPEC.enabled == "enabled"
    assert REGISTRY.get("bt_goal").key("exhausted").default == "ask_or_stop"
    loop, robot, planner, evs = _run(make_assist, on=False)
    assert planner.calls[0][0] == "generate_plan" and not _flags(loop) and done(evs)["status"] == "success"


@pytest.mark.parametrize("trees", [None, {}, {"on": {"tree": LOOP_ON}}, {"in": {"tree": LOOP_ON, "assumed": True}}])
def test_loop_final_on_with_no_usable_data_is_byte_identical_to_off_empty(make_assist, trees):
    def run(**o):
        loop, robot, _p, evs = _run(make_assist, trees=trees, faults=[{"skill": "move", "obj": BENCH, "kind": "fail", "msg": "path blocked"}], **o)
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, loop.lab_log
    assert run(on=True) == run(on=False) == run(on=True, policy="sage_once")


def test_loop_final_the_tree_reaches_the_same_outcome_as_the_sage_plan_with_no_planner_call(make_assist):
    _l, sage, sp, sage_evs = _run(make_assist, on=False)
    loop, robot, planner, evs = _run(make_assist)
    assert planner.calls == [] and sp.calls[0][0] == "generate_plan"
    assert (done(evs)["status"], robot.obj) == (done(sage_evs)["status"], sage.obj) == ("success", {"gizmo": BENCH, "widget": CRATE})
    assert _skills(robot)[:5] == [("move", SHELF), ("find", "gizmo"), ("pick", "gizmo"), ("move", BENCH), ("place", BENCH)]
    assert [f["status"] for f in _flags(loop)] == ["claimed", bt.SUCCESS]
    nodes = [f["node"] for f in _flags(loop, node=True)]
    assert f"0.1.1 act MoveTo {BENCH}" in nodes and nodes[-1] == "0 fallback"         # every node is logged, the root last


def test_loop_regression_a_blocked_move_recovers_by_the_engine_ladder_inside_the_tree_no_planner_call(make_assist):
    """The class: the prototype's tree had no move retry, so a blocked move failed the tree and SAGE replanned from scratch. Now the
    engine's own ladder (the declared BLOCKED rule: move again in place) runs inside the Act; no planner call, no question."""
    loop, robot, planner, evs = _run(make_assist, faults=[{"skill": "move", "obj": BENCH, "kind": "fail", "msg": "path blocked by a chair"}])
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == BENCH and planner.calls == [] and robot.asked == []
    assert _skills(robot).count(("move", BENCH)) == 2 and any(e["event"] == "recover" and e.get("kind") == "retry" for e in evs)
    assert not _exh(loop)


def test_loop_final_a_hardware_fault_recovers_by_the_declared_wait_schedule_inside_the_tree(make_assist, monkeypatch):
    hw = {"hw_wait": {"kinds": {"hw_fault": {"schedule_s": [1], "tries": 2, "action": "wait_health"}, "assumed": True}}}
    loop, robot, planner, evs = _run(make_assist, faults=[{"skill": "place", "kind": "fail", "msg": "gripper motor fault"}], hw_wait="1",
                                     pack=hw, monkeypatch=monkeypatch, health=True)
    assert done(evs)["status"] == "success" and planner.calls == [] and robot.asked == [] and robot.obj["gizmo"] == BENCH
    assert any(e["event"] == "recover" and e.get("kind") == "wait_health" for e in evs) and _skills(robot).count(("place", BENCH)) == 2


def test_loop_final_a_health_code_with_no_rung_left_reports_and_stops_without_a_question(make_assist):
    loop, robot, planner, evs = _run(make_assist, faults=[{"skill": "place", "kind": "fail", "msg": "gripper motor fault", "nth": "all"}])
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "hw_fault" and robot.asked == [] and planner.calls == []
    assert _exh(loop) == ["stop", "stop"]


def test_loop_final_absent_under_A_asks_the_menu_and_the_named_place_is_tried(make_assist):
    loop, robot, planner, evs = _run(make_assist, objects={"gizmo": CRATE}, search_legs=0, answers=["It is on the crate."])
    assert robot.asked == [FIND_MENU] and planner.calls == []
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == BENCH and _exh(loop) == ["place"]


def test_loop_final_absent_under_A_unanswered_reports_and_stops(make_assist):
    loop, robot, planner, evs = _run(make_assist, objects={"gizmo": CRATE}, search_legs=0, answers=[""])
    assert robot.asked[0] == FIND_MENU and all(FIND_MENU in q for q in robot.asked) and planner.calls == [] and done(evs)["status"] == "failed" and done(evs)["code"] == "absent"
    assert _exh(loop) == ["none", "stop"]


def test_loop_final_absent_under_B_unanswered_goes_to_one_sage_replan(make_assist):
    loop, robot, planner, evs = _run(make_assist, objects={"gizmo": CRATE}, search_legs=0, answers=[""], policy="sage_once",
                                     replans=[(f"MoveTo {CRATE}", "Find gizmo", "Pick gizmo", f"MoveTo {BENCH}", f"Place {BENCH}")])
    assert robot.asked[0] == FIND_MENU and all(FIND_MENU in q for q in robot.asked) and [c[0] for c in planner.calls] == ["replan"]
    assert "goals left: on gizmo from" in planner.calls[0][2] and "absent" in planner.calls[0][2]
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == BENCH and _exh(loop) == ["none", "sage"]


def test_loop_final_a_stop_word_ends_the_run_under_both_policies(make_assist):
    for policy in B.POLICIES:
        loop, robot, planner, evs = _run(make_assist, objects={"gizmo": CRATE}, search_legs=0, answers=["Stop."], policy=policy)
        assert done(evs)["status"] == "aborted" and planner.calls == [], policy


@pytest.mark.parametrize("policy", B.POLICIES)
def test_loop_final_an_estop_ends_at_once_no_question_no_replan(make_assist, policy):
    loop, robot, planner, evs = _run(make_assist, policy=policy, faults=[{"skill": "move", "obj": BENCH, "kind": "fail", "msg": "emergency stop pressed"}])
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "estop" and robot.asked == [] and planner.calls == [] and not _exh(loop)
    assert _skills(robot).count(("move", BENCH)) == 1


def test_loop_final_a_go_goal_runs_its_tree(make_assist):
    loop, robot, planner, evs = _run(make_assist, goal={"type": "go", "object": "", "from": "", "to": SHELF}, task="Go to the shelf.")
    assert done(evs)["status"] == "success" and planner.calls == [] and _skills(robot) == [("move", SHELF)]


def test_loop_final_a_failed_grasp_gets_the_engine_retries_and_its_help_question_inside_the_tree(make_assist):
    loop, robot, planner, evs = _run(make_assist, answers=["yes"], faults=[{"skill": "pick", "kind": "fail", "nth": [1, 2], "msg": "grasp failed: it slipped"}])
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == BENCH and planner.calls == [] and not _exh(loop)
    assert len(robot.asked) == 1 and "move it closer" in robot.asked[0] and _skills(robot).count(("pick", "gizmo")) == 3


# ── a goal change said while a tree runs (the class of a d5dev pilot episode: the tree finished the OLD goal) ─────────────────────
@pytest.fixture
def managed_run():
    from robot_agent.core import run_control
    from robot_agent.interact.run_manager import reset_manager
    reset_manager()
    run_control._CANCEL.clear()
    r = run_control.manager().start("Take the gizmo from the shelf and put it on the bench.", user_id="zq", role="member").run
    run_control.begin_run()
    yield r
    reset_manager()
    run_control._CANCEL.clear()


class SaysAfterPick(FakeRobot):
    """Says ``text`` into the run right after its first pick (a person speaking while the robot works)."""
    text = ""

    def execute(self, skill, params, node=None, log_fn=None):
        out = super().execute(skill, params, node, log_fn)
        if skill == "pick" and self.text:
            from robot_agent.core import run_control
            run_control.manager().inject(run_control.current_run().run_id, self.text, role="member")
            self.text = ""
        return out


def test_loop_regression_a_goal_change_said_during_a_tree_ends_the_trees_and_the_engine_applies_it(make_assist, managed_run):
    """The class: corrections were read only after the trees ended, so the tree finished the OLD goal (and met faults there). Now a change
    waiting before the next Act ends the trees; the engine applies it as between two plan steps (its repair: a replan for the new goal)."""
    robot = SaysAfterPick(ENV, {"gizmo": SHELF, "widget": BENCH}, start=CRATE)
    robot.text = "actually, put it on the crate instead"
    planner = ScriptedPlanner(PLAN, replans=[steps(f"MoveTo {CRATE}", f"Place {CRATE}")])
    loop = make_assist(robot, FakeLLM(default={"status": "clear", "reason": "", "question": "", "goals": [dict(GOAL)]}), planner,
                       detector_miss_rate=0, search_legs=1, bt_goal="1")
    loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"bt_goal": {"trees": LOOP_TREES, "menu": MENU}}}
    evs = run_task(loop, "Take the gizmo from the shelf and put it on the bench.", max_events=3000)
    assert [f["status"] for f in _flags(loop)][-1] == B.CHANGED and ("move", BENCH) not in _skills(robot)
    assert any(e["event"] == "goal_change" for e in evs) and [c[0] for c in planner.calls] == ["replan"]
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == CRATE


def test_loop_regression_a_menu_answer_naming_the_destination_is_no_sighting_of_the_object(make_assist):
    """The class: the answer named where the object should GO; it was remembered as where the object IS, the tree went there to look."""
    loop, robot, planner, evs = _run(make_assist, objects={"gizmo": CRATE}, search_legs=0, answers=["Put it on the bench."])
    assert robot.asked[0] == FIND_MENU and planner.calls == [] and done(evs)["status"] == "failed" and done(evs)["code"] == "absent"
    assert ("move", BENCH) not in _skills(robot) and _exh(loop) == ["none", "stop"]
