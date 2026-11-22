"""Step S04: the phrase tables are pack data (packs/core/phrases.yaml, packs/household/phrases.yaml) and ``speech.CORE_PHRASES`` is a view.

Proof of "behaviour identical": the two files say the 62 + 3 old texts character for character (the reference copy is
tests/legacy_ref/speech_phrases.py, never edited), no key is in both files, the view equals the old constant, and ``speech.phrase``
gives the same answer as the old function over a grid of robots, keys and languages. The lookup order is pinned: the robot's own
i18n, the language pack's speech block, the pack chain, the core English.
"""

from __future__ import annotations

import importlib.util
import string
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from robot_agent.core.pack import Pack, default_pack, phrases as pack_phrases
from robot_agent.core.planning import budget, langdata, speech
from robot_agent.packs import ROOT

_spec = importlib.util.spec_from_file_location("legacy_speech_phrases", Path(__file__).parent / "legacy_ref" / "speech_phrases.py")
OLD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OLD)

EXTRA = {"help.pick": OLD.HELP_PICK, "help.move": OLD.HELP_MOVE, "text.llm_budget": OLD.BUDGET_TEXT}


def _file(pack: str) -> dict:
    return yaml.safe_load((Path(ROOT) / pack / "phrases.yaml").read_text(encoding="utf-8"))["phrases"]


def old_phrase(robot, key, lang):
    """speech.phrase as it was at 8002347 (the lookup without the pack chain), written against the current helpers."""
    lg = (lang or "en")[:2]
    by = ((getattr(getattr(robot, "i18n", None), "phrases", None) or {}).get(key) or {}) if robot is not None else {}
    if by.get(lg):
        return by[lg]
    own = langdata.block(lg, "speech").get(key)
    if isinstance(own, str) and own:
        return own
    return OLD.CORE_PHRASES.get(key) if lg == "en" else None


def test_the_two_files_hold_the_old_texts_character_for_character():
    core, house = _file("core"), _file("household")
    assert not set(core) & set(house), "a key is in both files"
    union = {**core, **house}
    assert len(union) == len(OLD.CORE_PHRASES) + 3 == 65
    for key, text in {**OLD.CORE_PHRASES, **EXTRA}.items():
        assert union[key]["en"] == text, key
    assert {k for k in union if "." not in k} == set(OLD.CORE_PHRASES) and {k for k in union if "." in k} == set(EXTRA)


def test_the_core_file_holds_the_generic_keys_and_the_household_file_the_rest():
    core, house = _file("core"), _file("household")
    for key in ("q_confirm", "q_repeat", "say_stopped", "rb_will", "sep_then", "text.llm_budget"):
        assert key in core and key not in house
    for key in ("rb_on", "q_which_drawer", "q_where_put", "help.pick", "help.move"):
        assert key in house and key not in core


def test_every_entry_is_english_text_with_its_slots_declared():
    for pack in ("core", "household"):
        for key, entry in _file(pack).items():
            assert set(entry) <= {"en", "slots"}, (pack, key)
            fields = {f for _, f, _, _ in string.Formatter().parse(entry["en"]) if f}
            assert set(entry.get("slots", [])) == fields, (pack, key)


def test_the_pack_files_carry_a_source_label():
    for name in ("core", "household"):
        head = yaml.safe_load((Path(ROOT) / name / "pack.yaml").read_text(encoding="utf-8"))["pack"]
        assert str(head["source"]).strip()                 # the default label of every entry of the file


def test_the_view_equals_the_old_constants():
    assert speech.CORE_PHRASES == OLD.CORE_PHRASES and len(speech.CORE_PHRASES) == 62
    assert speech.HELP_PICK == OLD.HELP_PICK and speech.HELP_MOVE == OLD.HELP_MOVE and budget.BUDGET_TEXT == OLD.BUDGET_TEXT
    assert all(isinstance(v, str) for v in speech.CORE_PHRASES.values()) and type(speech.CORE_PHRASES) is dict


def test_the_view_is_a_plain_dict_the_caller_can_change_without_touching_the_pack(monkeypatch):
    monkeypatch.setitem(speech.CORE_PHRASES, "rb_go", "head to the {place}")
    assert speech.LocalText("rb_go", {"place": "kitchen"}) == "head to the kitchen"
    assert pack_phrases.text("rb_go") == OLD.CORE_PHRASES["rb_go"]


def test_a_dotted_key_is_not_in_the_view_but_is_read_by_text():
    assert not any("." in k for k in speech.CORE_PHRASES)
    assert pack_phrases.text("help.pick") == OLD.HELP_PICK
    with pytest.raises(KeyError):
        pack_phrases.text("help.pick", "ko")
    with pytest.raises(KeyError):
        pack_phrases.text("no_such_key")


def test_the_default_pack_chain_says_the_old_texts_and_names_its_origin():
    pack = default_pack()
    assert not pack.problems or all(p.kind != "phrases" for p in pack.problems)
    for key, text in {**OLD.CORE_PHRASES, **EXTRA}.items():
        assert pack_phrases.chain_text(pack, key, "en") == text, key
        assert pack_phrases.chain_text(pack, key, "ko") is None, key
    assert pack.origin["phrases"]["q_confirm"] == "core" and pack.origin["phrases"]["rb_on"] == "std"


def _robot(i18n=None, pack=None):
    return SimpleNamespace(i18n=SimpleNamespace(phrases=i18n or {}), pack=pack)


def test_phrase_gives_the_old_answer_for_every_key_language_and_robot():
    robots = [None, _robot(), _robot(pack=default_pack()), _robot({"rb_on": {"ko": "KO-ON", "en": "EN-ON"}}, default_pack()),
              _robot({"q_confirm": {"de": "DE-CONFIRM"}}), SimpleNamespace(i18n=None), SimpleNamespace(pack="not a pack")]
    keys = list(OLD.CORE_PHRASES) + ["no_such_key", ""]
    for robot in robots:
        for lang in ("en", "en-US", "ko", "de", "ja", "zh", "", None):
            for key in keys:
                assert speech.phrase(robot, key, lang) == old_phrase(robot, key, lang), (key, lang)


def test_a_dotted_key_is_answered_by_the_chain_only_for_a_robot_with_a_pack():
    """The one addition: the texts of the dotted keys were not phrases before (nothing asks for them today), so no old answer changes."""
    assert speech.phrase(None, "help.pick", "en") is None and speech.phrase(_robot(), "help.pick", "en") is None
    assert speech.phrase(_robot(pack=default_pack()), "help.pick", "en") == OLD.HELP_PICK


def test_the_lookup_order_is_robot_i18n_then_language_pack_then_pack_chain_then_core_english():
    own = Pack(tables={"phrases": {"x_new": {"en": "chain says {a}", "ko": "chain ko", "slots": ["a"]},
                                   "rb_go": {"en": "head to the {place}"}}})
    robot = _robot({"x_new": {"ko": "robot ko"}}, own)
    assert speech.phrase(robot, "x_new", "ko") == "robot ko"                     # the robot's own i18n first
    assert speech.phrase(_robot(pack=own), "x_new", "ko") == "chain ko"          # then (no language-pack text for the key) the chain
    assert speech.phrase(_robot(pack=own), "x_new", "en") == "chain says {a}"
    assert speech.phrase(_robot(pack=own), "x_new", "de") is None                # the chain has no text in that language: the translator's case
    assert speech.phrase(_robot(pack=own), "rb_go", "en") == "head to the {place}"   # a robot's entry replaces the shipped one
    assert speech.phrase(_robot(pack=own), "q_confirm", "en") == OLD.CORE_PHRASES["q_confirm"]   # the core English last
    block = langdata.block("ko", "speech")
    if block:                                                                     # the language pack's speech block beats the chain
        key, text = next((k, v) for k, v in block.items() if isinstance(v, str) and v)
        shadow = Pack(tables={"phrases": {key: {"ko": "chain text must lose"}}})
        assert speech.phrase(_robot(pack=shadow), key, "ko") == text


def test_chain_text_ignores_what_is_not_a_resolved_pack():
    assert pack_phrases.chain_text(None, "q_confirm", "en") is None
    assert pack_phrases.chain_text(SimpleNamespace(entry=lambda *a: {"en": "x"}), "q_confirm", "en") is None
    assert pack_phrases.chain_text(default_pack(), "", "en") is None


def test_local_text_and_render_read_the_view():
    q = speech.LocalText("q_confirm", {"summary": "I will go."})
    assert str(q) == "I will go. Is that right?"
    assert speech.render(q, "en") == "I will go. Is that right?"
    assert str(speech.LocalText.seq(["a", "b"])) == "a, then b"
