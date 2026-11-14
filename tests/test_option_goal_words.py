"""Option ``goal_words`` (default OFF): the goal filter compares whole words, trusts the object catalogue first, asks when a name is both an
object and a place, and keeps the receiver of a shared object. Made-up houses (a loft with a lamp-post and a lamp, a barn with a pail and a
pail-rack); nothing here is a real robot's place."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import goal_fix
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.goal_words import SPEC, GoalWords, Guard, catalogue, place_keys

ENV = {"lampshelf@loft": {}, "rack@barn": {"aliases": ["pail"]}, "tom@loft": {"person": True}, "zed@loft": {"person": True, "speaker": True},
       "stool@loft": {}}
PLACES = Places(ENV)


def make(known=("pail", "lamp"), answer="", goals=()):
    cfg = resolve(SPEC, {"goal_words": "1"}, {}, None)
    asks = []
    svc = NS(robot=NS(manifest=NS(limits={"objects": {n: {} for n in known}})), ask=lambda run, q: asks.append(q) or answer,
             cancelled=lambda run: None, lab=lambda *a, **k: svc.labs.append((a, k)), labs=[])
    run = NS(memory=NS(data={"objects": {}}), places=PLACES, goals=list(goals), filtered=[], asked=0, ev=lambda *a, **k: {"event": a[0], **k})
    return GoalWords(cfg, svc), run, svc, asks


def guarded(guard):
    p = Places(ENV)
    p.guard = guard
    return p


def goal(obj, to="stool", typ="on"):
    return {"type": typ, "object": obj, "from": "", "to": to}


def drain(gen):
    evs = []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as e:
        return evs, e.value


def test_a_name_inside_a_longer_place_name_is_not_that_place_substring():
    opt, run, _, _ = make(known=())
    g = opt.goal_guard(run, [goal("lamp")])
    assert g.place("lamp") == ""                    # "lamp" is inside "lampshelf" only as letters
    assert g.place("lampshelf") == "lampshelf@loft"
    assert goal_fix.drop_invented([goal("lamp")], "put the lamp on the stool", PLACES)[1], "the old matcher drops it (the class)"
    out, dropped = goal_fix.drop_invented([goal("lamp")], "put the lamp on the stool", guarded(g))
    assert out and not dropped


def test_a_whole_word_match_is_a_place_and_the_goal_is_dropped_as_before():
    opt, run, _, _ = make(known=())
    g = opt.goal_guard(run, [goal("lampshelf")])
    out, dropped = goal_fix.drop_invented([goal("lampshelf")], "put the lampshelf on the stool", guarded(g))
    assert not out and len(dropped) == 1
    assert place_keys("The Pail", PLACES) == ["rack@barn"]                # an alias, articles and case ignored


def test_the_catalogue_comes_first_and_the_goal_is_kept():
    opt, run, _, _ = make(known=("pail",))
    assert opt.goal_guard(run, []).place("pail") == ""
    out, dropped = goal_fix.drop_invented([goal("pail")], "carry the pail to the stool", guarded(opt.goal_guard(run, [])))
    assert out and not dropped
    run.memory.data["objects"] = {"the kettle": {}}
    assert "kettle" in catalogue(run, opt.svc.robot)


def test_a_name_that_is_an_object_and_a_place_is_asked_once():
    opt, run, svc, asks = make(goals=[goal("pail"), goal("pail", to="tom")])
    evs, out = drain(opt.on_goals(run))
    assert out is None and len(asks) == 1 and evs[0]["event"] == "clarify"        # once, although two goals name it
    assert len(run.goals) == 2                                                    # no answer: the object is kept
    assert svc.labs and svc.labs[0][1]["rule"] == "ask"


def test_an_answer_that_names_the_place_drops_the_goal_and_one_that_names_the_object_keeps_it():
    opt, run, _, _ = make(goals=[goal("pail")], answer="the rack, please")
    drain(opt.on_goals(run))
    assert run.goals == [] and len(run.filtered) == 1
    opt, run, _, _ = make(goals=[goal("pail")], answer="the pail itself")
    drain(opt.on_goals(run))
    assert len(run.goals) == 1


def test_a_shared_object_keeps_its_receiver_through_the_receiver_repairs():
    two = [goal("cup", to="tom@loft"), goal("cup", to="stool")]
    say = {"type": "say", "to": "tom@loft", "person": "tom@loft", "object": "", "text": "hi"}
    opt, run, _, _ = make(known=())
    g = opt.goal_guard(run, two)
    assert g.frozen(two[0]) and not g.frozen(goal("mug"))
    got, _ = goal_fix.shared_listener(two + [say], "tell tom hi and give the cup", guarded(g))
    assert [x["to"] for x in got[:2]] == ["tom@loft", "stool"]
    got0, _ = goal_fix.shared_listener(two + [say], "tell tom hi and give the cup", PLACES)
    assert got0[0]["to"] == "zed@loft", "without the option the receiver is changed (the class)"


def test_off_and_empty_a_registered_default_off_option_with_no_catalogue_and_no_places_does_nothing_empty():
    assert "goal_words" in ASSIST_OPTIONS and REGISTRY.get("goal_words").key("enabled").default is False
    opt, run, _, asks = make(known=(), goals=[goal("pail")])
    drain(opt.on_goals(run))
    assert not asks and len(run.goals) == 1                                       # no catalogue: nothing is asked
    g = Guard(set(), Places({}), [])
    assert g.place("pail") == "" and not g.frozen(goal("pail"))


def test_the_default_path_is_unchanged_off(make_assist, env):
    robot = FakeRobot(env, {"mug": "counter@kitchen"})
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "mug", "from": "counter", "to": "dining table"}]}
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner([]), detector_miss_rate=0)
    run_task(loop, "Put the mug on the dining table.", max_events=3000)
    assert not [e for e in loop.lab_log if e["flag"] == "goal_words"]
    assert loop.options.query("goal_guard", None, []) is None


def test_the_loop_gives_the_guard_when_on(make_assist, env):
    robot = FakeRobot(env, {})
    loop = make_assist(robot, FakeLLM(default={"status": "clear", "reason": "", "question": "", "goals": []}), ScriptedPlanner([]), goal_words="1")
    loop.robot.manifest.limits = {"objects": {"widget": {}}}
    gd = loop.options.query("goal_guard", NS(memory=NS(data={}), places=PLACES), [])
    assert isinstance(gd, Guard) and gd.place("widget") == ""


def _book_run(make_assist, env, on):
    env["bookshelf@study room"] = {}
    robot = FakeRobot(env, {"book": "counter@kitchen"})
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "book", "from": "counter", "to": "dining table"}]}
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner([]), detector_miss_rate=0, **({"goal_words": "1"} if on else {}))
    return [e["event"] for e in run_task(loop, "Put the book on the dining table.", max_events=3000)]


def test_the_loop_keeps_a_goal_whose_name_is_inside_a_place_name_on_and_drops_it_off(make_assist, env):
    off, on = _book_run(make_assist, env, False), _book_run(make_assist, env, True)
    assert "clarify" in off and "plan" not in off, off          # the goal was dropped, the robot asked what to do
    assert "plan" in on and "clarify" not in on, on
