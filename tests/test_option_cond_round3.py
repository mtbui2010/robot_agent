"""Layer cond_answer (option ``cond_goal``, key ``answer``, default OFF), round 3: an answer that says no and more ("No, the red gizmo.")
reads UNKNOWN by itself; after looks that all missed at a place the robot could see (not blind, not opened by this option) the look and
the no agree: False. A yes is never overridden; a blind or self-opened place, a doubt, a sighting and the confirm_none question stay strict.
Made-up cases (a gizmo, a widget, a bin, a desk, a bench), on the stubs of test_option_cond_answer."""
import pytest

from robot_agent.options.cond_goal import _UNKNOWN, look_agrees
from test_option_cond_answer import BIN, DESK, GOAL, OTHER, drain, stub, when

ANS = "No, the red gizmo."
ON = {"cond_goal.answer": "1"}


def missed_at(place, answers, opts, goals=None):
    """Every look misses at ``place`` while the memory puts the gizmo there: the person is asked once."""
    opt, run, log = stub(None, answers, opts)
    opt.svc.remembered = lambda r, o: place
    at = place.split("@")[0]
    run.goals = goals if goals is not None else [{**GOAL, "when": when(at)}, {**OTHER, "when": when(at, False)}]
    evs, res = drain(opt.on_goals(run))
    return run, log, evs, res


def agreed(log) -> bool:
    return any(x.get("part") == "answer_look_agree" for x in log.labs)


# ── the pure decision ────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("holds,yes,no,named,unsure,missed,want", [
    (_UNKNOWN, False, True, None, False, True, True),          # "no, the red one" after misses at a visible place
    (_UNKNOWN, False, True, None, False, False, False),        # a sighting, a blind or a self-opened place: no agreement
    (_UNKNOWN, True, False, None, False, True, False),         # "yes, probably late": a yes is never overridden
    (_UNKNOWN, False, True, None, True, True, False),          # "no idea, I don't know": a doubt stays unknown
    (_UNKNOWN, False, True, DESK, False, True, False),         # "no, on the desk": a place named decides (or contradicts) by itself
    (_UNKNOWN, False, False, None, False, True, False),        # "hmm": no no word, no negation
    (True, False, True, None, False, True, False),             # already decided: unchanged
    (False, False, True, None, False, True, False),
])
def test_look_agrees_only_an_unknown_no_after_misses_at_a_visible_place(holds, yes, no, named, unsure, missed, want):
    assert look_agrees(holds, yes, no, named, unsure, missed) is want


# ── through the option ───────────────────────────────────────────────────────────────────────────────────────────────
def test_answer_a_no_with_more_words_after_misses_at_a_visible_place_keeps_the_else_branch():
    run, log, evs, res = missed_at(DESK, [ANS], ON)
    assert res is None and [g["object"] for g in run.goals] == ["widget"] and len(log.asked) == 1
    assert {"part": "answer_look_agree", "place": DESK} in log.labs


def test_answer_the_same_no_at_a_place_the_option_opened_is_unknown():
    run, log, evs, res = missed_at(BIN, [ANS], {**ON, "cond_goal.open_closed": "1"})
    assert ("Open", BIN) in log.steps and res == "stop" and evs[-1]["status"] == "failed" and not agreed(log)


def test_answer_the_same_no_at_a_blind_place_is_unknown():
    run, log, evs, res = missed_at(BIN, [ANS], ON)
    assert ("Open", BIN) not in log.steps and res == "stop" and evs[-1]["status"] == "failed" and not agreed(log)


@pytest.mark.parametrize("ans", ["Yes, probably late.", "No, I don't know.", "Hmm, the red gizmo."])
def test_answer_a_yes_a_doubt_or_no_no_word_after_misses_stays_unknown(ans):
    run, log, evs, res = missed_at(DESK, [ans], ON)
    assert res == "stop" and evs[-1]["status"] == "failed" and not agreed(log)


def test_answer_a_miss_then_a_sighting_is_not_an_agreement():
    opt, run, log = stub(DESK, [ANS], ON)
    seen = iter([False, True])
    opt.svc.look_for = lambda r, o: (next(seen, False), "find")
    run.goals = [{**GOAL, "when": when("desk")}, {**OTHER, "when": when("desk", False)}]
    evs, res = drain(opt.on_goals(run))
    assert res == "stop" and evs[-1]["status"] == "failed" and not agreed(log)


@pytest.mark.parametrize("confirm", ["", ANS])
def test_confirm_none_every_goal_dropped_by_the_agreement_is_asked_again_and_unknown_there_fails(confirm):
    run, log, evs, res = missed_at(DESK, [ANS, confirm], {**ON, "cond_goal.confirm_none": "1"}, [{**GOAL, "when": when("desk")}])
    assert agreed(log) and len(log.asked) == 2 and res == "stop" and evs[-1]["status"] == "failed"


def test_cond_goal_answer_off_the_same_no_reads_as_no_as_before_and_logs_no_agreement():
    run, log, evs, res = missed_at(DESK, [ANS], {})
    assert [g["object"] for g in run.goals] == ["widget"] and not agreed(log)


def test_cond_goal_answer_empty_no_question_text_nothing_is_asked_and_the_run_fails_as_before():
    opt, run, log = stub(None, [ANS], ON, texts=False)
    opt.svc.remembered = lambda r, o: DESK
    run.goals = [{**GOAL, "when": when("desk")}, {**OTHER, "when": when("desk", False)}]
    evs, res = drain(opt.on_goals(run))
    assert log.asked == [] and res == "stop" and evs[-1]["status"] == "failed" and not agreed(log)


def test_cond_goal_answer_empty_no_condition_nothing_happens():
    opt, run, log = stub(None, [ANS], ON)
    run.goals = [dict(GOAL)]
    assert drain(opt.on_goals(run)) == ([], None) and log.asked == [] and log.steps == []


# ── regression: the class (the look and the person's no agree, the extra words made it unknown) ───────────────────────────
def test_regression_a_no_naming_another_object_after_two_misses_at_a_visible_place_does_not_give_up():
    """'If the gizmo is on the desk, bring it, else bring the widget': two looks at the open desk saw nothing, the memory said desk, the
    person said 'No, the red gizmo.' The extra words made the answer UNKNOWN and the run failed although the look and the no agree."""
    run, log, evs, res = missed_at(DESK, [ANS], {**ON, "cond_goal.open_closed": "1", "cond_goal.confirm_none": "1"})
    assert res is None and [g["object"] for g in run.goals] == ["widget"] and log.steps == [("MoveTo", DESK)]
