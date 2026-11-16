"""Option ``single_goals`` (default OFF): one action with no destination (find, pick, put down here) is its own goal kind. The goal tables of
``goals.py`` on small made-up cases, the pure helpers of the option (rule lines, kinds, prompt insertion) with a made-up language ``zq``, the
reader's schema with the key on / off, and loop-level tests of the FINAL outcome through the real AssistLoop on the test house with made-up
objects (``zorblet``, ``quib``). No real robot, task or benchmark id."""
import json
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, load_env, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.planning import goals as G
from robot_agent.core.planning import langdata, speech
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, UNDERSTAND_PROMPT
from robot_agent.options.single_goals import SPEC, SingleGoals, found, kinds_for, rule_lines, with_rules

ZQ = {"single_goals": {"prompt": {"find": ["  \"find\" = sercxu {objekto}."], "pick": ["  \"pick\" = prenu.", "  ne portu."]}, "assumed": True},
      "speech": {"say_found_at": "Mi trovis {object} ĉe {place}.", "assumed": True}}
ACTS = ("MoveTo", "Find", "Pick", "Place")
DESK, SIDE, COUNTER = "desk@study room", "side table@living room", "counter@kitchen"


def goal(t, o="", frm="", to=""):
    return {"type": t, "object": o, "from": frm, "to": to}


@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    return "zq"


# ── goals.py: the kinds, their validation and their check by the steps ─────────────────────────────────────────────────────
@pytest.mark.parametrize("g,acts,missing", [
    (goal("find", "zorblet"), ACTS, []), (goal("pick", "zorblet"), ACTS, []), (goal("put_here"), ACTS, []),
    (goal("find", "zorblet"), ("MoveTo", "Pick"), ["Find"]), (goal("put_here"), ("Find", "Pick"), ["Place"]),
    (goal("fetchy", "zorblet"), ACTS, ["fetchy"]),                       # an unknown kind still needs an action nobody has
])
def test_goal_validation_table_single_kinds_need_their_action(g, acts, missing):
    assert G.missing_actions(g, acts) == missing


def test_single_kinds_stay_out_of_the_default_kinds_and_vocabulary():
    assert not set(G.SINGLE_KINDS) & set(G.kinds_for(ACTS + ("Say",))) and "Find" not in G.domain_actions()
    assert G.has_destination("on") and not G.has_destination("find") and G.by_steps("go") and G.by_steps("pick") and not G.by_steps("on")


@pytest.mark.parametrize("g,plan,at,want", [
    (goal("find", "zorblet"), (f"MoveTo {DESK}", "Find zorblet"), "entrance", True),
    (goal("find", "zorblet"), (f"MoveTo {DESK}", "Find quib"), "entrance", False),
    (goal("pick", "zorblet"), ("Find zorblet", "Pick zorblet"), "entrance", True),
    (goal("pick", "zorblet"), ("Pick zorblet", f"MoveTo {SIDE}", f"Place {SIDE}"), "entrance", False),     # released after
    (goal("pick", "zorblet"), ("Pick zorblet", f"Place {SIDE}", "Pick zorblet"), "entrance", True),        # taken again
    (goal("put_here"), (f"Place {SIDE}",), SIDE, True),
    (goal("put_here"), (f"MoveTo {DESK}", f"Place {DESK}"), SIDE, False),                                 # left the place first
    (goal("put_here"), (f"Place {SIDE}",), None, False),
])
def test_single_reached_table(g, plan, at, want):
    assert G.reached(g, steps(*plan), at) is want


def test_compile_put_here_is_a_place_where_the_robot_is_and_other_kinds_keep_their_steps():
    assert G.is_template([goal("put_here")]) and not G.is_template([goal("find", "zorblet")])
    assert G.compile_steps([goal("put_here")], SIDE) == [{"action": "Place", "object": SIDE}]
    assert G.compile_steps([goal("put_here")], None) == []
    assert G.subgoal_text(goal("find", "zorblet", DESK)) == f"find the zorblet (it is at {DESK})"
    assert G.summary(goal("pick", "zorblet")) == "pick up the zorblet"


# ── the option's pure helpers ──────────────────────────────────────────────────────────────────────────────────────────
def test_rule_lines_per_kind_need_a_labelled_block(zq, monkeypatch):
    assert rule_lines(zq, ["pick", "find", "put_here"]) == {"pick": "  \"pick\" = prenu.\n  ne portu.", "find": "  \"find\" = sercxu {objekto}."}
    assert rule_lines(zq, ["find"]) == {"find": "  \"find\" = sercxu {objekto}."} and rule_lines(zq, []) == {}
    for blk in ({**ZQ["single_goals"], "assumed": False}, {"assumed": True}, {"prompt": {"find": []}, "assumed": True}, {"prompt": ["x"], "assumed": True}):
        monkeypatch.setitem(langdata._CACHE, "zq", {"single_goals": blk})
        assert rule_lines("zq", ["find"]) == {}, blk
    assert rule_lines("xx", ["find"]) == {}                                         # a language with no pack


def test_kinds_for_a_robot_only_the_ones_it_has_actions_for():
    assert kinds_for(ACTS) == ["find", "pick", "put_here"] and kinds_for(("MoveTo", "Find")) == ["find"] and kinds_for(()) == []
    assert kinds_for(ACTS, ["pick", "fetchy"]) == ["pick"]                         # only listed, known kinds


def test_with_rules_puts_the_lines_after_the_goal_kind_lines_escaped():
    out = with_rules(UNDERSTAND_PROMPT, "  X {y}")
    assert "{goal_kinds}\n  X {{y}}\n  Object names:" in out and with_rules("no mark", "  X") is None and with_rules(UNDERSTAND_PROMPT, "") is None


def test_found_lists_find_goals_with_a_known_place():
    gs = [goal("find", "zorblet"), goal("find", "quib"), goal("on", "zorblet", "", SIDE), goal("find", "zorblet")]
    assert found(gs, {"zorblet": DESK}.get) == [("zorblet", DESK)] and found([], {}.get) == []


# ── the hooks on a stub ────────────────────────────────────────────────────────────────────────────────────────────────────
def make_opt(acts=ACTS, kinds="find"):
    labs = []
    svc = NS(robot=NS(plan_actions=acts, i18n=None), lab=lambda flag, **i: labs.append((flag, i)))
    return SingleGoals({"kinds": kinds}, svc), labs


def schema():
    return {"properties": {"goals": {"items": {"properties": {"type": {"enum": ["on", "in", "go"]}}}}}}


def test_schema_gets_only_the_listed_kinds_that_have_pack_lines(monkeypatch, zq):
    monkeypatch.setattr(langdata, "prompt_lang", lambda: "zq")
    opt, labs = make_opt()                                                          # the default: find only
    out = opt.on_understand_schema(schema())
    assert out["properties"]["goals"]["items"]["properties"]["type"]["enum"] == ["on", "in", "go", "find"]
    assert labs == [("single_goals", {"part": "schema", "kinds": ["find"]})]
    assert opt.on_understand_prompt(UNDERSTAND_PROMPT).count("sercxu") == 1 and "prenu" not in opt.on_understand_prompt(UNDERSTAND_PROMPT)
    opt, labs = make_opt(kinds="find,pick,put_here")                                # put_here has no zq line: not offered
    out = opt.on_understand_schema(schema())
    assert out["properties"]["goals"]["items"]["properties"]["type"]["enum"] == ["on", "in", "go", "find", "pick"]


def test_single_goals_without_pack_lines_changes_neither_schema_nor_prompt_empty(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", {"speech": ZQ["speech"]})
    monkeypatch.setattr(langdata, "prompt_lang", lambda: "zq")
    opt, labs = make_opt()
    assert opt.on_understand_schema(schema()) is None and opt.on_understand_prompt(UNDERSTAND_PROMPT) is None and labs == []
    opt, labs = make_opt(acts=())                                                  # a robot with none of the actions
    assert opt.on_understand_schema(schema()) is None


# ── the reader's schema and prompt through the loop ────────────────────────────────────────────────────────────────────────
UND = {"status": "clear", "reason": "", "question": ""}


def _understand_call(llm):
    return next((p, f) for p, f in llm.calls if p.startswith("You turn a household robot task into goals."))


def test_registered_and_off_by_default_reader_schema_and_prompt_unchanged_off(make_assist):
    assert "single_goals" in ASSIST_OPTIONS and REGISTRY.get("single_goals").key("enabled").default is False and SPEC.enabled == "enabled"
    robot = FakeRobot(load_env(), {"zorblet": DESK})
    llm = FakeLLM(default={**UND, "goals": [goal("go", "", "", DESK)]})
    loop = make_assist(robot, llm, ScriptedPlanner([]))
    run_task(loop, "Go to the desk.")
    p, f = _understand_call(llm)
    assert not set(G.SINGLE_KINDS) & set(f["properties"]["goals"]["items"]["properties"]["type"]["enum"]) and "EXTRA kinds" not in p
    assert not [e for e in loop.lab_log if e["flag"] == "single_goals"]


def test_reader_schema_with_the_key_on_has_the_kinds_and_the_pack_lines(make_assist):
    robot = FakeRobot(load_env(), {"zorblet": DESK})
    llm = FakeLLM(default={**UND, "goals": [goal("go", "", "", DESK)]})
    loop = make_assist(robot, llm, ScriptedPlanner([]), single_goals="1")
    run_task(loop, "Go to the desk.")
    p, f = _understand_call(llm)
    enum = f["properties"]["goals"]["items"]["properties"]["type"]["enum"]
    line = rule_lines("en", ["find"])["find"]
    assert enum[-1] == "find" and "pick" not in enum and "put_here" not in enum           # the default kinds: find only
    assert line in p and p.index(line) < p.index("  Object names:") and '"pick" =' not in p
    llm = FakeLLM(default={**UND, "goals": [goal("go", "", "", DESK)]})
    run_task(make_assist(robot, llm, ScriptedPlanner([]), single_goals="1", **{"single_goals.kinds": "find,pick,put_here"}), "Go to the desk.")
    p, f = _understand_call(llm)
    assert f["properties"]["goals"]["items"]["properties"]["type"]["enum"][-3:] == ["find", "pick", "put_here"] and '"put_here" =' in p


# ── the three kinds through the real AssistLoop: the FINAL outcome ─────────────────────────────────────────────────────────
def _run(make_assist, robot, goals, plan, task, replans=(), world=None, lang="en", **kw):
    planner = ScriptedPlanner(steps(*plan), replans=[steps(*r) for r in replans])
    loop = make_assist(robot, FakeLLM(default={**UND, "goals": goals}), planner, world=world, detector_miss_rate=0, single_goals="1", **kw)
    return loop, planner, run_task(loop, task, lang=lang, max_events=3000)


def test_loop_final_find_is_done_when_seen_and_says_where_nothing_is_picked(make_assist):
    robot = FakeRobot(load_env(), {"zorblet": DESK})
    loop, planner, evs = _run(make_assist, robot, [goal("find", "zorblet")],
                              (f"MoveTo {DESK}", "Find zorblet", "Pick zorblet", f"MoveTo {SIDE}", f"Place {SIDE}"), "Find the zorblet.")
    d = done(evs)
    assert d["status"] == "success" and d["say"] == "I found the zorblet at the desk in the study room."
    assert d["found"] == [{"object": "zorblet", "place": DESK}]
    assert robot.obj == {"zorblet": DESK} and robot.holding is None and "pick" not in [t[0] for t in robot.trace]
    assert {"flag": "single_goals", "part": "found", "object": "zorblet", "place": DESK} in loop.lab_log


def test_found_place_is_said_with_the_pack_phrase_of_the_persons_language(zq):
    labs, places = [], NS(env=load_env())
    svc = NS(robot=NS(plan_actions=ACTS, i18n=None), lab=lambda flag, **i: labs.append(i), remembered=lambda run, o: {"zorblet": DESK}.get(o),
             said_of=lambda run, o: o, say_name=lambda pl, k: k, finish=lambda run, status, **kw: {"status": status, **kw})
    run = NS(goals=[goal("find", "zorblet"), goal("find", "quib")], lang="zq", places=places)
    out = SingleGoals({}, svc).on_goals_met(run)
    assert out["status"] == "success" and out["found"] == [{"object": "zorblet", "place": DESK}] and labs[0]["place"] == DESK
    assert speech.render(out["say"], "zq", places=places).startswith("Mi trovis zorblet ĉe ")
    run.lang = "xx"                                                                 # no phrase in the person's language: the usual done
    assert SingleGoals({}, svc).on_goals_met(run) is None
    run.goals = [goal("find", "quib")]                                              # no remembered place: nothing said, nothing recorded
    labs.clear()
    assert SingleGoals({}, svc).on_goals_met(run) is None and labs == []


def test_loop_final_pick_is_done_while_holding_it(make_assist):
    robot = FakeRobot(load_env(), {"zorblet": COUNTER})
    loop, planner, evs = _run(make_assist, robot, [goal("pick", "zorblet", COUNTER)],
                              (f"MoveTo {COUNTER}", "Find zorblet", "Pick zorblet", f"MoveTo {SIDE}", f"Place {SIDE}"), "Pick up the zorblet.")
    assert done(evs)["status"] == "success" and robot.holding == "zorblet" and robot.at == COUNTER
    assert [t[0] for t in robot.trace if t[0] != "ask"][-1] == "pick"


def test_loop_final_put_here_places_what_is_held_where_the_robot_is_no_planner_call(make_assist):
    robot = FakeRobot(load_env(), {"quib": "robot"}, start=SIDE)
    robot.holding = "quib"
    loop, planner, evs = _run(make_assist, robot, [goal("put_here")], (), "Put it down here.",
                              world={"arrived": SIDE, "holding": "quib"})
    assert done(evs)["status"] == "success" and robot.obj == {"quib": SIDE} and robot.holding is None
    assert not planner.calls and [t[0] for t in robot.trace if t[0] != "ask"] == ["place"]


def test_loop_final_regression_find_request_is_not_a_delivery_to_the_person(make_assist):
    """The class: "Find the X" was read as a fetch to the person (picked up and brought). With the key on, the reader's find goal ends when the
    object is seen; the plan's extra fetch steps are not run and the object stays where it was."""
    robot = FakeRobot(load_env(), {"quib": COUNTER})
    loop, planner, evs = _run(make_assist, robot, [goal("find", "quib")],
                              (f"MoveTo {COUNTER}", "Find quib", "Pick quib", "MoveTo entrance", "Place entrance"), "Where is the quib?")
    assert done(evs)["status"] == "success" and robot.obj == {"quib": COUNTER} and robot.at == COUNTER


def test_loop_final_find_that_fails_takes_the_usual_recovery(make_assist):
    robot = FakeRobot(load_env(), {"zorblet": COUNTER})
    loop, planner, evs = _run(make_assist, robot, [goal("find", "zorblet")], (f"MoveTo {DESK}", "Find zorblet"), "Find the zorblet.",
                              replans=[(f"MoveTo {COUNTER}", "Find zorblet")], search_legs="0")
    assert [c for c in planner.calls if c[0] == "replan"] and done(evs)["status"] == "success"
    assert done(evs)["found"] == [{"object": "zorblet", "place": COUNTER}] and robot.obj == {"zorblet": COUNTER}
