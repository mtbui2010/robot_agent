"""Option ``heard_check`` (default off): a goal whose own quote of the person (``said``, or a say goal's message) is not in the heard words is an
``object`` issue of the read-back, so the engine's own re-sample / question / read-back handle it. Made-up places (a galley shelf, a crate, a
zorbik table), made-up objects (zorbel, vumpa, kizmo), a made-up language ``zq`` and made-up words in a non-Latin script; no real robot, place
or benchmark text."""

import itertools
import json
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options import heard_check as H
from robot_agent.core.planning.base import WorldState

SHELF, CRATE, TABLE = "shelf@galley", "crate@galley", "zorbik table@den"
ENV = {SHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       CRATE: {"aliases": ["crate"], "loc": {"x": 2.0, "y": 1.0}, "surface": True},
       TABLE: {"aliases": ["zorbik"], "loc": {"x": 4.0, "y": 1.0}, "surface": True}}
DATA = {"min_prefix": 2, "min_ratio": 0.5, "assumed": True}
PLAN = steps(f"MoveTo {SHELF}", "Find zorbel", "Pick zorbel", f"MoveTo {CRATE}", f"Place {CRATE}")


def goal(said, kind="on", obj="zorbel", to=CRATE, **kw):
    return {"type": kind, "object": obj, "from": "", "to": to, "said": said, **kw}


def unheard(heard, *goals, data=DATA, strip=None):
    return H.unheard_goals(list(goals), H.tokens(heard), data, strip)


# ── the pure rule, on small made-up tables ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("heard, said, flagged", [
    ("ka bluvo lu crate zen", "zorbel", True),            # a quote the person never said
    ("ka zorbal lu crate", "zorbel", False),             # the recogniser's slip the reader corrected: same start
    ("ka qrimba lu crate", "trimba", False),             # a slip of the first letter: the same word, similar cut to its length
    ("ka zakamomu lu crate", "momu", True),              # only an ending shared: another word (cut to its length it is not similar)
    ("ka bazo lu crate", "ba", False),                   # a quote token shorter than min_prefix: all of it starts a heard word
    ("ka rel lu crate", "q", True),
    ("ka vumpa", "lu", False),                           # a two-letter token is found with one letter in place (0.5): short words are lenient
    ("ka zorbal lu crate", "glim zorbel", False),        # any token of the quote found (the reader added a colour word)
    ("ka zorbal lu crate", "", False),                   # no said: never flagged (fail open)
])
def test_unheard_goals_table(heard, said, flagged):
    assert bool(unheard(heard, goal(said))) is flagged


def test_a_quote_in_another_script_than_the_heard_words_is_not_checked_as_defined():
    """``said`` is the person's own words; a quote the reader wrote in another script cannot be compared letter by letter: fail open."""
    heard = "ка вумпа лу"                      # made-up words in Cyrillic letters
    assert unheard(heard, goal("zorbel")) == []
    same_script = goal("зорбел")                                  # an invented quote in the heard script
    assert unheard(heard, same_script) == [same_script]
    assert H.main_script("TV вумпа") == "CYRILLIC" and H.main_script("12 !") == ""


def test_only_on_in_and_say_goals_have_a_quote_and_a_message_is_its_content_first():
    say = {"type": "say", "object": "vumpa zen", "content": "kizmo dri", "to": "pip"}
    assert [H.quote(g) for g in (goal("glim"), goal("glim", "in"), say, {"type": "say", "object": "vumpa"},
                                 {"type": "go", "to": CRATE, "said": "x"}, {"type": "open", "object": CRATE}, "bad")] == \
        ["glim", "glim", "kizmo dri", "vumpa", "", "", ""]
    assert unheard("ka kizmoz lu", say) == [] and unheard("ka vumpa zen", say) == [say]


def test_place_words_are_cut_from_the_heard_words_and_the_quote():
    places = Places(ENV)
    cut = lambda s: H.understand.without_places(s, places, places.keys)                          # noqa: E731
    g = goal("zorb")                                                                              # the reader took part of a place name
    assert unheard("ka vumpa lu zorbik", g) == []                                                 # 'zorbik' starts like the quote
    heard = H.tokens(cut("ka vumpa lu zorbik"))
    assert "zorbik" not in heard and H.unheard_goals([g], heard, DATA, cut) == [g]
    assert H.unheard_goals([goal("zorbik")], heard, DATA, cut) == []                              # a quote that is only a place: nothing to check


def test_the_numbers_are_data_a_second_robot_with_stricter_limits_flags_more():
    stricter = {"min_prefix": 5, "min_ratio": 0.9, "source": "made-up second robot pack"}
    assert unheard("ka zorbal lu", goal("zorbel")) == []
    assert unheard("ka zorbal lu", goal("zorbel"), data=stricter) == [goal("zorbel")]


@pytest.mark.parametrize("data", [None, {}, {"min_prefix": 2, "min_ratio": 0.5}, {"min_prefix": "x", "min_ratio": 0.5, "assumed": True},
                                  {"min_prefix": 2, "assumed": True}])
def test_no_or_unlabelled_or_broken_data_flags_nothing_empty(data):
    assert unheard("ka bluvo lu", goal("zorbel"), data=data) == []
    assert unheard("", goal("zorbel")) == []                                                       # nothing heard: nothing to compare


# ── the hook, with a stub of the services ───────────────────────────────────────────────────────────
def _opt(data, lang="zq"):
    cfg = resolve(H.SPEC, {"heard_check": "1"}, {}, None)
    svc = NS(robot=NS(manifest=NS(planner={"options": {"heard_check": {"match": data}}} if data is not None else {})), labs=[])
    svc.lab = lambda *a, **k: svc.labs.append((a, k))
    return H.HeardCheck(cfg, svc), NS(places=Places(ENV), task="ka bluvo lu crate", dialogue=[("robot", "q"), ("user", "ka zorbel")], lang=lang)


def test_the_hook_adds_one_object_issue_per_unheard_goal_in_a_made_up_language_and_logs_it():
    opt, run = _opt(DATA)
    goals = [goal("vumpa"), goal("vumpa"), goal("zorbel"), {"type": "go", "to": CRATE}]
    old = [{"kind": "place", "what": "shelf", "keys": [SHELF]}]
    assert opt.on_issues(old, run, goals) == old + [{"kind": "object", "what": "vumpa"}]          # the answer said zorbel; vumpa never
    assert [k for _a, k in opt.svc.labs] == [{"goals": 4, "unheard": ["vumpa"]}]


def test_the_hook_changes_nothing_with_no_data_or_when_everything_was_heard_empty():
    opt, run = _opt(None)
    assert opt.on_issues([], run, [goal("vumpa")]) is None and not opt.svc.labs
    opt, run = _opt(DATA)
    assert opt.on_issues([], run, [goal("zorbel")]) is None and not opt.svc.labs


# ── through the loop ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def env():
    return dict(ENV)


def read(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": list(goals)}


def scrub(x):
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at", "plan_sec")}
    if isinstance(x, list):
        return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")]
    return x


@pytest.fixture
def run_loop(make_assist, tmp_path, agent_state):
    """One run of the loop with a fresh world and its own memory file, so two runs of a test start from the same state."""
    n = itertools.count()

    def run(said="zorbel", data=DATA, on=True, task="Put vumpa on the crate.", answers=("Put the zorbel on the crate.", "yes")):
        agent_state.world = WorldState()
        robot = FakeRobot(ENV, {"zorbel": SHELF}, start=SHELF, answers=list(answers))
        llm = FakeLLM(default=read(goal(said)))
        loop = make_assist(robot, llm, ScriptedPlanner(PLAN), search_legs=1, ask_help=False, detector_miss_rate=0,
                           memory_path=str(tmp_path / f"memory_{next(n)}.json"), **({"heard_check": "1"} if on else {}))
        if data is not None:
            loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": {"heard_check": {"match": data}}}
        return loop, robot, llm, run_task(loop, task, max_events=3000)
    return run


def _flags(loop):
    return [e for e in loop.lab_log if e["flag"] == H.FLAG]


def test_registered_and_off_by_default_the_loop_is_unchanged_off(run_loop):
    assert "heard_check" in ASSIST_OPTIONS and REGISTRY.get("heard_check").key("enabled").default is False
    loop, robot, _llm, evs = run_loop(on=False)
    assert not _flags(loop) and not loop.options.provides("on_issues") and not robot.asked and done(evs)["status"] == "success"


@pytest.mark.parametrize("on, data", [(False, DATA), (True, None), (True, {"min_prefix": 2, "min_ratio": 0.5})])
def test_loop_off_or_with_no_labelled_data_is_byte_identical_to_off_empty(run_loop, on, data):
    def run(**kw):
        loop, robot, llm, evs = run_loop(**kw)
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, robot.asked, [c[0] for c in llm.calls], loop.lab_log
    assert run(on=on, data=data) == run(on=False, data=None)


def test_regression_a_recogniser_gap_filled_with_an_object_never_said_is_asked_before_planning(run_loop):
    """The class: the heard words lost the object ("Put vumpa on the crate"), the reader filled the gap with an object the person never
    said ('zorbel'); the engine's own checks see nothing (no determiner, the place is used) and the run would plan it unasked. With the
    option, the engine re-samples (no issue-free reading), asks its own repeat question, and goes on with the answer that names the object."""
    off_loop, off_robot, off_llm, _ = run_loop(on=False)
    loop, robot, llm, evs = run_loop()
    assert not off_robot.asked and robot.asked, "the unheard quote was not asked about"
    assert _flags(loop)[0]["unheard"] == ["zorbel"] and len(llm.calls) > len(off_llm.calls)       # the engine's re-samples ran
    assert done(evs)["status"] == "success" and robot.obj.get("zorbel") == CRATE
