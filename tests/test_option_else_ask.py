"""Option ``else_ask`` (default OFF, D4 layer L25): a request with an "otherwise" clause that no goal carries asks the person ONCE what to do
otherwise; the goals the answer adds get the other branch's condition, so ``cond_goal`` keeps the branch that holds. Made-up objects and places,
a made-up language (its else word and question are monkeypatched pack data) and the shipped English pack; no benchmark text. The loop-level
tests go through the real AssistLoop with ``cond_extract``, ``cond_goal`` and a scripted model."""
from types import SimpleNamespace as NS

import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, run_task, steps
from robot_agent.core.options import REGISTRY
from robot_agent.core.options.config import resolve
from robot_agent.core.planning import langdata
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from robot_agent.options.else_ask import SPEC, ElseAsk, else_clauses, one_condition, question, uncarried

W = {"object": "urn", "at": "hall"}
KEEP = {"type": "on", "object": "urn", "from": "", "to": "shelf", "when": {**W, "present": True}}
PUT = {"type": "on", "object": "urn", "from": "", "to": "crate"}
TASK = "if the urn is in the hall, put it on the shelf, otherwise in the crate"


MAP = Places({"shelf": {}, "crate": {}, "hall": {}, "attic": {}})              # a made-up map
CATALOGUE = {"urn": {}, "vase": {}, "green lamp": {}}                             # a made-up object memory


def make(task=TASK, lang="en", goals=(KEEP,), answers=("in the crate",), added=(PUT,), english="", filtered=()):
    """(option, run, log): a stub of the services; ``added``: the goals the reader adds when it reads the answer as a change."""
    log = NS(asked=[], rereads=[], labs=[], gates=0)
    ans = list(answers)

    def do_ask(run, q):
        log.asked.append(str(q))
        return ans.pop(0) if ans else ""

    def reread(run, text):
        log.rereads.append(text)
        return NS(added=[dict(g) for g in added]) if added is not None else None

    def gate(run):
        log.gates += 1
        return None
        yield

    svc = NS(robot=None, ask=do_ask, reread_change=reread, safety_gate=gate, cancelled=lambda run: None,
             lab=lambda *a, **k: log.labs.append(k), request_english=lambda run: english)
    run = NS(task=task, lang=lang, goals=[dict(g) for g in goals], filtered=list(filtered), places=MAP, memory=NS(data={"objects": dict(CATALOGUE)}))
    return ElseAsk(resolve(SPEC, {"else_ask": "1"}, {}, None), svc), run, log


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
def test_else_ask_registered_default_off_and_between_cond_extract_and_cond_goal_off():
    assert "else_ask" in ASSIST_OPTIONS and REGISTRY.get("else_ask").key("enabled").default is False
    assert resolve(SPEC, {}, {}, None)["enabled"] is False
    assert REGISTRY.get("cond_extract").order < SPEC.order < REGISTRY.get("cond_goal").order      # sees cond_extract's `when`, before cond_goal reads it
    assert SPEC.hooks == ("on_goals",) and not SPEC.services


def test_else_ask_a_language_without_else_words_or_question_does_nothing_empty(monkeypatch):
    opt, run, log = make(task="sol the urn hall, shelf it, zam crate", lang="zq")       # a language no pack has: no else word, no phrase
    assert go(opt, run) == ([], None) and log.asked == [] and run.goals == [KEEP]
    monkeypatch.setitem(langdata._CACHE, "zq", {"patterns": {"otherwise": r"\bzam\b"}})  # else word but no question in zq nor English
    monkeypatch.setattr("robot_agent.options.else_ask.speech.phrase", lambda robot, key, lang: None)
    opt, run, log = make(task="sol the urn hall, shelf it, zam crate", lang="zq")
    assert go(opt, run) == ([], None) and log.asked == [] and log.rereads == [] and run.goals == [KEEP]
    opt, run, log = make(goals=())                                                      # no goals at all
    assert go(opt, run) == ([], None) and log.asked == []


# ── the class: an else clause and no goal that carries it ─────────────────────────────────────────────────────────────
def test_else_ask_an_else_clause_that_names_nothing_new_asks_nothing():
    # "otherwise leave it": the else branch asks for nothing, so no goal is missing; nor when a goal already carries what it names
    for task in ("if the urn is in the hall, put it on the shelf, otherwise just leave it. Then rest",
                 "if the urn is in the hall, put it on the shelf, otherwise leave the urn in the hall"):
        opt, run, log = make(task=task)
        assert go(opt, run) == ([], None) and log.asked == [] and log.rereads == [], task
    opt, run, log = make(goals=(KEEP, PUT))                                          # the crate is carried (an unconditional goal)
    assert go(opt, run) == ([], None) and log.asked == []


def test_else_ask_an_else_clause_naming_a_catalogue_object_asks():
    opt, run, log = make(task="if the urn is in the hall, put it on the shelf; otherwise I would like the green lamp.", added=())
    go(opt, run)
    assert len(log.asked) == 1 and log.labs[0]["names"] == ["green lamp"] and run.goals == [KEEP]     # no goal added: nothing changes
    assert uncarried([" the vase"], ["vase", "urn"], MAP, [{"object": "vase"}], ("urn", "hall"), ("en",)) == []



def test_else_ask_else_clause_with_one_branch_asks_once_and_the_answer_gets_the_other_branch():
    opt, run, log = make()
    assert go(opt, run) == ([], None)
    assert log.asked == ["And otherwise, what should I do?"] and log.rereads == ["in the crate"] and log.gates == 1
    assert run.goals == [KEEP, {**PUT, "when": {**W, "present": False}}]
    assert log.labs == [{"object": "urn", "at": "hall", "names": ["crate"], "added": 1}]


def test_else_ask_a_negative_condition_gives_the_answer_the_positive_branch():
    neg = {**KEEP, "when": {**W, "present": False}}
    opt, run, log = make(goals=(neg,))
    go(opt, run)
    assert run.goals[-1]["when"]["present"] is True and len(log.asked) == 1


def test_else_ask_ask_once_a_second_pass_and_a_later_same_reading_ask_nothing_more():
    opt, run, log = make()
    go(opt, run)
    assert go(opt, run) == ([], None) and len(log.asked) == 1                        # both branches carried now: nothing to do
    run.goals = [dict(KEEP)]                                                         # the reader read the request again, as the first time
    go(opt, run)
    assert len(log.asked) == 1 and len(log.rereads) == 1 and run.goals == [KEEP, {**PUT, "when": {**W, "present": False}}]
    run.goals = [{**KEEP, "to": "crate"}]                                            # another reading: it stands, no question
    go(opt, run)
    assert len(log.asked) == 1 and run.goals == [{**KEEP, "to": "crate"}]


def test_else_ask_no_else_clause_no_question():
    opt, run, log = make(task="if the urn is in the hall, put it on the shelf")
    assert go(opt, run) == ([], None) and log.asked == [] and run.goals == [KEEP]
    opt, run, log = make(task="sol urn", english="if the urn is in the hall, put it on the shelf, otherwise in the crate")
    go(opt, run)
    assert len(log.asked) == 1                                                        # the request's reading in the prompt language counts too


def test_else_ask_both_branches_carried_no_condition_or_two_conditions_no_question():
    other = {**PUT, "when": {**W, "present": False}}
    two = {**PUT, "when": {"object": "vase", "at": "hall", "present": True}}
    for goals in ((KEEP, other), (PUT,), (KEEP, two), ({**KEEP, "when": {**W, "present": "yes"}},)):
        opt, run, log = make(goals=goals)
        assert go(opt, run) == ([], None) and log.asked == [], goals
    assert one_condition([KEEP, PUT]) == ("urn", "hall", True) and one_condition([KEEP, other]) is None


def test_else_ask_never_forces_the_branch_no_answer_or_nothing_added_keeps_the_goals():
    for kw in ({"answers": ()}, {"added": ()}, {"added": None}, {"added": (dict(KEEP),)}, {"filtered": (PUT,)}):
        opt, run, log = make(**kw)
        assert go(opt, run) == ([], None) and len(log.asked) == 1 and run.goals == [KEEP], kw
        assert log.gates == 0
    opt, run, log = make(answers=())
    go(opt, run)
    go(opt, run)
    assert len(log.asked) == 1                                                        # no answer is not asked again


def test_else_ask_a_stop_while_asking_stops_and_a_refusal_of_the_gate_stops():
    opt, run, log = make()
    opt.svc.cancelled = lambda r: {"event": "done", "status": "cancelled"}
    evs, res = go(opt, run)
    assert res == "stop" and evs[-1]["status"] == "cancelled" and log.rereads == []

    def refuse(run):
        yield {"event": "done", "status": "refused"}
        return "stop"
    opt, run, log = make()
    opt.svc.safety_gate = refuse
    evs, res = go(opt, run)
    assert res == "stop" and evs == [{"event": "done", "status": "refused"}]


def test_else_ask_made_up_language_its_own_else_word_and_question(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", {"patterns": {"otherwise": r"\bzam\b"}, "speech": {"q_else": "Zam {object} ko?", "assumed": True}})
    assert else_clauses(["sol urn hall, shelf, zam crate. Then go"], ("zq",)) == [" crate"] and else_clauses(["sol urn hall, shelf"], ("zq",)) == []
    q = question(None, "zq", "urn", "hall")
    assert q.key == "q_else" and str(q) == "And otherwise, what should I do?"          # the English text, the zq phrase said by its key
    opt, run, log = make(task="sol urn hall, shelf, zam crate", lang="zq")
    go(opt, run)
    assert len(log.asked) == 1 and run.goals[-1]["to"] == "crate"


# ── through the loop with cond_extract and cond_goal ─────────────────────────────────────────────────────────────────
DT, DS, ST = "dining table@kitchen", "desk@study room", "stool@bedroom"
LTASK = "If the urn is on the dining table, put it on the desk, otherwise put it on the stool."
G_A, G_B = {"type": "on", "object": "urn", "from": "", "to": "desk"}, {"type": "on", "object": "urn", "from": "", "to": "stool"}
EXTRACT = {"conditional": True, "object": "urn", "at": "dining table", "present": True, "if_goals": [1], "else_goals": [], "drop": [], "unsure": False}
ANSWER = "Then put it on the stool."
PROMPT = "EXTRACTX split the request.\nRequest: {request}\nGoals:\n{goals}"
LOOP_OPTS = {"cond_extract": "1", "cond_extract.prompt": PROMPT, "cond_extract.assumed": "1", "cond_goal": "1"}


def loop_run(make_assist, env, house, plan, **opts):
    clear = lambda goals: {"status": "clear", "reason": "", "question": "", "goals": goals}      # noqa: E731
    llm = FakeLLM((r"EXTRACTX", EXTRACT), (r"Then put it on the stool", clear([G_A, G_B])), default=clear([G_A]))   # the reader reads only the if branch
    robot = FakeRobot(env, house, reply_rules=[{"if": r"otherwise, what should I do", "then": ANSWER}])
    loop = make_assist(robot, llm, ScriptedPlanner(plan), **LOOP_OPTS, **opts)
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    evs = run_task(loop, LTASK, max_events=3000)
    asked = [q for q in robot.asked if "otherwise, what should I do" in str(q)]
    return robot, evs, asked


PLAN_B = steps(f"MoveTo {DS}", "Find urn", "Pick urn", f"MoveTo {ST}", f"Place {ST}")
PLAN_A = steps(f"MoveTo {DT}", "Find urn", "Pick urn", f"MoveTo {DS}", f"Place {DS}")


def test_else_ask_loop_final_condition_false_the_answered_else_branch_is_done(make_assist, env):
    robot, evs, asked = loop_run(make_assist, env, {"urn": DS}, PLAN_B, else_ask="1")
    assert len(asked) == 1                                                             # asked once
    assert evs[-1]["event"] == "done" and evs[-1]["status"] == "success", evs[-1]
    assert robot.obj["urn"] == ST and any(t[0] == "place" and t[2] for t in robot.trace)          # the end state: the urn on the stool


def test_else_ask_loop_final_condition_true_the_if_branch_is_done_and_the_else_goal_dropped(make_assist, env):
    robot, evs, asked = loop_run(make_assist, env, {"urn": DT}, PLAN_A, else_ask="1")
    assert len(asked) == 1 and evs[-1]["status"] == "success", evs[-1]
    assert robot.obj["urn"] == DS and not any(t[1] == ST for t in robot.trace)         # never forced the else branch


def test_else_ask_loop_final_off_no_question_and_the_urn_stays_regression(make_assist, env):
    # the class: off, the reader's if branch is dropped by the look and nothing is done, the else clause is never served
    robot, evs, asked = loop_run(make_assist, env, {"urn": DS}, PLAN_B)
    assert asked == [] and robot.obj["urn"] == DS and not any(t[0] == "pick" for t in robot.trace)
    assert evs[-1]["event"] == "done"


@pytest.mark.parametrize("task, house, moved", [("If the urn is on the dining table, put it on the desk.", DT, DS),
                                                 ("If the urn is on the dining table, put it on the desk, otherwise just leave it.", DS, DS)])
def test_else_ask_loop_final_no_else_clause_or_one_that_asks_for_nothing_no_question(make_assist, env, task, house, moved):
    clear = {"status": "clear", "reason": "", "question": "", "goals": [G_A]}
    llm = FakeLLM((r"EXTRACTX", EXTRACT), default=clear)
    robot = FakeRobot(env, {"urn": house}, reply_rules=[{"if": r"otherwise", "then": ANSWER}])
    loop = make_assist(robot, llm, ScriptedPlanner(PLAN_A), **LOOP_OPTS, else_ask="1")
    loop.robot.manifest.planner["action_roles"] = {"move": "MoveTo", "find": "Find"}
    evs = run_task(loop, task, max_events=3000)
    assert not [q for q in robot.asked if "otherwise" in str(q)] and robot.obj["urn"] == moved and evs[-1]["status"] == "success", evs[-1]
