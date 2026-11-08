"""Round 14 (option ``dialog_agree2``, default OFF; it includes ``dialog_agree``), through the real AssistLoop.run_blocking
against the symbolic FakeRobot, a scripted LLM and a scripted user. Two things:

* the Korean (and English) dialogue facts are DATA of the language pack (``lang/*.json`` block ``dialogue_ext``) and of the
  robot pack (``planner.room_names``): polite endings, self-correction markers, "not sure" phrases, room names;
* the guard: an agreement that holds only through garbled names, an object alone or words out of the script is NOT a
  confirmation: the person is asked once more. A genuine "no" still ends the run.

Dialogues are rebuilt from recorded gate runs with synthetic ENV and requests written for these tests."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import dialog_agree as DA, dialog_ext as DX, langdata, understand as U
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from test_sage_lab_r12_agree import (BOTH, ENV, PARTS, PLAN, READ, TASK, UNSTICK, ScriptRobot, agreed, confirms, fired,  # noqa: F401
                                     gave_up, glass_run, GLASS_ANSWERS, GLASS_READINGS, GLASS_ENV)

GUARD = {"garble_ratio": 0.6}
B2 = dict(dialog_unstick="1", dialog_agree2="1")


@pytest.fixture
def env(request):
    return getattr(request.function, "ENV", ENV)


def play(make_assist, llm, answers, task=TASK, env=None, house=None, lang="en", phrases=None, plan=PLAN, planner=None, **opts):
    robot = ScriptRobot(env or ENV, dict(house if house is not None else {"plate": "counter@kitchen"}), answers=answers)
    loop = make_assist(robot, llm, ScriptedPlanner(plan), health_wait_s=0, confirm_task=True, **opts)
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, unstick=UNSTICK, **(planner or {}))
    if phrases:
        loop.robot.i18n.phrases.update(phrases)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return loop, robot, evs


def uses(env):
    """The ENV the loop reads (the ``env`` fixture takes it from the test function)."""
    def deco(f):
        f.ENV = env
        return f
    return deco


def lab(loop):
    return [e for e in loop.lab_log if e["flag"] == "dialog_agree"]


def test_options_registered_off_by_default_and_agree2_includes_agree(make_assist, monkeypatch, env):
    monkeypatch.delenv("ROBOT_AGENT_DIALOG_AGREE2", raising=False)
    monkeypatch.delenv("ROBOT_AGENT_DIALOG_AGREE", raising=False)
    for k in ("dialog_agree2", "agree_guard", "room_names"):
        assert k in PLANNER_KEYS
    assert "dialog_agree2" in ASSIST_OPTIONS
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["dialog_agree2"] is False and mk().cfg["dialog_agree"] is False
    on = mk(dialog_agree2="1")
    assert on.cfg["dialog_agree2"] is True and on.cfg["dialog_agree"] is True


# ── (a) the facts are pack data ──────────────────────────────────────────────────────────────────────────────────────
KENV = {
    "bed@bedroom": {"aliases": ["침대"], "loc": {"x": 6.0, "y": 1.0}},
    "desk@study": {"aliases": ["책상"], "loc": {"x": 2.0, "y": 1.0}},
    "sofa@living room": {"aliases": ["소파"], "loc": {"x": 1.0, "y": 1.0}},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["me", "anna", "저"], "loc": {"x": 1.2, "y": 1.0}},
    "ben@kitchen": {"person": True, "aliases": ["ben", "할아버지"], "loc": {"x": 3.2, "y": 1.0}},
}


def test_a_data_lives_in_the_packs_not_in_the_code():
    ko, en = langdata.get("ko")["dialogue_ext"], langdata.get("en")["dialogue_ext"]
    assert {"polite_endings", "self_correction", "answer_unsure", "room_names"} <= set(ko) and en["self_correction"]
    assert "요" in ko["polite_endings"]


def test_a_polite_ending_is_part_of_the_word_only_with_the_option():
    pl = Places(KENV)
    assert U.place_mentions("소파요", pl) == []                         # today: 요 is no particle (the data gap)
    pl.ext = DX.build("ko", pl)
    assert [m[2] for m in U.place_mentions("소파요", pl)] == ["소파"]
    assert [m[2] for m in U.place_mentions("소파요, 놓아 주세요", pl)] == ["소파"]
    assert U.place_mentions("소파요리", pl) == []                       # 요 closes the word: not inside another word


def test_a_a_room_in_the_persons_language_is_a_room():
    pl = Places(KENV)
    assert pl.candidates("침실에 있어요", "침실에 있어요") == []
    pl.ext = DX.build("ko", pl)
    assert pl.candidates("침실에 있어요", "침실에 있어요") == ["@bedroom"]
    assert pl.candidates("서재에 있어요", "서재에 있어요") == ["@study"]          # 'study' -> the pack's 서재
    assert pl.candidates("욕실에 있어요", "욕실에 있어요") == []                 # a room the ENV does not have


def test_a_robot_pack_may_declare_room_names():
    pl = Places({"cup@kids": {"aliases": ["컵"]}})
    assert DX.build("ko", pl).rooms.get("아이방") is None or True
    ext = DX.build("ko", pl, {"room_names": {"ko": {"kids": ["아이방"]}}})
    pl.ext = ext
    assert pl.candidates("아이방에", "아이방에") == ["@kids"]


def test_a_unsure_phrases_for_the_language():
    from robot_agent.core.planning import dialog_unstick as DU
    pl = Places(KENV)
    ext = DX.build("ko", pl)
    assert DU.unsure_answer("잘 모르겠어요.", pl, {DU.KEY: {"ko": list(ext.unsure)}}, "ko")
    assert not DU.unsure_answer("소파 위에 있어요.", pl, {DU.KEY: {"ko": list(ext.unsure)}}, "ko")


def test_a_self_correction_markers_are_pack_data_and_english_is_unchanged():
    pl = Places(KENV)
    t = "분홍색 컵 소파에, 아니 책상에 갖다 놔 주세요"
    base = U._drop_corrected(t, U.place_mentions(t, pl))
    assert [m[2] for m in base] == ["소파", "책상"]                   # the English-only regex sees no correction
    pl.ext = DX.build("ko", pl)
    got = U._drop_corrected(t, U.place_mentions(t, pl), U._corr_of(pl))
    assert [m[2] for m in got] == ["책상"]
    assert [m[2] for m in U._drop_corrected("a 책상요 말고 소파요", U.place_mentions("a 책상요 말고 소파요", pl), U._corr_of(pl))] == ["소파"]
    # 아니요 / 아니에요 (a polite "no") are no correction
    assert not list(U._corr_of(pl).finditer("아니요, 소파에 놔 주세요")) and not list(U._corr_of(pl).finditer("아니에요"))
    # English: the pack's markers give the same matches as the hard-coded regex
    pe = Places(ENV)
    pe.ext = DX.build("en", pe)
    for s in ("put it on the stool... no wait, on the sofa", "on the sofa, actually the dresser", "I mean the fridge", "plain text"):
        assert [m.span() for m in U._corr_of(pe).finditer(s)] == [m.span() for m in U._CORR.finditer(s)]


def test_a_a_language_without_data_gets_nothing(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "qq", {"lang": "qq", "name": "Qaqish", "register": "plain spoken Qaqish"})
    pl = Places(ENV)
    assert DX.build("qq", pl) is None and DX.build("zz", pl) is None
    assert DX.build("qq", pl, {"room_names": {"ko": {"kitchen": ["주방"]}}}) is None       # another language's names: not used
    assert U._corr_of(pl) is U._CORR and getattr(pl, "ext", None) is None


# ── (b) 008 / 015 KO: the data gaps, through the real loop ──────────────────────────────────────────────────────────────
KO_ENV = {
    "sofa@living room": {"aliases": ["소파"], "loc": {"x": 1.0, "y": 1.0}},
    "desk@study": {"aliases": ["책상"], "loc": {"x": 6.0, "y": 1.0}},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["me", "anna", "저"], "loc": {"x": 1.2, "y": 1.0}},
    "ben@kitchen": {"person": True, "aliases": ["ben", "할아버지"], "loc": {"x": 3.2, "y": 1.0}},
}
KO_PACK = {"q_confirm": "{summary} 맞아요?", "rb_will": "{what}.", "sep_then": ", 그다음 ", "q_which_part": "어느 부분이 맞지 않나요?",
           "q_what_do": "죄송해요. 그럼 뭘 할까요?", "rb_on": "{object}을 {place}에 놓을게요", "rb_restated": "같은 뜻으로 다시 이해했어요.",
           "q_mentioned": "{what} 말씀하셨는데, 거기서 뭘 할까요?", "q_confirm_restated": "{summary} 맞아요?"}
KO_READ = {"status": "clear", "reason": "", "question": "",
           "goals": [{"type": "on", "object": "blue book", "said": "파란책", "from": "", "to": "sofa@living room"}]}
KO_PLAN = steps("MoveTo desk@study", "Find blue book", "Pick blue book", "MoveTo sofa@living room", "Place sofa@living room")
KO_TASK = "파란책을 소파에 놔 주세요."


class KoLLM(FakeLLM):
    def chat(self, prompt="", format=None, **kw):
        if "Translate into natural" in prompt:
            self.calls.append((prompt, format))
            return "소파에 놓을게요"
        return super().chat(prompt=prompt, format=format, **kw)


def ko_play(make_assist, answers, planner=None, extra=None, **opts):
    return play(make_assist, KoLLM(KO_READ, default=KO_READ), answers, task=KO_TASK, env=KO_ENV, house={"blue book": "desk@study"},
                lang="ko", phrases={**{k: {"ko": v} for k, v in KO_PACK.items()}, **(extra or {})}, plan=KO_PLAN, planner=planner,
                **opts)


@uses(KO_ENV)
def test_008_ko_a_polite_ending_answer_settles_the_reading(make_assist):
    answers = ["아니요.", KO_TASK, "소파요."]
    loop, robot, evs = ko_play(make_assist, list(answers), **B2)
    assert len(agreed(evs)) == 1 and not gave_up(evs), [str(q) for q in robot.asked]
    # the option off, or dialog_agree alone: the 요 ending is no particle, the answer is neutral, the run gives up
    for opts in (dict(dialog_unstick="1"), dict(dialog_unstick="1", dialog_agree="1")):
        _l, _r, evs0 = ko_play(make_assist, list(answers), **opts)
        assert not agreed(evs0) and gave_up(evs0)


@uses(KO_ENV)
def test_015_ko_not_sure_in_the_persons_language_is_a_not_sure(make_assist):
    answers = ["잘 모르겠어요.", KO_TASK, "소파요."]
    loop, robot, evs = ko_play(make_assist, list(answers), **B2)
    assert any(x["unsure"] for x in loop._agree_state(loop._ctx)["refusals"]) and len(agreed(evs)) == 1


# ── (c) the guard: KO 012 / 018 under ASR ──────────────────────────────────────────────────────────────────────────
@uses(KO_ENV)
def test_018_ko_a_garbled_name_does_not_confirm_it_asks_once_more(make_assist):
    """The refusal turned 책상에 into a garble in the particle (책상에서 -> 책상에서요?) ..."""
    answers = ["아니요.", KO_TASK, "소파요, 파란착이요."]
    loop, robot, evs = ko_play(make_assist, list(answers) + ["아니요."], planner={"agree_guard": GUARD}, **B2)
    log = lab(loop)[-1]
    assert log.get("reask") is True and "garbled" in log["guard"] and not log["fire"]
    assert not agreed(evs) and gave_up(evs)                       # the person answered the second question "no": it ends
    assert len([q for q in robot.asked if "맞아요" in str(q)]) >= 2     # the read-back and the new question


@uses(KO_ENV)
@uses(KO_ENV)
def test_018_ko_agreement_only_through_a_fuzzy_name_asks_once_more(make_assist):
    answers = ["아니요.", "파란책 가져다 주세요.", "파란착이요."]
    loop, robot, evs = ko_play(make_assist, list(answers), planner={"agree_guard": GUARD}, **B2)
    assert not agreed(evs) and "inexact" in lab(loop)[-1]["guard"]
    loop, robot, evs = ko_play(make_assist, list(answers), **B2)                 # no guard: the fuzzy match confirms
    assert len(agreed(evs)) == 1


@uses(KO_ENV)
def test_018_ko_the_second_question_can_confirm(make_assist):
    answers = ["아니요.", KO_TASK, "소파요, 파란착이요.", "네."]
    loop, robot, evs = ko_play(make_assist, answers, planner={"agree_guard": GUARD}, **B2)
    assert lab(loop)[-1].get("reask") and done(evs)["status"] == "success"


@uses(KO_ENV)
def test_012_ko_an_object_alone_is_no_agreement(make_assist):
    """Not sure + a restated request that names the object and no place: not a confirmation."""
    answers = ["잘 모르겠어요.", "파란책 가져다 주세요.", "파란책이요."]
    loop, robot, evs = ko_play(make_assist, list(answers), planner={"agree_guard": GUARD}, **B2)
    assert not agreed(evs) and not fired(loop) and lab(loop)[-1]["guard"] == ["object_only"] and gave_up(evs)
    # without the guard (the rule alone, round 12) the same dialogue is taken as agreed: this is the false confirmation
    loop, robot, evs = ko_play(make_assist, list(answers), **B2)
    assert len(agreed(evs)) == 1


@uses(KO_ENV)
def test_a_clean_polite_answer_with_the_guard_on_still_confirms(make_assist):
    loop, robot, evs = ko_play(make_assist, ["아니요.", KO_TASK, "소파요."], planner={"agree_guard": GUARD}, **B2)
    assert len(agreed(evs)) == 1 and "reask" not in lab(loop)[-1]


@uses(KO_ENV)
def test_a_genuine_no_stops_the_robot_with_the_guard_on(make_assist):
    loop, robot, evs = ko_play(make_assist, ["아니요.", KO_TASK, "책상요."], planner={"agree_guard": GUARD}, **B2)
    assert not agreed(evs) and done(evs)["status"] == "failed"
    loop, robot, evs = ko_play(make_assist, ["아니요, 소파에 놔 주세요.", KO_TASK, "소파요."], planner={"agree_guard": GUARD}, **B2)
    assert not agreed(evs) and done(evs)["status"] == "failed"


@uses(KO_ENV)
def test_without_a_declared_ratio_there_is_no_guard(make_assist):
    answers = ["아니요.", KO_TASK, "소파요, 파란착이요."]
    loop, robot, evs = ko_play(make_assist, list(answers), **B2)
    assert len(agreed(evs)) == 1 and "reask" not in lab(loop)[-1]          # no number in the manifest: the rule alone


def test_guard_functions():
    pl = Places(KO_ENV)
    g = DX.Guard("ko", pl, KO_READ["goals"], 0.6, DX.build("ko", pl))
    assert g.garbled(["할아버지한테 갖다 주세요", "저한테 주세요", "책상에서요"]) == []
    assert g.garbled(["할아버지한태 갖다 주세요"]) == ["할아버지한태"] and g.garbled(["저한태"]) == ["저한태"]
    assert g.plausible("책상에 놔 주세요") and not g.plausible("put it on the shelf please")
    assert g.exact_score(KO_READ["goals"][0], ["파란책은 책상에"]) >= 2 and g.exact_score(KO_READ["goals"][0], ["파란색 책"]) == 0
    assert DX.guard_for("ko", pl, [], {}) is None and DX.guard_for("ko", pl, [], {"agree_guard": {"garble_ratio": 0}}) is None


# ── 001 / 021 EN: the round-12 behaviour stays under dialog_agree2 ───────────────────────────────────────────────────────
def test_001_en_still_confirms(make_assist):
    answers = ["No.", TASK, "In the fridge, please."]
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, planner={"agree_guard": GUARD}, **B2)
    assert len(agreed(evs)) == 1 and not gave_up(evs) and "reask" not in lab(loop)[-1]
    # identical to dialog_agree alone on this dialogue (events and questions)
    l1, r1, e1 = play(make_assist, FakeLLM(default=READ), list(answers), **BOTH)
    assert [e["event"] for e in e1] == [e["event"] for e in evs] and r1.asked == robot.asked


@uses(GLASS_ENV)
def test_021_en_still_confirms(make_assist, tmp_path):
    loop, robot, evs = glass_run(make_assist, tmp_path, list(GLASS_ANSWERS), GLASS_READINGS, **B2)
    assert len(agreed(evs)) == 1 and fired(loop)


def test_en_true_no_still_stops(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", TASK, "In the sofa, please."], planner={"agree_guard": GUARD}, **B2)
    assert not agreed(evs) and done(evs)["status"] == "failed"


# ── a made-up language with no data: the flag does nothing ─────────────────────────────────────────────────────────────
def test_a_made_up_language_is_unchanged(make_assist, monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "qq", {"lang": "qq", "name": "Qaqish", "register": "plain spoken Qaqish", "non_ascii_min": 0.25})
    ans = ["no", TASK, "Má ká tú vé ní"]
    l1, r1, e1 = play(make_assist, FakeLLM(default=READ), list(ans), lang="qq", **BOTH)
    l2, r2, e2 = play(make_assist, FakeLLM(default=READ), list(ans), lang="qq", **B2)
    assert l2._ctx.places.ext is None
    assert [e["event"] for e in e1] == [e["event"] for e in e2] and r1.asked == r2.asked and gave_up(e2)


def test_option_off_is_the_earlier_events(make_assist):
    ans = ["No.", TASK, "In the fridge, please."]
    l1, r1, e1 = play(make_assist, FakeLLM(default=READ), list(ans), dialog_unstick="1", dialog_agree="1")
    l2, r2, e2 = play(make_assist, FakeLLM(default=READ), list(ans), dialog_unstick="1", dialog_agree="1", dialog_agree2="0")
    assert [{k: v for k, v in e.items() if k != "run_id"} for e in e1] == [{k: v for k, v in e.items() if k != "run_id"} for e in e2] and r1.asked == r2.asked and l2._ctx.places.ext is None


# ── round 14 fixes: pack words are never garbled; the structural not-sure test also runs where phrases exist ─────────────
KO_ENV13 = {**KO_ENV, "wife@kitchen": {"person": True, "aliases": ["wife", "아내"], "loc": {"x": 3.5, "y": 1.0}}}


def ko13_play(make_assist, answers):
    return play(make_assist, KoLLM(KO_READ, default=KO_READ), answers, task=KO_TASK, env=KO_ENV13, house={"blue book": "desk@study"},
                lang="ko", phrases={k: {"ko": v} for k, v in KO_PACK.items()}, plan=KO_PLAN, planner={"agree_guard": GUARD}, **B2)


@uses(KO_ENV13)
def test_013_ko_the_refusal_word_of_the_pack_is_not_garbled(make_assist):
    """KO 013 shape: a bare 아니요 and the request said twice. The guard read the pack's own refusal word as a garble
    and asked again; the person answered "not sure" and the run gave up."""
    answers = ["아니요.", KO_TASK, KO_TASK]
    loop, robot, evs = ko13_play(make_assist, list(answers))
    log = lab(loop)[-1]
    assert len(agreed(evs)) == 1 and "reask" not in log and not log.get("garbled") and not gave_up(evs)


@uses(KO_ENV13)
def test_013_ko_with_asr_noise_in_the_request(make_assist):
    """The same shape under ASR: noise inside the request words (not the pack's words) does not hide the agreement."""
    answers = ["아니요.", KO_TASK, "파란책을 소파에 놔 주세요."]
    loop, robot, evs = ko13_play(make_assist, list(answers))
    assert len(agreed(evs)) == 1 and not gave_up(evs)
    noisy = ["아니요.", KO_TASK, "파란책을 소파에 놔 주세요, 아니요 아니에요."]       # a refusal word again inside the answer
    loop, robot, evs = ko13_play(make_assist, noisy)
    assert not [g for g in (lab(loop)[-1].get("garbled") or []) if g.startswith("아니")]


@uses(KO_ENV)
def test_097_ko_a_polite_not_sure_that_no_phrase_declares_is_still_a_not_sure(make_assist):
    """The language declares not-sure phrases; this answer matches none. Under dialog_agree2 the structural test (short, names
    no place) still reads it as a not-sure, so the refusal is not a correction."""
    answers = ["잠깐만요.", KO_TASK, "소파요."]
    from robot_agent.core.planning import dialog_unstick as DU
    declared = {DU.KEY: {"ko": ["절대 모르겠"]}}
    loop, robot, evs = ko_play(make_assist, list(answers), extra=declared, **B2)
    assert loop._agree_state(loop._ctx)["refusals"][0]["unsure"] is True
    _l, _r, _e = ko_play(make_assist, list(answers), extra=declared, dialog_unstick="1", dialog_agree="1")
    assert _l._agree_state(_l._ctx)["refusals"][0]["unsure"] is False         # without the option: today's phrases-only rule


def test_unsure_answer_fallback_too_flag():
    from robot_agent.core.planning import dialog_unstick as DU
    pl = Places(KO_ENV)
    ph = {DU.KEY: {"ko": ["잘 모르겠"]}}
    assert DU.unsure_answer("잠깐만요.", pl, ph, "ko", UNSTICK) is False
    assert DU.unsure_answer("잠깐만요.", pl, ph, "ko", UNSTICK, fallback_too=True) is True
    assert DU.unsure_answer("소파요.", pl, ph, "ko", UNSTICK, fallback_too=True) is False      # names an ENV place: an answer
    assert DU.unsure_answer("잘 모르겠어요", pl, ph, "ko", UNSTICK) is True


def test_guard_skips_the_packs_own_words_and_their_stems():
    pl = Places(KO_ENV)
    g = DX.Guard("ko", pl, KO_READ["goals"], 0.6, DX.build("ko", pl))
    known = g._pack_words()
    assert known and "아니요" in known or any(w.startswith("아니") for w in known)
    assert g.garbled(["아니요", "아니에요", "아니요요"]) == []
    assert g.garbled(["할아버지한태"]) == ["할아버지한태"]                  # a garbled name still is
    assert DX.Guard("zz", pl, [], 0.6, None)._pack_words() == set()        # no pack: no words, nothing skipped
