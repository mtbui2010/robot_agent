"""R4b item 2: dialogue convergence (core/planning/dialog_converge.py and its use in AssistLoop).

Synthetic words and places written for these tests (no benchmark text)."""
import types

from robot_agent.core.planning import dialog_converge as dc
from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places, _DONE, _NEXT, _STOP

ENV = {
    "shelf@hall": {"aliases": ["shelf", "선반"]},
    "bench@porch": {"aliases": ["bench"]},
    "crate@porch": {"aliases": ["crate"]},
    "owner": {"person": "owner", "speaker": True, "aliases": ["me"], "loc": {"x": 0.0, "y": 0.0}},
}


def G(obj, to, frm="", **kw):
    return {"type": "on", "object": obj, "from": frm, "to": to, **kw}


PITCHER_LAMP = [G("blue pitcher", "bench@porch"), G("red lamp", "shelf@hall")]


# ── goal_sig / same_question ──
def test_goal_sig_tells_a_new_reading_from_a_repeat():
    a = dc.goal_sig(PITCHER_LAMP)
    assert a == dc.goal_sig([dict(g) for g in PITCHER_LAMP])
    assert a != dc.goal_sig([G("blue pitcher", "shelf@hall"), PITCHER_LAMP[1]])
    assert dc.goal_sig([]) == ()


def test_same_question_ignores_politeness_not_content():
    q1 = "What do you mean by 'zonk widget'? Is it a toy or something else?"
    q2 = "Could you clarify what you mean by 'zonk widget'? Is it a toy or something else?"
    assert dc.same_question(q1, q2)
    assert not dc.same_question("Where should I put the cup?", "Where should I put the cap?")
    assert not dc.same_question("Which one?", "Which one?")          # too short to compare by words


# ── correction_edit ──
def places():
    return Places(ENV)


def test_correction_edits_only_the_named_goal():
    kind, edits = dc.correction_edit(PITCHER_LAMP, ["No, put the blue pitcher on the shelf."], places())
    assert kind == "edit" and edits == [(0, "shelf@hall")]


def test_correction_to_the_speaker():
    kind, edits = dc.correction_edit(PITCHER_LAMP, ["No, bring the red lamp to me."], places())
    assert kind == "edit" and edits == [(1, "@speaker")]


def test_a_heard_slip_in_the_object_word_still_names_the_goal():
    kind, edits = dc.correction_edit(PITCHER_LAMP, ["No, bring the blue pitchar to me"], places())
    assert kind == "edit" and edits == [(0, "@speaker")]


def test_same_destination_means_the_fault_is_elsewhere():
    kind, edits = dc.correction_edit(PITCHER_LAMP, ["No, the red lamp on the shelf."], places())
    assert (kind, edits) == ("same", [])


def test_no_single_object_or_destination_leaves_the_old_path():
    p = places()
    assert dc.correction_edit(PITCHER_LAMP, ["No, on the shelf."], p)[0] == "none"                      # no object
    assert dc.correction_edit(PITCHER_LAMP, ["No, the blue pitcher and the red lamp to me"], p)[0] == "none"
    assert dc.correction_edit(PITCHER_LAMP, ["No, the blue pitcher"], p)[0] == "none"                  # no destination
    assert dc.correction_edit(PITCHER_LAMP, ["No, the blue pitcher to me on the shelf"], p)[0] == "none"
    assert dc.correction_edit(PITCHER_LAMP, ["No, the red lamp on the porch"], p)[0] == "none"         # two places
    two = [G("lamp", "shelf@hall"), G("lamp", "bench@porch")]                                         # one name twice
    assert dc.correction_edit(two, ["No, the lamp to me"], p)[0] == "edit"                            # both go to the speaker


def test_korean_names_and_particles():
    goals = [G("blue pitcher", "bench@porch", name_local="파란 주전자"), G("red lamp", "shelf@hall", name_local="빨간 램프")]
    kind, edits = dc.correction_edit(goals, ["아니요, 주전자는 선반에 놔 주세요"], places())
    assert kind == "edit" and edits == [(0, "shelf@hall")]
    kind, edits = dc.correction_edit(goals, ["아니요, 램프는 저한테 갖다 주세요"], places())
    assert kind == "edit" and edits == [(1, "@speaker")]
    kind, edits = dc.correction_edit(goals, ["아니요, 주전차는 저한테 갖다 주세요"], places())          # a slip of the recognizer
    assert kind == "edit" and edits == [(0, "@speaker")]


def test_without_a_speaker_entry_a_correction_to_me_is_not_patched():
    env = {k: v for k, v in ENV.items() if k != "owner"}
    assert dc.correction_edit(PITCHER_LAMP, ["No, the red lamp to me"], Places(env))[0] == "none"


# ── the loop ──
def run_gen(g):
    out = []
    try:
        while True:
            out.append(next(g))
    except StopIteration as e:
        return e.value, out


class _R:
    def __init__(self, goals, **kw):
        self.goals, self.task, self.dialogue = [dict(g) for g in goals], "move the pitcher and the lamp", []
        self.places, self.lang, self.heard, self.confirms, self.asked = Places(ENV), "en", 0, 0, 0
        self.memory = types.SimpleNamespace(remember_answer=lambda *a: None, where=lambda o: None)
        self.events, self.node = [], None
        self.readings, self.rejected, self.refunds = [], [], 0
        self.seen_sigs, self.part_asked, self.patched = set(), False, None
        self.pin_to, self.converge = None, True
        self.__dict__.update(kw)

    def ev(self, name, **kw):
        self.events.append((name, kw))
        return {"event": name, **kw}


def loop(**cfg):
    lp = AssistLoop.__new__(AssistLoop)
    lp.cfg = {"confirm_answers": True, "confirm_task": True, "clarify_rounds": 3, "dialog_converge": "1",
              "yes_no_reasks": 1, **cfg}
    lp.robot = types.SimpleNamespace(plan_actions={"PutIn"}, text=lambda *a, **k: "")
    lp.original = None
    lp._answer_source = "robot"
    lp._cancelled = lambda r: None
    lp._log_fn = lambda k: None
    lp._restates = lambda r, a: False
    lp._finish = lambda r, status, msg="", **kw: {"event": "done", "status": status, "msg": msg, **kw}
    lp.__dict__["_language"] = types.SimpleNamespace(hear=lambda t: types.SimpleNamespace(english="", trusted=True))
    lp._remembered = lambda memory, obj: None
    return lp


def read_back(lp, r, answer, ok=False):
    lp._ask_yes_no = lambda r_, q: (answer, ok, answer.split(",", 1)[1].strip() if "," in answer else "")
    return run_gen(lp._read_back(r))


def test_a_correction_that_names_a_goal_edits_it_and_nothing_else():
    lp, r = loop(), _R(PITCHER_LAMP)
    (out, q), _ = read_back(lp, r, "No, put the blue pitcher on the crate")
    assert out is _NEXT and q is None
    assert [g["to"] for g in r.patched] == ["crate@porch", "shelf@hall"]
    assert r.patched[1] == PITCHER_LAMP[1]                      # the other goal is what it was
    assert r.pin_to is None and "bench@porch" in r.replaced


def test_a_correction_to_the_speaker_is_grounded_as_a_delivery():
    lp, r = loop(), _R(PITCHER_LAMP)
    (out, _q), _ = read_back(lp, r, "No, bring the red lamp to me")
    assert out is _NEXT and r.patched[1]["to"] == "owner" and r.patched[1].get("hand")
    assert r.patched[0] == PITCHER_LAMP[0]


def test_a_speaker_the_robot_cannot_place_is_not_patched_but_asked_about_later():
    env = {**ENV, "owner": {"person": "owner", "speaker": True, "aliases": ["me"]}}         # no last-seen position
    lp, r = loop(), _R(PITCHER_LAMP, places=Places(env))
    (out, q), _ = read_back(lp, r, "No, bring the red lamp to me")
    assert out is _NEXT and r.patched is None and r.pin_to is None        # the task is understood again with the answer


def test_the_patched_goals_are_taken_without_a_new_understanding():
    lp, r = loop(), _R(PITCHER_LAMP)
    r.patched = [dict(g, to="crate@porch") for g in PITCHER_LAMP]

    def boom(*a, **k):
        raise AssertionError("understand must not run")
    lp._understand = boom
    got, _ = run_gen(lp._take_understanding(r))
    u, q = got
    assert q is None and u["status"] == "clear" and r.goals[0]["to"] == "crate@porch" and r.patched is None


def test_a_correction_the_goal_already_satisfies_is_read_back_once_more_then_asks_which_part():
    lp, r = loop(), _R(PITCHER_LAMP)
    (out, q), _ = read_back(lp, r, "No, the red lamp on the shelf")
    assert out is _NEXT and r.patched is not None and r.allow_repeat       # maybe misheard: the same goals again
    r.patched = None
    (out, q), _ = read_back(lp, r, "No, the red lamp on the shelf")
    assert out is None and isinstance(q, speech.LocalText) and q.key == "q_which_part"


def test_a_bare_no_to_a_reading_refused_before_asks_which_part():
    lp, r = loop(), _R(PITCHER_LAMP)
    (out, q), _ = read_back(lp, r, "No")
    assert q.key == "q_what_do"
    (out, q), _ = read_back(lp, r, "No")                      # the same goals refused again
    assert q.key == "q_which_part"


def test_a_refused_reading_is_not_read_back_again():
    lp, r = loop(), _R(PITCHER_LAMP)
    r.rejected = [dc.goal_sig(r.goals)]
    asked = []
    lp._ask_clarify = lambda r_, q: iter(()) if asked.append(q) else iter(())
    out, _ = run_gen(lp._refused_reading(r))
    assert [q.key for q in asked] == ["q_which_part"] and r.part_asked
    out, evs = run_gen(lp._refused_reading(r))                # asked once; the second time the run ends
    assert out is _STOP and evs[-1]["status"] == "failed"
    r2 = _R(PITCHER_LAMP)                                     # a different reading: read back as usual
    r2.rejected = [dc.goal_sig([G("blue pitcher", "crate@porch")])]
    assert run_gen(lp._refused_reading(r2))[0] is None
    off = loop(dialog_converge="0")
    r3 = _R(PITCHER_LAMP)
    r3.rejected = [dc.goal_sig(r3.goals)]
    assert run_gen(off._refused_reading(r3))[0] is None


def test_the_read_back_cap_counts_readings_that_gained_nothing():
    lp = loop()
    same = _R(PITCHER_LAMP, confirms=2, readings=[dc.goal_sig(PITCHER_LAMP)] * 2)
    (out, _q), evs = read_back(lp, same, "No")
    assert out is _STOP and evs[-1]["msg"] == "the user did not confirm the task"
    new = _R(PITCHER_LAMP, confirms=2, readings=[dc.goal_sig(PITCHER_LAMP), dc.goal_sig([G("a", "shelf@hall")])])
    (out, q), _ = read_back(lp, new, "No")                      # two different readings: one more is allowed
    assert out is None and new.confirms == 3
    off = loop(dialog_converge="0")
    (out, _q), evs = read_back(off, _R(PITCHER_LAMP, confirms=2, readings=[(), ("x",)]), "No")
    assert out is _STOP


def test_a_question_that_changed_the_goals_gives_its_round_back_twice_at_most():
    lp = loop()
    r = _R([], asked=2, dialogue=[("robot", "q"), ("user", "a")])
    r.goals = [G("a", "shelf@hall")]
    lp._progress(r)
    assert r.asked == 1 and r.refunds == 1
    lp._progress(r)                                             # the same goals again: nothing gained
    assert r.asked == 1
    r.goals = [G("b", "shelf@hall")]
    lp._progress(r)
    r.goals = [G("c", "shelf@hall")]
    lp._progress(r)
    assert r.refunds == 2 and r.asked == 0                       # at most two refunds
    off = loop(dialog_converge="0")
    r2 = _R([G("a", "shelf@hall")], asked=2, dialogue=[("robot", "q"), ("user", "a")])
    off._progress(r2)
    assert r2.asked == 2


def test_no_first_progress_without_a_question():
    lp = loop()
    r = _R([G("a", "shelf@hall")], asked=0)
    lp._progress(r)
    assert r.asked == 0 and r.refunds == 0


def test_near_identical_question_is_reworded_once():
    r = types.SimpleNamespace(dialogue=[("robot", "What do you mean by 'zonk widget'? Is it a toy or something else?"),
                                        ("user", "not sure")], converge=True)
    q = AssistLoop._fresh_question(r, "Could you clarify what you mean by 'zonk widget'? Is it a toy or something else?")
    assert isinstance(q, speech.LocalText) and q.key == "q_unclear_task"
    r2 = types.SimpleNamespace(dialogue=r.dialogue, converge=False)          # the ablation: only word for word
    same = "Could you clarify what you mean by 'zonk widget'? Is it a toy or something else?"
    assert AssistLoop._fresh_question(r2, same) == same


def test_a_person_is_not_a_source_in_the_read_back():
    places_ = Places(ENV)
    nice = lambda k: speech.say_name(places_, k, "en")                       # noqa: E731
    g = G("blue pitcher", "shelf@hall", frm="owner")
    assert "from" not in str(AssistLoop._goal_phrase(g, places_, nice))
    g2 = G("blue pitcher", "shelf@hall", frm="bench@porch")
    assert "from the" in str(AssistLoop._goal_phrase(g2, places_, nice))


def test_which_part_phrase_exists_in_core_english():
    assert speech.CORE_PHRASES["q_which_part"].startswith("Which part")
    assert dc  # module imported
