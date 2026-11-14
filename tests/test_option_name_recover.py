"""Options ``name_recover`` and ``asr_guard`` (default OFF): an object name the recogniser garbled is matched to the catalogue by a strict letter
distance; an empty or holed request is asked again without calling the reader. Made-up catalogues and a made-up script; no benchmark text."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options import REGISTRY, STOP
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import langdata
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options import asr_guard, name_recover
from robot_agent.options.asr_guard import SPEC as GSPEC, AsrGuard
from robot_agent.options.name_recover import SPEC, NameRecover

PLACES = Places({"stool@loft": {"aliases": ["perch"]}})
CAT = ("kettle", "ladle", "red mug", "teapot")
# a made-up script: the letters of a name are "syllables" of three code points; ``zq`` declares the letters inside one as the unit
ZQ = {"asr": {"hole": [r"\bkal\s+(?:ze|mo)\b", r"\bkal\s*$"], "distance_unit": "jamo"}}


@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)


def make(objects=CAT, lang="en", **keys):
    cfg = resolve(SPEC, {"name_recover": "1", **{f"name_recover.{k}": v for k, v in keys.items()}}, {}, None)
    svc = NS(robot=NS(manifest=NS(limits={"objects": {n: {} for n in objects}})), labs=[], asked=[], answer="")
    svc.lab = lambda *a, **k: svc.labs.append((a, k))
    svc.ask = lambda run, q: (svc.asked.append(str(q)), svc.answer)[1]
    svc.cancelled = lambda run: None
    run = NS(places=PLACES, memory=NS(data={}), lang=lang, asked=0, ev=lambda etype, **kw: (etype, kw), goals=[])
    return NameRecover(cfg, svc), run, svc


def goal(obj, typ="on"):
    return {"type": typ, "object": obj, "from": "", "to": "stool@loft"}


def go(opt, run, goals):
    run.goals = goals
    gen = opt.on_goals(run)
    out, sent = None, []
    try:
        while True:
            sent.append(next(gen))
    except StopIteration as e:
        out = e.value
    return out, sent


def test_registered_default_off_and_the_off_path_changes_nothing_off(make_assist=None):
    assert "name_recover" in ASSIST_OPTIONS and "asr_guard" in ASSIST_OPTIONS
    assert REGISTRY.get("name_recover").key("enabled").default is False and REGISTRY.get("asr_guard").key("enabled").default is False
    cfg = resolve(SPEC, {}, {}, None)
    assert not cfg["enabled"]


def test_a_unique_near_name_is_recovered_one_wrong_letter_and_two():
    opt, run, svc = make()
    gs = [goal("kettel"), goal("tealpot")]                    # a swap of neighbours; one letter too many
    go(opt, run, gs)
    assert [g["object"] for g in gs] == ["kettle", "teapot"] and not svc.asked
    assert [k for a, k in svc.labs if k.get("took") == "kettle"]


def test_an_exact_name_or_a_name_that_holds_one_is_untouched_and_a_short_name_never_matched():
    opt, run, svc = make(("kettle", "red mug", "cup", "cap"))
    gs = [goal("kettle"), goal("mug"), goal("cut"), goal("red mug")]
    go(opt, run, gs)
    assert [g["object"] for g in gs] == ["kettle", "mug", "cut", "red mug"] and not svc.asked      # "cut" has 3 letters: never fuzzy


def test_ambiguous_two_near_names_ask_and_the_answer_decides_never_a_guess():
    opt, run, svc = make(("lamp", "limp", "kettle"))
    svc.answer = "the limp one"
    gs = [goal("lomp")]
    out, sent = go(opt, run, gs)
    assert svc.asked == ["Did you mean lamp or limp?"] and sent[0][0] == "clarify" and gs[0]["object"] == "limp"
    opt2, run2, svc2 = make(("lamp", "limp"))
    svc2.answer = "I do not know"
    gs2 = [goal("lomp")]
    go(opt2, run2, gs2)
    assert gs2[0]["object"] == "lomp" and len(svc2.asked) == 1                                # no guess; one question per run
    go(opt2, run2, [goal("lomp")])
    assert len(svc2.asked) == 1


def test_none_within_the_limit_asks_nothing_and_changes_nothing():
    opt, run, svc = make()
    gs = [goal("trowelhandle")]
    go(opt, run, gs)
    assert gs[0]["object"] == "trowelhandle" and not svc.asked and not svc.labs


def test_made_up_script_the_unit_is_the_letters_inside_a_syllable(zq):
    cat = ("가나다", "라마바")                          # made-up names of three syllables each
    opt, run, svc = make(cat, lang="zq")
    near_one = "가난다"                                             # one letter inside the middle syllable differs
    gs = [goal(near_one), goal("가버다")]                           # the second differs in two letters of one syllable
    go(opt, run, gs)
    assert gs[0]["object"] == cat[0] and not svc.asked
    assert name_recover.unit_fn(("zq",)) is not name_recover.unit_fn(("xx",))       # the unit is the pack's: letters inside a syllable, or characters
    assert name_recover.unit_fn(("xx",))(near_one) == near_one


def test_empty_catalogue_or_foreign_data_does_nothing_empty():
    opt, run, svc = make(objects=())
    gs = [goal("kettel")]
    assert go(opt, run, gs)[0] is None and gs[0]["object"] == "kettel"
    opt, run, svc = make(lang="xx")                                         # a language with no pack: letters, the catalogue still holds
    gs = [goal("kettel"), goal("hello", typ="say"), goal("perch")]
    go(opt, run, gs)
    assert [g["object"] for g in gs] == ["kettle", "hello", "perch"]       # a say goal and a place of the ENV are not names to recover


def test_a_cancel_while_asking_stops():
    opt, run, svc = make(("lamp", "limp"))
    svc.cancelled = lambda run: ("cancelled",)
    out, _ = go(opt, run, [goal("lomp")])
    assert out == STOP


# ── asr_guard ─────────────────────────────────────────────────────────────────
def guard(task, dialogue=(), repeat_asked=False, lang="en", **keys):
    """(the question the guard asked through the loop's clarify step or None, svc)."""
    cfg = resolve(GSPEC, {"asr_guard": "1", **{f"asr_guard.{k}": v for k, v in keys.items()}}, {}, None)
    svc = NS(labs=[], asked=[])
    svc.lab = lambda *a, **k: svc.labs.append((a, k))

    def ask_clarify(run, q):
        svc.asked.append(q)
        return "next"
        yield                                                                       # a flow service is a generator
    svc.ask_clarify = ask_clarify
    run = NS(task=task, dialogue=list(dialogue), repeat_asked=repeat_asked, lang=lang, ev=lambda etype, **kw: (etype, kw))
    gen, out = AsrGuard(cfg, svc).on_heard(run), None
    try:
        while True:
            next(gen)
    except StopIteration as e:
        out = e.value
    return (svc.asked[0] if svc.asked else None), svc


def test_empty_request_asks_to_repeat_without_the_reader_and_only_once_empty():
    q, svc = guard("  ")
    assert q is not None and q.key == "q_repeat" and svc.labs[0][1]["what"] == "empty" and svc.asked == [q]
    assert guard("", repeat_asked=True)[0] is None and guard("", dialogue=[("user", "ladle please")])[0] is None
    assert guard("", empty=False)[0] is None


def test_hole_a_possessive_before_a_function_word_or_the_end_asks_once():
    for text in ("bring me the to the table", "put it in my", "move the cup onto a and then go"):
        assert guard(text)[0] is not None, text
    for text in ("bring me the cup", "put it in my room", "the table is clean"):
        assert guard(text)[0] is None, text
    assert guard("bring me the to", repeat_asked=True)[0] is None and guard("bring me the to", hole=False)[0] is None


def test_made_up_language_hole_pattern_comes_from_its_own_pack(zq):
    assert guard("tor kal ze nu", lang="zq")[0] is not None and guard("tor kal", lang="zq")[0] is not None
    assert guard("tor kal nu", lang="zq")[0] is None
    assert guard("the to", lang="xx")[0] is not None                                  # the prompt language's pack still applies
    assert asr_guard.has_hole("the", ("xx",)) is False                                 # no pack data for xx: only the empty check
    assert name_recover.unit_fn(("xx",))("abc") == "abc"


def test_asr_guard_empty_pack_data_foreign_language_does_nothing_empty(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", {})
    assert guard("a", lang="zq")[0] is not None                                       # the prompt language's pack is read as well as the run's
    assert asr_guard.hole_patterns(("zq",)) == []


def _loop(make_assist, env, heard, answers=(), on=True):
    robot = FakeRobot(env, {"kettle": "counter@kitchen"}, start="counter@kitchen", answers=list(answers))
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "kettle", "from": "counter", "to": "dining table"}]}
    plan = steps("MoveTo counter@kitchen", "Find kettle", "Pick kettle", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    llm = FakeLLM(default=und)
    loop = make_assist(robot, llm, ScriptedPlanner(plan), detector_miss_rate=0, **({"asr_guard": "1"} if on else {}))
    run_task(loop, heard, max_events=3000)
    return loop, robot, llm


def test_loop_empty_request_asks_to_repeat_and_the_reader_is_not_called(make_assist, env):
    loop, robot, llm = _loop(make_assist, env, "   ", answers=["put the kettle on the dining table"])
    assert robot.asked and [e for e in loop.lab_log if e["flag"] == "asr_guard"]
    assert "put the kettle on the dining table" in llm.calls[0][0]                  # the first reader call already holds the repeated request


def test_loop_asr_guard_off_the_reader_reads_the_empty_request(make_assist, env):
    loop, robot, llm = _loop(make_assist, env, "   ", on=False)
    assert not [e for e in loop.lab_log if e["flag"] == "asr_guard"] and len(llm.calls) >= 1
    assert "put the kettle" not in llm.calls[0][0]                                   # off: the reader's first call read the empty request


def test_loop_a_garbled_name_is_read_back_as_the_catalogue_name_and_off_it_stays(make_assist, env):
    def run_it(on):
        robot = FakeRobot(env, {"kettle": "counter@kitchen"}, start="counter@kitchen", answers=["yes"])
        und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "kettel", "from": "counter", "to": "dining table"}]}
        plan = steps("MoveTo counter@kitchen", "Find kettle", "Pick kettle", "MoveTo dining table@kitchen", "Place dining table@kitchen")
        loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, **({"name_recover": "1"} if on else {}))
        loop.robot.manifest.limits = {"objects": {"kettle": {}, "lamp": {}}}
        run_task(loop, "Put the kettel on the dining table.", max_events=3000)
        return loop
    assert [e for e in run_it(True).lab_log if e["flag"] == "name_recover" and e.get("took") == "kettle"]
    assert not [e for e in run_it(False).lab_log if e["flag"] == "name_recover"]
