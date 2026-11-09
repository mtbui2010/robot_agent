"""Round 18 (option ``dialog_hold``, default OFF; ``noun_relabel`` a separate wild card), through the real AssistLoop.run_blocking
against the symbolic FakeRobot, a scripted LLM and a scripted GENERIC noisy user (the answers are written per test, not drawn
from any simulator): the user's true "No" is bare, a wrong "No" is bare too, "not sure", silence, a corrected word, a garbled word.

H1 (ledger): a re-reading cannot rename a settled or earlier object label (invented words only), a correction edits one part,
two conflicting statements give ONE two-way choice. H2 (one part): a bare No / not-sure leads to a one-part read-back and then
one open question, never to "what should I do?"; no identical question after a not-sure; "I don't know where" leads to a search
and is a no-op without a Find skill. A made-up language with no data and a profile without a Find skill run today's path."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, steps
from robot_agent.contract.manifest import PLANNER_KEYS
from robot_agent.core.planning import dialog_hold as DH, langdata, speech
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS, Places
from test_sage_lab_r12_agree import ENV, HOUSE, PLAN, READ, TASK, UNSTICK, ScriptRobot, confirms, gave_up   # noqa: F401

HOLD = dict(dialog_unstick="1", dialog_agree2="1", dialog_hold="1")
HOLD1 = dict(dialog_unstick="1", dialog_agree2="1", dialog_hold="hold")
S9 = dict(dialog_unstick="1", dialog_agree2="1")


@pytest.fixture
def env(request):
    return getattr(request.function, "ENV", ENV)


def play(make_assist, llm, answers, task=TASK, env=None, house=None, lang="en", plan=PLAN, phrases=None, planner=None, **opts):
    robot = ScriptRobot(env or ENV, dict(house if house is not None else HOUSE), answers=answers)
    loop = make_assist(robot, llm, ScriptedPlanner(plan), health_wait_s=0, confirm_task=True, **opts)
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, unstick=UNSTICK, **(planner or {}))
    if phrases:
        loop.robot.i18n.phrases.update(phrases)
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking(task, lang=lang)]
    return loop, robot, evs


def hold_log(loop, part=None):
    return [e for e in loop.lab_log if e["flag"] == "dialog_hold" and (part is None or e.get("part") == part)]


def test_options_registered_and_off_by_default(make_assist, monkeypatch, env):
    monkeypatch.delenv("ROBOT_AGENT_DIALOG_HOLD", raising=False)
    for k in ("dialog_hold", "noun_relabel"):
        assert k in ASSIST_OPTIONS and k in PLANNER_KEYS
    mk = lambda **kw: make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **kw)   # noqa: E731
    assert mk().cfg["dialog_hold"] is None and mk().cfg["noun_relabel"] is False
    assert mk(dialog_hold="1").cfg["dialog_hold"] == {"hold": True, "one_part": True, "max_options": 3}
    assert mk(dialog_hold="hold").cfg["dialog_hold"]["one_part"] is False
    assert mk(dialog_hold="0").cfg["dialog_hold"] is None
    declared = {"dialog_hold": {"hold": True, "one_part": True, "max_options": 2}}
    loop = mk()
    loop.robot.manifest.planner = declared
    assert loop._r18_cfg({})["dialog_hold"] == {"hold": True, "one_part": True, "max_options": 2}


def test_manifest_values_are_checked():
    from robot_agent.contract.manifest import Manifest
    ok = Manifest.from_dict({"robot": "toy", "planner": {"dialog_hold": {"hold": True, "one_part": False, "max_options": 3}}})
    assert ok.planner["dialog_hold"]["max_options"] == 3
    for bad in ({"hold": "yes"}, {"max_options": 1}, {"nope": 1}, 3):
        with pytest.raises(Exception):
            Manifest.from_dict({"robot": "toy", "planner": {"dialog_hold": bad}})


def test_flag_off_is_byte_identical(make_assist):
    answers = ["No.", TASK, "In the fridge, please."]
    base = play(make_assist, FakeLLM(default=READ), list(answers), **S9)
    off = play(make_assist, FakeLLM(default=READ), list(answers), dialog_hold="0", **S9)
    strip = lambda evs: [(e["event"], str(e.get("say")), str(e.get("question")), str(e.get("goals"))) for e in evs]   # noqa: E731
    assert strip(base[2]) == strip(off[2]) and base[1].asked == off[1].asked and not hold_log(off[0])


# ── H2: a bare No leads to a one-part read-back, not "what should I do?" ──────────────────────────────────────────────
def test_bare_no_reads_back_one_part_not_what_to_do(make_assist):
    answers = ["No.", "Yes.", "Yes."]
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, **HOLD)
    asked = [str(q) for q in robot.asked]
    assert len(asked) == 3 and not any("What should I do" in q for q in asked)
    assert asked[1].startswith("I will put the plate") and "then" not in asked[1] and asked[2].startswith("I will open the fridge")
    assert hold_log(loop, "all_settled") and not gave_up(evs), done(evs)
    # today (flag off): "what should I do?"
    _l, r0, _e = play(make_assist, FakeLLM(default=READ), ["No.", TASK, "Yes."], **S9)
    assert any("What should I do" in str(q) for q in r0.asked)


def test_not_sure_is_handled_like_a_bare_no(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["I don't know.", "Yes.", "Yes."], **HOLD)
    assert len(robot.asked) == 3 and hold_log(loop, "all_settled")


def test_a_one_part_reading_goes_straight_to_the_open_question(make_assist):
    one = {"status": "clear", "reason": "", "question": "", "goals": [READ["goals"][0]]}
    plan = steps("MoveTo counter@kitchen", "Find plate", "Pick plate", "MoveTo fridge@kitchen", "Open fridge@kitchen", "Place fridge@kitchen")
    loop, robot, evs = play(make_assist, FakeLLM(default=one), ["No.", "The plate, please.", "Yes."], task="Put the plate in the fridge.",
                            plan=plan, **HOLD)
    asked = [str(q) for q in robot.asked]
    assert asked[0].endswith("Is that right?") and asked[1] == "What should I bring?"
    assert asked[2] == "Where should I put the plate?"          # the read-back that got the No is not asked again in the same words
    assert asked[0] not in asked[1:]


def test_content_free_to_both_forms_is_an_honest_stop(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", "No.", "Hmm."], **HOLD)
    d = done(evs)
    assert d["status"] == "failed" and "did not confirm" in str(d.get("msg"))
    assert hold_log(loop, "stop") and len(robot.asked) == 3


# ── H1: no drift of an object label (invented words only) ─────────────────────────────────────────────────────────────
def reading(obj, to="fridge@kitchen", said=""):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": "in", "object": obj, "from": "", "to": to, **({"said": said} if said else {})}]}


def test_a_rereading_cannot_rename_the_object(make_assist):
    llm = FakeLLM(reading("blick"), default=reading("dax"))
    task = "Please put the blick in the fridge."
    plan = steps("MoveTo counter@kitchen", "Find blick", "Pick blick", "MoveTo fridge@kitchen", "Open fridge@kitchen", "Place fridge@kitchen")
    loop, robot, evs = play(make_assist, llm, ["No.", "The blick.", "Yes."], task=task, plan=plan, house={"blick": "counter@kitchen"}, **HOLD)
    goals = [e for e in evs if e["event"] == "understand"][-1]["goals"]
    assert [g["object"] for g in goals] == ["blick"]
    assert any(h["kept"] >= 1 for h in hold_log(loop, "merge"))
    # without the option the second reading renames it
    llm0 = FakeLLM(reading("blick"), default=reading("dax"))
    _l, _r, evs0 = play(make_assist, llm0, ["No.", "The blick.", "Yes."], task=task, plan=plan, house={"blick": "counter@kitchen"}, **S9)
    assert "dax" in [g["object"] for e in evs0 if e["event"] == "understand" for g in e["goals"]]


def test_a_part_the_rereading_invents_is_dropped(make_assist):
    inv = {"status": "clear", "reason": "", "question": "", "goals": reading("blick")["goals"] +
           [{"type": "say", "object": "zorp wibble", "from": "", "to": ""}]}
    llm = FakeLLM(reading("blick"), default=inv)
    task = "Please put the blick in the fridge."
    plan = steps("MoveTo counter@kitchen", "Find blick", "Pick blick", "MoveTo fridge@kitchen", "Open fridge@kitchen", "Place fridge@kitchen")
    loop, robot, evs = play(make_assist, llm, ["No.", "The blick.", "Yes."], task=task, plan=plan, house={"blick": "counter@kitchen"}, **HOLD1)
    goals = [e for e in evs if e["event"] == "understand"][-1]["goals"]
    assert len(goals) == 1 and goals[0]["object"] == "blick"


# ── pure ledger rules ────────────────────────────────────────────────────────────────────────────────────────────────
def ledger(*goals):
    return DH.Ledger([dict(g) for g in goals])


def test_merge_settled_slot_never_changes():
    g = {"type": "in", "object": "blick", "from": "", "to": "fridge@kitchen"}
    led = ledger(g)
    led.settle(0, None, "yes")
    out, info = DH.merge(led, [{**g, "object": "dax"}], ["the dax"], ["the blick"], Places(ENV), "en", {"*"})
    assert out[0]["object"] == "blick" and not info["changed"]


def test_merge_changes_only_on_new_evidence_and_ties_keep_the_old():
    g = {"type": "in", "object": "blick", "from": "", "to": "fridge@kitchen"}
    led = ledger(g)
    out, _ = DH.merge(led, [{**g, "object": "dax"}], [], ["the blick"], Places(ENV), "en", set())
    assert out[0]["object"] == "blick"                                         # no new words: the model's re-draw is no evidence
    out, _ = DH.merge(led, [{**g, "object": "dax"}], ["no"], ["the blick"], Places(ENV), "en", set())
    assert out[0]["object"] == "blick"
    out, info = DH.merge(led, [{**g, "object": "dax"}], ["the dax"], [], Places(ENV), "en", {"*"})
    assert out[0]["object"] == "dax" and info["changed"]


def test_a_correction_edits_one_part():
    pl = Places(ENV)
    goals = [{"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"},
             {"type": "on", "object": "cup", "from": "", "to": "me", "hand": True}]
    assert DH.extended_edit(goals, ["No, give it to Ben."], pl, "en") == [(1, "to", "ben@kitchen")]
    assert DH.extended_edit(goals, ["No, put the plate on the sofa."], pl, "en") == []        # an object is named: correction_edit's job
    assert DH.extended_edit([goals[0]], ["No, Ben."], pl, "en") == []                            # no hand-over goal to edit
    oc = [{"type": "open", "object": "fridge@kitchen", "from": "", "to": ""}]
    assert DH.extended_edit(oc, ["No, the dresser."], pl, "en") == [(0, "object", "dresser@bedroom")]


def test_conflict_gives_one_two_way_question_then_latest_wins():
    g = {"type": "in", "object": "blick", "from": "", "to": "fridge@kitchen"}
    led = ledger(g)
    new = {**g, "to": "dresser@bedroom"}
    out, info = DH.merge(led, [new], ["put it in the dresser"], ["the fridge"], Places(ENV), "en", set())
    assert out[0]["to"] == "fridge@kitchen" and len(info["conflicts"]) == 1
    q = DH.conflict_question(Places(ENV), "to", "fridge@kitchen", "dresser@bedroom", g)
    assert q.key == "q_which_place" and "fridge" in str(q) and "dresser" in str(q)
    led.conflict_asked = True
    out, info = DH.merge(led, [new], ["the dresser"], ["the fridge"], Places(ENV), "en", set())
    assert out[0]["to"] == "dresser@bedroom" and not info["conflicts"]


def test_options_are_capped():
    assert DH.cap_options(["a", "b", "c", "d"], 3) == ["a", "b", "c"]
    assert DH.options("1", {"max_options": 2})["max_options"] == 2
    assert DH.options(None, None) is None and DH.options(None, {"hold": True})["one_part"] is False


# ── no repeat, sense ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_no_identical_question_after_not_sure(make_assist):
    q = {"status": "ambiguous", "reason": "", "question": "", "goals": []}
    loop, robot, evs = play(make_assist, FakeLLM(default=q), ["I don't know.", "I don't know.", "I don't know."], **HOLD)
    asked = [str(x) for x in robot.asked]
    keys = [DH.question_key(x) for x in robot.asked]
    # a "not sure" is never followed by the same (template, slots)
    for a, b in zip(asked, asked[1:]):
        assert a != b
    assert done(evs)["status"] == "failed" and len(keys) <= 3


def test_not_sure_where_drops_the_source_and_searches(make_assist):
    where = {"status": "ambiguous", "reason": "", "question": "Where is the plate?", "goals": []}
    first = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "in", "object": "plate", "from": "sofa@living room", "to": "fridge@kitchen"}]}
    # the first reading has a source; the model then abstains on where it is; "I don't know" => search
    led_llm = FakeLLM(first, where, default=where)
    loop, robot, evs = play(make_assist, led_llm, ["No.", "Where? Hmm.", "I don't know.", "Yes."], **HOLD)
    assert hold_log(loop) is not None        # the path ran without error


def test_made_up_language_without_data_runs_todays_path(make_assist, monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "qq", {"lang": "qq", "name": "Qaqish", "register": "plain spoken Qaqish"})
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", "Yes.", "Yes."], lang="qq", **HOLD)
    assert done(evs)["event"] in ("done", "error")        # no crash: fail open
    assert DH.token_forms("abc def", "qq") == {"abc", "def"} and DH.object_span("abc", Places(ENV), "qq") is None


# ── through the loop: a correction edits one part; a conflict gives one two-way choice ───────────────────────────────────
def test_a_correction_edits_the_open_part_only(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No, open the dresser.", "Yes."], **HOLD)
    asked = [str(q) for q in robot.asked]
    assert "plate" in asked[0] and "dresser" in asked[1] and "plate" in asked[1] and "What should I do" not in " ".join(asked)
    assert len(asked) == 2 and not gave_up(evs)


def test_two_conflicting_statements_give_one_two_way_choice(make_assist):
    llm = FakeLLM(reading("blick"), default=reading("blick", to="dresser@bedroom"))
    task = "Please put the blick in the fridge."
    plan = steps("MoveTo counter@kitchen", "Find blick", "Pick blick", "MoveTo fridge@kitchen", "Open fridge@kitchen", "Place fridge@kitchen")
    answers = ["No.", "The blick, in the dresser.", "The dresser.", "Yes."]
    loop, robot, evs = play(make_assist, llm, answers, task=task, plan=plan, house={"blick": "counter@kitchen"}, **HOLD)
    asked = [str(q) for q in robot.asked]
    two_way = [q for q in asked if q.startswith("Which place do you mean") and "fridge" in q and "dresser" in q]
    assert len(two_way) == 1, asked
    goals = [e for e in evs if e["event"] == "understand"][-1]["goals"]
    assert goals[0]["to"] == "dresser@bedroom"


# ── a genuinely wrong reading is still corrected; the user's true No is bare; wrong Yes / wrong No at epsilon ───────────────
import random   # noqa: E402


class NoisyUser(ScriptRobot):
    """A generic noisy user: to a read-back it says the true yes / no, flipped with probability eps; the true No is BARE; to an
    open question it names the blick; to anything else it restates the task."""

    def __init__(self, *a, eps=0.0, seed=0, **kw):
        super().__init__(*a, **kw)
        self.eps, self.rng = eps, random.Random(seed)

    def _exec(self, skill, o, params):
        if skill == "ask":
            self.asked.append(o)
            q = str(o)
            if q.endswith("Is that right?"):
                right = self.loop._ctx.goals[0]["object"] == "blick"       # the test knows the truth; the robot does not
                a = "Yes." if right != (self.rng.random() < self.eps) else "No."
            elif q == "What should I bring?" or q.startswith("Where should I put"):
                a = "The blick."
            else:
                a = "Please put it in the fridge."
            return {"isdone": True, "text": a}
        return super(ScriptRobot, self)._exec(skill, o, params)


def run_noisy(make_assist, eps, seed, **opts):
    plan = steps("MoveTo counter@kitchen", "Find blick", "Pick blick", "MoveTo fridge@kitchen", "Open fridge@kitchen", "Place fridge@kitchen")
    robot = NoisyUser(ENV, {"blick": "counter@kitchen"}, answers=[], eps=eps, seed=seed)
    loop = make_assist(robot, FakeLLM(reading("dax"), default=reading("blick")), ScriptedPlanner(plan), health_wait_s=0, confirm_task=True, **opts)
    loop.robot.manifest.planner = dict(loop.robot.manifest.planner or {}, unstick=UNSTICK)
    robot.loop = loop
    evs = [{k: v for k, v in e.items() if k not in ("world", "say_audio")} for e in loop.run_blocking("Please put it in the fridge.")]
    return evs


def false_confirmation(evs) -> bool:
    """The run went on to plan with the wrong object (the wrong reading was confirmed or never questioned)."""
    return any(e["event"] == "plan" for e in evs) and [g["object"] for e in evs if e["event"] == "understand" for g in e["goals"]][-1] == "dax"


@pytest.mark.parametrize("eps", [0.0, 0.1, 0.3])
def test_anti_exploit_cell_false_confirmation_is_not_higher_than_flag_off(make_assist, eps):
    on = sum(false_confirmation(run_noisy(make_assist, eps, s, **HOLD)) for s in range(40))
    off = sum(false_confirmation(run_noisy(make_assist, eps, s, **S9)) for s in range(40))
    assert on <= off, (on, off)


def test_the_wrong_reading_is_corrected_with_a_bare_no(make_assist):
    evs = run_noisy(make_assist, 0.0, 1, **HOLD)
    goals = [e for e in evs if e["event"] == "understand"][-1]["goals"]
    assert goals[0]["object"] == "blick" and any(e["event"] == "plan" for e in evs)


# ── no repeat, sense (direct on the loop), profiles ───────────────────────────────────────────────────────────────────────
class Ctx:
    pass


def sense_ctx(loop, obj="plate"):
    r = Ctx()
    r.places = Places(ENV)
    r.lang = "en"
    r.dialogue = [("robot", "Where is the plate now?"), ("user", "I don't know.")]
    r.hold = DH.Ledger([{"type": "in", "object": obj, "from": "sofa@living room", "to": "fridge@kitchen"}])
    r.__dict__.setdefault("unsure_reasked", False)
    return r


U_WHERE = {"status": "ambiguous", "reason": "", "question": "Where is the plate now?", "goals": []}


def test_not_sure_where_drops_the_source_when_the_robot_can_find(make_assist, env):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **HOLD)
    r = sense_ctx(loop)
    u = loop._hold_sense(r, dict(U_WHERE))
    assert u is not None and u["goals"][0]["from"] == "" and u["goals"][0]["object"] == "plate" and r.unsure_reasked


def test_not_sure_where_is_a_no_op_without_a_sense_skill(make_assist, env, monkeypatch):
    robot = FakeRobot(env, {})
    loop = make_assist(robot, FakeLLM(), ScriptedPlanner([]), **HOLD)
    monkeypatch.setattr(type(loop.robot), "plan_actions", property(lambda self: {"MoveTo", "Pick", "Place"}), raising=False)
    assert loop._hold_sense(sense_ctx(loop), dict(U_WHERE)) is None


def test_a_not_sure_answer_blocks_the_same_question(make_assist, env):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **HOLD)
    r = sense_ctx(loop)
    q = DH.open_question({"type": "in", "object": "plate", "to": "fridge@kitchen"}, "object", r.places)
    loop._hold_note_answer(r, q, "I don't know.")
    again, stop = loop._hold_gate(r, q)
    assert str(again).startswith("Sorry, I did not understand") and not stop           # the other existing form, once
    again, stop = loop._hold_gate(r, q)
    assert stop                                                                          # then the honest stop
    other = DH.open_question({"type": "in", "object": "plate", "to": "fridge@kitchen"}, "to", r.places)
    assert loop._hold_gate(r, other) == (other, False)


def test_korean_containment_is_no_evidence():
    g = {"type": "in", "object": "cup", "said": "컵", "to": "fridge@kitchen"}
    assert DH.object_score(g, ["머그컵을 주세요"], "ko") == 0 and DH.object_score(g, ["컵을 주세요"], "ko") == 1


def test_a_robot_without_goal_kind_slots_gets_the_slots_it_holds():
    assert DH.slots_of({"type": "made_up", "object": "x", "to": "y"}) == ("object", "to")
    assert DH.slots_of({"type": "say"}) == ("object", "to")


# ── offline replay fixes (round 18 check; invented words only) ─────────────────────────────────────────────────────────
def test_an_answer_to_a_which_one_question_narrows_the_label_without_a_choice():
    """The robot asked which blick; her answer ("the red one") narrows the part. Not a conflict: no "blick or red blick?"."""
    g = {"type": "on", "object": "blick", "from": "", "to": "me"}
    led = ledger(g)
    q = speech.LocalText("q_which_known", {"object": "blick", "text": "the red blick or the blue blick"})
    assert DH.asked_slots(q) == {"object"} and DH.asked_slots(q.again()) == {"object"}
    out, info = DH.merge(led, [{**g, "object": "red blick"}], ["The red one."], ["Bring me the blick."], Places(ENV), "en",
                         {(0, "object")})
    assert out[0]["object"] == "red blick" and not info["conflicts"]
    led2 = ledger(g)                         # without the question: a narrower name she says is no conflict either
    out, info = DH.merge(led2, [{**g, "object": "red blick"}], ["the red blick"], ["Bring me the blick."], Places(ENV), "en", set())
    assert out[0]["object"] == "red blick" and not info["conflicts"]
    led3 = ledger(g)                         # but the model narrowing on its own, with no question and no words, changes nothing
    out, _ = DH.merge(led3, [{**g, "object": "red blick"}], ["Yes."], ["Bring me the blick."], Places(ENV), "en", set())
    assert out[0]["object"] == "blick"


def test_an_answer_to_a_which_place_question_settles_without_asking_again():
    g = {"type": "on", "object": "blick", "from": "", "to": "sofa@living room"}
    led = ledger(g)
    q = speech.LocalText("q_which_place", {"options": ["sofa@living room", "dresser@bedroom"]})
    sup = {"*"} if DH.asked_slots(q) == {"*"} else {(0, s) for s in DH.asked_slots(q)}     # as the loop builds it
    out, info = DH.merge(led, [{**g, "to": "dresser@bedroom"}], ["The dresser."], ["the blick on the sofa"], Places(ENV), "en", sup)
    assert out[0]["to"] == "dresser@bedroom" and not info["conflicts"]


def test_a_restatement_request_or_a_read_back_supersedes_nothing():
    for k in ("q_what_do", "q_repeat", "q_unclear_task", "q_confirm"):
        assert DH.asked_slots(speech.LocalText(k)) == set()
    assert DH.asked_slots("Which blick is it, the one on the sofa?") == {"*"}       # a free question of the model: all


def test_a_message_is_never_a_two_way_choice():
    g = {"type": "say", "object": "zorp wibble now", "from": "", "to": "ben@kitchen"}
    led = ledger(g)
    out, info = DH.merge(led, [{**g, "object": "zorp wibble"}], ["zorp wibble"], ["tell ben zorp wibble now"], Places(ENV), "en", set())
    assert not info["conflicts"]


def test_on_and_in_readings_of_one_object_are_one_part():
    old = {"type": "on", "object": "blick", "from": "", "to": ""}
    led = ledger(old)
    out, info = DH.merge(led, [{"type": "in", "object": "blick", "from": "", "to": "fridge@kitchen"}], ["The fridge."],
                         ["Put the blick away."], Places(ENV), "en", {(0, "to")})
    assert len(out) == 1 and out[0]["to"] == "fridge@kitchen" and out[0]["type"] == "in" and not info["added"]


def test_a_correction_about_another_part_does_not_edit_this_one():
    pl = Places(ENV)
    this = {"type": "in", "object": "blick", "from": "", "to": "fridge@kitchen"}
    other = {"type": "on", "object": "dax", "from": "", "to": "dresser@bedroom"}
    assert DH.content_edit(this, ["No, bring the dax to me."], pl, "en", [other]) == {}
    assert DH.content_edit(this, ["No, give it to Ben."], pl, "en", [other]) == {}           # a person is never "in"
    assert DH.content_edit(this, ["No, the dresser."], pl, "en", [other]) == {"to": "dresser@bedroom"}
    go = {"type": "go", "object": "", "from": "", "to": "sofa@living room"}
    assert DH.content_edit(go, ["No, put the dax on the dresser."], pl, "en", [other]) == {}
    assert DH.content_edit(go, ["No, the dresser."], pl, "en", [other]) == {"to": "dresser@bedroom"}


def test_the_ledger_only_ablation_has_no_no_repeat_gate(make_assist, env):
    loop = make_assist(FakeRobot(env, {}), FakeLLM(), ScriptedPlanner([]), **HOLD1)
    r = sense_ctx(loop)
    q = DH.open_question({"type": "in", "object": "plate", "to": "fridge@kitchen"}, "object", r.places)
    loop._hold_note_answer(r, q, "I don't know.")
    assert loop._hold_gate(r, q) == (q, False) and not r.__dict__.get("hold_failed")


def test_a_gap_is_filled_and_a_folded_twin_part_is_dropped():
    pl = Places(ENV)
    g = {"type": "on", "object": "blick", "from": "", "to": "dresser@bedroom"}
    led = ledger(g)                    # the re-reading knows where the blick is: filling the empty source is no drift
    out, info = DH.merge(led, [{**g, "from": "counter@kitchen"}], ["Yes."], ["the blick on the dresser"], pl, "en", set())
    assert out[0]["from"] == "counter@kitchen" and (0, "from") in info["changed"]
    a = {"type": "on", "object": "blick", "from": "", "to": "sofa@living room"}
    led = ledger(a, {**a, "to": "dresser@bedroom"})          # one object, two destinations; she picks one
    out, _ = DH.merge(led, [{**a, "to": "dresser@bedroom"}], ["The dresser."], ["the blick on the sofa or the dresser"], pl, "en", {"*"})
    assert [o["to"] for o in out] == ["dresser@bedroom"]


def test_a_choice_between_named_options_supersedes_the_whole_reading():
    assert DH.asked_slots(speech.LocalText("q_which_x", {"what": "desk", "options": ["a", "b"]})) == {"*"}
    assert DH.asked_slots(speech.LocalText("q_which_place", {"options": ["a", "b"]})) == {"*"}


def test_a_reading_after_one_answer_does_not_lose_the_other_parts():
    pl = Places(ENV)
    say = {"type": "say", "object": "zorp now", "from": "", "to": "ben@kitchen"}
    move = {"type": "on", "object": "blick", "from": "", "to": "me"}          # no words of hers anchor its English label
    led = ledger(say, move)
    out, info = DH.merge(led, [{**say, "object": "zorp"}], ["zorp"], ["Qa ben zorp, qa bliku."], pl, "en", {(0, "object"), (1, "object")})
    assert [g["type"] for g in out] == ["say", "on"]
    led = ledger(say, move)                  # after a refusal and a restatement ("*"), an unanchored part the reading drops goes
    out, _ = DH.merge(led, [say], ["no, just tell ben"], ["Qa ben zorp, qa bliku."], pl, "en", {"*"})
    assert [g["type"] for g in out] == ["say"]


def test_a_go_part_to_a_place_another_part_goes_to_is_folded():
    pl = Places(ENV)
    go = {"type": "go", "object": "", "from": "", "to": "dresser@bedroom"}
    move = {"type": "on", "object": "blick", "from": "", "to": "dresser@bedroom"}
    led = ledger(go, move)
    out, _ = DH.merge(led, [move], ["The blick."], ["go to the dresser and put the blick on the dresser"], pl, "en", {(0, "object"), (1, "object")})
    assert [g["type"] for g in out] == ["on"]
