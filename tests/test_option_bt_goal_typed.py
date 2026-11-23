"""Step S25: typed ``act`` arguments in a bt_goal tree, ``[ask, <pack question>]`` and the answer kept as a fact. Both are opt-in: a tree with no
mapping and an ``ask`` whose first argument names no question of the pack behave as before. The runner's ``valid`` / ``fill`` / ``leaves`` are
compared with a reference copy of the code they replaced (copied here as it was at 84d7edb); the leaves are driven with a stub service. The
runs through the real AssistLoop are the cassettes of tests/golden/scenario_sets/s25_*.py."""

import copy
from types import SimpleNamespace as NS
from typing import Optional

import pytest

from robot_agent.core.planning import behavior_tree as bt
from robot_agent.core.planning import names, tree_host
from robot_agent.options import bt_goal as B

SHELF, BENCH = "shelf@loft", "bench@den"
ACTS = {"MoveTo", "Find", "Pick", "Place", "Seat"}
GOAL = {"type": "on", "object": "gizmo", "from": SHELF, "to": BENCH}


# ── the reference: valid / leaves / fill as they were before the step ─────────────────────────────────────────────────────────────
def ref_valid(node) -> bool:
    if not isinstance(node, list) or not node or not isinstance(node[0], str):
        return False
    kind, args = node[0], node[1:]
    if kind in bt.COMPOSITES:
        return bool(args) and all(ref_valid(c) for c in args)
    if kind == "retry":
        n = args[0] if len(args) == 2 else None
        return isinstance(n, int) and not isinstance(n, bool) and n >= 1 and ref_valid(args[1])
    return kind in bt.LEAVES and bool(args) and all(isinstance(a, str) for a in args)


def ref_fill(node, values: dict) -> Optional[list]:
    out = [node[0]]
    for a in node[1:]:
        if isinstance(a, list):
            a = ref_fill(a, values)
        elif isinstance(a, str) and len(a) > 2 and a[0] == "{" and a[-1] == "}":
            a = values.get(a[1:-1]) or None
        if a is None:
            return None
        out.append(a)
    return out


GO = ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]]
FETCH = ["sequence", ["act", "MoveTo", "{from}"], ["act", "Find", "{object}"], ["retry", 2, ["act", "Pick", "{object}"]],
         ["act", "MoveTo", "{to}"], ["act", "Place", "{to}"], ["cond", "placed", "{object}", "{to}"]]
OLD_TREES = [GO, FETCH, ["act", "Pick", "{object}"], ["ask", "q_x", "a.b", "c.d"], ["confirm", "q"], ["reply", "q"], ["act"], ["ask"], [],
             "x", None, ["cond"], ["nope", "x"], ["sequence"], ["retry", 0, GO], ["retry", True, GO], ["retry", 2], ["act", 3],
             ["act", "Pick", ["x"]], ["act", "Pick", None], ["sequence", ["act", "Pick", "{object}"], ["fallback", GO, ["act", "Find", "{x}"]]],
             ["act", "Pick", "{}"], ["act", "Pick", "{", "}"], ["cond", "at", "{to}", "{nothing}"]]
VALUES = [{}, {"object": "gizmo", "to": BENCH, "from": SHELF}, {"object": "gizmo", "to": "", "from": SHELF}, {"x": "y"}]


@pytest.mark.parametrize("tree", range(len(OLD_TREES)))
def test_valid_fill_and_leaves_are_the_reference_for_every_tree_without_a_mapping(tree):
    node = OLD_TREES[tree]
    assert bt.valid(node) == ref_valid(node)
    for values in VALUES:
        try:
            want = ref_fill(node, values)
        except Exception as exc:                       # a malformed node: the old code raised, the new code raises the same kind of error
            with pytest.raises(type(exc)):
                bt.fill(node, values)
            continue
        assert bt.fill(node, values) == want


# ── the mapping ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_an_act_may_end_with_one_mapping_of_typed_arguments_and_no_other_leaf_may():
    ok = ["act", "Seat", "{to}", {"arm": "left", "dz_mm": -2, "slow": True, "k": 0.5}]
    assert bt.valid(ok) and bt.valid(["sequence", ok, GO]) and bt.valid(["retry", 2, ok])
    assert bt.valid(["act", "Seat", {}])                                       # an empty mapping is a mapping of no arguments
    assert not bt.valid(["act", {"arm": "left"}])                              # a mapping alone is no action
    assert not bt.valid(["act", "Seat", "{to}", {"arm": "left"}, "x"])         # only at the end
    assert not bt.valid(["act", "Seat", "{to}", {"arm": "left"}, {"b": 1}])    # only ONE
    assert not bt.valid(["act", "Seat", {"arm": ["l"]}]) and not bt.valid(["act", "Seat", {"arm": None}])
    assert not bt.valid(["act", "Seat", {1: "l"}])
    for kind in ("cond", "ask", "confirm", "reply"):
        assert not bt.valid([kind, "x", {"arm": "left"}])
    assert not bt.valid(["fallback", ["act", "Seat", {"a": {"b": 1}}]])        # a value is a string, number or boolean, no nesting


def test_a_string_value_of_the_mapping_is_filled_like_a_slot_and_a_missing_one_drops_the_tree():
    tree = ["sequence", ["act", "Seat", "{to}", {"arm": "left", "where": "{to}", "n": 3}]]
    assert bt.fill(tree, {"to": BENCH}) == ["sequence", ["act", "Seat", BENCH, {"arm": "left", "where": BENCH, "n": 3}]]
    assert bt.fill(tree, {"to": ""}) is None and bt.fill(tree, {}) is None
    assert bt.fill(["act", "Seat", "{to}", {"where": "{from}"}], {"to": "a"}) is None
    assert tree[1][3] == {"arm": "left", "where": "{to}", "n": 3}                  # the declared tree is not changed
    assert bt.fill(["act", "Seat", "x", {"zero": 0, "no": False}], {}) == ["act", "Seat", "x", {"zero": 0, "no": False}]   # 0 / False are values, not "missing"


def test_the_leaves_of_a_tree_with_a_mapping_keep_the_mapping_after_the_action_and_object():
    tree = ["sequence", ["act", "Seat", "x", {"arm": "left"}], ["cond", "at", "x"]]
    assert list(bt.leaves(tree)) == [["act", "Seat", "x", {"arm": "left"}], ["cond", "at", "x"]]


def test_a_tree_with_a_mapping_is_picked_when_its_action_is_known_and_its_slots_are_filled():
    trees = {"on": {"tree": ["sequence", ["act", "Seat", "{to}", {"arm": "left"}]], "assumed": True}}
    assert B.tree_for(GOAL, trees, ACTS, True, lambda k: True) == ["sequence", ["act", "Seat", BENCH, {"arm": "left"}]]
    assert B.tree_for(GOAL, trees, ACTS - {"Seat"}, True, lambda k: True) is None
    assert B.tree_for({**GOAL, "to": ""}, trees, ACTS, True, lambda k: True) is None


def test_the_trace_names_an_act_with_its_arguments():
    assert B.label(["act", "Seat", BENCH, {"arm": "left"}]) == f"act Seat {BENCH} {{'arm': 'left'}}"
    assert B.label(["act", "Seat", BENCH]) == f"act Seat {BENCH}"


class Svc:
    """A stub of the services the leaves use: it records every step, lab row and event, and answers from a list."""

    def __init__(self, answers=(), ok=True):
        self.steps, self.labs, self.events, self.said, self.ok = [], [], [], list(answers), ok
        self.robot = NS()
        self.asked = []

    def try_step(self, run, step, ladder=False):
        self.steps.append((dict(step), ladder))
        return self.ok
        yield

    def lab(self, flag, **info):
        self.labs.append((flag, info))

    def emit(self, run, event, **fields):
        self.events.append({"event": event, **fields})

    def ask(self, run, q):
        self.asked.append(q)
        return self.said.pop(0) if self.said else ""

    def cancelled(self, run):
        return None

    def said_of(self, run, obj):
        return obj


def drive(gen):
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def host(svc=None, keys=None, world=None):
    evs = []
    run = NS(lang="en", world=world or NS(holding=None, facts={}), ev=lambda event, **k: evs.append({"event": event, **k}) or evs[-1])
    h = B._Leaves(svc or Svc(), run, GOAL, 0, keys)
    h._phrase = lambda key: f"<{key}>"
    h.evs = evs
    return h


def test_an_act_without_a_mapping_is_the_old_step_and_with_one_the_step_carries_args():
    h = host()
    assert drive(h.act("Pick", "gizmo")) is True and h.svc.steps == [({"action": "Pick", "object": "gizmo"}, True)]
    h = host()
    drive(h.act("Seat", BENCH, {"arm": "left", "dz_mm": -2}))
    assert h.svc.steps == [({"action": "Seat", "object": BENCH, "args": {"arm": "left", "dz_mm": -2}}, True)]
    h = host(keys={"ladder": False})
    drive(h.act("Seat", BENCH, {}))                                            # an empty mapping adds nothing
    assert h.svc.steps == [({"action": "Seat", "object": BENCH}, False)]


def test_the_runner_hands_the_mapping_to_the_host_as_the_third_argument():
    h = host()
    assert drive(bt.tick(["sequence", ["act", "Find", "gizmo"], ["act", "Seat", "x", {"arm": "right"}]], h)) == bt.SUCCESS
    assert [s[0] for s in h.svc.steps] == [{"action": "Find", "object": "gizmo"}, {"action": "Seat", "object": "x", "args": {"arm": "right"}}]


# ── a question of the pack, read as a choice and kept as a fact ──────────────────────────────────────────────────────────────────
QUESTIONS = {"route": {"phrase": "q_route", "kind": "route",
                       "answer": {"type": "choice", "keep_text": True,
                                  "choices": {"shortcut": {"words": {"en": ["shortcut", "direct way"]}},
                                              "detour": {"words": {"en": ["detour", "crate"]}}}}},
             "quiet": {"phrase": "q_quiet", "answer": {"type": "choice", "choices": {"yes": {"words": {"en": ["go"]}}}}},
             "no_phrase": {"answer": {"type": "choice", "choices": {"a": {"words": {"en": ["a"]}}}}},
             "other_type": {"phrase": "q_o", "answer": {"type": "yes_no"}},
             "not_a_dict": 4}


def qs():
    return QUESTIONS


def test_only_a_question_with_a_phrase_and_a_choice_answer_is_a_pack_question_for_the_leaf():
    assert tree_host.question_of(qs, "route") is QUESTIONS["route"] and tree_host.question_of(qs, "quiet") is QUESTIONS["quiet"]
    for name in ("no_phrase", "other_type", "not_a_dict", "nope", None, "q_route"):
        assert tree_host.question_of(qs, name) is None
    assert tree_host.question_of(lambda: None, "route") is None and tree_host.question_of(lambda: {}, "route") is None

    def boom():
        raise RuntimeError("no pack")
    assert tree_host.question_of(boom, "route") is None


def test_the_first_choice_in_declaration_order_whose_words_the_answer_says_is_read():
    spec, read = QUESTIONS["route"], tree_host.read_choice
    assert read(spec, "Take the direct way, please.", "en") == "shortcut"           # a phrase of two words: the run of whole words
    assert read(spec, "THE SHORTCUT!", "en") == "shortcut"                          # case and punctuation do not matter
    assert read(spec, "via the crate", "en") == "detour"
    assert read(spec, "the direct way, not via the crate", "en") == "shortcut"      # two choices said: the first declared
    assert read(spec, "direct then way", "en") is None and read(spec, "short cut", "en") is None   # not that run of whole words
    assert read(spec, "", "en") is None and read(spec, "no idea", "en") is None
    assert read(spec, "shortcut", "ko") is None                                    # words of another language are not read
    assert read({"answer": {"choices": {"x": {}}}}, "x", "en") is None and read({}, "x", "en") is None


def test_a_single_word_choice_is_read_as_the_word_lists_read_it():
    spec = QUESTIONS["quiet"]
    for text in ("go", "Go!", "ok go now"):
        assert tree_host.read_choice(spec, text, "en") == "yes"
    assert tree_host.read_choice(spec, "gone", "en") is None and names.says(["gone"], ["go"], ("en",)) is False


def test_the_choice_is_kept_as_a_fact_replacing_an_earlier_answer_of_the_same_question():
    facts = {"holding": {("x",)}}
    tree_host.keep_answer(facts, "route", "shortcut", "the direct way", True)
    assert facts == {"holding": {("x",)}, "answered": {("route", "shortcut")}, "answer_text": {("route", "the direct way")}}
    tree_host.keep_answer(facts, "other", "a", "a", False)
    tree_host.keep_answer(facts, "route", "detour", "via the crate", True)
    assert facts["answered"] == {("route", "detour"), ("other", "a")} and facts["answer_text"] == {("route", "via the crate")}
    tree_host.keep_answer(facts, "route", None, "hmm", False)                      # no choice read: the earlier choice goes, the text only with keep_text
    assert facts["answered"] == {("other", "a")} and facts["answer_text"] == set()
    tree_host.keep_answer(facts, "route", None, "hmm", True)
    assert facts["answer_text"] == {("route", "hmm")}
    tree_host.keep_answer(facts, "route", None, "", True)                          # silence is not kept
    assert facts["answer_text"] == set()
    for odd in (None, [], "x", 3):
        tree_host.keep_answer(odd, "route", "a", "a", True)                         # anything but a dict is left alone, no error


def test_the_condition_answered_holds_only_for_the_choice_that_was_read():
    facts = {"answered": {("route", "shortcut")}}
    assert tree_host.answered(facts, ("route", "shortcut")) and tree_host.answered(facts, ["route", "shortcut"])
    for args in (("route", "detour"), ("other", "shortcut"), ("route",), ("route", "shortcut", "x"), ()):
        assert not tree_host.answered(facts, args)
    assert not tree_host.answered({}, ("route", "shortcut")) and not tree_host.answered(None, ("route", "shortcut"))


def test_the_condition_answered_is_known_to_the_tree_check_and_the_six_belief_conditions_are_unchanged():
    assert B.CONDITIONS == ("holding", "hand_free", "at", "placed", "opened", "closed")
    tree = ["sequence", ["ask", "route"], ["cond", "answered", "route", "shortcut"], ["act", "MoveTo", "{to}"]]
    trees = {"on": {"tree": tree, "assumed": True}}
    assert B.tree_for(GOAL, trees, ACTS, True, lambda k: True) is not None
    assert B.tree_for(GOAL, {"on": {"tree": ["cond", "zork", "x"], "assumed": True}}, ACTS, True, lambda k: True) is None


def test_the_cond_leaf_reads_the_fact_store_and_every_other_name_the_belief_as_before():
    h = host(world=NS(holding="gizmo", facts={"answered": {("route", "shortcut")}}))
    assert h.cond("answered", "route", "shortcut") is True and h.cond("answered", "route", "detour") is False
    assert h.cond("holding", "gizmo") is True and h.cond("holding", "other") is False
    assert host(world=NS(holding=None)).cond("answered", "route", "shortcut") is False      # a world with no fact store


def say_all(key):
    return key in ("q_route", "q_x")


def test_a_robot_that_says_the_phrase_of_a_pack_question_can_use_a_tree_that_asks_it():
    assert tree_host.has_phrase(say_all, qs, "q_x") and tree_host.has_phrase(say_all, qs, "route")        # a phrase of its own; a question's phrase
    assert not tree_host.has_phrase(say_all, qs, "quiet")                                                 # its phrase q_quiet is not said
    assert not tree_host.has_phrase(say_all, qs, "nope") and not tree_host.has_phrase(say_all, lambda: None, "nope")
    called = []
    assert tree_host.has_phrase(say_all, lambda: called.append(1), "q_x") and called == []                # the pack is read only when needed


def pack_robot(questions=QUESTIONS):
    return NS(pack=NS(table=lambda name: questions if name == "questions" else {}), ask_spec=lambda: None)


def test_the_ask_leaf_of_a_pack_question_puts_its_phrase_with_its_kind_reads_a_choice_and_keeps_facts():
    svc = Svc(["Take the direct way, please."])
    svc.robot = pack_robot()
    h = host(svc)
    assert drive(h.ask("route")) is True
    assert h.evs == [{"event": "clarify", "question": "<q_route>", "round": 0, "kind": "route"}] and svc.asked == ["<q_route>"]
    assert h.run.world.facts == {"answered": {("route", "shortcut")}, "answer_text": {("route", "Take the direct way, please.")}}


def test_an_answer_that_names_no_choice_fails_the_leaf_and_silence_keeps_no_text():
    for said, text in (("hmm, I do not know", {("route", "hmm, I do not know")}), ("", set())):
        svc = Svc([said])
        svc.robot = pack_robot()
        h = host(svc, world=NS(holding=None, facts={"answered": {("route", "detour")}}))
        assert drive(h.ask("route")) is False
        assert h.run.world.facts == {"answered": set(), "answer_text": text}             # the earlier answer of the question is gone


def test_a_question_with_no_kind_puts_a_clarify_event_with_no_kind():
    svc = Svc(["go"])
    svc.robot = pack_robot()
    h = host(svc)
    assert drive(h.ask("quiet")) is True and h.evs == [{"event": "clarify", "question": "<q_quiet>", "round": 0}]
    assert h.run.world.facts == {"answered": {("quiet", "yes")}, "answer_text": set()}   # no keep_text: no raw text


def test_a_stop_while_waiting_for_the_answer_ends_the_run_and_keeps_nothing():
    svc = Svc(["go"])
    svc.robot = pack_robot()
    svc.cancelled = lambda run: {"event": "done"}
    h = host(svc)
    assert drive(h.ask("quiet")) is None and h.run.world.facts == {}


def test_an_ask_that_names_no_pack_question_is_the_old_leaf_with_its_old_event_and_no_fact():
    svc = Svc(["the shortcut please"])
    svc.robot = pack_robot()
    h = host(svc)
    assert drive(h.ask("q_x", "route.shortcut")) is True                             # the refs form: the words of a choice, no fact kept
    assert h.evs == [{"event": "clarify", "question": "<q_x>", "round": 0, "kind": B.FLAG}] and h.run.world.facts == {}
    svc = Svc(["the shortcut please"])
    svc.robot = NS()
    h = host(svc)
    assert drive(h.ask("q_x", "route.shortcut")) is False and h.run.world.facts == {}        # a robot with no pack: no word, as before
    svc = Svc(["yes"])
    svc.robot = NS()
    assert drive(host(svc).ask("q_x", "dialogue.yes")) is True                       # a language-pack list: as before


def test_a_name_that_is_both_a_phrase_key_and_a_pack_question_is_the_question():
    q = {"q_x": {"phrase": "q_route", "answer": {"type": "choice", "choices": {"yes": {"words": {"en": ["go"]}}}}}}
    svc = Svc(["go"])
    svc.robot = pack_robot(q)
    h = host(svc)
    assert drive(h.ask("q_x")) is True and h.run.world.facts["answered"] == {("q_x", "yes")} and svc.asked == ["<q_route>"]


def test_the_put_leaf_keeps_the_hosts_kind_unless_a_kind_is_given():
    h = host(Svc(["x"]))
    drive(h.put("q"))
    drive(h.put("q", None))
    drive(h.put("q", "route"))
    assert [e.get("kind", "-") for e in h.evs] == [B.FLAG, "-", "route"]


def test_picking_a_tree_with_a_mapping_does_not_change_the_declared_entry():
    entry = {"tree": ["act", "Seat", "{to}", {"where": "{to}"}], "assumed": True}
    keep = copy.deepcopy(entry)
    B.pick(GOAL, {"on": entry}, ACTS, True, lambda k: True)
    assert entry == keep
