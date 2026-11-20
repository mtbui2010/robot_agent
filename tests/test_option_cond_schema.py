"""Option ``cond_extract``, the keys of layer cond_schema (each default OFF): ``kinds`` (the extraction schema gets ``kind``, ``always`` and
``question``; an unobservable condition hands a yes / no question to ``cond_goal``, which reads it only with its key ``answer``) and
``heard_match`` (condition words no heard word matches are not acted on). Made-up requests (an urn, a hall, a scarf, snow), on stubs and
through the real AssistLoop with ``cond_goal``."""
import json
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options import STOP
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import word_match
from robot_agent.options.cond_extract import SCHEMA, SCHEMA_KINDS, SPEC, CondExtract, parse, parse_kinds, unheard

PROMPT = "EXTRACTX split the request.\nRequest: {request}\nGoals:\n{goals}"
ASK = "Where should I look for the {object}?"
A, B, C = ({"type": "on", "object": "urn", "from": "", "to": t} for t in ("shelf", "crate", "bench"))
PLACES = {"hall", "shelf", "crate", "bench", "attic"}
MATCH = {"min_prefix": 2, "min_ratio": 0.5, "assumed": True}
TASK = "if the urn is in the hall, put it on the shelf, otherwise in the crate, and put one on the bench"


def answer(**kw):
    base = {"conditional": True, "kind": "presence", "object": "urn", "at": "hall", "present": True, "if_goals": [1], "else_goals": [2],
            "always": [3], "drop": [], "question": "", "unsure": False}
    return {**base, **kw}


def make(reply, task=TASK, answers=(), match=None, **opts):
    log = NS(calls=[], asked=[], labs=[])
    ans = list(answers)

    def ask_model(run, text, schema):
        log.calls.append(schema)
        return reply

    def do_ask(run, q):
        log.asked.append(str(q))
        return ans.pop(0) if ans else ""

    svc = NS(ask_model=ask_model, ask=do_ask, cancelled=lambda r: None, lab=lambda *a, **k: log.labs.append(k),
             ground_place=lambda r, t: t if t in PLACES else None, goal_phrase=lambda g, p: f"put {g['object']} to {g['to']}",
             request_english=lambda r: "")
    planner = {"options": {"cond_extract": {"heard_match": match}}} if match else None
    keys = {"cond_extract": "1", "cond_extract.prompt": PROMPT, "cond_extract.q_unclear": ASK, "cond_extract.assumed": "1", **opts}
    run = NS(task=task, lang="en", goals=[dict(A), dict(B), dict(C)], places=NS(), filtered=[], dialogue=[])
    return CondExtract(resolve(SPEC, keys, {}, planner), svc), run, log


def go(opt, run):
    out, gen = [], opt.on_goals(run)
    try:
        while True:
            out.append(next(gen))
    except StopIteration as e:
        return out, e.value


KINDS = {"cond_extract.kinds": "1"}


# ── the pure parts ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_schema_kinds_adds_kind_always_question_and_keeps_the_old_keys_in_their_order():
    keys = list(SCHEMA_KINDS["properties"])
    assert [k for k in keys if k in SCHEMA["properties"]] == list(SCHEMA["properties"]) and SCHEMA_KINDS["required"] == keys
    assert SCHEMA_KINDS["properties"]["kind"]["enum"] == ["presence", "state", "unobservable"] and json.loads(json.dumps(SCHEMA_KINDS))


def test_parse_kinds_presence_is_parse_with_the_always_goals_in_no_other_list():
    assert parse_kinds(answer(), 3) == {**parse(answer(), 3), "kind": "presence"}
    assert parse_kinds(answer(always=[1]), 3) == {"state": "none", "kind": "presence"}               # a goal in two lists
    assert parse_kinds(answer(always=[7]), 3) == {"state": "none", "kind": "presence"}


def test_parse_kinds_unobservable_with_a_question_is_an_ask_plan():
    got = parse_kinds(answer(kind="unobservable", object="", at="", question="Will it snow today?"), 3)
    assert got == {"state": "ask", "kind": "unobservable", "object": "", "at": "", "present": True, "if": [1], "else": [2],
                   "drop": [], "question": "Will it snow today?"}
    assert parse_kinds(answer(kind="unobservable", question=" "), 3) == {"state": "none", "kind": "unobservable"}


def test_parse_kinds_a_state_with_a_question_is_asked_too_the_look_sees_only_an_object_at_a_place():
    got = parse_kinds(answer(kind="state", object="hatch", at="", question="Is the hatch open?", present=False), 3)
    assert got["state"] == "ask" and got["question"] == "Is the hatch open?" and got["present"] is False and got["object"] == ""
    assert parse_kinds(answer(kind="state", question=""), 3) == {"state": "none", "kind": "state"}


@pytest.mark.parametrize("d", [answer(kind="state"), answer(kind="mood"), {k: v for k, v in answer().items() if k != "kind"}, None,
                               answer(conditional=False)])
def test_parse_kinds_a_state_an_unknown_kind_or_an_old_answer_is_no_condition(d):
    assert parse_kinds(d, 3)["state"] == "none"


def test_unheard_flags_condition_words_no_heard_word_matches():
    heard = word_match.tokens("if the urn is in the hall, put it on the shelf")
    lim = word_match.limits(MATCH)
    assert unheard({"object": "urn", "at": "the hall"}, heard, lim) == []
    assert unheard({"object": "the urn", "at": "the attic"}, heard, lim) == ["at"]        # "the" is an article, not a heard match
    assert unheard({"object": "scarf", "at": "hall"}, heard, lim) == ["object"]
    assert unheard({"object": "항아리", "at": "hall"}, heard, lim) == []                  # another script: not checked (fail open)


def test_unheard_empty_no_limits_or_nothing_heard_flags_nothing():
    assert unheard({"object": "scarf", "at": "attic"}, ["urn"], None) == []
    assert unheard({"object": "scarf", "at": "attic"}, [], word_match.limits(MATCH)) == []
    assert word_match.limits({"min_prefix": 2, "min_ratio": 0.5}) is None                  # unlabelled data is no data


# ── kinds ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_cond_extract_kinds_off_sends_the_old_schema_and_ignores_kind_and_always_off():
    opt, run, log = make(answer(kind="unobservable", question="Will it snow?"))
    go(opt, run)
    assert log.calls == [SCHEMA] and run.goals[0]["when"] == {"object": "urn", "at": "hall", "present": True} and "when" not in run.goals[2]


def test_cond_extract_kinds_on_presence_writes_when_on_the_branches_and_none_on_always():
    opt, run, log = make(answer(), **KINDS)
    go(opt, run)
    assert log.calls == [SCHEMA_KINDS] and [("when" in g) for g in run.goals] == [True, True, False]
    assert log.labs[-1]["kind"] == "presence"


def test_cond_extract_kinds_on_unobservable_hands_a_question_to_cond_goal():
    opt, run, log = make(answer(kind="unobservable", object="", at="", question="Will it snow today?"), **KINDS)
    go(opt, run)
    assert run.goals[0]["when"] == {"object": "", "at": "", "ask": "Will it snow today?", "present": True}     # no name to recover or look for
    assert run.goals[1]["when"]["present"] is False and "when" not in run.goals[2] and log.asked == []


def test_cond_extract_kinds_on_a_state_condition_leaves_the_readers_goals():
    opt, run, log = make(answer(kind="state", object="crate", at=""), **KINDS)
    go(opt, run)
    assert run.goals == [A, B, C] and log.labs[-1] == {"state": "none", "object": None, "at": None, "if": None, "else": None, "drop": None,
                                                       "kind": "state"}


# ── heard_match ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_cond_extract_heard_match_regression_an_invented_object_is_unknown_the_run_ends_failed():
    opt, run, log = make(answer(object="scarf"), match=MATCH)
    opt.svc.finish = lambda r, status, **kw: {"type": "done", "status": status, **kw}
    out, res = go(opt, run)
    assert res is STOP and out[-1]["status"] == "failed" and run.goals == [A, B, C] and log.asked == []
    assert log.labs[-1]["state"] == "unknown" and log.labs[-1]["unheard"] == ["object"]


def test_cond_extract_heard_match_a_heard_object_in_other_words_is_acted_on():
    opt, run, log = make(answer(object="the old urns"), match=MATCH)
    go(opt, run)
    assert run.goals[0]["when"]["object"] == "the old urns" and "unheard" not in log.labs[-1]


def test_cond_extract_heard_match_an_invented_place_is_asked_once_and_the_answer_completes_it():
    opt, run, log = make(answer(at="attic"), answers=["it should be in the hall"], match=MATCH)
    opt.svc.ground_place = lambda r, t: "hall" if "hall" in t else None
    go(opt, run)
    assert log.asked == ["Where should I look for the urn?"] and run.goals[0]["when"]["at"] == "it should be in the hall"


def test_cond_extract_heard_match_empty_no_data_checks_nothing_empty():
    opt, run, log = make(answer(object="scarf"))
    go(opt, run)
    assert run.goals[0]["when"]["object"] == "scarf" and "unheard" not in log.labs[-1]


def test_cond_extract_heard_match_unlabelled_data_checks_nothing_empty():
    opt, run, log = make(answer(object="scarf"), match={"min_prefix": 2, "min_ratio": 0.5})
    go(opt, run)
    assert run.goals[0]["when"]["object"] == "scarf"


# ── through the loop with cond_goal: the class (an unobservable condition read as an invented look, or not asked) ───────────
GOAL = {"type": "on", "object": "scarf", "from": "", "to": "dining table"}
SNOW = {"conditional": True, "kind": "unobservable", "object": "snow", "at": "", "present": True, "if_goals": [1], "else_goals": [],
        "always": [], "drop": [], "question": "Will it snow today?", "unsure": False}
LOOP = {"cond_extract": "1", "cond_extract.prompt": PROMPT, "cond_extract.assumed": "1", "cond_goal": "1", "cond_extract.kinds": "1"}
DS, DT = "desk@study room", "dining table@kitchen"


def snow_run(make_assist, env, answers, **opts):
    llm = FakeLLM((r"EXTRACTX", SNOW), default={"status": "clear", "reason": "", "question": "", "goals": [GOAL]})
    robot = FakeRobot(env, {"scarf": DS}, answers=answers)
    loop = make_assist(robot, llm, ScriptedPlanner(steps(f"MoveTo {DS}", "Find scarf", "Pick scarf", f"MoveTo {DT}", f"Place {DT}")), **{**LOOP, **opts})
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    return robot, run_task(loop, "If it snows today, put the scarf on the dining table.", max_events=3000)


def test_loop_regression_an_unobservable_condition_is_asked_and_a_no_does_nothing(make_assist, env):
    robot, evs = snow_run(make_assist, env, ["No, not today."], **{"cond_goal.answer": "1"})
    assert robot.asked[0] == "Will it snow today?" and evs[-1]["status"] == "success" and not any(t[0] == "pick" for t in robot.trace)


def test_loop_an_unobservable_condition_asked_and_a_yes_keeps_the_goal(make_assist, env):
    robot, evs = snow_run(make_assist, env, ["Yes, it will."], **{"cond_goal.answer": "1"})
    assert robot.asked[0] == "Will it snow today?" and any(t[0] == "pick" for t in robot.trace)


def test_loop_without_cond_goal_answer_the_question_when_is_a_no_op_nothing_asked(make_assist, env):
    robot, evs = snow_run(make_assist, env, ["No, not today."])
    assert "Will it snow today?" not in robot.asked and any(t[0] == "pick" for t in robot.trace)


def test_cond_extract_kinds_empty_no_prompt_text_no_call_goals_unchanged_empty():
    opt, run, log = make(answer(kind="unobservable", question="Will it snow?"), **{**KINDS, "cond_extract.prompt": ""})
    go(opt, run)
    assert log.calls == [] and run.goals == [A, B, C]


def test_loop_without_cond_goal_answer_nothing_is_asked_and_the_goals_run_as_read(make_assist, env):
    two = dict(SNOW, if_goals=[1], else_goals=[2])
    other = {"type": "on", "object": "mitten", "from": "", "to": "dining table"}
    llm = FakeLLM((r"EXTRACTX", two), default={"status": "clear", "reason": "", "question": "", "goals": [GOAL, other]})
    robot = FakeRobot(env, {"scarf": DS, "mitten": DS}, answers=["No."])
    loop = make_assist(robot, llm, ScriptedPlanner(steps(f"MoveTo {DS}", "Find scarf", "Pick scarf", f"MoveTo {DT}", f"Place {DT}")), **LOOP)
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    run_task(loop, "If it snows today, put the scarf on the dining table, otherwise the mitten.", max_events=3000)
    assert "Will it snow today?" not in robot.asked and any(t[0] == "pick" for t in robot.trace)
