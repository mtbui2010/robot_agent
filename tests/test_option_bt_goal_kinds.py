"""Option ``bt_goal``, iteration 3: trees for the goal kinds ``in``, ``say`` and ``deliver`` (a kind may declare a list of entries, the first
usable wins; slot ``{delivery}`` = the robot's delivery phrase). Made-up objects (``gizmo``), a made-up base-only robot (``cartbot``: drive,
speak, wait for its tray to empty) and a made-up language ``zq``. ``say`` / ``deliver`` trees give the SAME robot calls as the engine's
template (``goals.compile_steps``); an ``in`` tree opens a closed container first; a failure code inside each tree; off / empty = off."""
import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.contract import Manifest, Messages, Unbindable
from robot_agent.core.planning import behavior_tree as bt
from robot_agent.core.planning.base import WorldState
from robot_agent.core.planning.robot import RobotView
from robot_agent.options import bt_goal as B
from test_option_bt_goal import scrub

DR, DT, CT = "drawer@kitchen", "dining table@kitchen", "counter@kitchen"
GO_TO = ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]]
SAY_AT = {"tree": ["sequence", GO_TO, ["act", "Say", "{object}"]], "assumed": True}
SAY_HERE = {"tree": ["act", "Say", "{object}"], "skip_if": ["to"], "assumed": True}
DELIVER_SAY = {"tree": ["sequence", GO_TO, ["act", "Say", "{delivery}"], ["act", "WaitForUnload", ""], ["cond", "hand_free"]], "assumed": True}
DELIVER = {"tree": ["sequence", GO_TO, ["act", "WaitForUnload", ""], ["cond", "hand_free"]], "assumed": True}
FETCH = ["fallback", ["cond", "holding", "{object}"],
         ["sequence", ["cond", "hand_free"], ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], ["act", "Pick", "{object}"],
          ["cond", "holding", "{object}"]]]
OPENED = ["fallback", ["cond", "opened", "{to}"],
          ["sequence", ["cond", "hand_free"], ["act", "MoveTo", "{to}"], ["act", "Open", "{to}"], ["cond", "opened", "{to}"]]]


def in_tree(release):
    return ["fallback", ["cond", "placed", "{object}", "{to}"],
            ["sequence", OPENED, FETCH, ["act", "MoveTo", "{to}"], ["act", release, "{to}"], ["cond", "placed", "{object}", "{to}"],
             ["cond", "hand_free"]]]


IN = [{"tree": in_tree("PutIn"), "assumed": True}, {"tree": in_tree("Place"), "assumed": True}]
TREES = {"say": [SAY_AT, SAY_HERE], "deliver": [DELIVER_SAY, DELIVER], "in": IN, "go": {"tree": GO_TO, "assumed": True}}


def ok(_k):
    return True


def acts(tree):
    return [tuple(x[1:]) for x in bt.leaves(tree) if x[0] == "act"]


# ── which entry of a kind a goal gets (pure) ─────────────────────────────────────────────────────────────────────────────────────
def test_a_say_goal_with_a_place_goes_there_first_and_one_without_says_it_here():
    say = {"type": "say", "object": "Vree tak.", "from": "", "to": DT}
    assert acts(B.tree_for(say, TREES, {"MoveTo", "Say"}, False, ok)) == [("MoveTo", DT), ("Say", "Vree tak.")]
    assert acts(B.tree_for({**say, "to": ""}, TREES, {"MoveTo", "Say"}, False, ok)) == [("Say", "Vree tak.")]


@pytest.mark.parametrize("actions,delivery,want", [
    ({"MoveTo", "Say", "WaitForUnload"}, "Vree tak.", [("MoveTo", CT), ("Say", "Vree tak."), ("WaitForUnload", "")]),
    ({"MoveTo", "WaitForUnload"}, "Vree tak.", [("MoveTo", CT), ("WaitForUnload", "")]),          # no Say: as compile_steps(can_say=False)
    ({"MoveTo", "Say", "WaitForUnload"}, "", [("MoveTo", CT), ("WaitForUnload", "")])])          # no delivery phrase
def test_a_deliver_goal_says_the_robot_delivery_phrase_only_when_it_can(actions, delivery, want):
    goal = {"type": "deliver", "object": "", "from": "", "to": CT}
    assert acts(B.tree_for(goal, TREES, actions, False, ok, extra={"delivery": delivery})) == want


def test_an_in_goal_uses_put_in_and_a_robot_without_it_the_place_entry():
    goal = {"type": "in", "object": "gizmo", "from": CT, "to": DR}
    assert ("PutIn", DR) in acts(B.tree_for(goal, TREES, {"MoveTo", "Find", "Pick", "Place", "PutIn", "Open"}, False, ok))
    assert ("Place", DR) in acts(B.tree_for(goal, TREES, {"MoveTo", "Find", "Pick", "Place", "Open"}, False, ok))
    assert B.tree_for(goal, TREES, {"MoveTo", "Find", "Pick", "Place"}, False, ok) is None             # it cannot open: no tree


@pytest.mark.parametrize("entries", [[], [None], [{"tree": ["act", "Say", "{object}"]}], [{"tree": ["jump"], "assumed": True}]])
def test_a_list_with_no_usable_entry_gives_no_tree_empty(entries):
    assert B.tree_for({"type": "say", "object": "Vree.", "to": ""}, {"say": entries}, {"Say"}, False, ok) is None


def test_the_engine_template_and_the_tree_name_the_same_steps_table():
    """The tree is a data view of the engine's template (goals.compile_steps) for each case of a say / deliver goal."""
    from robot_agent.core.planning import goals as G
    for goal, at in (({"type": "say", "object": "Vree.", "to": DT}, CT), ({"type": "say", "object": "Vree.", "to": ""}, CT),
                     ({"type": "deliver", "object": "", "to": CT}, DT)):
        tree = B.tree_for({"from": "", **goal}, TREES, {"MoveTo", "Say", "WaitForUnload"}, False, ok, extra={"delivery": "Ree."})
        want = [(s["action"], s["object"]) for s in G.compile_steps([goal], at, "Ree.", can_say=True)]
        assert acts(tree) == want, goal


# ── a made-up base-only robot through the real AssistLoop ───────────────────────────────────────────────────────────────────────
CARTBOT = {"robot": "cartbot", "version": "1.0", "language": "en", "capabilities": {"base": True, "arms": 0, "hands": [], "speaker": True},
           "geometry": {"arrive_tol_m": 0.3},
           "skills": [{"name": n, "action": a, "params": {"type": "object", "properties": {"inputs": {"type": "string"}}}}
                      for n, a in (("drive", "MoveTo"), ("speak", "Say"), ("ask", "Ask"), ("tray_wait", "WaitForUnload"))]}


class CartBot:
    """Adapter and skills of a robot with no arm: drives between ENV places, speaks, waits until its tray is empty (``fails``: skill -> count)."""

    def __init__(self, env, fails=None, answers=()):
        self.manifest = Manifest.from_dict(CARTBOT)
        self.i18n = Messages(language="en", phrases={"delivery": {"en": "Your parcel, please take it."}})
        self.env, self.at, self.trace, self.asked, self.fails, self.answers = env, "entrance", [], [], dict(fails or {}), list(answers)

    def bind(self, step, world=None):
        spec = self.manifest.skill_for(step.get("action"))
        if spec is None:
            raise Unbindable(f"no skill for {step.get('action')}")
        return spec.name, ({"inputs": step["object"]} if step.get("object") else {})

    def observe(self, node=None):
        from robot_agent.contract import Observation
        return Observation(text=f"at {self.at}")

    def reconcile(self, world, node=None):
        return world

    def health(self, node=None):
        return {"ok": True}

    def execute(self, skill, params, node=None, log_fn=None):
        o = (params or {}).get("inputs", "")
        if skill == "ask":
            self.asked.append(o)
            a = self.answers.pop(0) if self.answers else ""
            return {"isdone": bool(a), "text": a}
        self.trace.append((skill, o))
        if self.fails.get(skill):
            self.fails[skill] -= 1
            return {"isdone": False, "msg": "path blocked by a box" if skill == "drive" else f"{skill} failed"}
        if skill == "drive":
            self.at = o
        return {"isdone": True, "msg": "ok"}


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": [{"type": t, "object": o, "from": "", "to": to} for t, o, to in goals]}


def _cart(make_assist, env, goals, on=True, trees=TREES, fails=None, answers=(), policy=None, menu=None):
    bot = CartBot(env, fails, answers)
    planner = ScriptedPlanner([])
    opts = {**({"bt_goal": "1"} if on else {}), **({"bt_goal.exhausted": policy} if policy else {})}
    loop = make_assist(bot, FakeLLM(default=understood(*goals)), planner, **opts)
    loop.robot, loop.sr = RobotView(bot), bot
    data = {k: v for k, v in (("trees", trees), ("menu", menu)) if v is not None}
    loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"bt_goal": data}}
    task = " Then ".join((f"Say '{o}'" if t == "say" else f"{'Go' if t == 'go' else 'Deliver the ' + o}") + (f" at the {to}." if to else ".")
                         for t, o, to in goals)
    evs = run_task(loop, task, max_events=3000)
    return loop, bot, planner, evs


def _claimed(loop):
    return [e["status"] for e in loop.lab_log if e["flag"] == B.FLAG and "node" not in e]


SAY_GO = [("go", "", "counter"), ("say", "Dinner is ready.", "dining table"), ("say", "Come and eat.", "")]


@pytest.mark.parametrize("goals", [SAY_GO, [("deliver", "parcel", "counter")], [("say", "Come and eat.", ""), ("deliver", "", "dining table")]])
def test_loop_say_and_deliver_trees_make_the_same_robot_calls_as_the_engine_template(make_assist, env, agent_state, goals):
    _l, off, _p, off_evs = _cart(make_assist, env, goals, on=False)
    agent_state.world = WorldState()                       # the second run starts from the same belief as the first
    loop, bot, planner, evs = _cart(make_assist, env, goals)
    assert bot.trace == off.trace and bot.trace and planner.calls == []
    assert done(evs)["status"] == done(off_evs)["status"] == "success" and _claimed(loop)[0] == "claimed"


def test_loop_a_deliver_tree_says_the_robot_own_delivery_phrase(make_assist, env):
    _loop, bot, _p, _evs = _cart(make_assist, env, [("deliver", "parcel", "counter")])
    assert bot.trace == [("drive", CT), ("speak", "Your parcel, please take it."), ("tray_wait", "")]


@pytest.mark.parametrize("trees", [None, {}, {"say": [], "go": TREES["go"]}, {"say": [{"tree": SAY_AT["tree"]}], "go": TREES["go"]}, {"in": IN}])
def test_loop_say_with_no_usable_data_is_byte_identical_to_off_empty(make_assist, env, agent_state, trees):
    def run(**o):
        agent_state.world = WorldState()
        loop, bot, _p, evs = _cart(make_assist, env, SAY_GO, trees=trees, **o)
        return json.dumps(scrub(evs), sort_keys=True, default=str), bot.trace, loop.lab_log
    assert run(on=True) == run(on=False)


MENU = {"phrases": {"MoveTo": "q_ask_next_move"}, "retry": ["ask_next.retry", "dialogue.yes"], "stop": ["ask_next.stop", "dialogue.stop"],
        "assumed": True}


def test_loop_regression_a_blocked_move_inside_a_say_tree_retries_by_the_ladder_no_planner_call(make_assist, env):
    loop, bot, planner, evs = _cart(make_assist, env, [("say", "Dinner is ready.", "dining table")], fails={"drive": 1})
    assert done(evs)["status"] == "success" and planner.calls == [] and bot.asked == []
    assert bot.trace == [("drive", DT), ("drive", DT), ("speak", "Dinner is ready.")]


@pytest.mark.parametrize("policy", B.POLICIES)
def test_loop_a_move_that_stays_blocked_ends_by_the_engine_ladder_and_the_sentence_is_never_said(make_assist, env, policy):
    """The MoveTo ladder's own rungs (retry, its help question, its stop) run inside the say tree; no planner call under either policy."""
    loop, bot, planner, evs = _cart(make_assist, env, [("say", "Dinner is ready.", "dining table")], fails={"drive": 99}, menu=MENU,
                                    answers=[""], policy=policy)
    assert done(evs)["status"] == "failed" and planner.calls == [] and bot.asked and all("dining table" in q and "Shall I" not in q for q in bot.asked)
    assert ("speak", "Dinner is ready.") not in bot.trace and _claimed(loop)[-1] == bt.STOPPED


def test_loop_a_deliver_whose_tray_never_empties_stops_without_a_planner_call(make_assist, env):
    loop, bot, planner, evs = _cart(make_assist, env, [("deliver", "parcel", "counter")], fails={"tray_wait": 99})
    assert done(evs)["status"] == "failed" and planner.calls == [] and ("drive", CT) in bot.trace


# ── an in goal with a closed container, through the real AssistLoop and the fake manipulator ────────────────────────────────────
def _in(make_assist, env, on=True, faults=(), opened=False):
    robot = FakeRobot(env, {"gizmo": DT}, faults=list(faults), start=CT, opened={DR} if opened else ())
    goal = {"type": "in", "object": "gizmo", "from": DT, "to": DR}
    planner = ScriptedPlanner(steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DT}", "Find gizmo", "Pick gizmo", f"MoveTo {DR}", f"PutIn {DR}"))
    loop = make_assist(robot, FakeLLM(default={"status": "clear", "reason": "", "question": "", "goals": [goal]}), planner,
                       world={"opened": [DR]} if opened else None, detector_miss_rate=0, search_legs=1, **({"bt_goal": "1"} if on else {}))
    loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"bt_goal": {"trees": TREES}}}
    return loop, robot, planner, run_task(loop, "Take the gizmo from the dining table and put it in the kitchen drawer.", max_events=3000)


IN_CALLS = [("move", DR), ("open_drawer", DR), ("move", DT), ("find", "gizmo"), ("pick", "gizmo"), ("move", DR), ("place", DR)]


def _core(robot):
    return [t[:2] for t in robot.trace if t[0] in ("move", "find", "pick", "place", "open_drawer")]


@pytest.mark.parametrize("on", [False, True])
def test_loop_an_in_tree_opens_the_closed_container_first_and_makes_the_sage_plan_calls(make_assist, env, on):
    """Each arm on a fresh belief (a second run in one test would see the first run's open drawer): the tree's calls = the SAGE plan's."""
    loop, robot, planner, evs = _in(make_assist, env, on=on)
    assert done(evs)["status"] == "success" and robot.obj["gizmo"] == DR + "/in" and _core(robot)[:7] == IN_CALLS
    assert [c[0] for c in planner.calls] == ([] if on else ["generate_plan"])


def test_loop_an_in_tree_with_the_container_open_already_does_not_open_it(make_assist, env):
    loop, robot, planner, evs = _in(make_assist, env, opened=True)
    assert done(evs)["status"] == "success" and planner.calls == [] and robot.obj["gizmo"] == DR + "/in"
    assert "open_drawer" not in [t[0] for t in robot.trace]


def test_loop_a_grasp_failure_inside_the_in_tree_gets_the_engine_retry(make_assist, env):
    loop, robot, planner, evs = _in(make_assist, env, faults=[{"skill": "pick", "kind": "fail", "nth": 1, "msg": "grasp failed: it slipped"}])
    assert done(evs)["status"] == "success" and planner.calls == [] and robot.obj["gizmo"] == DR + "/in"
    assert [t[0] for t in robot.trace].count("pick") == 2


def test_a_made_up_language_gets_the_second_robot_own_delivery_phrase_zq(env):
    bot = CartBot(env)
    bot.i18n = Messages(language="en", phrases={"delivery": {"en": "Your parcel, please take it.", "zq": "Vree tak."}})
    view = RobotView(bot)
    tree = B.tree_for({"type": "deliver", "object": "", "from": "", "to": CT}, TREES, view.plan_actions, False, ok,
                      extra={B.DELIVERY: view.text(B.DELIVERY, "zq")})
    assert acts(tree) == [("MoveTo", CT), ("Say", "Vree tak."), ("WaitForUnload", "")]


# ── a menu answer that names the goal's destination (the class of a d5dev pilot episode: "In the dresser, please." to "shall I look elsewhere?")
@pytest.mark.parametrize("place,goal,want", [("crate@den", {"to": "bench@den"}, "crate@den"), ("bench@den", {"to": "bench@den"}, None),
                                             (None, {"to": "bench@den"}, None), ("crate@den", {}, "crate@den")])
def test_a_place_answer_is_a_source_unless_it_is_the_goal_destination_table(place, goal, want):
    assert B.source_place(place, goal) == want

