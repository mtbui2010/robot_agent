"""Option ``cond_goal`` (default OFF): a goal with ``when`` {object, at, present} is decided by the robot looking at the place.
Made-up cases (a mug on a desk, a crate), through the real AssistLoop and on stubs. Off / empty pack data = the schema and the prompt are the
same bytes as the engine's own, and the golden default path (tests/test_default_path_golden.py) stays green."""
import json
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options.config import resolve
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, UNDERSTAND_PROMPT, UNDERSTAND_SCHEMA
from robot_agent.options.cond_goal import SPEC, CondGoal, valid_when, without_when

DS, DT = "desk@study room", "dining table@kitchen"
PROMPT = "A condition (if X is at P) is a goal's when: {object, at, present}."
TASK = "If the mug is on the desk, put it on the dining table."
PLAN = steps(f"MoveTo {DS}", "Find mug", "Pick mug", f"MoveTo {DT}", f"Place {DT}")
WHEN = {"object": "mug", "at": "desk", "present": True}
GOAL = {"type": "on", "object": "mug", "from": "desk", "to": "dining table"}


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": list(goals)}


def loop_with(make_assist, env, house, goals, **opts):
    robot = FakeRobot(env, house)
    loop = make_assist(robot, FakeLLM(default=understood(*goals)), ScriptedPlanner(PLAN), **opts)
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    return robot, loop


# ── default off: the engine's own schema and prompt, byte for byte ───────────────────────────────────────────────────
def test_registered_and_off_by_default():
    assert "cond_goal" in ASSIST_OPTIONS
    assert resolve(SPEC, {}, {}, None)["enabled"] is False


def bare(loop):
    """What the loop's schema and prompt are with no option dispatcher at all."""
    from robot_agent.core.options.dispatch import NULL
    keep, loop.options = loop.options, NULL
    try:
        return json.dumps(loop._understand_schema()), loop._flag_prompt(UNDERSTAND_PROMPT)
    finally:
        loop.options = keep


def test_cond_goal_off_schema_and_prompt_are_the_engines_own_bytes(make_assist, env):
    _r, loop = loop_with(make_assist, env, {}, [])
    schema, prompt = bare(loop)
    assert json.dumps(loop._understand_schema()) == schema
    assert loop.options.transform("on_understand_prompt", loop._flag_prompt(UNDERSTAND_PROMPT)) == prompt
    assert "when" not in loop._understand_schema()["properties"]["goals"]["items"]["properties"]


def test_cond_goal_empty_pack_enabled_without_prompt_changes_neither_schema_nor_prompt(make_assist, env):
    _r, off = loop_with(make_assist, env, {}, [])
    _r, on = loop_with(make_assist, env, {}, [], cond_goal="1")
    assert json.dumps(on._understand_schema()) == bare(on)[0] == json.dumps(off._understand_schema())
    assert on.options.transform("on_understand_prompt", UNDERSTAND_PROMPT) == UNDERSTAND_PROMPT
    assert "when" not in on._understand_schema()["properties"]["goals"]["items"]["properties"]


def test_cond_goal_on_with_prompt_adds_when_after_content_and_the_lines_before_task(make_assist, env):
    _r, off = loop_with(make_assist, env, {}, [])
    _r, on = loop_with(make_assist, env, {}, [], cond_goal="1", **{"cond_goal.prompt": PROMPT})
    items = on._understand_schema()["properties"]["goals"]["items"]
    keys = list(items["properties"])
    assert keys[-1] == "when" and keys.index("content") < keys.index("when")
    assert items["required"] == off._understand_schema()["properties"]["goals"]["items"]["required"]     # optional
    assert items["properties"]["when"]["required"] == ["object", "at", "present"]
    text = on.options.transform("on_understand_prompt", UNDERSTAND_PROMPT)
    assert text.index(PROMPT.replace("{", "{{").replace("}", "}}")) < text.index("Task: {task}")
    assert PROMPT in text.format(abilities="", cannot="", goal_kinds="", places="", answers="", robot="", dialogue="", task="t", original="")
    # the other prompt text is untouched
    assert text.replace("\n" + PROMPT.replace("{", "{{").replace("}", "}}") + "\n", "", 1) == UNDERSTAND_PROMPT


# ── the pieces ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_valid_when_needs_two_strings_and_a_boolean():
    assert valid_when({"when": WHEN}) == WHEN
    for bad in ({}, {"when": "x"}, {"when": {"object": "mug", "at": "desk"}}, {"when": {**WHEN, "present": "yes"}},
                {"when": {**WHEN, "at": " "}}, {"when": {**WHEN, "object": ""}}):
        assert valid_when(bad) is None
    assert without_when({"type": "on", "when": WHEN}) == {"type": "on"}


def stub(seen_seq=(True,), remembered=None, role="MoveTo", answer="", at="desk@study room", where_answer=""):
    seen = list(seen_seq)
    log = NS(asked=[], steps=[], looks=0, events=[])

    def look_for(run, obj):
        log.looks += 1
        return (seen.pop(0) if seen else False), "find"

    def ask(run, q):
        log.asked.append(str(q))
        return where_answer if str(q).startswith("Where") else answer

    svc = NS(role=lambda r: role if r == "move" else None, lab=lambda *a, **k: None, look_for=look_for, ask=ask,
             ground_place=lambda run, t: at if t in ("desk", at) else None, remembered=lambda run, o: remembered,
             run_step=lambda run, st: (log.steps.append(st), NS(ok=True))[1], cancelled=lambda run: None,
             in_lang=lambda t, lang: t, finish=lambda run, status, **kw: {"event": "done", "status": status, **kw})
    cfg = resolve(SPEC, {"cond_goal": "1", "cond_goal.q_unsure": "Is the {object} at {place}?",
                         "cond_goal.q_where": "Where should I look for the {object}?"}, {}, None)
    run = NS(goals=[], world=NS(arrived=""), filtered=[], ev=lambda e, **k: {"event": e, **k}, lang="en")
    return CondGoal(cfg, svc), run, log


def drain(gen):
    out = []
    try:
        while True:
            out.append(next(gen))
    except StopIteration as e:
        return out, e.value


def test_cond_goal_without_a_when_does_nothing():
    opt, run, log = stub()
    run.goals = [dict(GOAL)]
    evs, res = drain(opt.on_goals(run))
    assert (evs, res, log.steps, log.looks) == ([], None, [], 0) and run.goals == [GOAL]


def test_cond_goal_seen_keeps_present_true_and_drops_present_false_goals():
    opt, run, log = stub((True,))
    other = {"type": "on", "object": "cup", "from": "", "to": "dining table"}
    run.goals = [{**GOAL, "when": WHEN}, {**other, "when": {**WHEN, "present": False}}, dict(other, object="pen")]
    evs, res = drain(opt.on_goals(run))
    assert res is None and [g["object"] for g in run.goals] == ["mug", "pen"] and all("when" not in g for g in run.goals)
    assert log.looks == 1 and log.steps == [{"action": "MoveTo", "object": "desk@study room"}]      # one group: one trip, one look
    assert [g["object"] for g in run.filtered] == ["cup"]


def test_cond_goal_two_misses_is_absent_and_a_branch_on_absence_is_kept():
    opt, run, log = stub((False, False))
    run.goals = [{**GOAL, "when": WHEN}, {**GOAL, "object": "cup", "when": {**WHEN, "present": False}}]
    drain(opt.on_goals(run))
    assert [g["object"] for g in run.goals] == ["cup"] and log.looks == 2 and log.asked == []


def test_cond_goal_a_miss_then_a_sighting_asks_once_and_a_yes_decides():
    opt, run, log = stub((False, True), answer="yes")
    run.goals = [{**GOAL, "when": WHEN}]
    drain(opt.on_goals(run))
    assert log.asked == ["Is the mug at desk@study room?"] and [g["object"] for g in run.goals] == ["mug"]


def test_cond_goal_unsure_and_no_answer_is_unknown_no_branch_is_kept_and_the_run_ends_with_the_question():
    opt, run, log = stub((False, True), answer="")
    run.goals = [{**GOAL, "when": WHEN}, {**GOAL, "object": "cup", "when": {**WHEN, "present": False}}]
    evs, res = drain(opt.on_goals(run))
    assert res == "stop" and evs[-1]["status"] == "failed" and "Is the mug at desk?" in str(evs[-1]["say"])
    assert len(log.asked) == 1                                                          # asked once, and no guess about either branch


def test_cond_goal_dropped_goals_are_served_as_refused_and_recognised_when_read_again():
    opt, run, log = stub((True,))
    run.goals = [{**GOAL, "when": WHEN}, {**GOAL, "object": "cup", "when": {**WHEN, "present": False}}]
    drain(opt.on_goals(run))
    assert opt.refused_goals(run) == [{**GOAL, "object": "cup"}]
    assert opt.goal_dropped(run, {**GOAL, "object": "cup"}) is True and opt.goal_dropped(run, GOAL) is None


def test_cond_goal_memory_says_there_but_the_looks_do_not_asks():
    opt, run, log = stub((False, False), remembered="desk@study room", answer="no")
    run.goals = [{**GOAL, "when": WHEN}, {**GOAL, "object": "cup", "when": {**WHEN, "present": False}}]
    drain(opt.on_goals(run))
    assert len(log.asked) == 1 and [g["object"] for g in run.goals] == ["cup"]


def test_cond_goal_no_move_role_or_no_place_cannot_tell_asks_and_keeps_no_branch_without_moving():
    opt, run, log = stub(role=None)
    run.goals = [{**GOAL, "when": WHEN}, {**GOAL, "object": "cup", "when": {**WHEN, "present": False}}]
    evs, res = drain(opt.on_goals(run))
    assert res == "stop" and evs[-1]["status"] == "failed" and log.steps == [] and log.looks == 0 and len(log.asked) == 1
    opt, run, log = stub(role=None, answer="yes")                                       # the person says it is there: that decides, no look needed
    run.goals = [{**GOAL, "when": WHEN}]
    drain(opt.on_goals(run))
    assert [g["object"] for g in run.goals] == ["mug"] and log.looks == 0
    opt, run, log = stub(at="nowhere")
    run.goals = [{**GOAL, "when": {**WHEN, "at": "attic"}}]
    evs, res = drain(opt.on_goals(run))
    assert log.asked == ["Where should I look for the mug?"] and res == "stop" and evs[-1]["status"] == "failed" and log.looks == 0


def test_cond_goal_an_incomplete_when_is_no_condition_the_goal_stays():
    opt, run, log = stub()
    run.goals = [{**GOAL, "when": {"object": "mug"}}]
    assert drain(opt.on_goals(run)) == ([], None) and run.goals == [GOAL] and log.looks == 0


def test_cond_goal_empty_pack_texts_ask_nothing_and_say_nothing():
    opt, run, log = stub((False, True), answer="yes")
    opt.cfg["q_unsure"] = ""
    run.goals = [{**GOAL, "when": WHEN}]
    evs, res = drain(opt.on_goals(run))
    assert log.asked == [] and res == "stop" and [e["event"] for e in evs] == ["done"]


# ── through the loop ─────────────────────────────────────────────────────────────────────────────────────────────────
OPTS = {"cond_goal": "1", "cond_goal.prompt": PROMPT}


def test_cond_goal_through_the_loop_a_mug_on_the_desk_keeps_the_goal(make_assist, env):
    robot, loop = loop_with(make_assist, env, {"mug": DS}, [{**GOAL, "when": WHEN}], **OPTS)
    evs = run_task(loop, TASK, max_events=3000)
    und = [e for e in evs if e["event"] == "understand"]
    assert und and und[0]["goals"][0]["when"] == WHEN       # the field lives through grounding and the filters
    assert [e["flag"] for e in loop.lab_log if e["flag"] == "cond_goal"] == ["cond_goal"]
    assert any(t[0] == "pick" for t in robot.trace)
    assert evs[-1]["event"] == "done"


def test_cond_goal_through_the_loop_a_missing_mug_does_nothing_and_says_done(make_assist, env):
    robot, loop = loop_with(make_assist, env, {"mug": "bed@bedroom"}, [{**GOAL, "when": WHEN}], **OPTS)
    evs = run_task(loop, TASK, max_events=3000)
    assert evs[-1]["event"] == "done" and evs[-1]["status"] == "success"
    assert not any(t[0] == "pick" for t in robot.trace)


def test_cond_goal_through_the_loop_off_a_when_in_the_answer_changes_nothing_of_the_plan(make_assist, env):
    robot, loop = loop_with(make_assist, env, {"mug": DS}, [dict(GOAL)])
    evs = run_task(loop, TASK, max_events=3000)
    assert [e["flag"] for e in loop.lab_log if e["flag"] == "cond_goal"] == [] and evs[-1]["event"] == "done"
