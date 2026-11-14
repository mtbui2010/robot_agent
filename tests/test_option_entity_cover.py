"""Option ``entity_cover`` (default OFF): every object the request names is a goal or a stated reason for dropping it; a dropped one is asked about.
Made-up world: objects a kettle, a lamp, a red mug, a blue mug, a ladle; places a stool and a rack. Nothing here is a real robot's object or place."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.entity_cover import SPEC, EntityCover, held, reason, said

PLACES = Places({"stool@loft": {}, "rack@loft": {"aliases": ["hanger"]}})


def make(text, objects=("kettle", "lamp", "red mug", "blue mug", "ladle"), filtered=(), reasoned=True, english=True):
    cfg = resolve(SPEC, {"entity_cover": "1", "entity_cover.ask_reasoned": reasoned}, {}, None)
    svc = NS(robot=NS(manifest=NS(limits={"objects": {n: {} for n in objects}})), lab=lambda *a, **k: svc.labs.append((a, k)), labs=[])
    if english:
        svc.request_english = lambda run: run.task
    run = NS(task=text, memory=NS(data={}), places=PLACES, filtered=list(filtered))
    return EntityCover(cfg, svc), run, svc


def goal(obj, to="stool", typ="on"):
    return {"type": typ, "object": obj, "from": "", "to": to}


def test_all_the_objects_of_the_request_covered_asks_nothing():
    opt, run, svc = make("Put the kettle and the lamp on the stool.")
    assert opt.on_issues([], run, [goal("kettle"), goal("lamp")]) == []
    assert not svc.labs


def test_an_object_the_goals_dropped_is_an_open_issue_and_the_covered_one_is_not():
    opt, run, svc = make("Put the kettle and the lamp on the stool.")
    got = opt.on_issues([], run, [goal("kettle")])
    assert got == [{"kind": "object", "what": "lamp"}]
    assert [k for a, k in svc.labs if k.get("what") == "lamp"]
    other, run2, _ = make("Bring the ladle and the red mug to the rack, then the kettle.")
    assert [i["what"] for i in other.on_issues([], run2, [goal("red mug", "rack")])] == ["kettle", "ladle"]


def test_a_drop_with_a_stated_reason_is_asked_once_with_the_reason_and_the_key_can_leave_it():
    note = "the lamp is named only in a negation: not a goal"
    opt, run, _ = make("Put the kettle on the stool, the lamp too.", filtered=[note])
    assert opt.on_issues([], run, [goal("kettle")]) == [{"kind": "object", "what": "lamp", "reason": note}]
    assert reason("lamp", [note]) == note and reason("ladle", [note]) == ""
    quiet, run2, _ = make("Put the kettle on the stool, the lamp too.", filtered=[note], reasoned=False)
    assert quiet.on_issues([], run2, [goal("kettle")]) == []
    plain, run3, _ = make("Put the kettle on the stool, the lamp too.", filtered=["a note about the ladle"], reasoned=False)
    assert [i["what"] for i in plain.on_issues([], run3, [goal("kettle")])] == ["lamp"]     # an unrelated note is no reason


def test_a_noun_the_catalogue_does_not_know_is_ignored_and_whole_words_only():
    opt, run, _ = make("Put the kettle and the trowel on the stool.")
    assert opt.on_issues([], run, [goal("kettle")]) == []                    # "trowel" is not in the catalogue
    opt, run, _ = make("Put the kettle on the stool and the ladles.", objects=("kettle", "ladle", "lad"))
    assert [i["what"] for i in opt.on_issues([], run, [goal("kettle")])] == ["ladle"]    # a plural folds; "lad" is not a word of "ladles"
    assert said("red mug", "the red mugs") and not said("red mug", "a red and a mug")
    assert held("mug", [goal("red mug")]) and held("red mug", [goal("mug")]) and not held("lamp", [goal("lampshade")])


def test_a_name_that_is_a_place_or_already_an_issue_is_not_added_again():
    opt, run, _ = make("Put the kettle on the stool.", objects=("kettle", "stool"))
    assert opt.on_issues([], run, [goal("kettle")]) == []                    # a place the robot knows
    opt, run, _ = make("Put the kettle and the lamp on the stool.")
    have = [{"kind": "object", "what": "lamp"}]
    assert opt.on_issues(have, run, [goal("kettle")]) == have


def test_a_message_that_names_the_object_covers_it():
    opt, run, _ = make("Tell the neighbour the kettle is hot.")
    assert opt.on_issues([], run, [{"type": "say", "object": "the kettle is hot", "to": "neighbour"}]) == []


def test_the_request_in_another_language_is_read_through_the_service_empty():
    opt, run, svc = make("some foreign words", english=False)
    assert opt.on_issues([], run, [goal("kettle")]) == []                    # no service: the task itself, no catalogue name in it
    svc.request_english = lambda r: ""                                        # no trusted reading: fail open
    assert opt.on_issues([], run, [goal("kettle")]) == []
    svc.request_english = lambda r: "the kettle and the lamp"
    assert [i["what"] for i in opt.on_issues([], run, [goal("kettle")])] == ["lamp"]


def test_empty_catalogue_no_goals_or_no_words_does_nothing_empty():
    opt, run, _ = make("Put the kettle and the lamp on the stool.", objects=())
    assert opt.on_issues([], run, [goal("kettle")]) == []
    opt, run, _ = make("Put the kettle and the lamp on the stool.")
    assert opt.on_issues([], run, []) == []
    run.task = ""
    assert opt.on_issues([], run, [goal("kettle")]) == []
    run.memory = None
    run.task = "the lamp"
    opt.svc.robot = None
    assert opt.on_issues([], run, [goal("kettle")]) == []


def _loop(make_assist, env, on):
    robot = FakeRobot(env, {"kettle": "counter@kitchen", "lamp": "counter@kitchen"}, start="counter@kitchen", answers=["both of them"])
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "kettle", "from": "counter", "to": "dining table"}]}
    plan = steps("MoveTo counter@kitchen", "Find kettle", "Pick kettle", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, **({"entity_cover": "1"} if on else {}))
    loop.robot.manifest.limits = {"objects": {"kettle": {}, "lamp": {}}}
    loop.fake = robot
    run_task(loop, "Put kettle and lamp on the dining table.", max_events=3000)
    return loop, robot


def test_registered_and_off_by_default_the_loop_is_unchanged_off(make_assist, env):
    assert "entity_cover" in ASSIST_OPTIONS and REGISTRY.get("entity_cover").key("enabled").default is False
    loop, robot = _loop(make_assist, env, on=False)
    assert not [e for e in loop.lab_log if e["flag"] == "entity_cover"]
    assert not loop.options.provides("on_issues")


def test_the_loop_asks_about_a_dropped_bare_noun_the_engine_check_does_not_see(make_assist, env):
    loop, robot = _loop(make_assist, env, on=True)
    assert [e for e in loop.lab_log if e["flag"] == "entity_cover" and e.get("what") == "lamp"], loop.lab_log
    assert robot.asked, "the dropped object was not asked about"
    loop0, robot0 = _loop(make_assist, env, on=False)
    assert len(robot.asked) > len(robot0.asked)



def test_a_name_only_in_a_negation_a_condition_or_as_a_landmark_is_not_an_open_issue():
    for text in ("Put the kettle on the stool, but do not touch the lamp.", "If the lamp is on, put the kettle on the stool.",
                 "Put the kettle on the stool next to the lamp.", "Put the kettle on the stool with the ladle."):
        opt, run, svc = make(text)
        assert opt.on_issues([], run, [goal("kettle")]) == [], text
        assert not svc.labs
    opt, run, _ = make("Put the kettle on the stool and then the lamp.")                 # a joiner of the pack: an object again
    assert [i["what"] for i in opt.on_issues([], run, [goal("kettle")])] == ["lamp"]


def test_articles_and_plurals_come_from_the_language_pack_and_nothing_is_assumed_without_it(monkeypatch):
    from robot_agent.core.planning import langdata, names
    assert names.has("the lamp", "a lamp", ("en",)) and names.has("lamp", "two lamps", ("en",)) and names.has("pony", "the ponies", ("en",))
    fake = {"zz": {"articles": ["ka"], "plural_rules": [["ne", ""]]}}
    monkeypatch.setattr(langdata, "get", lambda lg: fake.get(str(lg)[:2], {}))
    monkeypatch.setattr(langdata, "prompt_lang", lambda: "zz")
    assert names.plain("ka lampo") == "lampo" and names.has("lampo", "ka lampone")          # a made-up language: its own article and plural
    monkeypatch.setattr(langdata, "prompt_lang", lambda: "yy")
    assert names.plain("ka lampo") == "ka lampo" and not names.has("lampo", "lampone")      # no pack data: nothing stripped, nothing folded


def test_entity_cover_runs_before_ask_focus_so_a_redundant_name_is_dropped_after_it_is_added():
    from robot_agent.options.ask_focus import SPEC as FOCUS, redundant
    assert SPEC.order < FOCUS.order
    opt, run, _ = make("Use the ladle to put the kettle on the stool.")
    g = {"type": "on", "object": "kettle", "from": "", "to": "stool@loft", "with": "ladle"}
    added = opt.on_issues([], run, [g])
    assert added == [{"kind": "object", "what": "ladle"}] or added == []
    assert all(redundant(i, run.places, [g]) for i in added)                               # what is added, ask_focus then drops
