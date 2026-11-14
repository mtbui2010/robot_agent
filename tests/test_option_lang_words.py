"""Option ``lang_words`` (default OFF): the condition and negation words come from the language pack. Hook-level tests with a language invented here
(``zq``: "nuvo" opens a condition, "kelu" a negation), the shipped English and Korean packs, and a loop-level run. Nothing here is a real robot's
place or object."""
import re
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import clauses, goal_filter, langdata, understand
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.lang_words import SPEC, LangWords

MADE_UP = {"patterns": {"condition": r"\bnuvo\b[^,;.]*", "negation": r"\bkelu\b[^,;.]*"}}
ENV = {"plinth@loft": {}, "rack@loft": {}}


@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", MADE_UP)
    return "zq"


def make(lang="en"):
    svc = NS(lab=lambda *a, **k: svc.labs.append((a, k)), labs=[])
    run = NS(lang=lang, places=Places(ENV))
    return LangWords(resolve(SPEC, {"lang_words": "1"}, {}, None), svc), run, svc


def test_registered_and_off_by_default_the_loop_is_unchanged_off(make_assist, env):
    assert "lang_words" in ASSIST_OPTIONS and REGISTRY.get("lang_words").key("enabled").default is False
    robot = FakeRobot(env, {"mug": "counter@kitchen"}, start="counter@kitchen")
    loop = make_assist(robot, FakeLLM(default={"status": "clear", "reason": "", "question": "", "goals": []}), ScriptedPlanner(steps("MoveTo counter@kitchen")))
    run_task(loop, "Put the mug on the dining table.", max_events=500)
    assert getattr(loop._env_places, "clauses", None) is None
    assert understand._active("go. if the box is open, close it", places=NS(clauses=None)) == understand._active("go. if the box is open, close it")


def test_a_made_up_language_clause_words_are_read_from_its_pack(zq):
    opt, run, svc = make(zq)
    opt.on_start(run)
    assert run.places.clauses is not None
    act = understand._active("take the fig. nuvo the lid is up, shut it. take the plum kelu the fig", places=run.places)
    assert "fig" in act and "plum" in act and "lid" not in act                       # the condition clause is taken out
    assert act.count("fig") == 1 and "kelu" not in act                               # the negation clause too
    kept = understand._active("nuvo the lid is up, shut it kelu the pan", conds=False, places=run.places)
    assert "lid" in kept and "pan" not in kept                                       # a condition is not a negation


def test_made_up_words_are_not_read_without_the_option_and_other_languages_keep_their_own(zq):
    assert "lid" in understand._active("nuvo the lid is up, shut it")               # the engine knows no such word
    opt, run, _ = make("en")
    opt.on_start(run)                                                               # the run's language has no "nuvo": untouched
    assert "lid" in understand._active("nuvo the lid is up, shut it", places=run.places)
    assert "lid" not in understand._active("if the lid is up, shut it", places=run.places)    # and English is read from the shipped pack


def test_a_pack_with_one_kind_takes_nothing_out_for_the_other_empty_data(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "yy", {"patterns": {"negation": r"\bgrr\b[^,;.]*"}})
    monkeypatch.setitem(langdata._CACHE, "xx", {"lang": "xx", "words": {}})
    monkeypatch.setattr(langdata, "_PROMPT_LANG", "xx")                             # the prompt language has no data either
    cl = clauses.build("yy")
    assert cl.cond is None and cl.neg is not None
    assert understand._active("if the lid is up, shut it grr the pan", places=NS(clauses=cl)) .count("lid") == 1
    assert "pan" not in understand._active("shut it grr the pan", places=NS(clauses=cl))


def test_a_language_without_data_leaves_the_places_alone_empty(monkeypatch):
    monkeypatch.setattr(langdata, "_PROMPT_LANG", "xx")
    monkeypatch.setitem(langdata._CACHE, "xx", {})
    for lang in ("xx", "", None, "q"):
        opt, run, svc = make(lang)
        opt.on_start(run)
        assert getattr(run.places, "clauses", None) is None
    opt, run, svc = make("en")
    run.places = None
    opt.on_start(run)                                                               # no places: nothing to set, no error
    assert clauses.of(None) is None and clauses.build(None) is None


def test_the_shipped_english_pack_reads_like_the_engine_and_the_korean_pack_reads_a_condition():
    en = clauses.build("en")
    sents = ["put the pear in the bowl, and if the lid is up, shut it", "do not touch the vase; bring the cup", "never move the lamp. open the box",
             "bring the tray unless the door is shut, then rest", "leave the book without the cover", "take it in case the rain starts"]
    for s in sents:
        assert understand._active(s, places=NS(clauses=en)) == understand._active(s), s
        assert understand._active(s, conds=False, places=NS(clauses=en)) == understand._active(s, conds=False), s
    ko = clauses.build("ko")
    assert ko.cond is not None and ko.neg is not None
    cond = "컵을 식탁에 놓고 서랍이 열려 있으면 닫아줘"
    assert "서랍" not in understand._active(cond, places=NS(clauses=ko)) and "컵" in understand._active(cond, places=NS(clauses=ko))
    neg = "컵 말고 접시를 가져와"
    assert "접시" in understand._active(neg, places=NS(clauses=ko))


def test_the_negation_filter_and_the_object_mentions_read_the_run_clauses(zq):
    opt, run, _ = make(zq)
    opt.on_start(run)
    goals = [{"type": "on", "object": "fig", "from": "", "to": "plinth"}, {"type": "on", "object": "plum", "from": "", "to": "rack"}]
    text = "take the plum to the rack kelu the fig"
    kept, notes = goal_filter.before_grounding(goals, text, run.places)
    assert [g["object"] for g in kept] == ["plum"] and notes
    assert [g["object"] for g in goal_filter.before_grounding(goals, text)[0]] == ["fig", "plum"]     # no clauses: the engine's words only
    text = "bring the plum. nuvo put the lid on the rack"
    on = " ".join(understand._object_mentions(text, run.places, set()))
    assert "plum" in on and "lid" not in on                                         # the condition's object is no mention
    assert "lid" in " ".join(understand._object_mentions(text, Places(ENV), set()))   # without clauses it is


def test_the_loop_gives_the_run_its_clauses_when_on(make_assist, env, monkeypatch):
    robot = FakeRobot(env, {"mug": "counter@kitchen"}, start="counter@kitchen")
    und = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "mug", "from": "counter", "to": "dining table"}]}
    plan = steps("MoveTo counter@kitchen", "Find mug", "Pick mug", "MoveTo dining table@kitchen", "Place dining table@kitchen")
    loop = make_assist(robot, FakeLLM(default=und), ScriptedPlanner(plan), detector_miss_rate=0, lang_words="1")
    run_task(loop, "Put the mug on the dining table, but not the plate.", max_events=3000)
    assert loop._env_places.clauses.cond is not None and loop._env_places.clauses.neg is not None
    assert isinstance(loop._env_places.clauses, clauses.Clauses)
