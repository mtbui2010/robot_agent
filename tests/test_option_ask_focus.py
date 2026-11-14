"""Option ``ask_focus`` (default OFF): purposeful questions. Hook-level tests on made-up worlds (a loft with a stool, a rack and a plinth; objects
a mug, a red mug, a blue mug, a kettle) and loop-level tests of the read-back. Nothing here is a real robot's place or object."""
from types import SimpleNamespace as NS

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import understand
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.ask_focus import SPEC, AskFocus, fits, redundant, world_names

ENV = {"stool@loft": {}, "rack@loft": {"aliases": ["hanger"]}, "plinth@loft": {}}
PLACES = Places(ENV)


def make(objects=("kettle", "red mug", "blue mug"), mem=(), cap=3, answer=None):
    cfg = resolve(SPEC, {"ask_focus": "1", "ask_focus.max_confirms": cap}, {}, None)
    svc = NS(robot=NS(manifest=NS(limits={"objects": {n: {} for n in objects}})), lab=lambda *a, **k: svc.labs.append((a, k)), labs=[],
             is_unsure=lambda run, t: bool(answer and answer in t),
             unsure_leftover=lambda run, t: t.replace(answer, " ").strip(" ,.") if answer and answer in t else None)
    run = NS(memory=NS(data={"objects": {n: {} for n in mem}}), places=PLACES)
    return AskFocus(cfg, svc), run, svc


def goal(obj, to="stool", typ="on"):
    return {"type": typ, "object": obj, "from": "", "to": to}


def test_an_object_that_is_a_place_name_or_a_word_of_another_field_is_no_open_issue():
    opt, run, svc = make()
    issues = [{"kind": "object", "what": "hanger"}, {"kind": "object", "what": "plinth"}, {"kind": "object", "what": "tray"}]
    got = opt.on_issues(issues, run, [goal("kettle")])
    assert got == [{"kind": "object", "what": "tray"}]                       # a place by its alias, a place by its head; "tray" is unknown
    tool = [{"type": "on", "object": "kettle", "to": "stool", "tool": "tray"}]
    assert opt.on_issues([{"kind": "object", "what": "tray"}], run, tool) == []    # the plan reaches it by another field
    assert [a for a, k in svc.labs if k.get("rule") == "redundant"]
    assert redundant({"kind": "place", "what": "hanger"}, PLACES, []) is False       # only object issues


def test_two_objects_of_the_world_that_fit_one_description_are_asked_about_from_the_world():
    opt, run, svc = make()
    got = opt.on_issues([], run, [goal("mug")])
    assert got == [{"kind": "choice", "what": "mug", "options": ["red mug", "blue mug"]}]
    assert fits("mug", ["red mug", "blue mug", "kettle"]) == ["red mug", "blue mug"]
    assert understand.issue_question(got, PLACES) == "Which mug do you mean: red mug or blue mug?"
    assert understand.describe(got[0]) == "more than one mug fits"


def test_one_match_or_an_exact_name_is_no_ambiguity():
    opt, run, _ = make(objects=("kettle", "red mug"))
    assert opt.on_issues([], run, [goal("mug")]) == []                        # one fits
    opt, run, _ = make(objects=("mug", "red mug", "blue mug"))
    assert opt.on_issues([], run, [goal("mug")]) == []                        # a "mug" is named exactly
    opt, run, _ = make()
    assert opt.on_issues([], run, [goal("red mug")]) == []                    # the description is already narrow
    assert opt.on_issues([], run, [goal("mu")]) == []                         # whole words, not letters
    assert opt.on_issues([], run, [goal("mug", typ="go")]) == []


def test_the_object_memory_counts_as_the_world():
    opt, run, _ = make(objects=(), mem=("green pot", "iron pot"))
    assert world_names(run, opt.svc.robot) == ["green pot", "iron pot"]
    assert opt.on_issues([], run, [goal("pot")])[0]["options"] == ["green pot", "iron pot"]


def test_an_i_dont_know_to_the_read_back_is_no_refusal_and_a_refusal_is_one():
    opt, run, svc = make(answer="no idea")
    assert opt.unrefused(run, "no idea") is True
    assert opt.unrefused(run, "no, the other one") is None
    assert opt.unrefused(run, "") is None
    assert [k for a, k in svc.labs if k.get("rule") == "unsure_is_no_refusal"]


def test_an_unsure_phrase_inside_a_refusal_with_a_correction_is_still_a_refusal():
    opt, run, svc = make(answer="not sure")
    assert opt.unrefused(run, "No, I am not sure why you said kettle. I meant the lamp.") is None
    assert opt.unrefused(run, "no, not sure") is None                       # a no-marker beside the phrase
    assert opt.unrefused(run, "uh, not sure") is True                      # fillers do not count as words
    assert [k for a, k in svc.labs if k.get("rule") == "unsure_with_more"]


def opt_real(svc):
    return AskFocus(resolve(SPEC, {"ask_focus": "1"}, {}, None), svc)


def test_the_real_service_takes_the_phrases_out_and_keeps_the_rest(make_assist, env):
    from robot_agent.core.planning.option_services import LoopServices
    loop = make_assist(FakeRobot(env, {}, start="counter@kitchen"), FakeLLM(default={}), ScriptedPlanner(steps("MoveTo counter@kitchen")))
    svc, run = LoopServices(loop), NS(places=PLACES, lang="en", robot=loop.robot)
    assert opt_real(svc).unrefused(run, "I don't know.") is True and opt_real(svc).unrefused(run, "Um, I'm not sure.") is True
    assert svc.unsure_leftover(run, "I don't know") is not None and svc.unsure_leftover(run, "put it on the stool") is None
    mixed = svc.unsure_leftover(run, "No, I am not sure why you said cup. I meant the mug.")
    assert mixed and "meant" in mixed.split()
    opt = AskFocus(resolve(SPEC, {"ask_focus": "1"}, {}, None), svc)
    assert opt.unrefused(run, "No, I am not sure why you said cup. I meant the mug.") is None


def test_the_confirmation_rounds_are_bounded_by_the_key():
    assert make(cap=2)[0].confirm_limit(None) == 2
    assert make(cap=0)[0].confirm_limit(None) == 1                           # never below one read-back
    assert make()[0].confirm_limit(None) == 3                                # the documented default


def test_empty_world_and_empty_issues_does_nothing_empty():
    opt, run, _ = make(objects=())
    assert opt.on_issues([], run, [goal("mug")]) == []
    assert opt.on_issues([], run, []) == []
    run.memory = None
    assert opt.on_issues([], run, [goal("mug")]) == []
    opt.svc = NS(robot=None, lab=lambda *a, **k: None)                       # a host without is_unsure: nothing claimed
    assert opt.unrefused(run, "no idea") is None


def test_registered_and_off_by_default_the_loop_is_unchanged_off(make_assist, env):
    assert "ask_focus" in ASSIST_OPTIONS and REGISTRY.get("ask_focus").key("enabled").default is False
    loop, d = _loop(make_assist, env, on=False, answers=["no idea"])
    assert not [e for e in loop.lab_log if e["flag"] == "ask_focus"]
    assert loop.options.query("confirm_limit", None, default=9) == 9 and loop.options.query("unrefused", None, "x") is None
    assert not loop.options.provides("on_issues")


def _loop(make_assist, env, on, answers=(), confirms=None, **extra):
    robot = FakeRobot(env, {"mug": "counter@kitchen"}, start="counter@kitchen", answers=list(answers))
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "mug", "from": "counter", "to": "dining table"}]}
    opts = {"ask_focus": "1"} if on else {}
    if confirms is not None:
        opts["ask_focus.max_confirms"] = confirms
    plan = steps("MoveTo counter@kitchen", "Find mug", "Pick mug", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, confirm_task="1", **opts, **extra)
    loop.fake = robot
    loop.robot.manifest.planner["unstick"] = {"fallback_words": 3, "fallback_chars": 12}
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find", "pick": "Pick", "put_down": "Place"}
    return loop, done(run_task(loop, "Put the mug on the dining table.", max_events=3000))


def test_the_loop_goes_on_after_an_i_dont_know_on_and_asks_for_a_correction_off(make_assist, env):
    loop, d = _loop(make_assist, env, on=True, answers=["no idea"])
    assert [e for e in loop.lab_log if e["flag"] == "ask_focus"], "the option did not act"
    assert d["status"] == "success", d
    loop0, d0 = _loop(make_assist, env, on=False, answers=["no idea"])
    assert not [e for e in loop0.lab_log if e["flag"] == "ask_focus"]
    assert len(loop0.fake.asked) > len(loop.fake.asked), "off: the answer started the correction path (more questions)"


def test_the_loop_stops_after_the_limit_of_read_back_rounds(make_assist, env):
    refusals = ["no", "no", "no", "no", "no", "no"]
    loop, d = _loop(make_assist, env, on=True, answers=refusals, confirms=1)
    assert d["status"] == "failed" and "did not confirm" in str(d.get("msg") or d), d
    n = sum(1 for q in loop.fake.asked if "right?" in q)
    assert n == 1, loop.fake.asked


def test_the_loop_asks_which_one_when_the_world_holds_two_that_fit(make_assist, env):
    def run(on):
        robot = FakeRobot(env, {"red mug": "counter@kitchen", "blue mug": "counter@kitchen"}, start="counter@kitchen", answers=["the red one"])
        und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "mug", "from": "counter", "to": "dining table"}]}
        loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(steps("MoveTo counter@kitchen")), detector_miss_rate=0,
                           **({"ask_focus": "1"} if on else {}))
        loop.robot.manifest.limits = {"objects": {"red mug": {}, "blue mug": {}}}
        run_task(loop, "Put the mug on the dining table.", max_events=3000)
        return loop, robot
    loop, robot = run(True)
    assert [e for e in loop.lab_log if e["flag"] == "ask_focus" and e.get("rule") == "world_ambiguity"], loop.lab_log
    assert any("Which mug" in q for q in robot.asked), robot.asked
    loop0, robot0 = run(False)
    assert not any("Which mug" in q for q in robot0.asked)
