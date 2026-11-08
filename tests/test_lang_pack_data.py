"""Round 16: the language literals of the core moved into the packs. Two kinds of check.

1. Golden: the data of the English and Korean packs equals the literals that used to be written in the code (copied here
   as the golden text), so the move changed nothing. The offline replay of the recorded episodes is the proof on the real
   path; these are the quick guard.
2. The settings that replaced constants: ``planner.prompt_lang`` (the language of the core's prompts, default English) and
   ``planner.restates`` (the numbers of "the answer says the request again"), through the real ``AssistLoop``.
"""
import re

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner
from robot_agent.contract.manifest import PLANNER_KEYS, Manifest
from robot_agent.core.planning import goal_fix, langdata, understand as U
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.interact import dialog as D


@pytest.fixture
def two_packs(monkeypatch):
    """Only the two packs the golden literals knew (a shipped pack of another language is left out of the table)."""
    monkeypatch.setattr(langdata, "_FILES", frozenset({"en", "ko"}))
    monkeypatch.setattr(langdata, "_CACHE", {k: v for k, v in langdata._CACHE.items() if k in ("en", "ko")})
    return langdata


# the literals as they were written in the code before the move
OLD = {
    "into_before": r"\b(?:in|into|inside)\s+(?:the\s+|my\s+|your\s+|a\s+|our\s+|her\s+|his\s+)?(?:[\w'-]+\s+){0,2}$",
    "ontop_before": r"\bon\s+top\s+of\s+(?:the\s+|my\s+|your\s+)?(?:[\w'-]+\s+){0,2}$|\bonto\s+(?:the\s+)?(?:[\w'-]+\s+){0,2}$",
    "on_before": r"\b(?:on|onto|atop)\s+(?:top\s+of\s+)?(?:the\s+|my\s+|your\s+|a\s+|our\s+)?(?:[\w'-]+\s+){0,2}$",
    "into_after": r"^\s*(?:의\s*)?(?:안|속|안쪽)|^(?:에|에다|에다가)?\s*(?:\S+\s+){0,2}?\S*넣",
    "ontop_after": r"^\s*(?:의\s*)?위",
    "from_after": r"(?:에서|에게서|한테서|부터|으로부터|로부터)",
    "answer_tail": r"(?:에서|에|이요|요|이에요|예요|있어요|요\.)$",
}
OLD_CORR = r"(?:\.\.\.|,|-)?\s*\b(?:no wait|wait,? no|no,? i mean|i mean|actually|sorry|rather)\b"
OLD_OPEN, OLD_CLOSE = r"\bopen\b|열어|열어\s*줘", r"\b(?:close|shut)\b|닫아"
OLD_PART = ("이랑", "에서", "한테", "에게", "으로", "까지", "부터", "하고", "에다", "에", "을", "를", "이",
            "가", "은", "는", "께", "로", "도", "만", "의", "과", "와", "랑", "위", "안", "옆", "쪽")
OLD_YES = r"\b(yes|yeah|yep|ok|okay|right|correct|sure|go ahead|네|예|응|맞아|맞아요|좋아)\b"
OLD_NO = r"\b(no|nope|not|wrong|아니|아니요|아뇨)\b"
OLD_NON_LATIN = "[가-힣぀-ヿ㐀-䶿一-鿿Ѐ-ӿ]"


def _joined(key, langs=("en", "ko")):
    return langdata.rx(key, langs=langs).pattern


@pytest.mark.parametrize("key", sorted(OLD))
def test_marker_patterns_are_the_old_literals(two_packs, key):
    assert langdata.pattern_list(key) == [OLD[key]]


def test_self_correction_open_close_and_particles_are_the_old_literals(two_packs):
    assert langdata.pattern_list("self_correction") == [OLD_CORR]
    assert U.default_corr().pattern == f"{OLD_CORR}" and U.default_corr().flags & re.I
    assert langdata.pattern_list("open_verb") == [r"\bopen\b", r"열어|열어\s*줘"]
    assert langdata.pattern_list("close_verb") == [r"\b(?:close|shut)\b", r"닫아"]
    assert tuple(langdata.grammar("ko", "noun_particles")) == OLD_PART and langdata.grammar("ko", "verb_noun_suffix") == "(?:하|해|했|할|한|합|함)"
    old_rx = "|".join(sorted(OLD_PART, key=len, reverse=True))
    assert U._particle_rx("ko") == old_rx


def test_yes_no_and_script_literals_are_the_old_ones(two_packs):
    yes = D._words_rx("yes").pattern.replace("go\\ ahead", "go ahead")
    no = D._words_rx("no").pattern
    assert yes == OLD_YES and no == OLD_NO
    assert langdata.get("ko")["detect_marks"] == OLD_NON_LATIN
    assert langdata.get("ko")["guard"]["forbid"] == "[぀-ヿ㐀-䶿一-鿿]|[Ѐ-ӿ]"
    assert langdata.get("ko")["grammar"]["sep_or"] == " 아니면 " and langdata.get("en")["grammar"]["sep_or"] == " or "


def test_the_refusal_marks_are_the_old_regexes(two_packs, household):
    old_safety = re.compile(r"\b(?:knife|knives|blade|scissors|razor|fire|flame|stove|burn|hot|boil|window|throw|"
                            r"hit|hurt|poison|bleach|medicine|pills?|child|baby|kid|outside)\b|칼|불|창문|던지|가위|독|약|"
                            r"아이", re.I)
    old_cap = re.compile(r"\b(?:throw|pour|cook|wash|washer|climb|window|door|tools?|turn on|switch on|lift|"
                         r"furniture|sofa|bed|leave|outside|clean)\b|던지|붓|요리|씻|세탁|올라|창문|문을|켜", re.I)
    for kind, old in (("safety", old_safety), ("capability", old_cap)):
        new = U._mark(kind, household)
        for text in ("put the knife away", "칼을 가져와", "bring the cup", "컵을 가져다 줘", "throw it", "창문을 열어", "turn on the light", "불 켜줘"):
            assert bool(new.search(text)) == bool(old.search(text)), (kind, text)


# ── the settings that replaced constants ──
def test_the_prompt_language_defaults_to_english_and_can_be_set(monkeypatch):
    monkeypatch.setattr(langdata, "_PROMPT_LANG", None)
    assert langdata.prompt_lang() == "en" and langdata.is_prompt_lang("en") and langdata.is_prompt_lang("EN-us")
    assert not langdata.is_prompt_lang("ko")
    assert langdata.set_prompt_lang("ko") == "ko" and langdata.prompt_lang() == "ko" and langdata.is_prompt_lang("ko")
    assert langdata.set_prompt_lang("") == "en" and langdata.set_prompt_lang("x1") == "en"


def test_prompt_lang_is_a_manifest_key_and_a_run_option(make_assist, monkeypatch, env):
    monkeypatch.setattr(langdata, "_PROMPT_LANG", None)
    monkeypatch.delenv("ROBOT_AGENT_PROMPT_LANG", raising=False)
    assert "prompt_lang" in PLANNER_KEYS and "prompt_lang" in ASSIST_OPTIONS
    assert Manifest.from_dict({"robot": "toy", "planner": {"prompt_lang": "ko", "restates": {"frac": 0.5}}}).planner["prompt_lang"] == "ko"
    for bad in ({"prompt_lang": "korean"}, {"prompt_lang": 5}, {"restates": {"nope": 1}}, {"restates": {"frac": 2}},
                {"restates": {"word_len": 0}}):
        with pytest.raises(Exception):
            Manifest.from_dict({"robot": "toy", "planner": bad})
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["prompt_lang"] == ""                                  # default: the pack default, English
    loop = mk(prompt_lang="qq")
    assert loop.cfg["prompt_lang"] == "qq"
    list(loop.run_blocking("go to the table", plan_only=True))
    assert langdata.prompt_lang() == "qq"
    u = {"status": "ambiguous", "reason": "", "question": "Where is the cup?", "goals": []}
    assert goal_fix.abstains_on_location(u) is None                       # no data for the prompt language: the check does nothing
    langdata.set_prompt_lang("")
    assert goal_fix.abstains_on_location(u) == ["cup"]


@pytest.fixture
def env():
    return {"table@kitchen": {"aliases": ["table"], "loc": {"x": 3.0, "y": 1.0}}}


def test_restates_numbers_come_from_the_manifest(make_assist, env):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]))
    loop.original = "please bring the red mug to the kitchen table"
    r = type("R", (), {"task": loop.original})()
    ans = "please bring the red mug"                                      # 5 of 8 words of 3+ letters: 0.625 of them
    assert loop._restates(r, ans)                                          # default 0.6
    loop.robot.manifest.planner = {"restates": {"frac": 0.9}}
    assert not loop._restates(r, ans)
    loop.robot.manifest.planner = {"restates": {"min_words": 30}}
    assert not loop._restates(r, loop.original)                            # the request is shorter than min_words
