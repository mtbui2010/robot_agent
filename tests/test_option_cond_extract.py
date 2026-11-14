"""Option ``cond_extract`` (default OFF): a request with a condition word is split by ONE extra model call into the condition and its two branches and
handed to ``cond_goal`` as ``when``. Made-up objects and places, two made-up languages (their condition words are declared pack data) and the shipped
English pack; no benchmark text. The loop-level tests go through the real AssistLoop with ``cond_goal`` and a scripted model."""
import json
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.options.cond_extract import SCHEMA, SPEC, CondExtract, parse

PROMPT = "EXTRACTX split the request into a condition and two branches.\nRequest: {request}\nGoals:\n{goals}"
ASK = "Where should I look for the {object}?"
KEEP, PUT = {"type": "on", "object": "urn", "from": "", "to": "shelf"}, {"type": "on", "object": "urn", "from": "", "to": "crate"}
PLACES = {"hall", "shelf", "crate"}


def answer(**kw):
    base = {"conditional": True, "object": "urn", "at": "hall", "present": True, "if_goals": [1], "else_goals": [2], "drop": [], "unsure": False}
    return {**base, **kw}


def make(reply, task="if the urn is in the hall, put it on the shelf, otherwise in the crate", lang="en", answers=(), prompt=PROMPT, ask=ASK,
         declared=None, label=True, **opts):
    """(option, run, log): a stub of the services; ``reply`` is the model's answer (a dict, or None = no answer)."""
    log = NS(calls=[], asked=[], labs=[])
    ans = list(answers)

    def ask_model(run, text, schema):
        log.calls.append((text, schema))
        return reply

    def do_ask(run, q):
        log.asked.append(str(q))
        return ans.pop(0) if ans else ""

    svc = NS(ask_model=ask_model, ask=do_ask, cancelled=lambda run: None, lab=lambda *a, **k: log.labs.append(k),
             ground_place=lambda run, t: t if t in PLACES else None, goal_phrase=lambda g, places: f"put {g['object']} to {g['to']}",
             request_english=lambda run: "")
    planner = {"options": {"cond_extract": {"clauses": declared}}} if declared else None
    keys = {"cond_extract": "1", "cond_extract.prompt": prompt, "cond_extract.q_unclear": ask, **({"cond_extract.assumed": "1"} if label else {}), **opts}
    run = NS(task=task, lang=lang, goals=[dict(KEEP), dict(PUT)], places=NS(), filtered=[])
    return CondExtract(resolve(SPEC, keys, {}, planner), svc), run, log


def drain(gen):
    out = []
    try:
        while True:
            out.append(next(gen))
    except StopIteration as e:
        return out, e.value


def go(opt, run):
    return drain(opt.on_goals(run))


# ── off and empty ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_cond_extract_registered_and_off_by_default_the_goals_and_the_model_are_untouched_off():
    assert "cond_extract" in ASSIST_OPTIONS and REGISTRY.get("cond_extract").key("enabled").default is False
    assert resolve(SPEC, {}, {}, None)["enabled"] is False
    assert SPEC.hooks == ("on_goals",) and SPEC.order < REGISTRY.get("cond_goal").order      # it writes `when` before cond_goal reads it


def test_cond_extract_empty_pack_no_prompt_or_no_condition_words_does_nothing_empty():
    for kw in ({"prompt": ""}, {"prompt": "  "}):
        opt, run, log = make(answer(), **kw)
        assert go(opt, run) == ([], None) and log.calls == [] and run.goals == [KEEP, PUT]
    opt, run, log = make(answer(), task="sol the urn is in the hall, shelf it", lang="zq")       # a language no pack has words for
    assert go(opt, run) == ([], None) and log.calls == [] and run.goals == [KEEP, PUT]
    opt, run, log = make(answer())
    run.goals = []
    assert go(opt, run) == ([], None) and log.calls == []


def test_cond_extract_unlabelled_prompt_and_question_are_not_used_empty():
    opt, run, log = make(answer(), label=False)                                    # run options without source / assumed: no extraction
    assert go(opt, run) == ([], None) and log.calls == [] and run.goals == [KEEP, PUT]
    opt, run, log = make(answer(), label=False, **{"cond_extract.source": "  "})    # a blank source is no label
    assert go(opt, run) == ([], None) and log.calls == []
    opt, run, log = make(answer(), label=False, **{"cond_extract.source": "made-up pack, test"})
    assert go(opt, run) == ([], None) and len(log.calls) == 1


# ── the extraction ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_cond_extract_condition_present_branch_a_one_extra_call_writes_when_on_both_branches_one_call():
    opt, run, log = make(answer(drop=[]))
    assert go(opt, run) == ([], None)
    assert len(log.calls) == 1 and log.calls[0][1] is SCHEMA
    assert "Request: if the urn is in the hall" in log.calls[0][0] and "1. put urn to shelf\n2. put urn to crate" in log.calls[0][0]
    w = {"object": "urn", "at": "hall"}
    assert run.goals == [{**KEEP, "when": {**w, "present": True}}, {**PUT, "when": {**w, "present": False}}]
    assert go(opt, run) == ([], None) and len(log.calls) == 1                  # a second pass of the same run asks the model nothing more


def test_cond_extract_a_negative_condition_flips_both_branches_and_a_statement_goal_is_dropped():
    third = {"type": "on", "object": "urn", "from": "", "to": "hall"}
    opt, run, log = make(answer(present=False, drop=[3]))
    run.goals.append(third)
    go(opt, run)
    assert [g["to"] for g in run.goals] == ["shelf", "crate"] and [g["when"]["present"] for g in run.goals] == [False, True]
    assert run.filtered == [third] and "when" not in third


def test_cond_extract_a_goal_in_no_list_stays_unconditional():
    opt, run, log = make(answer(if_goals=[1], else_goals=[]))
    go(opt, run)
    assert "when" in run.goals[0] and run.goals[1] == PUT


def test_cond_extract_no_keyword_zero_extra_calls():
    opt, run, log = make(answer(), task="put the urn on the shelf and the vase in the crate")
    assert go(opt, run) == ([], None) and log.calls == [] and log.asked == [] and run.goals == [KEEP, PUT]


def test_cond_extract_invalid_output_leaves_the_goals_unchanged():
    bad = [None, {}, answer(conditional=False), answer(if_goals=[5]), answer(if_goals="1"), answer(if_goals=[True]), answer(object=" "),
           answer(present="yes"), answer(if_goals=[], else_goals=[]), answer(if_goals=[1], else_goals=[1]), answer(drop=[1]), ["x"]]
    for reply in bad:
        opt, run, log = make(reply)
        assert go(opt, run) == ([], None) and len(log.calls) == 1 and run.goals == [KEEP, PUT] and log.asked == [], reply


def test_cond_extract_a_goal_that_already_has_a_when_is_left_to_the_reader():
    opt, run, log = make(answer())
    run.goals[0]["when"] = {"object": "urn", "at": "hall", "present": True}
    assert go(opt, run) == ([], None) and log.calls == []


# ── unsure: asks, never guesses ───────────────────────────────────────────────────────────────────────────────────────
def test_cond_extract_unsure_asks_once_and_a_known_place_in_the_answer_completes_it():
    opt, run, log = make(answer(at="", unsure=True), answers=["crate"])
    go(opt, run)
    assert log.asked == ["Where should I look for the urn?"] and len(log.calls) == 1
    assert run.goals[0]["when"] == {"object": "urn", "at": "crate", "present": True} and run.goals[1]["when"]["present"] is False


def test_cond_extract_unsure_and_a_useless_answer_keeps_the_readers_goals_no_guess():
    for ans in ("", "I do not know", "somewhere"):
        opt, run, log = make(answer(unsure=True), answers=[ans])
        assert go(opt, run) == ([], None) and len(log.asked) == 1 and run.goals == [KEEP, PUT], ans
    opt, run, log = make(answer(at="attic"), answers=[""])                        # a place the robot does not know: asked, not trusted
    go(opt, run)
    assert len(log.asked) == 1 and run.goals == [KEEP, PUT]


def test_cond_extract_unsure_without_question_text_or_with_asking_off_asks_nothing():
    for kw in ({"ask": ""}, {"cond_extract.ask_when_unsure": False}):
        opt, run, log = make(answer(unsure=True), answers=["crate"], **kw)
        go(opt, run)
        assert log.asked == [] and run.goals == [KEEP, PUT]


def test_cond_extract_a_stop_while_asking_stops_the_run():
    opt, run, log = make(answer(unsure=True), answers=["crate"])
    opt.svc.cancelled = lambda r: {"event": "done", "status": "cancelled"}
    evs, res = go(opt, run)
    assert res == "stop" and evs[-1]["status"] == "cancelled"


# ── parse ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_cond_extract_parse_states():
    assert parse(answer(), 2)["state"] == "ready" and parse(answer(at=""), 2)["state"] == "unsure" and parse(answer(unsure=True), 2)["state"] == "unsure"
    assert parse(answer(), 1)["state"] == "none"                                   # index 2 does not exist with one goal
    assert parse("x", 2) == {"state": "none"}


# ── made-up languages: their condition words are declared pack data ────────────────────────────────────────────────────
ZQ = {"zq": {"condition": r"\bsol\b[^,;.]*", "assumed": True}}
YY = {"yy": {"condition": [r"\bmir\b[^,;.]*", r"\bplo\b"], "source": "made up"}}


def test_cond_extract_made_up_language_zq_declared_word_one_call_and_another_language_other_words():
    opt, run, log = make(answer(), task="sol the urn hall, shelf it", lang="zq", declared={**ZQ, **YY})
    go(opt, run)
    assert len(log.calls) == 1 and run.goals[0]["when"]["present"] is True
    opt, run, log = make(answer(), task="zam plo the urn hall", lang="yy", declared={**ZQ, **YY})
    go(opt, run)
    assert len(log.calls) == 1 and "when" in run.goals[0]
    opt, run, log = make(answer(), task="mir the urn hall", lang="zq", declared={**ZQ, **YY})        # a word of another language: not a condition here
    assert go(opt, run) == ([], None) and log.calls == [] and run.goals == [KEEP, PUT]


def test_cond_extract_a_declared_word_without_label_is_not_used_and_a_bad_regex_is_ignored():
    opt, run, log = make(answer(), task="sol the urn hall", lang="zq", declared={"zq": {"condition": r"\bsol\b"}})
    assert go(opt, run) == ([], None) and log.calls == []
    opt, run, log = make(answer(), task="sol the urn hall", lang="zq", declared={"zq": {"condition": "(unclosed", "assumed": True}})
    assert go(opt, run) == ([], None) and log.calls == []


def test_cond_extract_the_english_reading_of_a_foreign_request_is_searched_too():
    opt, run, log = make(answer(), task="zzz the urn", lang="zq")
    opt.svc.request_english = lambda r: "if the urn is in the hall, shelf it"
    go(opt, run)
    assert len(log.calls) == 1 and "if the urn is in the hall" in log.calls[0][0] and "Request: zzz the urn / if the urn" in log.calls[0][0]


# ── through the loop with cond_goal ───────────────────────────────────────────────────────────────────────────────────
DS, DT = "desk@study room", "dining table@kitchen"
TASK = "If the urn is not on the dining table, put it on the dining table."
GOAL = {"type": "on", "object": "mug", "from": "", "to": "dining table"}
EXTRACT = {"conditional": True, "object": "mug", "at": "dining table", "present": False, "if_goals": [1], "else_goals": [], "drop": [], "unsure": False}
PLAN = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
LOOP_OPTS = {"cond_extract": "1", "cond_extract.prompt": PROMPT, "cond_extract.assumed": "1", "cond_goal": "1"}


def loop_run(make_assist, env, house, **opts):
    llm = FakeLLM((r"EXTRACTX", EXTRACT), default={"status": "clear", "reason": "", "question": "", "goals": [GOAL]})
    robot = FakeRobot(env, house)
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN), **opts)
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    return robot, llm, run_task(loop, TASK, max_events=3000)


def test_cond_extract_through_the_loop_an_object_already_at_the_goal_place_stays_unmoved(make_assist, env):
    robot, llm, evs = loop_run(make_assist, env, {"mug": DT}, **LOOP_OPTS)
    assert sum("EXTRACTX" in p for p, _f in llm.calls) == 1                         # one extra call
    assert evs[-1]["event"] == "done" and evs[-1]["status"] == "success"
    assert not any(t[0] in ("pick", "place") for t in robot.trace)                  # looked at the place, found it there, moved nothing


def test_cond_extract_through_the_loop_an_object_elsewhere_is_moved_as_the_condition_says(make_assist, env):
    robot, llm, evs = loop_run(make_assist, env, {"mug": DS}, **LOOP_OPTS)
    assert sum("EXTRACTX" in p for p, _f in llm.calls) == 1 and any(t[0] == "pick" for t in robot.trace)
    assert evs[-1]["event"] == "done"


def test_cond_extract_through_the_loop_off_the_model_is_not_asked_a_second_time(make_assist, env):
    robot, llm, evs = loop_run(make_assist, env, {"mug": DT})
    assert not any("EXTRACTX" in p for p, _f in llm.calls) and not [e for e in loop_lab(evs)]


def loop_lab(evs):
    return [e for e in evs if e.get("event") == "lab" and e.get("flag") == "cond_extract"]


def test_cond_extract_json_of_the_schema_is_a_plain_object():
    assert json.loads(json.dumps(SCHEMA))["required"] and set(SCHEMA["properties"]) >= {"conditional", "if_goals", "else_goals", "drop"}


# ── the branch that does not hold stays dropped through the read-back and a re-read ────────────────────────────────────
TASK2 = "If the mug is on the dining table, put it on the desk, otherwise put it in the chest."
G_A, G_B = {"type": "on", "object": "mug", "from": "", "to": "desk"}, {"type": "on", "object": "mug", "from": "", "to": "chest"}
EXTRACT2 = {"conditional": True, "object": "mug", "at": "dining table", "present": True, "if_goals": [1], "else_goals": [2], "drop": [], "unsure": False}
PLAN2 = steps(f"MoveTo {DT}", "Find mug", "Pick mug", f"MoveTo {DS}", f"Place {DS}")


def two_branch(make_assist, env, **opts):
    llm = FakeLLM((r"EXTRACTX", EXTRACT2), default={"status": "clear", "reason": "", "question": "", "goals": [G_A, G_B]})
    robot = FakeRobot(env, {"mug": DT})
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN2), **LOOP_OPTS, **opts)
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    return robot, llm, run_task(loop, TASK2, max_events=3000)


@pytest.mark.parametrize("samples", [2, 0])
def test_cond_extract_two_branches_the_dropped_branch_is_not_asked_about_nor_brought_back_by_the_read_back(make_assist, env, samples):
    robot, llm, evs = two_branch(make_assist, env, understand_samples=samples)
    assert evs[-1]["event"] == "done" and evs[-1]["status"] == "success", evs[-1]
    assert not [e for e in evs if e.get("event") == "clarify"]                      # no question about the chest the robot just dropped
    assert any(t[0] == "place" for t in robot.trace) and not any("chest" in str(t) for t in robot.trace)


def test_cond_extract_the_first_plan_applies_again_to_a_later_reading_without_a_new_call():
    opt, run, log = make(answer())
    go(opt, run)
    run.goals = [dict(KEEP), dict(PUT)]                                             # the reader is called again: both branches come back
    assert go(opt, run) == ([], None) and len(log.calls) == 1
    assert [g["when"]["present"] for g in run.goals] == [True, False]
    run.goals = [dict(KEEP)]                                                        # another number of goals: not the plan's reading, it stands
    go(opt, run)
    assert run.goals == [KEEP] and len(log.calls) == 1


# ── the pack data only in the manifest, merged after the loop was built (as a host's manifest overlay does) ──────────────
# The class: the option's keys are resolved once, at construction; data a host merges into the robot view later is not seen, so the option
# is on and silently does nothing (no extraction call, no lab flag). ``refresh_options`` resolves the keys again against the manifest now.
MF_OPTS = {"cond_extract": "1", "cond_goal": "1"}                                      # switched on as run options, NO prompt there
UNSURE = {**EXTRACT, "at": "", "unsure": True}


def manifest_run(make_assist, env, monkeypatch, house, task=TASK, extract=EXTRACT, refresh=True, answers=(), label=None):
    from robot_agent.core.planning.option_services import refresh_options
    llm = FakeLLM((r"EXTRACTX", extract), default={"status": "clear", "reason": "", "question": "", "goals": [GOAL]})
    robot = FakeRobot(env, house, answers=list(answers))
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN), **MF_OPTS)
    planner = dict(loop.robot.manifest.planner or {})
    options = {**(planner.get("options") or {}), "cond_extract": {"prompt": PROMPT, "q_unclear": ASK, **({"assumed": True} if label is None else label)}}
    monkeypatch.setattr(loop.robot.manifest, "planner", {**planner, "action_roles": {"move": "MoveTo", "find": "Find"}, "options": options})
    if refresh:
        refresh_options(loop, MF_OPTS)
    evs = run_task(loop, task, max_events=3000)
    return robot, llm, evs, [x for x in getattr(loop, "lab_log", []) if x.get("flag") == "cond_extract"]


def calls(llm):
    return sum("EXTRACTX" in p for p, _f in llm.calls)


def test_cond_extract_prompt_only_in_a_manifest_merged_after_build_one_call_per_conditional_request(make_assist, env, monkeypatch):
    robot, llm, evs, lab = manifest_run(make_assist, env, monkeypatch, {"mug": DT})
    assert calls(llm) == 1 and len(lab) == 1                              # the option fired: one extraction call, one lab flag
    assert evs[-1]["event"] == "done" and evs[-1]["status"] == "success"
    assert not any(t[0] in ("pick", "place") for t in robot.trace)                  # the object already at the goal place stays unmoved


def test_cond_extract_prompt_only_in_a_manifest_merged_after_build_fires_without_the_refresh_too(make_assist, env, monkeypatch):
    # the keys were resolved before the data was there; the option reads an empty key again from the manifest at run time (as the D2 options do)
    robot, llm, evs, lab = manifest_run(make_assist, env, monkeypatch, {"mug": DT}, refresh=False)
    assert calls(llm) == 1 and len(lab) == 1 and evs[-1]["status"] == "success"


@pytest.mark.parametrize("refresh", [True, False])
def test_cond_extract_unlabelled_prompt_in_the_manifest_is_not_used_empty(make_assist, env, monkeypatch, refresh):
    robot, llm, evs, lab = manifest_run(make_assist, env, monkeypatch, {"mug": DT}, refresh=refresh, label={})
    assert calls(llm) == 0 and lab == [] and evs[-1]["event"] == "done"
    robot, llm, evs, lab = manifest_run(make_assist, env, monkeypatch, {"mug": DT}, refresh=refresh, label={"source": "made-up pack, test"})
    assert calls(llm) == 1 and len(lab) == 1


def test_cond_extract_prompt_only_in_the_manifest_no_keyword_zero_calls(make_assist, env, monkeypatch):
    robot, llm, evs, lab = manifest_run(make_assist, env, monkeypatch, {"mug": DS}, task="Put the mug on the dining table.")
    assert calls(llm) == 0 and lab == [] and evs[-1]["event"] == "done"


def test_cond_extract_prompt_only_in_the_manifest_unsure_asks_once(make_assist, env, monkeypatch):
    robot, llm, evs, lab = manifest_run(make_assist, env, monkeypatch, {"mug": DT}, extract=UNSURE, answers=["dining table"])
    asked = [q for q in robot.asked if "look for the mug" in str(q)]
    assert calls(llm) == 1 and len(asked) == 1, robot.asked                         # the question text is the manifest's, asked once
    assert evs[-1]["event"] == "done"
