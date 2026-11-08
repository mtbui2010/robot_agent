"""Round 16: a third language as DATA ONLY. Vietnamese is shipped as one file, ``lang/vi.json``; no code of the package names
it. These tests show that the mechanisms the English and Korean packs drive work for a language the code knows nothing about:
the into / on-top markers, the open verb, the self-correction markers, yes / no and the polite words, the control words
(stop / pause / resume), "I do not know", the speaker's words, room and object aliases, the safety words, and the places
that used to assume a non-Latin script (``language.localized``, the translate trigger, ``speech.guard``, whole-word place
matching), checked on the helpers and through the real ``AssistLoop.run_blocking`` against the symbolic FakeRobot with a scripted
LLM and a scripted user.

THE VOCABULARY IS DRAFTED BY THE MODEL AND HAS NOT BEEN CHECKED BY A NATIVE SPEAKER (the pack says so in its ``review`` key).
A wrong word makes a test here pass on a draft; a native speaker should read ``lang/vi.json`` and these requests before any
number is reported for the language. Synthetic ENV and requests written for these tests (no benchmark text).
"""
import json
import re

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, steps
from robot_agent.core.planning import (dialog_converge, dialog_unstick, goal_fix, langdata, language, lexicon, persons,
                                       speech, understand as U)
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.safety import hazards
from robot_agent.core.safety.place_class import place_class
from robot_agent.interact import dialog as D, intent_router as R

VENV = {
    "fridge@kitchen": {"aliases": ["tủ lạnh"], "openable": True, "loc": {"x": 4.0, "y": 1.0}},
    "table@kitchen": {"aliases": ["bàn ăn"], "loc": {"x": 3.0, "y": 1.0}},
    "sofa@living room": {"aliases": ["ghế sofa"], "loc": {"x": 1.0, "y": 1.0}},
    "shelf@hall": {"aliases": ["kệ"], "loc": {"x": 6.0, "y": 1.0}},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["tôi", "anna"], "loc": {"x": 1.2, "y": 1.0}},
    "ben@kitchen": {"person": True, "aliases": ["ben", "bố"], "loc": {"x": 3.2, "y": 1.0}},
}
HOUSE = {"cup": "table@kitchen"}


@pytest.fixture
def env():
    return VENV


@pytest.fixture
def pl():
    return Places(VENV)


# ── the pack is data ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_pack_is_valid_data_and_says_who_wrote_it():
    pack = langdata.get("vi")
    assert pack["lang"] == "vi" and pack["name"] == "Vietnamese" and pack["latin_script"] is True
    assert "DRAFTED BY THE MODEL" in pack["review"] and "NOT CHECKED BY A NATIVE SPEAKER" in pack["review"]
    for key, value in pack["patterns"].items():                     # every regex compiles (with a sample name filled in)
        for p in ([value] if isinstance(value, str) else value):
            re.compile(p.replace("{name}", "bố"))
    for table in pack["words"].values():
        for p in table:
            re.compile(p)
    re.compile(pack["detect_marks"])


def test_no_pack_key_is_unknown_to_the_loader():
    """Every top-level key of the pack is one the loader reads (a typo would silently do nothing)."""
    known = {"lang", "name", "register", "review", "latin_script", "non_ascii_min", "detect_marks", "words", "answer_unsure",
             "articles", "plural_rules", "patterns", "grammar", "dialogue", "dialogue_ext", "object_aliases", "script",
             "particles", "text_fallback", "guard"}
    assert set(langdata.get("vi")) <= known


def test_the_same_pack_keys_as_the_other_languages_are_read_for_it():
    assert lexicon.PUTIN.get("vi") and lexicon.ON_TOP.get("vi") and lexicon.OPEN_WORDS.get("vi") and lexicon.TELL.get("vi")
    assert lexicon.has(lexicon.PUTIN, "bỏ cốc vào tủ lạnh") and lexicon.has(lexicon.ON_TOP, "đặt cốc lên trên kệ")
    assert lexicon.has(lexicon.HAND, "đưa tận tay tôi") and lexicon.has(lexicon.RECIPIENT, "mang cho tôi cái cốc")
    assert lexicon.has(lexicon.TELL, "nhắn bố là ăn cơm đi") and lexicon.has(lexicon.OPEN_WORDS, "mở tủ lạnh")
    assert lexicon.lang_of("Mở tủ lạnh") == "vi" and lexicon.lang_of("open the fridge") == "en"
    assert lexicon.lang_of("냉장고를 열어") == "ko"


def test_adding_the_pack_did_not_change_how_english_and_korean_read():
    """The marker tables are unions over the packs: English and Korean sentences give the answers they gave before."""
    pl = Places({"fridge@kitchen": {"aliases": ["fridge", "냉장고"], "openable": True}})
    assert goal_fix.says_into("fridge@kitchen", "put the milk in the fridge", pl)
    assert goal_fix.says_into("fridge@kitchen", "냉장고 안에 넣어 주세요", pl)
    assert not goal_fix.says_into("fridge@kitchen", "put the milk on top of the fridge", pl)
    assert D.read_yes_no("yes please").kind == "yes" and D.read_yes_no("네").kind == "yes" and D.read_yes_no("아니요").kind == "no"
    assert R.classify("stop now").intent == R.Intent.STOP and R.classify("멈춰").intent == R.Intent.STOP


# ── the markers, on the helpers ───────────────────────────────────────────────────────────────────────────────────────
def test_into_and_on_top_markers(pl):
    assert goal_fix.says_into("fridge@kitchen", "Bỏ cái cốc vào tủ lạnh nhé", pl)
    assert goal_fix.says_into("fridge@kitchen", "Để sữa trong tủ lạnh", pl)
    assert not goal_fix.says_into("fridge@kitchen", "Đặt cốc lên trên tủ lạnh", pl)
    assert goal_fix.says_on("fridge@kitchen", "Đặt cốc lên trên tủ lạnh", pl)
    assert not goal_fix.says_on("fridge@kitchen", "Bỏ cái cốc vào tủ lạnh nhé", pl)
    goals = [{"type": "on", "object": "cốc", "from": "", "to": "fridge@kitchen"}]
    got, notes = goal_fix.kind_from_destination(goals, "Bỏ cái cốc vào tủ lạnh nhé", pl)
    assert got[0]["type"] == "in" and notes
    got, _ = goal_fix.kind_from_destination(goals, "Đặt cái cốc lên trên tủ lạnh nhé", pl)
    assert got[0]["type"] == "on"


def test_a_place_name_with_marks_is_a_whole_word(pl):
    assert [m[2] for m in U.place_mentions("đặt cốc lên bàn ăn nhé", pl)] == ["bàn ăn"]
    assert U.place_mentions("cái bàn ăng ten", pl) == []             # not inside another word
    assert U._find("kệ", "để lên kệ", "") == [(7, 9)] and U._find("kệ", "kệch cỡm", "") == []


def test_the_open_verb_and_the_close_verb(pl):
    goals = [{"type": "on", "object": "fridge@kitchen", "from": "", "to": "table@kitchen"}]
    got, dropped = goal_fix.drop_invented(goals, "mở tủ lạnh ra", pl)
    assert [g["type"] for g in got] == ["open"] and dropped
    got, _ = goal_fix.drop_invented(goals, "đóng tủ lạnh lại", pl)
    assert [g["type"] for g in got] == ["close"]


def test_the_self_correction_marker_takes_the_mention_before_it_back(pl):
    t = U._norm("đặt cốc lên ghế sofa, à không, lên bàn ăn")
    ms = U.place_mentions(t, pl)
    assert [m[2] for m in ms] == ["ghế sofa", "bàn ăn"]
    assert [m[2] for m in U._drop_corrected(t, ms, U._corr_of(pl))] == ["bàn ăn"]
    t2 = U._norm("đặt cốc lên ghế sofa, nhầm rồi, lên bàn ăn")
    assert [m[2] for m in U._drop_corrected(t2, U.place_mentions(t2, pl), U._corr_of(pl))] == ["bàn ăn"]
    t3 = U._norm("đặt cốc lên ghế sofa, rồi lên bàn ăn")                 # no marker: both stay
    assert len(U._drop_corrected(t3, U.place_mentions(t3, pl), U._corr_of(pl))) == 2


def test_yes_no_and_the_polite_words():
    assert D.read_yes_no("Dạ vâng ạ").kind == "yes" and D.read_yes_no("Đúng rồi").kind == "yes"
    r = D.read_yes_no("Không ạ")
    assert (r.kind, r.rest) == ("no", "")                             # the polite word is manners, not a correction
    r = D.read_yes_no("không, cái cốc xanh ạ")
    assert (r.kind, r.rest) == ("no", "cái cốc xanh")
    assert D.read_yes_no("ừm").kind == "unclear" and D.read_yes_no("vang").kind == "unclear"   # noise / a near-miss: asked again
    assert D.read_yes_no("cái cốc ở bàn ăn").kind == "correction"
    assert D.read_yes_no("không biết").kind == "no"                   # a "no" word is a "no": the unsure phrase is read elsewhere


def test_control_words_stop_pause_resume_and_their_negation():
    assert R.classify("dừng lại").intent == R.Intent.STOP and R.classify("Dừng lại đi ạ").intent == R.Intent.STOP
    assert R.classify("làm ơn dừng lại").intent == R.Intent.STOP
    assert R.classify("đợi chút").intent == R.Intent.PAUSE and R.classify("tiếp tục đi").intent == R.Intent.RESUME
    assert R.classify("đừng dừng").intent == R.Intent.NEW             # a negated command is not a command
    assert R.classify("dừng ở cửa").intent != R.Intent.STOP           # a sentence that mentions the word is not one either
    assert R.classify("bạn đang làm gì").intent == R.Intent.STATUS    # the keyword fallback


def test_not_sure_is_an_unsure_answer(pl):
    assert dialog_unstick.unsure_answer("Tôi không biết ạ", pl, None, "vi")
    assert dialog_unstick.unsure_answer("không nhớ rõ lắm", pl, None, "vi")
    assert not dialog_unstick.unsure_answer("Ở trên bàn ăn", pl, None, "vi")


def test_speaker_words_room_names_and_object_aliases(pl):
    assert persons.is_speaker_word("tôi") and persons.is_speaker_word("나") and persons.is_speaker_word("me")
    assert dialog_converge.speaker_said(["mang cho tôi cái cốc"]) and not dialog_converge.speaker_said(["mang cho Ben"])
    from robot_agent.core.planning import dialog_ext as DX
    pl.ext = DX.build("vi", pl)
    assert pl.candidates("cốc ở phòng khách", "cốc ở phòng khách") == ["@living room"]
    assert goal_fix.alias_groups(None, "vi") == [["cốc", "ly"], ["thìa", "muỗng"]]
    assert goal_fix.alias_groups(None, "en") == [] and goal_fix.alias_groups(None, "ko") == []
    assert goal_fix._is_request("nhắn bố là ăn cơm đi", "x", ["bố"])


def test_the_safety_words(pl):
    assert hazards.hazard_class("thuốc tẩy") == "cleaning_chemical" and hazards.hazard_class("bột giặt") == "cleaning_chemical"
    assert place_class(pl, "fridge@kitchen") == "food_storage"
    assert hazards.check_goal({"type": "in", "object": "thuốc tẩy", "to": "fridge@kitchen"}, pl)
    assert hazards.check_goal({"type": "in", "object": "cốc", "to": "fridge@kitchen"}, pl) is None
    r = U.typed_refusal("unsafe", "", None, "hãy ném cái cốc ra cửa sổ", True)
    assert r.misheard is False                                         # the words name something unsafe: a real refusal
    assert U.typed_refusal("unsafe", "", None, "mang cốc nước cho tôi", True).misheard is True


# ── the places that assumed a non-Latin script ────────────────────────────────────────────────────────────────────────
def test_places_that_assumed_a_non_latin_script_are_pack_driven(pl):
    ok, en = "Tôi sẽ mang cốc nước đến cho bạn ạ", "I will bring the cup to you"
    assert language.localized(ok, "vi") and not language.localized(en, "vi")           # the pack's non_ascii_min, flag or not
    assert not language.localized(ok, "ko") and language.localized("컵을 가져다 드릴게요", "ko")
    assert language.LANGS["vi"] == "Vietnamese" and "polite" in language.REGISTER["vi"]
    assert langdata.has_marks("tủ lạnh") and not langdata.has_marks("fridge") and langdata.has_marks("냉장고")
    assert speech.guard("bất kỳ câu nào", "vi") is None and speech.guard("I will bring it", "vi") is None   # fail open: no script
    assert speech.guard("tên", "vi", keep=["Anna"]) == "the name 'Anna' is missing"
    assert speech.guard("컵", "ko", keep=()) is None and speech.guard("cup", "ko") is not None       # Korean is still guarded
    assert speech._script_ok("tủ lạnh", "vi") and speech._script_ok("냉장고", "ko") and not speech._script_ok("fridge", "ko")
    assert speech._script_ok("fridge", "en") and not speech._script_ok("tủ lạnh", "en")
    assert speech.say_name(pl, "fridge@kitchen", "vi") == "tủ lạnh"


def test_the_translate_trigger_reads_the_marks_of_a_latin_script_language(make_assist):
    """A Vietnamese request carries marks, so the language layer translates it (``input_pivot``); an English one costs no call."""
    robot = FakeRobot(VENV, dict(HOUSE))
    llm = FakeLLM(default="Open the fridge.")
    loop = make_assist(robot, llm, ScriptedPlanner([]), input_pivot="translate")
    h = loop.language.hear("Mở tủ lạnh giúp tôi", "vi")
    assert h.english == "Open the fridge." and any("from Vietnamese into plain English" in str(c) for c in llm.calls)
    n = len(llm.calls)
    assert loop.language.hear("Open the fridge", "vi").english == "" and len(llm.calls) == n     # no marks, no call


# ── through the real AssistLoop ───────────────────────────────────────────────────────────────────────────────────────
OPEN_FRIDGE = {"status": "clear", "reason": "", "question": "",
               "goals": [{"type": "open", "object": "fridge@kitchen", "from": "", "to": "fridge@kitchen"},
                         {"type": "on", "object": "cup", "from": "table@kitchen", "to": "shelf@hall"}]}


def _first_understand(evs):
    return next(e for e in evs if e["event"] == "understand")


def _run(make_assist, task, lang="vi", answers=(), llm=None, **opts):
    robot = FakeRobot(VENV, dict(HOUSE), answers=list(answers))
    opts.setdefault("confirm_task", False)
    loop = make_assist(robot, llm or FakeLLM(default=OPEN_FRIDGE), ScriptedPlanner(steps("MoveTo table@kitchen", "Find cup")),
                       **opts)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return [g["type"] for g in _first_understand(evs)["goals"]], robot, evs


def test_kind_anchor_keeps_an_open_goal_the_words_ask_for_and_drops_the_unasked_one(make_assist):
    assert "open" in _run(make_assist, "Mở tủ lạnh rồi chuyển cái cốc lên kệ giúp tôi.", kind_anchor="1")[0]
    assert "open" not in _run(make_assist, "Chuyển cái cốc ở bàn ăn lên kệ giúp tôi.", kind_anchor="1")[0]


def test_kind_anchor_ask_reads_back_the_doubt(make_assist):
    kinds, robot, _ = _run(make_assist, "Chuyển cái cốc ở bàn ăn lên kệ giúp tôi.", kind_anchor="1", kind_anchor_ask="1",
                           answers=["Dạ vâng ạ"], confirm_task=True)
    assert "open" in kinds


def test_a_self_correction_in_the_request_is_not_asked_about(make_assist):
    """"on the sofa, no wait, on the dining table": the sofa the person took back is not a place left unused."""
    shelf = {"status": "clear", "reason": "", "question": "",
             "goals": [{"type": "on", "object": "cup", "from": "table@kitchen", "to": "shelf@hall"}]}
    text = "Đặt cốc lên ghế sofa, à không, lên kệ nhé."
    kinds, robot, evs = _run(make_assist, text, llm=FakeLLM(default=shelf))
    assert kinds == ["on"] and not any("sofa" in str(q) for q in robot.asked)
    plain = "Đặt cốc lên ghế sofa, rồi lên kệ nhé."                       # no marker: the sofa is named and unused
    issues = U.find_issues(shelf["goals"], plain, Places(VENV))
    assert any(i["kind"] == "place" and "sofa" in str(i["keys"]) for i in issues)
    assert not any(i["kind"] == "place" for i in U.find_issues(shelf["goals"], text, Places(VENV)))


PACK_VI = {"q_confirm": "{summary} Đúng không ạ?", "rb_will": "{what}.", "sep_then": ", rồi ", "rb_go": "đi đến {place}",
           "q_unclear_task": "Xin lỗi, tôi chưa hiểu. Bạn nói lại được không ạ?", "q_which_part": "Phần nào chưa đúng ạ?",
           "q_what_do": "Xin lỗi. Vậy tôi cần làm gì ạ?", "say_untranslated": "Xin lỗi, tôi chưa nói được câu này ạ."}
GO_TABLE = {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "go", "object": "", "from": "", "to": "table@kitchen"}]}


class DownTranslator(FakeLLM):
    """Understands the request; the translator (the model that writes the robot's words) is down: every line must come from the pack."""

    def chat(self, prompt="", format=None, **kw):
        if "Translate into natural" in prompt:
            self.calls.append((prompt, format))
            raise ConnectionError("translator down")
        return super().chat(prompt=prompt, format=format, **kw)


UNSTICK = {"fallback_words": 5, "fallback_chars": 24, "restate_frac": 0.6, "restate_word_len": 3, "restate_min_words": 4,
           "restate_min_bigrams": 6}


def _talk(make_assist, answers, task="Hãy đi thẳng đến bàn ăn ngay bây giờ nhé"):
    robot = FakeRobot(VENV, {}, answers=list(answers))
    loop = make_assist(robot, DownTranslator(default=GO_TABLE), ScriptedPlanner(steps("MoveTo table@kitchen")), health_wait_s=0,
                       confirm_task=True, dialog_unstick="1")
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, unstick=UNSTICK)
    loop.robot.i18n.phrases.update({k: {"vi": v} for k, v in PACK_VI.items()})
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang="vi")]
    return loop, robot, evs


def test_a_read_back_is_said_from_the_pack_and_a_polite_yes_confirms_it(make_assist):
    loop, robot, evs = _talk(make_assist, ["Dạ vâng ạ"])
    assert robot.asked == ["đi đến bàn ăn. Đúng không ạ?"]            # said in the pack's words, no model call for it
    assert done(evs)["status"] == "success"


def test_a_polite_no_then_the_restated_request_is_read_back_again(make_assist):
    loop, robot, evs = _talk(make_assist, ["Không ạ", "Tôi bảo là hãy đi thẳng đến bàn ăn ngay bây giờ nhé", "Dạ đúng rồi ạ"])
    assert robot.asked[0] == "đi đến bàn ăn. Đúng không ạ?" and len(robot.asked) >= 2
    assert not any(re.search(r"[A-Za-z]{4,} (?:the|you|is)\b", q) for q in robot.asked)       # no English line reaches the user
    assert done(evs)["status"] == "success"


# ── the generalisation rule: a language with no pack gets nothing from the pack rules ───────────────────────────────────
def test_a_language_without_a_pack_gets_no_marker_from_the_vietnamese_pack(pl):
    """"qq" has no pack: the words of Vietnamese are not read for it, and the English / Korean ones are not either; only
    the held English reading of an answer is read with the English markers."""
    assert langdata.get("qq") == {}
    assert langdata.rx("into_before", langs=["qq"]) is None and langdata.pattern_list("self_correction", langs=["qq"]) == []
    assert langdata.dialogue_all("yes", langs=["qq"]) == [] and langdata.grammar("qq", "sep_or") is None
    assert not language.localized("bất kỳ", "qq") is False                # no pack: nothing can show it is not localized
    assert langdata.script_rx("vi") is None and langdata.in_script("tủ lạnh", "vi") is False


def test_dropping_the_vietnamese_pack_leaves_english_and_korean_alone(monkeypatch):
    """The pack is data: take it away and the other two languages behave the same."""
    before = (D.read_yes_no("no, the sofa"), D.read_yes_no("아니요"), lexicon.has(lexicon.PUTIN, "put it in the box"))
    monkeypatch.setattr(langdata, "_FILES", frozenset({"en", "ko", "ja", "zh"}))
    monkeypatch.setitem(langdata._CACHE, "vi", {})
    assert langdata.languages() == ("en", "ja", "ko", "zh")
    after = (D.read_yes_no("no, the sofa"), D.read_yes_no("아니요"), lexicon.has(lexicon.PUTIN, "put it in the box"))
    assert before == after


def test_the_vietnamese_pack_json_is_plain_utf8_data():
    import os
    path = os.path.join(os.path.dirname(langdata.__file__), "lang", "vi.json")
    raw = open(path, encoding="utf-8").read()
    assert json.loads(raw)["lang"] == "vi" and "�" not in raw

from robot_agent.core.planning import dialog_hold as DH  # noqa: E402


# round 18 (dialog_hold): evidence in her words reads the pack only (whole tokens, articles from the pack)
def test_dialog_hold_evidence_by_the_pack_only():
    chiec, coc, cai, chen = "chi\u1ebfc", "c\u1ed1c", "c\u00e1i", "ch\u00e9n"          # written as escapes: the vi data lives in lang/vi.json
    assert DH.token_forms(f"{chiec} {coc}", "vi") == {chiec, coc}
    g = {"type": "in", "object": "glass", "said": f"{cai} {coc}", "to": "fridge@kitchen"}
    assert DH.object_score(g, [f"{chiec} {coc}"], "vi") == 1 and DH.object_score(g, [f"{coc} {chen}"], "vi") == 1
    assert DH.object_score(g, [coc + "c"], "vi") == 0                                      # whole tokens only


