"""Layers cond_answer (option ``cond_goal``) and cond_schema (option ``cond_extract``), round 2 (each key default OFF):
``open_closed`` does not open a place that is not the condition's container (a goal's destination on the condition's object, or the
condition's object itself); ``confirm_none`` asks before "nothing to do" whenever no sighting decided; ``answer`` lets a yes / no decide
only when nothing else is said (or a place is named); ``kinds`` reads a presence condition that restates its branch goals' end as none.
Made-up cases (a gizmo, a widget, a bin, a desk, a bench; an urn, a shelf, a crate), on stubs."""
import pytest

from robot_agent.core.planning import clauses, goals, names
from robot_agent.options.cond_extract import restates
from robot_agent.options.cond_goal import _UNKNOWN, answer_holds, bare, reply_rx
from test_option_cond_answer import BIN, DESK, ENV, GOAL, LAYER, OTHER, drain, stub, when
from test_option_cond_schema import KINDS, answer, go, make

IN_BIN = {"type": "in", "object": "gizmo", "from": "", "to": BIN}
OC = {"cond_goal.open_closed": "1"}
OC_CN = {"cond_goal.open_closed": "1", "cond_goal.confirm_none": "1"}
EN = ("en",)


def ground(t):
    hits = [k for k in ENV if k.split("@")[0] in str(t or "").lower()]
    return hits[0] if len(hits) == 1 else None


# ── pure parts ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_ends_at_a_goal_on_the_same_object_whose_destination_is_the_place():
    assert goals.ends_at(IN_BIN, "gizmo", BIN) and goals.ends_at(dict(IN_BIN, to="bin"), "blue gizmo", BIN, ground, EN)
    assert not goals.ends_at(dict(IN_BIN, object="widget"), "gizmo", BIN, ground, EN)          # another object
    assert not goals.ends_at(dict(IN_BIN, to=DESK), "gizmo", BIN, ground, EN)                  # another destination
    assert not goals.ends_at(dict(IN_BIN, type="go"), "gizmo", BIN) and not goals.ends_at(IN_BIN, "gizmo", None)


@pytest.mark.parametrize("text,keep,want", [
    ("yes", "", True), ("no.", "", True), ("yes, that is it.", "", True), ("yes, the gizmo.", "gizmo", True),
    ("it is not there.", "", True),                                        # the negation span runs to the clause end
    ("yes, likely after dinner.", "gizmo", False), ("no, fetch the widget instead.", "gizmo", False), ("hmm, maybe", "", False),
])
def test_bare_says_nothing_beyond_yes_no_fillers_negation_and_the_condition_words(text, keep, want):
    neg = clauses.build("en").neg
    assert bare(text, reply_rx(EN), neg, names.words(keep, EN), EN) is want


def test_answer_holds_more_without_a_place_is_unknown_with_a_place_the_place_decides():
    assert answer_holds(True, False, False, None, BIN, False, more=True) == _UNKNOWN
    assert answer_holds(False, True, True, None, BIN, False, more=True) == _UNKNOWN
    assert answer_holds(True, False, False, BIN, BIN, False, more=True) is True
    assert answer_holds(False, False, True, None, BIN, False, more=True) is False             # "it isn't there": a negation alone
    assert answer_holds(True, False, False, None, BIN, False) is True                         # the default: as before


def test_restates_every_branch_goal_puts_the_condition_object_at_the_condition_place():
    gs = [{"type": "on", "object": "urn", "to": "shelf"}, {"type": "on", "object": "urn", "to": "crate"}]
    plan = {"state": "ready", "kind": "presence", "object": "urn", "at": "shelf", "if": [1], "else": []}
    assert restates(plan, gs, "shelf", lambda t: t) and not restates({**plan, "else": [2]}, gs, "shelf", lambda t: t)
    assert not restates({**plan, "kind": "state"}, gs, "shelf", lambda t: t) and not restates(plan, gs, None, lambda t: t)
    assert not restates({"state": "none"}, gs, "shelf", lambda t: t) and not restates({**plan, "if": []}, gs, "shelf", lambda t: t)


# ── (1) open_closed: the place's role ───────────────────────────────────────────────────────────────────────────────
def test_open_closed_a_goal_destination_on_the_condition_object_is_not_opened():
    opt, run, log = stub(None, opts=OC)
    run.goals = [{**IN_BIN, "when": when("bin", False)}]
    drain(opt.on_goals(run))
    assert log.steps == [("MoveTo", BIN)] and {"part": "open_skip", "place": BIN, "why": "destination"} in log.labs


def test_open_closed_a_condition_object_that_names_the_place_is_not_opened():
    opt, run, log = stub(None, opts=OC)
    run.goals = [{**GOAL, "when": {"object": "bin lid", "at": "bin", "present": True}}]
    drain(opt.on_goals(run))
    assert log.steps == [("MoveTo", BIN)] and {"part": "open_skip", "place": BIN, "why": "object"} in log.labs


def test_open_closed_another_object_to_the_place_still_opens_as_before():
    opt, run, log = stub(BIN, opts=OC)
    run.goals = [{**GOAL, "when": when("bin")}, dict(IN_BIN, object="widget")]
    drain(opt.on_goals(run))
    assert log.steps == [("MoveTo", BIN), ("Open", BIN)]


def test_cond_goal_open_closed_off_a_goal_destination_is_not_opened_either_and_nothing_logged():
    opt, run, log = stub(None)
    run.goals = [{**IN_BIN, "when": when("bin", False)}]
    drain(opt.on_goals(run))
    assert log.steps == [("MoveTo", BIN)] and not any(x.get("part") == "open_skip" for x in log.labs)


# ── (2) confirm_none: no sighting decided ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ans,status,kept", [("", "failed", None), ("no", "success", []), ("It is in the bin.", None, ["gizmo"])])
def test_confirm_none_a_miss_inside_a_place_the_option_opened_is_asked_once(ans, status, kept):
    opt, run, log = stub(DESK, [ans], OC_CN)
    run.goals = [{**GOAL, "when": when("bin")}]
    evs, res = drain(opt.on_goals(run))
    assert log.steps[:2] == [("MoveTo", BIN), ("Open", BIN)] and log.asked == ["Is the gizmo at the bin@den?"]
    assert (evs[-1]["status"] if evs else None) == status and (kept is None or [g["object"] for g in run.goals] == kept)


def test_cond_goal_confirm_none_off_a_miss_inside_the_opened_place_ends_success_without_a_question():
    opt, run, log = stub(DESK, ["no"], OC)
    run.goals = [{**GOAL, "when": when("bin")}]
    evs, res = drain(opt.on_goals(run))
    assert log.asked == [] and evs[-1]["status"] == "success"


def test_cond_goal_confirm_none_empty_no_question_text_after_an_opened_miss_ends_success_as_before():
    opt, run, log = stub(DESK, ["It is in the bin."], OC_CN, texts=False)
    run.goals = [{**GOAL, "when": when("bin")}]
    evs, res = drain(opt.on_goals(run))
    assert log.asked == [] and evs[-1]["status"] == "success"


def test_confirm_none_a_positive_sighting_keeps_the_silent_success():
    opt, run, log = stub(BIN, ["yes"], OC_CN)
    run.goals = [{**GOAL, "when": when("bin", False)}]                                            # do it only when the gizmo is NOT there
    evs, res = drain(opt.on_goals(run))
    assert log.asked == [] and evs[-1]["status"] == "success" and run.goals == []


def test_confirm_none_a_told_decision_that_drops_every_goal_is_asked_once_more():
    opt, run, log = stub(DESK, ["no", ""], {"cond_goal.answer": "1", "cond_goal.confirm_none": "1"})
    seen = iter([False, True])
    opt.svc.look_for = lambda r, o: (next(seen, False), "find")
    run.goals = [{**GOAL, "when": when("desk")}]
    evs, res = drain(opt.on_goals(run))
    assert len(log.asked) == 2 and evs[-1]["status"] == "failed" and res == "stop"


def test_confirm_none_a_question_when_is_confirmed_with_its_own_question():
    opt, run, log = stub(DESK, ["No.", "No."], {"cond_goal.answer": "1", "cond_goal.confirm_none": "1"})
    run.goals = [{**GOAL, "when": {"present": True, "ask": "Will it snow today?"}}]
    evs, res = drain(opt.on_goals(run))
    assert log.asked == ["Will it snow today?"] * 2 and evs[-1]["status"] == "success"


# ── (3) answer: a yes / no decides only a bare answer ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ans,kept", [("Yes, likely after dinner.", None), ("No, fetch the widget instead.", None), ("Yes, the gizmo.", ["gizmo"]),
                                      ("No.", ["widget"]), ("Yes, it is on the desk.", ["gizmo"])])
def test_answer_a_yes_or_no_followed_by_something_else_decides_nothing(ans, kept):
    opt, run, log = stub(DESK, [ans], {"cond_goal.answer": "1"})
    seen = iter([False, True])
    opt.svc.look_for = lambda r, o: (next(seen, False), "find")
    run.goals = [{**GOAL, "when": when("desk")}, {**OTHER, "when": when("desk", False)}]
    evs, res = drain(opt.on_goals(run))
    if kept is None:
        assert res == "stop" and evs[-1]["status"] == "failed"
    else:
        assert res is None and [g["object"] for g in run.goals] == kept


def test_cond_goal_answer_off_a_yes_with_more_words_still_reads_as_yes():
    opt, run, log = stub(DESK, ["Yes, likely after dinner."], {})
    seen = iter([False, True])
    opt.svc.look_for = lambda r, o: (next(seen, False), "find")
    run.goals = [{**GOAL, "when": when("desk")}, {**OTHER, "when": when("desk", False)}]
    drain(opt.on_goals(run))
    assert [g["object"] for g in run.goals] == ["gizmo"]


# ── (4) kinds: a condition that restates the goals' own end ─────────────────────────────────────────────────────────────
def test_kinds_a_presence_condition_restating_its_only_branch_goal_leaves_the_goals_as_read():
    opt, run, log = make(answer(at="shelf", present=False, if_goals=[1], else_goals=[], always=[]), **KINDS)
    before = [dict(g) for g in run.goals]
    assert go(opt, run) == ([], None) and run.goals == before
    assert log.labs[-1]["state"] == "none" and log.labs[-1]["restates"] is True


def test_kinds_a_condition_with_another_branch_goal_is_still_applied():
    opt, run, log = make(answer(at="shelf", present=False, if_goals=[1], else_goals=[2], always=[3]), **KINDS)
    go(opt, run)
    assert [bool(g.get("when")) for g in run.goals] == [True, True, False] and "restates" not in log.labs[-1]


def test_cond_extract_kinds_off_a_restating_condition_is_applied_as_before():
    opt, run, log = make({k: v for k, v in answer(at="shelf", if_goals=[1], else_goals=[], always=[]).items()})
    go(opt, run)
    assert run.goals[0].get("when") and "restates" not in log.labs[-1]


def test_cond_extract_kinds_empty_no_goals_nothing_is_called():
    opt, run, log = make(answer(), **KINDS)
    run.goals = []
    assert go(opt, run) == ([], None) and log.calls == []


# ── regression: the class (the condition read as the goal's own destination, an answer that says more than yes) ──────────
def test_regression_destination_read_as_condition_place_and_a_hedged_yes_ends_failed_not_success():
    """'If the parcel arrives, drop the gizmo in the bin': the condition came back as 'the gizmo is not in the bin'; the bin was opened, the
    miss was doubted by the memory, and 'Yes, likely after dinner.' read as yes dropped the only goal: 'success, nothing to do'."""
    opt, run, log = stub(None, ["Yes, likely after dinner."], LAYER)
    opt.svc.remembered = lambda r, o: BIN
    run.goals = [{**IN_BIN, "when": when("bin", False)}]
    evs, res = drain(opt.on_goals(run))
    assert ("Open", BIN) not in log.steps and res == "stop" and evs[-1]["status"] == "failed"
