"""Round 18 fixes A..H of ``dialog_hold`` (option on only; off is byte for byte the earlier behaviour), through the real
AssistLoop.run_blocking against the symbolic FakeRobot, a scripted LLM and a scripted generic user. The words of the users are
written per test; nothing here knows a simulator.

A false success: a part settled by an answer (not by a yes) is read back as a whole before the run goes on.
B order: the ledger follows the order of the new reading.   C Korean evidence: the noun ending of the pack ("우유요").
D an answer that names a value the part does not have edits the part.   E a No about another part: no loop, no wrong refusal.
F the stop tries the agreed reading (dialog_agree).   G a corrected part the reading lost stays.   H the repair reads back
only the parts of the reading that was read back."""
import pytest

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, steps
from robot_agent.core.planning import dialog_hold as DH, langdata
from robot_agent.core.planning.assist_loop import AssistLoop, Places
from test_sage_lab_r12_agree import ENV, KO_ENV, KO_PACK, READ
from test_sage_lab_r18_dialog_hold import HOLD, S9, env, hold_log, ledger, play   # noqa: F401

NOAGREE = dict(dialog_unstick="1", dialog_hold="1")
TWO = {"status": "clear", "reason": "", "question": "", "goals": [
    {"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"},
    {"type": "on", "object": "cup", "from": "", "to": "sofa@living room"}]}
TASK2 = "Put the plate in the fridge and the cup on the sofa."
HOUSE2 = {"plate": "counter@kitchen", "cup": "counter@kitchen"}


def held(evs):
    return [e for e in evs if e.get("correction") == "held"]


def confirms(robot):
    return [str(q) for q in robot.asked if str(q).endswith("Is that right?")]


# ── A: false success ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_a_part_settled_by_an_answer_is_read_back_as_a_whole(make_assist):
    answers = ["No.", "Yes.", "No.", "The fridge.", "Yes."]      # the open part is settled by her answer, not by a yes
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, **NOAGREE)
    whole = "I will put the plate on the fridge in the kitchen, then open the fridge in the kitchen. Is that right?"
    assert confirms(robot)[-1] == whole and hold_log(loop, "whole_readback") and not held(evs)


def test_a_a_no_to_that_whole_read_back_is_not_a_success(make_assist):
    answers = ["No.", "Yes.", "No.", "The fridge.", "No."]
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, **NOAGREE)
    assert done(evs)["status"] == "failed" and "did not confirm" in str(done(evs).get("msg")) and not held(evs)
    assert hold_log(loop, "stop")


def test_a_every_slot_settled_by_a_yes_keeps_the_statement(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", "Yes.", "Yes."], **NOAGREE)
    assert held(evs) and not hold_log(loop, "whole_readback") and len(robot.asked) == 3


def test_a_a_change_no_yes_covered_asks_the_whole_reading_again(make_assist):
    # her No to the cup read-back is a correction of the plate (a settled part): the edited reading is read back as a whole
    answers = ["No.", "Yes.", "No, put the plate on the dresser.", "Yes.", "Yes."]
    loop, robot, evs = play(make_assist, FakeLLM(default=TWO), answers, task=TASK2, house=HOUSE2, **HOLD)
    assert confirms(robot)[-1].startswith("I will put the plate on the dresser in the bedroom, then put the cup")
    assert hold_log(loop, "whole_readback") and not held(evs)


def test_a_the_flag_clears_with_a_yes_and_the_check_reads_provenance():
    led = ledger({"type": "go", "object": "", "from": "", "to": "sofa@living room"})
    led.settle(0, ["to"], "answer")
    assert led.all_settled() and AssistLoop._hold_all_yes(led) is False
    led.parts[0]["changed_since_yes"] = True
    led.settle(0, None, "yes")
    assert "changed_since_yes" not in led.parts[0] and AssistLoop._hold_all_yes(led) is True
    led.parts[0]["changed_since_yes"] = True
    assert AssistLoop._hold_all_yes(led) is False


def test_a_merge_reports_the_parts_a_reading_added_and_the_loop_flags_them(make_assist, env):
    a = {"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}
    b = {"type": "on", "object": "cup", "from": "", "to": "sofa@living room"}
    led = ledger(a)
    led.settle(0, None, "yes")
    _g, info = DH.merge(led, [a, b], ["the cup on the sofa"], ["the plate in the fridge"], Places(ENV), "en", set())
    assert info["added_idx"] == [1] and info["remap"] == {0: 0, 1: 1}


# ── B: order ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_b_the_ledger_follows_the_order_of_the_new_reading():
    pl = Places(ENV)
    a = {"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}
    b = {"type": "on", "object": "cup", "from": "", "to": "sofa@living room"}
    c = {"type": "on", "object": "book", "from": "", "to": "dresser@bedroom"}
    led = ledger(a, b)
    out, info = DH.merge(led, [b, a], [], ["plate fridge cup sofa"], pl, "en", set())
    assert [g["object"] for g in out] == ["cup", "plate"] and info["remap"] == {0: 1, 1: 0}
    led = ledger(a, b)                                    # an added part goes to its place in the reading, not to the end
    out, info = DH.merge(led, [c, a, b], ["the book on the dresser"], ["plate fridge cup sofa"], pl, "en", set())
    assert [g["object"] for g in out] == ["book", "plate", "cup"] and info["added"] == ["book"]
    assert info["remap"] == {0: 1, 1: 2, 2: 0}


def test_b_a_kept_part_the_reading_lacks_goes_after_its_nearest_earlier_part():
    pl = Places(ENV)
    a = {"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}
    b = {"type": "on", "object": "cup", "from": "", "to": "sofa@living room"}
    c = {"type": "on", "object": "book", "from": "", "to": "dresser@bedroom"}
    led = ledger(a, b, c)
    led.settle(1, None, "yes")                            # the reading forgot the cup, which is settled: it stays
    out, info = DH.merge(led, [c, a], [], ["plate fridge cup sofa book dresser"], pl, "en", set())
    assert [g["object"] for g in out] == ["book", "plate", "cup"] and info["remap"] == {2: 0, 0: 1, 1: 2}
    led = ledger(a, b)                                    # no earlier part: just before the first of the reading
    led.settle(0, None, "yes")
    out, _ = DH.merge(led, [b], [], ["plate fridge cup sofa"], pl, "en", set())
    assert [g["object"] for g in out] == ["plate", "cup"]


# ── C: Korean evidence ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_c_the_noun_ending_is_pack_data():
    assert "우유" in DH.token_forms("아니요, 우유요.", "ko") and "책" in DH.token_forms("책요", "ko")
    assert "책" in DH.token_forms("책이에요", "ko") and "우유" in DH.token_forms("우유를요", "ko")
    assert all(not langdata.grammar(lg, "noun_endings", []) for lg in langdata.languages() if lg != "ko")
    assert DH.token_forms("cups mugs", "en") == {"cups", "mugs"}
    assert "컵" not in DH.token_forms("머그컵을 주세요", "ko")


def test_c_no_milk_changes_the_bottle_to_milk(make_assist, monkeypatch):
    ko_env = {**KO_ENV, "sofa@living room": {"aliases": ["소파", "소파에"], "loc": {"x": 1.0, "y": 1.0}}}
    r1 = {"status": "clear", "reason": "", "question": "", "goals": [
        {"type": "on", "object": "water bottle", "said": "물병", "from": "", "to": "sofa@living room"}]}
    r2 = {**r1, "goals": [{"type": "on", "object": "milk", "said": "우유", "from": "", "to": "sofa@living room"}]}
    plan = steps("MoveTo sofa@living room", "Place sofa@living room")

    def run():
        return play(make_assist, FakeLLM(r1, default=r2), ["소파에 놓아 주세요.", "아니요, 우유요.", "네"], task="물병을 소파에 놓아 주세요.",
                    env=ko_env, house={}, lang="ko", plan=plan, phrases={k: {"ko": v} for k, v in KO_PACK.items()}, **HOLD)
    loop, robot, evs = run()
    assert "milk" in confirms(robot)[-1]
    monkeypatch.setattr(langdata, "grammar", lambda lg, k, d=None: [] if k == "noun_endings" else langdata.block(lg, "grammar").get(k, d))
    loop, robot, evs = run()                                       # without the pack's ending the old name stays
    assert "water bottle" in confirms(robot)[-1] and "milk" not in confirms(robot)[-1]


def test_c_her_texts_add_the_english_of_the_answer_without_its_yes_no_word(make_assist, env):
    from robot_agent.core.planning.language import Heard
    loop = make_assist(FakeRobot(KO_ENV, {}), FakeLLM(), ScriptedPlanner([]), **HOLD)
    loop.language.heard["우유요"] = Heard("우유요", "No, milk.")
    loop.language.heard["아니요, 우유요."] = Heard("아니요, 우유요.", "No, milk.")
    loop.language.heard["우유요 "] = Heard("x", "Untrusted milk.", False)
    texts = loop._her_texts(["아니요, 우유요."])
    assert "milk." in texts and texts.count("milk.") == 1 and "아니요, 우유요." in texts
    assert loop._her_texts(["그냥 해 주세요."]) == ["그냥 해 주세요."]


# ── D: the answer names a value the part does not have ────────────────────────────────────────────────────────────────────
def test_d_an_answer_naming_another_place_edits_the_part_and_reads_it_back(make_assist, monkeypatch):
    one = {"status": "clear", "reason": "", "question": "", "goals": [{"type": "on", "object": "plate", "from": "", "to": "counter@kitchen"}]}
    plan = steps("MoveTo counter@kitchen", "Find plate", "Pick plate", "MoveTo sofa@living room", "Place sofa@living room")
    og = AssistLoop._ground_understood

    def raw(self, r, u):                     # a re-reading that does not take her answer (the model's noise)
        g, q = og(self, r, u)
        return ([dict(x) for x in u["goals"]], None) if r.dialogue else (g, q)
    monkeypatch.setattr(AssistLoop, "_ground_understood", raw)
    loop, robot, evs = play(make_assist, FakeLLM(default=one), ["Whatever.", "No.", "On the sofa, please.", "Yes."],
                            task="Please put the plate away.", plan=plan, **HOLD)
    assert confirms(robot)[-1] == "I will put the plate on the sofa in the living room. Is that right?"
    assert hold_log(loop, "edit") and done(evs)["status"] == "success"


def test_d_not_sure_or_garbled_marks_the_part_empty_and_a_value_of_its_own_settles_it(make_assist):
    from test_sage_lab_r18_dialog_hold import Ctx
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), **HOLD)
    g = {"type": "on", "object": "plate", "from": "", "to": "counter@kitchen"}

    def answer(text, changed=frozenset()):
        r = Ctx()
        r.places, r.lang, r.dialogue, r.goals = Places(ENV), "en", [("robot", "Where?"), ("user", text)], [g]
        led = r.hold = ledger(g)
        led.asked_slot = (0, "to")
        list(loop._hold_answered(r, led, [text], {0: 0}, changed))
        return led.parts[0]
    assert answer("I don't know.")["empty"] is True
    assert answer("The counter, please.")["settled"] == {"to"}
    p = answer("The counter, please.", {(0, "to")})                      # the merge just changed this slot: read it back first
    assert not p["settled"] and p["opened"] and not p["empty"]


# ── E: a No about another part ────────────────────────────────────────────────────────────────────────────────────────────
def test_e_a_no_that_agrees_with_a_settled_part_asks_the_whole_reading(make_assist):
    answers = ["No.", "Yes.", "No, the plate in the fridge.", "Yes."]
    loop, robot, evs = play(make_assist, FakeLLM(default=TWO), answers, task=TASK2, house=HOUSE2, **HOLD)
    asked = [str(q) for q in robot.asked]
    assert asked[-1] == "I will put the plate on the fridge in the kitchen, then put the cup on the sofa in the living room. Is that right?"
    assert "What should I bring?" not in asked and hold_log(loop, "whole_readback") and not hold_log(loop, "open_question")
    assert not [p for p in loop._ctx.hold.parts if p["status"] == DH.REFUSED]


def test_e_a_no_about_an_open_part_edits_that_part(make_assist):
    answers = ["No.", "No, put the plate on the dresser.", "Yes.", "Yes.", "Yes."]
    loop, robot, evs = play(make_assist, FakeLLM(default=TWO), answers, task=TASK2, house=HOUSE2, **HOLD)
    cq = confirms(robot)
    assert "I will put the plate on the dresser in the bedroom. Is that right?" in cq and hold_log(loop, "edit")


def test_e_a_read_back_that_got_a_no_without_an_edit_is_not_asked_again(make_assist):
    # the open question's answer names a slot but changes nothing: the part is not read back with the same words again
    answers = ["No.", "No.", "The plate.", "Hmm.", "No.", "No."]
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), answers, **NOAGREE)
    cq = confirms(robot)
    assert len(cq) == len(set(cq)), cq
    assert done(evs)["status"] == "failed"
    assert hold_log(loop, "stop")


def test_e_the_failed_set_holds_the_one_part_read_back_key(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=TWO), ["No.", "No, not that.", "Hmm."], task=TASK2, house=HOUSE2, **HOLD)
    failed = loop._ctx.__dict__.get("hold_failed")
    assert failed and any(k[0] == "q_confirm" for k in failed)


def test_e_other_parts_named_helper():
    a = {"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}
    b = {"type": "on", "object": "cup", "from": "", "to": "sofa@living room"}
    assert DH.other_parts_named(b, ["no, the plate"], "en", [a]) == [0]
    assert DH.other_parts_named(b, ["no, the cup"], "en", [a]) == []
    assert DH.agrees_with(a, ["the plate in the fridge"], Places(ENV), "en")
    assert not DH.agrees_with(a, ["the plate on the dresser"], Places(ENV), "en")
    assert not DH.agrees_with(a, ["the cup"], Places(ENV), "en")


# ── F: the stop tries the agreed reading ──────────────────────────────────────────────────────────────────────────────────
ANSWERS_F = ["No.", "No.", "The plate.", "The plate, yes the plate."]


def test_f_the_stop_tries_the_agreed_reading(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ANSWERS_F, **HOLD)
    assert [e for e in evs if e.get("correction") == "agreed"] and hold_log(loop, "stop")
    assert [e for e in loop.lab_log if e["flag"] == "dialog_agree" and e["fire"]]
    assert "did not confirm" not in str(done(evs).get("msg"))


def test_f_without_dialog_agree_the_stop_is_the_old_stop(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ANSWERS_F, **NOAGREE)
    assert not [e for e in evs if e.get("correction") == "agreed"] and done(evs)["status"] == "failed"
    assert "did not confirm" in str(done(evs).get("msg"))


def test_f_it_is_tried_once(make_assist):
    loop, robot, evs = play(make_assist, FakeLLM(default=READ), ["No.", "No.", "Hmm.", "No."], **HOLD)
    assert len([e for e in loop.lab_log if e["flag"] == "dialog_agree"]) <= 1 and done(evs)["status"] == "failed"


def test_f_the_open_question_skips_a_settled_slot():
    g = {"type": "on", "object": "plate", "from": "", "to": "fridge@kitchen"}
    pl = Places(ENV)
    assert DH.least_anchored_slot(g, ["bring it"], pl, "en") == "object"
    assert DH.least_anchored_slot(g, ["bring it"], pl, "en", {"object"}) == "to"
    assert DH.least_anchored_slot(g, ["the plate"], pl, "en", {"to", "object"}) == "object"          # nothing left: as before


# ── G: a corrected part the reading lost ──────────────────────────────────────────────────────────────────────────────────
def test_g_a_part_her_new_words_name_stays_when_the_reading_lost_it():
    pl = Places(ENV)
    a = {"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}
    b = {"type": "on", "object": "cup", "from": "", "to": "sofa@living room"}
    led = ledger(a)
    DH.merge(led, [a, b], ["the cup on the sofa"], ["the plate in the fridge"], pl, "en", set())      # b is added later
    assert len(led.parts) == 2 and led.parts[1]["first"] is False
    out, info = DH.merge(led, [a], ["no, the cup on the sofa"], ["the plate in the fridge"], pl, "en", {"*"})
    assert [g["object"] for g in out] == ["plate", "cup"] and not info["dropped"]
    led.refuse(1)                                         # a part she refused is dropped by the same reading
    out, _ = DH.merge(led, [a], ["no, the cup on the sofa"], [], pl, "en", {"*"})
    assert [g["object"] for g in out] == ["plate"]


def test_g_a_first_part_her_restatement_does_not_name_is_dropped():
    pl = Places(ENV)
    a = {"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}
    b = {"type": "on", "object": "cup", "from": "", "to": "sofa@living room"}
    led = ledger(a, b)
    out, _ = DH.merge(led, [a], ["no, the plate in the fridge"], ["the plate and the cup"], pl, "en", {"*"})
    assert [g["object"] for g in out] == ["plate"]
    led = ledger(a, b)                                    # no restatement: the forgotten first part stays
    out, _ = DH.merge(led, [a], ["yes"], ["the plate and the cup"], pl, "en", set())
    assert [g["object"] for g in out] == ["plate", "cup"]


# ── H: the repair reads back only what was read back ──────────────────────────────────────────────────────────────────────
def test_h_the_repair_rebuilds_the_ledger_from_the_read_back_goals(make_assist, monkeypatch):
    from test_sage_lab_r18_dialog_hold import Ctx
    loop = make_assist(FakeRobot(ENV, {}), FakeLLM(), ScriptedPlanner([]), **HOLD)
    a = {"type": "in", "object": "plate", "from": "", "to": "fridge@kitchen"}
    b = {"type": "on", "object": "cup", "from": "", "to": "sofa@living room"}
    c = {"type": "on", "object": "book", "from": "", "to": "dresser@bedroom"}
    r = Ctx()
    r.places, r.lang, r.dialogue, r.task = Places(ENV), "en", [], "x"
    led = r.hold = ledger(a, b, c)
    led.settle(0, None, "yes")
    led.settle(2, ["to"], "answer")
    led.parts[2]["edits"] = 1
    led.seen = 3
    r.goals = [dict(c), dict(a)]                          # what the robot read back: the book part and the plate part only
    seen = {}

    def parts(self, rr):
        seen["parts"], seen["active"] = rr.hold.parts, rr.hold.active
        return None, "q"
        yield                                             # noqa: unreachable, a generator
    monkeypatch.setattr(type(loop), "_hold_parts", parts)
    out = list(loop._hold_repair(r))
    assert out == [] and seen["active"] is True
    ps = seen["parts"]
    assert [p["goal"]["object"] for p in ps] == ["book", "plate"]                 # the cup was not read back: gone
    assert ps[1]["settled"] == {"object", "from", "to"} and set(ps[1]["prov"].values()) == {"yes"}
    assert ps[0]["settled"] == {"to"} and ps[0]["prov"] == {"to": "answer"} and ps[0]["edits"] == 1
    assert r.hold.seen == 3
