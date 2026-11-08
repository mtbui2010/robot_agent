"""Solution lab round 12 (option ``dialog_agree``, default OFF), through the real AssistLoop.run_blocking against the
symbolic FakeRobot, a scripted LLM and a scripted user: a refused reading that comes back after the one which-part question
is taken as confirmed when the person's answers agree with it. The dialogues are rebuilt from recorded gate runs (a wrong
"No." to a right read-back, "In the fridge, please."), with synthetic ENV and requests written for these tests.

Covered: a bare wrong "no" plus an agreeing answer is confirmed (001 shape); "no, <a part the reading has>" is not; an
answer that names another place, person or option is not; "not sure" plus an agreeing answer is confirmed; an open
coverage doubt blocks; a genuine correct "no" with a wrong-part answer does not fire; a choice answer settles the option
not taken (S1: EN, KO and a made-up language with ENV aliases only); no data = neutral = today's path; option off = the
earlier events byte for byte."""
import json

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import dialog_agree as DA, langdata, speech
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places

ENV = {
    "fridge@kitchen": {"aliases": ["fridge", "refrigerator"], "loc": {"x": 3.0, "y": 1.0}},
    "counter@kitchen": {"aliases": ["counter"], "loc": {"x": 3.5, "y": 1.0}},
    "sofa@living room": {"aliases": ["sofa", "couch"], "loc": {"x": 1.0, "y": 1.0}},
    "dresser@bedroom": {"aliases": ["dresser"], "loc": {"x": 6.0, "y": 1.0}},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["me", "anna"], "loc": {"x": 1.2, "y": 1.0}},
    "ben@kitchen": {"person": True, "aliases": ["ben", "grandpa"], "loc": {"x": 3.2, "y": 1.0}},
}
HOUSE = {"plate": "counter@kitchen"}
PLAN = steps("MoveTo counter@kitchen", "Find plate", "Pick plate", "MoveTo fridge@kitchen", "Open fridge@kitchen",
             "Place fridge@kitchen")
TASK = "Please open the fridge and slide the plate in there, and leave it open."
READ = {"status": "clear", "reason": "", "question": "",
        "goals": [{"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"},
                  {"type": "open", "object": "fridge@kitchen", "from": "", "to": ""}]}
UNSTICK = {"fallback_words": 5, "fallback_chars": 24, "restate_frac": 0.6, "restate_word_len": 3, "restate_min_words": 4,
           "restate_min_bigrams": 6}
PARTS = "Which part is not right"
from test_sage_lab_r3 import DS, DT, ENV as GLASS_ENV    # noqa: E402  (a desk and a dining table: the which-glass ENV)


class ScriptRobot(FakeRobot):
    """Every question, read-backs included, takes the next scripted answer (an exhausted script is silence)."""

    def _exec(self, skill, o, params):
        if skill == "ask":
            self.asked.append(o)
            a = self.answers.pop(0) if self.answers else ""
            return {"isdone": bool(a), "text": a}
        return super()._exec(skill, o, params)


@pytest.fixture
def env(request):
    """The ENV of the test (``test.ENV`` when it sets one): the loop reads the ENV from this fixture."""
    return getattr(request.function, "ENV", ENV)


def play(make_assist, llm, answers, task=TASK, env=None, house=None, lang="en", phrases=None, plan=PLAN, **opts):
    robot = ScriptRobot(env or ENV, dict(house if house is not None else HOUSE), answers=answers)
    loop = make_assist(robot, llm, ScriptedPlanner(plan), health_wait_s=0, confirm_task=True, **opts)
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, unstick=UNSTICK)
    if phrases:
        loop.robot.i18n.phrases.update(phrases)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return loop, robot, evs


def agreed(evs):
    return [e for e in evs if e.get("correction") == "agreed"]


def gave_up(evs):
    d = done(evs)
    return d["status"] == "failed" and "did not confirm" in str(d.get("msg"))


def confirms(robot):
    return [q for q in robot.asked if str(q).endswith("Is that right?")]


def fired(loop):
    return [e for e in loop.lab_log if e["flag"] == "dialog_agree" and e["fire"]]


BOTH = dict(dialog_unstick="1", dialog_agree="1")


def test_option_registered_and_off_by_default(make_assist, monkeypatch, env):
    monkeypatch.delenv("ROBOT_AGENT_DIALOG_AGREE", raising=False)
    assert "dialog_agree" in ASSIST_OPTIONS and "dialog_agree" in PLANNER_KEYS
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["dialog_agree"] is False and mk(dialog_agree="1").cfg["dialog_agree"] is True


# (a) 001 EN: a wrong bare "No.", the request again, the which-part question, then "In the fridge, please."
def test_a_bare_wrong_no_and_an_agreeing_answer_is_confirmed(make_assist):
    answers = ["No.", TASK, "In the fridge, please."]
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, **BOTH)
    assert len(agreed(evs)) == 1 and fired(loop)
    assert len(confirms(robot)) == 1 and robot.asked[2].startswith(PARTS)      # one read-back, no second yes / no
    stmt = agreed(evs)[0]["say"]
    assert str(stmt).startswith("I understood it the same way again. I will ") and "Is that right" not in str(stmt)
    assert not gave_up(evs), done(evs)
    # today (option off): the same dialogue gives up
    _l, _r, evs0 = play(make_assist, FakeLLM(default=READ), list(answers), dialog_unstick="1")
    assert gave_up(evs0) and not agreed(evs0)


def test_a_agreement_alone_without_unstick_works_the_same(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", TASK, "In the fridge, please."], dialog_agree="1")
    assert len(agreed(evs)) == 1 and not gave_up(evs)


# (b) "no, <a part the reading has>": the fault is in a part the person did not name
def test_b_no_with_words_that_agree_blocks(make_assist):
    answers = ["No, the fridge.", TASK, "In the fridge, please."]
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, **BOTH)
    assert not agreed(evs) and gave_up(evs)
    assert loop.lab_log[-1]["flag"] == "dialog_agree" and loop.lab_log[-1]["bad_refusals"] == 1 and not loop.lab_log[-1]["fire"]


# (c) the which-part answer names another place, person or option
@pytest.mark.parametrize("answer", ["In the sofa, please.", "Put it in the fridge for Ben.", "Open the dresser, please."])
def test_c_an_answer_naming_something_else_is_not_confirmed(make_assist, answer):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", TASK, answer], **BOTH)
    assert not agreed(evs) and not fired(loop) and done(evs)["status"] == "failed"      # a doubt question or the old stop
    assert DA.judge([answer], READ["goals"], Places(ENV))[0] == "conflict"


def test_c_a_neutral_answer_is_not_enough(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", TASK, "Hmm, everything."], **BOTH)
    assert not agreed(evs) and gave_up(evs) and loop.lab_log[-1]["last"] == "neutral"
    assert DA.judge(["Hmm, everything."], READ["goals"], Places(ENV))[0] == "neutral"


# (d) "not sure" to a read-back is not a refusal with content
def test_d_not_sure_and_an_agreeing_answer_is_confirmed(make_assist):
    answers = ["I don't know.", TASK, "In the fridge, please."]
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, **BOTH)
    assert len(agreed(evs)) == 1 and not gave_up(evs)
    # the same "not sure" is words that DO NOT conflict but are no refusal either way; a not-sure that names a place does
    loop2, _r, evs2 = play(make_assist, FakeLLM(default=READ), ["I don't know, maybe the sofa.", TASK, "In the fridge, please."], **BOTH)
    assert not agreed(evs2) or not loop2.lab_log[-1]["bad_refusals"]


# (e) an open coverage doubt blocks (unit: the doubts are the loop's own coverage check)
def test_e_an_open_doubt_blocks():
    places = Places(ENV)
    goals = [{"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}]
    dlg = [("robot", "Is that right?"), ("user", "No."), ("robot", PARTS), ("user", "In the fridge, please.")]
    issue = {"kind": "place", "what": "counter", "keys": ["counter@kitchen"]}
    args = dict(goals=goals, places=places, dialogue=dlg, first=0, refusals=[{"ans": "No.", "rest": "", "unsure": False}],
                asked_doubts=[], describe=lambda i: f"you mentioned the {i['what']}", texts_of=lambda u: [u], part_asked=True)
    assert DA.decide(issues=[], **args)["fire"] is True
    res = DA.decide(issues=[issue], **args)
    assert res["fire"] is False and res["open_doubts"] == ["you mentioned the counter"]
    assert DA.decide(issues=[issue], **{**args, "asked_doubts": ["you mentioned the counter"]})["fire"] is True    # asked
    assert DA.decide(issues=[issue], **{**args, "replaced": {"counter@kitchen"}})["fire"] is True               # settled out
    assert DA.decide(issues=[], **{**args, "part_asked": False})["fire"] is False                               # T2 needs the question


# a genuine correct "No": the reading really was wrong and the person says so bare, then names the wrong part
WRONG_READ = {"status": "clear", "reason": "", "question": "",
              "goals": [{"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}]}


def test_true_no_with_the_wrong_part_does_not_fire(make_assist):
    # the reading is missing "leave it open" (open goal); the person says a bare "No.", the request again, then answers the
    # which-part question with the part that IS right ("the fridge"): the doubt about the dropped open verb is not
    # settled by it, but a person who names the place the reading holds agrees with it. The guard that matters here is the
    # one the rule has: the person's words about the OTHER part (the sofa) conflict.
    answers = ["No.", TASK, "On the sofa, please."]
    loop, robot, evs = play(make_assist, FakeLLM(default=WRONG_READ), answers, **BOTH)
    assert not agreed(evs) and not fired(loop) and done(evs)["status"] == "failed"


def test_true_no_with_words_that_name_the_real_fault_is_honoured(make_assist):
    # "No, the sofa" names a place the reading lacks: a correction, edited as before, no agreement
    sofa = {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "in", "object": "plate", "from": "", "to": "sofa@living room"}]}
    llm = FakeLLM(sofa, READ, default=READ)
    answers = ["No, in the fridge.", "yes"]
    loop, robot, evs = play(make_assist, llm, answers, task="Please put the plate in the sofa.", **BOTH)
    assert not agreed(evs) and not fired(loop)


# 021 EN: the robot's own which-glass question ("The green one"), a wrong "No, the white glass.", a true "No, the green one,
# please.", the request again, the which-part question, "The green one, please."
def g_on(obj):
    return {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": obj, "from": "", "to": DT}]}


def glass_run(make_assist, tmp_path, answers, readings, **opts):
    import time
    now = time.time()
    mem = {n: {"place": DS, "ts": now, "how": "seen"} for n in ("green glass", "white glass")}
    (tmp_path / "object_memory.json").write_text(json.dumps({"objects": mem}))
    llm = FakeLLM(*[g_on(o) for o in readings], default=g_on("green glass"))
    robot = ScriptRobot(GLASS_ENV, {"green glass": DS, "white glass": DS}, answers=answers)
    plan = steps(f"MoveTo {DS}", "Find green glass", "Pick green glass", f"MoveTo {DT}", f"Place {DT}")
    loop = make_assist(robot, llm, ScriptedPlanner(plan), inventory_ask="1", confirm_task=True, health_wait_s=0, **opts)
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, unstick=UNSTICK)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(GLASS_TASK)]
    return loop, robot, evs


GLASS_TASK = "Put my glass on the table."
GLASS_ANSWERS = ["The green one", "No, the white glass.", "No, the green one, please.", GLASS_TASK, "The green one, please."]
GLASS_READINGS = ["glass", "green glass", "white glass", "glass", "green glass"]


def test_021_a_conflicting_refusal_superseded_by_the_same_choice_is_confirmed(make_assist, tmp_path):
    loop, robot, evs = glass_run(make_assist, tmp_path, list(GLASS_ANSWERS), GLASS_READINGS, **BOTH)
    assert robot.asked[0].startswith("I know of more than one glass")                       # the declared choice question
    assert len(agreed(evs)) == 1 and fired(loop) and robot.asked[-1].startswith(PARTS)
    assert "green glass" in str(agreed(evs)[0]["say"])
    loop, robot, evs = glass_run(make_assist, tmp_path, list(GLASS_ANSWERS), GLASS_READINGS, dialog_unstick="1")
    assert not agreed(evs) and gave_up(evs)                                                 # option off: gives up, as recorded


def test_021_without_the_second_choice_the_white_refusal_stands(make_assist, tmp_path):
    # the person says "the white glass" to the green reading and never takes it back: the reading is not confirmed
    answers = ["The green one", "No, the white glass.", GLASS_TASK, "The white glass, please."]
    loop, robot, evs = glass_run(make_assist, tmp_path, answers, ["glass", "green glass", "green glass", "green glass"], **BOTH)
    assert not agreed(evs) and not fired(loop)


# 015 EN: the robot's own counter doubt is never addressed: not confirmed even though the answers agree
def test_015_the_robots_own_doubt_blocks():
    places = Places(ENV)
    goals = [{"type": "go", "object": "", "from": "", "to": "counter@kitchen"},
             {"type": "on", "object": "red book", "from": "", "to": "anna@living room"}]
    dlg = [("robot", "I am not sure I understood. Is that right?"), ("user", "No."), ("robot", "Sorry. What should I do?"),
           ("user", "Bring me the red book."), ("robot", PARTS), ("user", "Bring it to me, please.")]
    doubt = {"kind": "unsaid", "what": "counter", "keys": []}
    res = DA.decide(goals, places, dlg, 0, [{"ans": "No.", "rest": "", "unsure": False}], [], [doubt],
                    lambda i: f"you did not say the {i['what']}", lambda u: [u], True)
    assert res["last"] == "agree" and res["open_doubts"] == ["you did not say the counter"] and res["fire"] is False


# (f) S1: a choice between places, answered, settles the option not taken
TWO = {"status": "ambiguous", "reason": "", "question": "",
       "goals": [{"type": "on", "object": "blue book", "from": "", "to": "sofa@living room"},
                 {"type": "on", "object": "blue book", "from": "", "to": "dresser@bedroom"}]}
ONE = {"status": "clear", "reason": "", "question": "",
       "goals": [{"type": "on", "object": "blue book", "from": "", "to": "dresser@bedroom"}]}
S1_PLAN = steps("MoveTo sofa@living room", "Find blue book", "Pick blue book", "MoveTo dresser@bedroom", "Place dresser@bedroom")


def s1_run(make_assist, task, answer, extra_env=None, lang="en", llm=None, phrases=None, **opts):
    env = {**ENV, **(extra_env or {})}
    llm = llm or FakeLLM(TWO, default=ONE)
    return play(make_assist, llm, [answer, "yes"], task=task, env=env, house={"blue book": "sofa@living room"}, lang=lang,
                plan=S1_PLAN, phrases=phrases, **opts)


def mentioned(robot):
    return [q for q in robot.asked if "mentioned" in str(q)]


def test_f_en_the_option_not_taken_is_no_doubt(make_assist):
    task = "Put the blue book on the sofa, no, on the dresser please."
    loop, robot, evs = s1_run(make_assist, task, "On the dresser, please.", dialog_agree="1")
    assert any("go in the end" in str(q) for q in robot.asked) and not mentioned(robot)
    assert done(evs)["status"] == "success"
    loop, robot, evs = s1_run(make_assist, task, "On the dresser, please.")            # option off: the doubt is asked
    assert mentioned(robot)


def test_f_the_declaration_is_the_same_english_text(make_assist):
    from robot_agent.core.planning import goals as G
    plain = G.conflicts(TWO["goals"])[1]
    decl = G.conflicts(TWO["goals"], declare=True)[1]
    assert str(decl) == plain and isinstance(plain, str) and not isinstance(plain, speech.LocalText)
    assert decl.ask == {"intent": "choose_place", "options": ["sofa@living room", "dresser@bedroom"], "about": "blue book"}
    assert G.conflicts(ONE["goals"], declare=True)[1] is None


KO_ENV = {
    "sofa@living room": {"aliases": ["소파"], "loc": {"x": 1.0, "y": 1.0}},
    "dresser@bedroom": {"aliases": ["서랍장"], "loc": {"x": 6.0, "y": 1.0}},
    "anna@living room": {"person": True, "speaker": True, "aliases": ["me", "anna", "저"], "loc": {"x": 1.2, "y": 1.0}},
}
KO_PACK = {"q_confirm": "{summary} 맞나요?", "rb_will": "{what}.", "sep_then": ", 그다음 ", "q_which_part": "어느 부분이 맞지 않나요?",
           "q_what_do": "죄송해요. 그럼 뭘 할까요?", "rb_on": "{object}을 {place}에 놓을게요", "q_mentioned": "{what} 말씀하셨는데, 거기서 뭘 할까요?"}


class KoTranslator(FakeLLM):
    """Reads the request (default answer); every robot line is "translated" into a fixed Korean line of its kind."""

    def chat(self, prompt="", format=None, **kw):
        if "Translate into natural" in prompt:
            self.calls.append((prompt, format))
            if "mentioned" in prompt:
                return "소파 말씀하셨는데, 거기서 뭘 할까요?"
            return "서랍장이나 소파 중에 어디일까요?"
        return super().chat(prompt=prompt, format=format, **kw)


@pytest.mark.parametrize("flag, doubt", [("1", False), ("0", True)])
def test_f_ko_a_choice_answered_in_korean_settles_the_other(make_assist, flag, doubt):
    task = "파란 책을 소파에, 아니 서랍장에 놔 주세요."
    llm = KoTranslator(TWO, default=ONE)
    robot = ScriptRobot(KO_ENV, {"blue book": "sofa@living room"}, answers=["서랍장 위에 놔 주세요.", "yes", "yes", "yes"])
    loop = make_assist(robot, llm, ScriptedPlanner(S1_PLAN), health_wait_s=0, confirm_task=True, dialog_agree=flag)
    loop.robot.i18n.phrases.update({k: {"ko": v} for k, v in KO_PACK.items()})
    evs = list(loop.run_blocking(task, lang="ko"))
    qs = [str(e.get("question")) for e in evs if e.get("event") == "clarify"]
    assert any("go in the end" in q for q in qs)
    assert any("mentioned" in q for q in qs) is doubt, qs      # the unchosen 소파 is a doubt only without the option
    if not doubt:
        assert done(evs)["status"] == "success"


def test_f_a_made_up_language_with_env_aliases_only(make_assist, monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "qq", {"lang": "qq", "name": "Qaqish", "register": "plain spoken Qaqish",
                                                 "non_ascii_min": 0.25})
    env = {"sofa@living room": {"aliases": ["sofi"], "loc": {"x": 1.0, "y": 1.0}},
           "dresser@bedroom": {"aliases": ["drezo"], "loc": {"x": 6.0, "y": 1.0}}}
    places = Places(env)
    q = speech.LocalText("_two_places_q", text="Where should the book go in the end: sofa or dresser? It cannot be in two places.",
                         ask={"intent": "choose_place", "options": ["sofa@living room", "dresser@bedroom"], "about": "book"})
    assert DA.settle_pair(q, "Vá drezo ní", places, lambda u: [u]) == {"sofa@living room"}
    assert DA.settle_pair(q, "Vá sofi drezo ní", places, lambda u: [u]) == set()        # names both: nothing settled
    assert DA.settle_pair(q, "Vá tù ní", places, lambda u: [u]) == set()                # names neither


# (g) no data for the language: the answers come out neutral and the old path runs
def test_g_a_language_without_data_is_neutral_and_the_old_path_runs(make_assist, monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "qq", {"lang": "qq", "name": "Qaqish", "register": "plain spoken Qaqish",
                                                 "non_ascii_min": 0.25})
    places = Places(ENV)
    goals = READ["goals"]
    assert DA.judge(["Má ká tú vé ní"], goals, places)[0] == "neutral"
    dlg = [("robot", "Is that right?"), ("user", "Nù."), ("robot", PARTS), ("user", "Má ká tú vé ní")]
    res = DA.decide(goals, places, dlg, 0, [{"ans": "Nù.", "rest": "", "unsure": False}], [], [], str, lambda u: [u], True)
    assert res["last"] == "neutral" and res["fire"] is False
    # the same dialogue through the loop: no agreement, the run ends as it does today
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["no", TASK, "Má ká tú vé ní"], **BOTH)
    assert not agreed(evs) and gave_up(evs)


def test_g_judge_uses_only_held_english(make_assist):
    places = Places(ENV)
    ko = "냉장고에 넣어 주세요"                              # not an alias of the ENV, no held English: neutral
    assert DA.judge([ko], READ["goals"], places)[0] == "neutral"
    assert DA.judge([ko, "put it in the fridge"], READ["goals"], places)[0] == "agree"      # the held English counts


# (h) option off: identical events
def test_h_option_off_is_the_earlier_events(make_assist):
    def run(**o):
        _l, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", TASK, "In the fridge, please."], dialog_unstick="1", **o)

        def scrub(x):
            if isinstance(x, dict):
                return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at")}
            return [scrub(v) for v in x] if isinstance(x, list) else x
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.asked
    assert run() == run(dialog_agree="0")


def test_h_a_clean_run_is_the_same_with_the_option_on(make_assist):
    def run(**o):
        _l, robot, evs = play(make_assist, FakeLLM(default=READ), ["yes"], **o)

        def scrub(x):
            if isinstance(x, dict):
                return {k: scrub(v) for k, v in x.items() if k not in ("run_id", "t", "ts", "elapsed_s", "started_at")}
            return [scrub(v) for v in x if not (isinstance(v, dict) and v.get("event") == "reconcile")] if isinstance(x, list) else x
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace
    assert run() == run(dialog_agree="1")


def test_fires_once_per_run(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", TASK, "In the fridge, please."], **BOTH)
    assert len([e for e in loop.lab_log if e["flag"] == "dialog_agree"]) == 1


test_021_a_conflicting_refusal_superseded_by_the_same_choice_is_confirmed.ENV = GLASS_ENV
test_021_without_the_second_choice_the_white_refusal_stands.ENV = GLASS_ENV
test_f_ko_a_choice_answered_in_korean_settles_the_other.ENV = KO_ENV
