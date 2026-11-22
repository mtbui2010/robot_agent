"""The phrase tables as pack data (Step S04 of docs/proposals/generalize_core_design.md).

The 62 phrases of ``speech.CORE_PHRASES`` and the three single texts (``speech.HELP_PICK``, ``speech.HELP_MOVE``,
``budget.BUDGET_TEXT``) live in two pack files; the old constants are copied verbatim in tests/legacy_ref/s04_speech_phrases.py.
Proved here: the union of the two files is the old table, character for character, and no key is in both; the engine's names
are loaded views of it; a lookup in every language gives what the old code gave (a differential test against a copy of the
old ``phrase``); the pure rules of core/pack/phrases.py on made-up packs. Made-up robots only."""

import types
from pathlib import Path

import pytest

from legacy_ref import s04_speech_phrases as old
from robot_agent.contract import pack as schema
from robot_agent.core.pack import loader, phrases as pp
from robot_agent.core.planning import budget, langdata, speech
from robot_agent.core.planning.robot import CORE_TEXT

ROOT = Path(__file__).resolve().parents[1]
CHAIN = pp.default_chain()
CORE_FILE, HOUSE_FILE = CHAIN[0], CHAIN[-1]


def decl(name, phrases):
    return schema.parse({"schema": 2, "name": name, "source": "made up", "phrases": phrases}, name)


# -- the default data equals the old constants ------------------------------------------------------------------------------
def test_the_chain_is_the_two_shipped_files():
    assert [d.name for d in CHAIN] == ["core", "std"]
    assert (ROOT / "robot_agent/packs/core/phrases.yaml").is_file() and (ROOT / "robot_agent/packs/household/phrases.yaml").is_file()


def test_union_of_the_two_files_is_the_old_table_character_for_character():
    en = pp.union(CHAIN, "en")
    singles = {k: v for k, v in en.items() if k.startswith(pp.TEXT_PREFIX)}
    assert {k: v for k, v in en.items() if k not in singles} == old.LEGACY_PHRASES
    assert len(old.LEGACY_PHRASES) == 62 and len(singles) == 3
    assert singles == {"text.help_pick": old.LEGACY_HELP_PICK, "text.help_move": old.LEGACY_HELP_MOVE,
                       "text.llm_budget": old.LEGACY_BUDGET_TEXT}


def test_no_key_is_in_both_files_and_each_file_holds_something():
    a, b = set(CORE_FILE.tables["phrases"]), set(HOUSE_FILE.tables["phrases"])
    assert not a & b and a and b and pp.repeated(CHAIN) == []
    assert len(a) + len(b) == 62 + 3


def test_every_entry_is_english_only_with_its_slots_declared():
    import string
    for d in CHAIN:
        for key, e in d.tables["phrases"].items():
            assert set(e) <= {"en", "slots", "source", "assumed"}, key
            names = {n for _, n, _, _ in string.Formatter().parse(e["en"]) if n}
            assert set(e.get("slots", [])) == names, key
        assert d.label() is not None                       # the pack's own source label covers every entry


def test_the_engines_names_are_views_of_the_data():
    assert speech.CORE_PHRASES == old.LEGACY_PHRASES
    assert speech.HELP_PICK == old.LEGACY_HELP_PICK and speech.HELP_MOVE == old.LEGACY_HELP_MOVE
    assert budget.BUDGET_TEXT == old.LEGACY_BUDGET_TEXT == CORE_TEXT["llm_budget"]
    assert pp.view() == old.LEGACY_PHRASES and pp.view() is not pp.view()      # a fresh dict: a caller may change its own
    assert pp.text("help_pick") == old.LEGACY_HELP_PICK


def test_the_help_questions_stay_attributes_of_speech():
    for name in ("HELP_PICK", "HELP_MOVE"):
        assert isinstance(getattr(speech, name), str) and name in vars(speech)


# -- the lookup, differential against a copy of the old function --------------------------------------------------------------
def old_phrase(robot, key, lang):
    """``speech.phrase`` as it was at 2d57c99, reading the verbatim constant."""
    lg = (lang or "en")[:2]
    by = ((getattr(getattr(robot, "i18n", None), "phrases", None) or {}).get(key) or {}) if robot is not None else {}
    if by.get(lg):
        return by[lg]
    own = langdata.block(lg, "speech").get(key)
    if isinstance(own, str) and own:
        return own
    return old.LEGACY_PHRASES.get(key) if lg == "en" else None


def robot_with(phrases):
    return types.SimpleNamespace(i18n=types.SimpleNamespace(phrases=phrases))


LANGS = ("en", "ko", "ja", "zh", "fr", "xx", "", None, "EN", "ko-KR")
ROBOTS = (None, robot_with({}), robot_with({"rb_on": {"en": "set the {object} on the {place}", "ko": "x"},
                                            "q_repeat": {"ko": "again?"}, "q_where_go": {"ko": "where?"}}))


@pytest.mark.parametrize("robot", ROBOTS, ids=("none", "empty", "overrides"))
def test_phrase_lookup_is_the_old_one_for_every_key_and_language(robot):
    keys = list(old.LEGACY_PHRASES) + ["text.help_pick", "help_pick", "llm_budget", "no_such_key", "", "_join"]
    for key in keys:
        for lang in LANGS:
            assert speech.phrase(robot, key, lang) == old_phrase(robot, key, lang), (key, lang)


def test_a_language_pack_still_wins_over_the_default_english():
    ko = langdata.block("ko", "speech")
    assert ko, "the Korean pack has a speech block"
    hit = next(k for k in ko if isinstance(ko[k], str) and ko[k] and k in old.LEGACY_PHRASES)
    assert speech.phrase(None, hit, "ko") == ko[hit]
    assert pp.other(hit, "ko") is None and speech.phrase(None, hit, "en") == old.LEGACY_PHRASES[hit]


def test_a_key_with_no_text_in_the_language_is_left_to_the_translator():
    for key in old.LEGACY_PHRASES:
        if not isinstance(langdata.block("ko", "speech").get(key), str):
            assert speech.phrase(None, key, "ko") is None


def test_local_text_and_render_say_what_they_said():
    for key, tpl in old.LEGACY_PHRASES.items():
        slots = {n: f"<{n}>" for n in ("object", "place", "person", "what", "text", "options", "summary", "source", "room",
                                         "first", "second", "why", "said", "issues", "verb", "rest")}
        want = tpl.format(**slots) if "{" in tpl else tpl
        got = speech.LocalText(key, slots)
        assert str(got) == want and (speech.render(got, "en", None, None) in (want, None)), key
    assert str(speech.LocalText.seq(["a", "b", "c"])) == "a, then b, then c"
    assert str(speech.LocalText("no_such_key")) == "no_such_key"


# -- the pure rules, on made-up packs --------------------------------------------------------------------------------------------
def test_union_later_pack_replaces_an_earlier_key_and_other_languages_are_separate():
    a = decl("a", {"q_one": {"en": "One?", "ko": "kor"}, "q_two": {"en": "Two?"}})
    b = decl("b", {"q_one": {"en": "Uno?"}, "q_three": {"ko": "kor3"}})
    assert pp.union([a, b], "en") == {"q_one": "Uno?", "q_two": "Two?"}
    assert pp.union([a, b], "ko") == {"q_one": "kor", "q_three": "kor3"}
    assert pp.union([a, b], "fr") == {} and pp.union([], "en") == {}
    assert pp.repeated([a, b]) == ["q_one"] and pp.repeated([a]) == []


def test_a_key_without_text_is_left_out():
    a = decl("a", {"q_slots_only": {"slots": ["x"]}, "q_ok": {"en": "ok"}})
    assert pp.union([a], "en") == {"q_ok": "ok"}
