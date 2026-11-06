"""Solution lab round 4 (option ``dialog_unstick``, default OFF), through the real AssistLoop.run_blocking against the
symbolic FakeRobot and a scripted LLM: a restatement that reproduces the refused reading, a "not sure" answer to a
where-is question (English, and a made-up third language with and without data), and the stacked apology. Synthetic requests written for
these tests (no benchmark text). Off = the earlier events."""
import types

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import dialog_unstick as DU, speech
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places

ENV = {
    "sofa@living room": {"aliases": ["sofa"], "loc": {"x": 1.0, "y": 1.0}},
    "table@kitchen": {"aliases": ["table", "tavo"], "loc": {"x": 3.0, "y": 1.0}},
    "shelf@hall": {"aliases": ["shelf", "kele"], "loc": {"x": 6.0, "y": 1.0}},
}
HOUSE = {"cup": "table@kitchen"}
PLAN = steps("MoveTo table@kitchen", "Find cup", "Pick cup", "MoveTo shelf@hall", "Place shelf@hall")
TASK = "Please put the red cup from the table onto the shelf in the hall."
READ = {"status": "clear", "reason": "", "question": "",
        "goals": [{"type": "on", "object": "cup", "from": "table@kitchen", "to": "shelf@hall"}]}
ABSTAIN = {"status": "ambiguous", "reason": "", "question": "Where is the cup?", "goals": []}
NOTE = r"does not know where the object is"
# the numbers a robot pack declares (manifest planner.unstick); without them the numeric parts of dialog_unstick do nothing
UNSTICK = {"fallback_words": 5, "fallback_chars": 24, "restate_frac": 0.6, "restate_word_len": 3, "restate_min_words": 4,
           "restate_min_bigrams": 6}


@pytest.fixture
def env():
    return ENV


def declare(loop, unstick=UNSTICK):
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, **({"unstick": unstick} if unstick else {}))
    return loop


def play(make_assist, llm, answers, task=TASK, lang="en", phrases=None, unstick=UNSTICK, **opts):
    robot = FakeRobot(ENV, dict(HOUSE), answers=answers)
    loop = declare(make_assist(robot, llm, ScriptedPlanner(PLAN), health_wait_s=0, **opts), unstick)
    if phrases:
        loop.robot.i18n.phrases.update(phrases)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return loop, robot, evs


def test_option_registered_and_off_by_default(make_assist, monkeypatch, env):
    monkeypatch.delenv("ROBOT_AGENT_DIALOG_UNSTICK", raising=False)
    assert "dialog_unstick" in ASSIST_OPTIONS and "dialog_unstick" in PLANNER_KEYS
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["dialog_unstick"] is False and mk(dialog_unstick="1").cfg["dialog_unstick"] is True


# ── G1: a restatement that gives the refused reading again ──
RESTATE = "I said: please put the red cup from the table onto the shelf in the hall, not on the sofa."
BARE = "I said: please put the red cup from the table onto the shelf in the hall."


def test_restated_reading_is_read_back_instead_of_a_stop(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["no", RESTATE], confirm_task=True, dialog_unstick="1")
    assert done(evs)["status"] == "success"
    again = [q for q in robot.asked if "same way again" in q]
    assert len(again) == 1 and again[0].endswith("Is that right?")
    assert not any("Which part is not right" in q for q in robot.asked)
    assert loop.lab_log == [{"flag": "dialog_unstick", "part": "restated_reading", "confirms": 1}]


def test_a_bare_restatement_is_not_read_back_again(make_assist):
    # the same words again add nothing a read-back can show: the targeted question comes instead
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["no", BARE, "it is what I said"], confirm_task=True,
                            dialog_unstick="1")
    assert not any("same way again" in q for q in robot.asked) and any("Which part is not right" in q for q in robot.asked)
    assert loop.lab_log == []


def test_adds_entity():
    p = Places(ENV)
    assert DU.adds_entity("put the cup on the shelf", "no, put it on the sofa", p)
    assert not DU.adds_entity("put the cup on the shelf", "I said put the cup on the shelf", p)


def test_without_the_option_the_same_dialogue_ends(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["no", RESTATE, "it is what I said"], confirm_task=True)
    assert done(evs)["status"] != "success" and any("Which part is not right" in q for q in robot.asked)


def test_a_restatement_that_is_refused_again_still_ends(make_assist):
    # the second read-back is also answered "no": the run ends, it does not loop
    llm = FakeLLM(default=READ)
    robot = FakeRobot(ENV, dict(HOUSE), answers=["no", RESTATE, "no", RESTATE, "no"])
    loop = declare(make_assist(robot, llm, ScriptedPlanner(PLAN), health_wait_s=0, confirm_task=True, dialog_unstick="1"))
    evs = list(loop.run_blocking(TASK))
    assert done([{k: v for k, v in e.items() if k != "world"} for e in evs])["status"] != "success"
    assert len(robot.asked) < 12


def test_a_short_answer_is_not_a_restatement(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["no", "the red cup", "not that"], confirm_task=True,
                            dialog_unstick="1")
    assert not any("same way again" in q for q in robot.asked)


@pytest.mark.parametrize("original,answer,yes", [
    ("Please put the red cup on the shelf.", "I said please put the red cup on the shelf", True),
    ("Please put the red cup on the shelf.", "the blue one", False),
    ("컵을 선반에 올려놔 주세요", "컵을 선반에 올려놔 주세요 제발", True),            # no word breaks inside a clause: character pairs
])
def test_restates(original, answer, yes):
    assert DU.restates(original, answer, UNSTICK) is yes


def test_restates_needs_the_declared_numbers():
    said = ("Please put the red cup on the shelf.", "I said please put the red cup on the shelf")
    assert DU.restates(*said, UNSTICK) and not DU.restates(*said) and not DU.restates(*said, {})
    assert not DU.restates(*said, {k: v for k, v in UNSTICK.items() if k != "restate_frac"})
    assert not DU.restates(*said, {**UNSTICK, "restate_frac": 1.0 + 1e-9})            # numbers are read, not assumed


def test_without_declared_numbers_a_restatement_is_not_read_back_again(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["no", RESTATE, "it is what I said"], confirm_task=True,
                            dialog_unstick="1", unstick=None)
    assert not any("same way again" in q for q in robot.asked) and loop.lab_log == []


# ── G2: a not-sure answer to a where-is question is a cue to search ──
def where_llm():
    return FakeLLM((NOTE, READ), default=ABSTAIN)


def test_not_sure_answer_leads_to_find_not_the_same_question(make_assist):
    loop, robot, evs = play(make_assist, where_llm(), ["I don't know"], task="Put the cup on the shelf.",
                            confirm_task=False, dialog_unstick="1")
    assert done(evs)["status"] == "success" and "find" in [s for s, *_ in robot.trace]
    assert sum("Where is the cup?" in q for q in robot.asked) == 1
    assert loop.lab_log == [{"flag": "dialog_unstick", "part": "unsure_search", "taken": True}]


def test_without_the_option_the_question_comes_back(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=ABSTAIN), ["I don't know", "no idea"], task="Put the cup on the shelf.",
                            confirm_task=False)
    assert done(evs)["status"] != "success" and "find" not in [s for s, *_ in robot.trace]


def test_third_language_without_data_uses_the_structural_fallback(make_assist):
    # "qq" is made up: no language pack, no pack phrases; the manifest declares the two fallback numbers
    loop, robot, evs = play(make_assist, where_llm(), ["nu sabi"], task="Pon qa tazu na kele", lang="qq",
                            confirm_task=False, confirm_answers=False, dialog_unstick="1")
    assert done(evs)["status"] == "success" and "find" in [s for s, *_ in robot.trace]
    assert [e.get("question") for e in evs if e["event"] == "clarify"] == ["Where is the cup?"]


def test_third_language_without_data_and_without_numbers_does_nothing(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=ABSTAIN), ["nu sabi", "nu sabi"], task="Pon qa tazu na kele", lang="qq",
                            confirm_task=False, confirm_answers=False, dialog_unstick="1", unstick=None)
    assert "find" not in [s for s, *_ in robot.trace] and loop.lab_log == []


def test_third_language_with_pack_data(make_assist):
    loop, robot, evs = play(make_assist, where_llm(), ["nu sabi tava kele ki sa mi no ve ra to"],
                            task="Pon qa tazu na kele", lang="zz", confirm_task=False, confirm_answers=False, dialog_unstick="1",
                            phrases={"answer_unsure": {"zz": ["nu sabi"]}}, unstick=None)
    assert done(evs)["status"] == "success" and "find" in [s for s, *_ in robot.trace]
    assert loop.lab_log == [{"flag": "dialog_unstick", "part": "unsure_search", "taken": True}]


def test_a_named_place_is_an_answer_not_a_not_sure(make_assist):
    places = Places(ENV)
    assert DU.unsure_answer("I don't know", places) and DU.unsure_answer("nu sabi", places, None, "qq", UNSTICK)
    assert not DU.unsure_answer("it is on the table", places) and not DU.unsure_answer("tavo", places, None, "qq", UNSTICK)
    assert not DU.unsure_answer("", places)
    long = "it should be somewhere around the back of the house near the old garden shed"
    assert not DU.unsure_answer(long, places)                      # a long answer is not guessed at
    assert DU.unsure_phrases({"answer_unsure": {"ko": ["모르겠"]}}, "ko") == ("모르겠",)
    assert DU.unsure_answer("글쎄 모르겠어요", places, {"answer_unsure": {"ko": ["모르겠"]}}, "ko")


def test_english_has_data_so_no_structural_guess():
    places = Places(ENV)
    for a in ("Yes.", "The red one.", "No."):
        assert not DU.unsure_answer(a, places, None, "en", UNSTICK)
    # a language with no data: the fallback needs the declared numbers (none declared = no guess)
    assert DU.unsure_answer("Yes.", places, None, "qq", UNSTICK) and not DU.unsure_answer("Yes.", places, None, "qq")
    assert not DU.unsure_answer("Yes.", places, None, "qq", {"fallback_words": 5})


def test_the_model_asking_something_else_is_left_alone(make_assist):
    ask_which = {"status": "ambiguous", "reason": "", "question": "Which cup do you mean?", "goals": []}
    loop, robot, evs = play(make_assist, FakeLLM(default=ask_which), ["I don't know"], task="Put the cup on the shelf.",
                            confirm_task=False, dialog_unstick="1")
    assert loop.lab_log == []


# ── G2: the stacked apology ──
def test_calm_question_is_asked_again_as_it_is():
    q = speech.LocalText("q_what_do")
    assert str(q.again()).startswith("Sorry, I didn't hear you.") and "Sorry. What" in str(q.again())      # today
    c = speech.calm(q)
    assert c.again() is c and str(c) == str(q) and c.key == "q_what_do"
    plain = speech.LocalText("q_where_go")
    assert speech.calm(plain) is plain and str(plain.again()).startswith("Sorry, I didn't hear you.")
    joined = speech.LocalText.join(speech.LocalText("q_what_do"), speech.LocalText("q_where_go"))
    assert speech.calm(joined).again() is not None


def test_silence_after_the_what_do_question_does_not_stack(make_assist):
    answers = ["no", "", RESTATE]            # bare no, then silence to "Sorry. What should I do?", then the request again
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, confirm_task=True, dialog_unstick="1")
    said = [q for q in robot.asked if "What should I do?" in q]
    assert len(said) == 2 and all(q == "Sorry. What should I do?" for q in said)
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, confirm_task=True)
    assert any("didn't hear you. Sorry. What should I do?" in q for q in robot.asked)


def test_pack_phrase_keys_carry_the_apology_per_language():
    robot = types.SimpleNamespace(i18n=types.SimpleNamespace(phrases={"q_what_do": {"qq": "Sa ri. Ka mi?"},
                                                                       "q_silence": {"qq": "Sa ri, nu ka."}}))
    q = speech.LocalText("q_what_do")
    assert speech.render(speech.calm(q).again(), "qq", robot) == "Sa ri. Ka mi?"
    assert speech.render(q.again(), "qq", robot) == "Sa ri, nu ka. Sa ri. Ka mi?"


def test_option_on_changes_nothing_on_a_clean_run(make_assist):
    import json

    def run(**o):
        _l, robot, evs = play(make_assist, FakeLLM(default=READ), [], task=TASK, confirm_task=True, **o)

        def scrub(x):
            if isinstance(x, dict):
                return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at")}
            return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")] if isinstance(x, list) else x
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace
    assert run() == run(dialog_unstick="1")
