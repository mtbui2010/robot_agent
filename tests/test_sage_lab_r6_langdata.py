"""Lab round 6 (generalisation): what the planner knows about a language is DATA (language packs ``lang/<code>.json``, the
robot pack's i18n, the manifest ``planner`` numbers), never code. Checked on the real path (AssistLoop.run_blocking with the
symbolic FakeRobot and a scripted LLM) and on the helpers: English and Korean read their words from the packs; "qq" and
"zz" are made-up languages (qq: no data at all, so every option does nothing for it; zz: data given, so it works); no
language is Vietnamese. Synthetic ENV and requests written for these tests (no benchmark text)."""
import os
import re
import types

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, steps
from robot_agent.contract.manifest import PLANNER_KEYS, Manifest
from robot_agent.core.planning import exec_integrity, goal_fix, langdata, language, lexicon
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from test_sage_lab_r2_grounding import ENV, HOUSE, OPEN_FRIDGE, G, U, first_understand


@pytest.fixture
def env():
    return ENV


@pytest.fixture
def zz(monkeypatch):
    """"zz": a made-up language whose pack declares open words, an unsure phrase, articles and plural rules."""
    monkeypatch.setitem(langdata._CACHE, "zz", {
        "lang": "zz", "name": "Zeezee", "register": "plain spoken Zeezee", "non_ascii_min": 0.25,
        "words": {"open_words": [r"\bapri"], "tell": [r"\bdiku\b"]}, "answer_unsure": ["no sabo"],
        "articles": ["la", "el"], "plural_rules": [["ozi", "o"]]})


# ── the packs ──
def test_packs_ship_for_english_and_korean_and_never_for_vietnamese():
    assert {"en", "ko"} <= set(langdata.languages()) and "vi" not in langdata.languages()
    assert lexicon.OPEN_WORDS.langs() == ("en", "ko") and lexicon.PUTIN.langs() == ("en", "ko")
    assert langdata.get("vi") == {} and langdata.get("qq") == {} and langdata.words("qq", "open_words") == []


def test_no_file_of_the_package_claims_vietnamese():
    root = os.path.join(os.path.dirname(__file__), "..")
    vi = re.compile("[ăơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]|\\bvi\\b|Vietnam", re.I)
    hits = []
    for base in ("robot_agent", "tests", "docs"):
        for d, _dirs, files in os.walk(os.path.join(root, base)):
            for f in files:
                if f.endswith((".py", ".json", ".md", ".yaml")) and f != os.path.basename(__file__):
                    text = open(os.path.join(d, f), encoding="utf-8").read()
                    hits += [f"{f}: {m.group(0)}" for m in vi.finditer(text)][:1]
    assert not hits, hits


def test_a_table_reads_the_pack_and_the_robots_words():
    assert lexicon.has(lexicon.PUTIN, "put it into the box") and lexicon.has(lexicon.PUTIN, "상자 안에 넣어줘")
    assert not lexicon.has(lexicon.PUTIN, "mete la taza")
    robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={"put_in_words": {"zz": "mete"}}))
    assert lexicon.has(lexicon.PUTIN, "mete la taza", robot, "put_in_words")           # the pack's words for any language
    assert lexicon.particles("ko") and lexicon.particles("qq") == ()
    assert lexicon.lang_of("컵을 가져와") == "ko" and lexicon.lang_of("bring the cup") == "en"


def test_a_language_pack_gives_words_for_a_third_language(zz):
    assert lexicon.TELL.get("zz") == [r"\bdiku\b"] and lexicon.has(lexicon.TELL, "diku a ben")
    assert r"\bapri" in goal_fix.open_words_for("zz") and goal_fix.open_words_for("qq") is None


# ── kind_anchor, on the real path ──
def _run(make_assist, tmp_path, task, lang, ask="0", answers=(), phrases=None):
    robot = FakeRobot(ENV, dict(HOUSE), answers=list(answers))
    loop = make_assist(robot, FakeLLM(default=OPEN_FRIDGE), ScriptedPlanner(steps("MoveTo table@kitchen", "Find cup")),
                       kind_anchor="1", kind_anchor_ask=ask, confirm_task=False)
    if phrases:
        loop.robot.i18n.phrases.update(phrases)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return [g["type"] for g in first_understand(evs)["goals"]], robot, evs


def test_kind_anchor_with_no_data_for_the_language_does_nothing(make_assist, tmp_path):
    assert "open" in _run(make_assist, tmp_path, "Qa tazu na kele, sa ri.", "qq")[0]


def test_kind_anchor_with_language_pack_data_works(make_assist, tmp_path, zz):
    assert "open" in _run(make_assist, tmp_path, "Apri la nevera y pon la taza en el estante.", "zz")[0]
    assert "open" not in _run(make_assist, tmp_path, "Pon la taza en el estante.", "zz")[0]


def test_kind_anchor_with_robot_pack_data_works(make_assist, tmp_path):
    pack = {"open_words": {"qq": "dasa"}}
    assert "open" in _run(make_assist, tmp_path, "Dasa la nevera, pon la taza.", "qq", phrases=pack)[0]
    assert "open" not in _run(make_assist, tmp_path, "Pon la taza.", "qq", phrases=pack)[0]


PARAPHRASE = "Pop the fridge door, then take the cup from the table to the shelf."


def test_an_english_paraphrase_is_dropped_without_the_ask_option(make_assist, tmp_path):
    kinds, robot, _ = _run(make_assist, tmp_path, PARAPHRASE, "en")
    assert "open" not in kinds and not any("not sure you asked" in q for q in robot.asked)


def test_kind_anchor_ask_keeps_the_goal_and_the_existing_read_back_asks(make_assist, tmp_path):
    kinds, robot, evs = _run(make_assist, tmp_path, PARAPHRASE, "en", ask="1", answers=["yes"])
    assert "open" in kinds
    asked = [q for q in robot.asked if "not sure you asked me to open the fridge in the kitchen" in q]
    assert len(asked) == 1 and asked[0].endswith("Is that right?") and "I am not sure I understood" in asked[0]


def test_kind_anchor_ask_is_quiet_when_the_open_word_is_there(make_assist, tmp_path):
    kinds, robot, _ = _run(make_assist, tmp_path, "Open the fridge, then take the cup from the table to the shelf.", "en",
                           ask="1")
    assert "open" in kinds and not any("not sure you asked" in q for q in robot.asked)


def test_kind_anchor_ask_with_no_data_asks_nothing(make_assist, tmp_path):
    kinds, robot, _ = _run(make_assist, tmp_path, "Qa tazu na kele, sa ri.", "qq", ask="1")
    assert "open" in kinds and not any("not sure you asked" in q for q in robot.asked)


def test_the_ask_option_is_registered_and_off_by_default(make_assist, monkeypatch):
    monkeypatch.delenv("ROBOT_AGENT_KIND_ANCHOR_ASK", raising=False)
    assert "kind_anchor_ask" in ASSIST_OPTIONS and "kind_anchor_ask" in PLANNER_KEYS
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]))
    assert loop.cfg["kind_anchor_ask"] is False


# ── plural and article rules, the model's own question ──
def test_articles_and_plural_rules_are_pack_data(zz):
    assert goal_fix._plain("The red mugs") == "red mugs" and goal_fix._singular("mailboxes") == "mailbox"
    assert goal_fix._plain("la taza", "zz") == "taza" and goal_fix._plain("the mug", "qq") == "the mug"
    assert goal_fix._singular("tazozi", "zz") == "tazo" and goal_fix._singular("mugs", "qq") == "mugs"
    assert goal_fix.fold_rules(None, "zz") == [("ozi", "o")] and goal_fix.fold_rules(None, "qq") == []
    robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={"plural_fold": {"qq": "xa>"}}))
    assert goal_fix.fold_rules(robot, "qq") == [("xa", "")]


def test_the_models_where_question_needs_data_for_the_prompt_language(monkeypatch):
    u = {"status": "ambiguous", "reason": "", "question": "Where is the cup?", "goals": []}
    assert goal_fix.abstains_on_location(u) == ["cup"]
    monkeypatch.setattr(langdata, "PROMPT_LANG", "qq")
    assert goal_fix.abstains_on_location(u) is None


# ── localized, translate targets ──
def test_localized_and_translate_targets_come_from_the_packs(zz):
    assert language.LANGS["ko"] == "Korean" and "vi" not in language.LANGS and "qq" not in language.LANGS
    assert language.localized("컵을 가져다 주세요", "ko") and not language.localized("bring the cup", "ko")
    assert language.localized("anything at all", "qq") and language.localized("anything at all", "en")
    t = "tazú kélé sá"                                      # about a quarter of the letters carry a mark
    assert not language.localized(t, "zz") and language.localized(t, "zz", per_language=True)
    assert "zz" in language.LANGS and language.REGISTER["zz"] == "plain spoken Zeezee"


# ── the manifest numbers ──
def test_manifest_numbers_are_validated():
    ok = Manifest.from_dict({"robot": "toy", "planner": {"unstick": {"fallback_words": 5, "restate_frac": 0.6},
                                                         "recovery": {"return_tries": 2}, "kind_anchor_ask": 1}})
    assert ok.planner["unstick"]["fallback_words"] == 5 and "unstick" in PLANNER_KEYS
    for bad in ({"unstick": {"fallback_words": "five"}}, {"unstick": {"nope": 1}}, {"recovery": {"return_tries": 0}},
                {"recovery": {"return_tries": 1.5}}):
        with pytest.raises(Exception):
            Manifest.from_dict({"robot": "toy", "planner": bad})


def test_exec_integrity_return_tries_has_no_default():
    assert exec_integrity.return_tries(None) == 0 and exec_integrity.return_tries(0) == 0 and exec_integrity.return_tries("3") == 0
    assert exec_integrity.return_tries(3) == 3
    assert langdata.number({"a": 1}, "a") == 1.0 and langdata.number({"a": True}, "a") is None and langdata.number(None, "a", 7) == 7

