"""Step S25: typed ``act`` arguments in a tree, ``[ask, <pack question>]``, the answer kept as a fact.

An ``act`` leaf may end with one mapping (``[act, Seat, "{to}", {arm: left}]``): it reaches the step as ``args`` (the typed arguments the pack
declares, Step S26). An ``ask`` leaf whose first argument names a question of the robot's pack (a phrase, ``answer.type: choice``) reads the
answer as one of the choices and keeps it as the fact ``answered(<question>, <choice>)`` (and with ``keep_text`` the raw text as
``answer_text``); the leaf ``cond answered <question> <choice>`` branches on it. A tree with no mapping and no pack question runs as before:
``valid`` and ``fill`` are compared with a reference copy of the code they had before the step (copied here as it was at ef42ad3). The runs
through the real AssistLoop are the cassettes of tests/golden/scenario_sets/s25_*.py."""

import itertools
from types import SimpleNamespace as NS
from typing import Optional

import pytest

from robot_agent.core.planning import behavior_tree as bt
from robot_agent.core.planning import tree_host
from robot_agent.options import bt_goal as B

SHELF, BENCH = "shelf@loft", "bench@den"
GOAL = {"type": "on", "object": "gizmo", "from": SHELF, "to": BENCH}
ACTS = {"MoveTo", "Find", "Pick", "Place", "Seat"}


# ── the reference: valid / fill as they were before the step ───────────────────────────────────────────────────────────────────────
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


LEAF_ARGS = [[], ["x"], ["x", "y"], ["{to}"], ["{to}", "{object}"], ["{nope}"], ["{}"], ["{a", "b}"], [""], ["x", 3], [3], [None], [["act", "x"]]]
TREES = ([["act", *a] for a in LEAF_ARGS] + [[k, *a] for k in ("cond", "ask", "confirm", "reply", "bogus") for a in LEAF_ARGS[:6]]
         + [["sequence", ["act", "Find", "{object}"], ["act", "Pick", "{object}"]], ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]],
            ["retry", 2, ["act", "Pick", "{object}"]], ["retry", 0, ["act", "Pick", "x"]], ["retry", True, ["act", "x"]], ["sequence"], [], None, "act"])
VALUES = [{}, {"to": BENCH, "object": "gizmo"}, {"to": "", "object": "gizmo"}, {"nope": "n", "a": "b"}]


def test_a_tree_with_no_mapping_is_valid_and_filled_exactly_as_before():
    for tree in TREES:
        assert bt.valid(tree) == ref_valid(tree), tree
        if ref_valid(tree):
            for values in VALUES:
                assert bt.fill(tree, values) == ref_fill(tree, values), (tree, values)


# ── the mapping of an act ───────────────────────────────────────────────────────────────────────────────────────────────────────
def test_an_act_may_end_with_one_mapping_of_scalars_and_no_other_leaf_may():
    assert bt.valid(["act", "Seat", "{to}", {"arm": "left", "dz_mm": -2, "slow": True, "k": 0.5}])
    assert bt.valid(["act", "Seat", "{to}", {}])
    assert bt.valid(["sequence", ["act", "Seat", "x", {"arm": "left"}], ["retry", 2, ["act", "Seat", "x", {"arm": "right"}]]])
    for bad in (["act", {"arm": "left"}],                       # no action before it
                ["act", "Seat", {"arm": "left"}, "x"],          # not last
                ["act", "Seat", "x", {"arm": "l"}, {"b": 1}],    # two
                ["act", "Seat", "x", {1: "l"}],                  # a key that is no string
                ["act", "Seat", "x", {"arm": ["l"]}],            # a value that is no scalar
                ["act", "Seat", "x", {"arm": None}],
                ["ask", "q", {"arm": "l"}], ["cond", "at", {"arm": "l"}], ["confirm", "q", {"a": "b"}], ["reply", "q", {"a": "b"}]):
        assert not bt.valid(bad), bad


def test_fill_puts_the_goals_values_into_the_mapping_and_a_missing_one_drops_the_tree():
    tree = ["act", "Seat", "{to}", {"arm": "left", "where": "{to}", "n": 3, "on": True}]
    assert bt.fill(tree, {"to": BENCH}) == ["act", "Seat", BENCH, {"arm": "left", "where": BENCH, "n": 3, "on": True}]
    assert bt.fill(["act", "Seat", "{to}", {"where": "{from}"}], {"to": BENCH}) is None
    assert bt.fill(["act", "Seat", "{to}", {"where": "{from}"}], {"to": BENCH, "from": ""}) is None
    assert bt.fill(["act", "Seat", "x", {"n": 0, "on": False}], {}) == ["act", "Seat", "x", {"n": 0, "on": False}]      # a literal 0 / False is a value
    src = ["act", "Seat", "x", {"where": "{to}"}]
    bt.fill(src, {"to": BENCH})
    assert src == ["act", "Seat", "x", {"where": "{to}"}]                                              # the declared tree is not changed
    assert list(bt.leaves(bt.fill(tree, {"to": BENCH})))[0][:3] == ["act", "Seat", BENCH]


class Svc:
    """A stub of the services the leaves use: it records every step given to ``try_step``, the clarify events and the answers it gives."""

    def __init__(self, said=""):
        self.steps, self.labs, self.events, self.asked, self.said = [], [], [], [], said
        self.robot = NS(pack=NS(table=lambda name: QUESTIONS if name == "questions" else {}))

    def try_step(self, run, step, ladder=False):
        self.steps.append(step)
        return True
        yield

    def lab(self, flag, **info):
        self.labs.append((flag, info))

    def ask(self, run, q):
        self.asked.append(q)
        return self.said

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


def host(said="", facts=True):
    svc, world = Svc(said), NS(holding=None, facts={})
    if not facts:
        del world.facts
    run = NS(lang="en", world=world, ev=lambda name, **f: {"event": name, **f})
    h = B._Leaves(svc, run, GOAL, 0)
    h._phrase = lambda key: f"<{key}>"
    return h


def test_an_act_with_a_mapping_gives_the_step_its_args_and_one_without_gives_the_step_it_always_had():
    h = host()
    assert drive(h.act("Seat", BENCH, {"arm": "left", "dz_mm": -2})) is True
    assert drive(h.act("Seat", BENCH)) is True and drive(h.act("Seat", BENCH, None)) is True and drive(h.act("Seat", BENCH, {})) is True
    assert h.svc.steps == [{"action": "Seat", "object": BENCH, "args": {"arm": "left", "dz_mm": -2}}] + [{"action": "Seat", "object": BENCH}] * 3
    h = host()
    assert drive(bt.tick(["act", "Seat", BENCH, {"arm": "left"}], h)) == bt.SUCCESS
    assert h.svc.steps == [{"action": "Seat", "object": BENCH, "args": {"arm": "left"}}]
    assert h.svc.labs[-1][1]["node"] == "0 act Seat bench@den {'arm': 'left'}"                                      # the trace names the mapping


def test_the_step_does_not_share_the_trees_mapping():
    tree = ["act", "Seat", BENCH, {"arm": "left"}]
    h = host()
    drive(bt.tick(tree, h))
    h.svc.steps[0]["args"]["arm"] = "right"
    assert tree == ["act", "Seat", BENCH, {"arm": "left"}]


# ── the question of the pack ────────────────────────────────────────────────────────────────────────────────────────────────────
QUESTIONS = {
    "clip": {"phrase": "q_clip", "kind": "clip.decision", "answer": {"type": "choice", "keep_text": True, "choices": {
        "use": {"words": {"en": ["use the measured", "go ahead"]}}, "again": {"words": {"en": ["measure again"]}}, "leave": {"words": {"en": ["leave it"]}}}}},
    "mute": {"phrase": "q_mute", "kind": None, "answer": {"type": "choice", "choices": {"yes": {"words": {"en": ["yes"]}}}}},
    "nophrase": {"phrase": None, "answer": {"type": "choice", "choices": {}}},
    "yesno": {"phrase": "q_yn", "answer": {"type": "yes_no"}},
    "broken": "text",
}
questions = lambda: QUESTIONS                                    # noqa: E731


def test_question_of_gives_only_a_question_with_a_phrase_and_a_choice_answer():
    assert tree_host.question_of(questions, "clip") is QUESTIONS["clip"] and tree_host.question_of(questions, "mute") is QUESTIONS["mute"]
    for name in ("nophrase", "yesno", "broken", "absent", "q_clip", "", None):
        assert tree_host.question_of(questions, name) is None, name
    for table in (lambda: {}, lambda: None, lambda: [], lambda: 7, lambda: 1 / 0):                      # no table, or one that cannot be read
        assert tree_host.question_of(table, "clip") is None


def test_the_answer_is_the_first_choice_in_declaration_order_whose_words_it_says():
    spec = QUESTIONS["clip"]
    for text, want in (("Use the measured clip positions and try once more.", "use"), ("please measure again", "again"), ("LEAVE IT", "leave"),
                       ("go ahead, or measure again", "use"), ("whatever", None), ("", None), ("  ", None)):
        assert tree_host.read_choice(spec, text, "en") == want, text
    assert tree_host.read_choice({"answer": {"choices": {"a": {"words": {"en": ["?? ??", "- -"]}}}}}, "anything at all", "en") is None   # a phrase with no word
    assert tree_host.read_choice(spec, "use the measured", "ko") is None and tree_host.read_choice({}, "use", "en") is None
    assert tree_host.read_choice({"answer": {"choices": {"a": "text", "b": {"words": {"en": ["b"]}}}}}, "b", "en") == "b"


def test_the_answer_is_kept_as_a_fact_that_replaces_the_earlier_answer_of_that_question():
    facts = {"inspected": {("part",)}}
    tree_host.keep_answer(facts, "clip", "use", "use the measured clip", True)
    tree_host.keep_answer(facts, "other", "x", "x", False)
    assert facts == {"inspected": {("part",)}, "answered": {("clip", "use"), ("other", "x")}, "answer_text": {("clip", "use the measured clip")}}
    tree_host.keep_answer(facts, "clip", "again", "measure again", True)
    assert facts["answered"] == {("clip", "again"), ("other", "x")} and facts["answer_text"] == {("clip", "measure again")}
    tree_host.keep_answer(facts, "clip", None, "mumble", True)                                       # not understood: no choice, the text is kept
    assert facts["answered"] == {("other", "x")} and facts["answer_text"] == {("clip", "mumble")}
    tree_host.keep_answer(facts, "clip", None, "", True)                                             # silence: nothing kept for it
    assert facts["answer_text"] == set()
    for odd in (None, [], "x", 3):
        tree_host.keep_answer(odd, "clip", "use", "use", True)                                       # a world with no fact store: left alone


def test_the_condition_answered_reads_the_fact_with_exactly_two_arguments():
    facts = {"answered": {("clip", "use")}}
    assert tree_host.answered(facts, ("clip", "use")) and tree_host.answered(facts, ["clip", "use"])
    for args in (("clip", "again"), ("clip",), ("clip", "use", "x"), (), ("use", "clip")):
        assert not tree_host.answered(facts, args), args
    assert not tree_host.answered({}, ("clip", "use")) and not tree_host.answered(None, ("clip", "use"))


def test_the_ask_leaf_reads_a_pack_question_keeps_the_facts_and_puts_the_declared_kind():
    for said, ok, choice in (("Use the measured clip positions and try once more.", True, "use"), ("measure again", True, "again"), ("hmm", False, None),
                             ("", False, None)):
        h = host(said)
        events = []
        h.run.ev = lambda name, **f: events.append((name, f)) or {}
        assert drive(h.ask("clip")) is ok
        assert events == [("clarify", {"question": "<q_clip>", "round": 0, "kind": "clip.decision"})] and h.svc.asked == ["<q_clip>"]
        assert h.run.world.facts.get("answered", set()) == ({("clip", choice)} if choice else set())
        assert h.run.world.facts.get("answer_text", set()) == ({("clip", said)} if said else set())
        assert h.cond("answered", "clip", choice or "use") is (choice is not None)


def test_a_question_with_no_kind_puts_a_clarify_without_a_kind_and_keeps_no_text_unless_it_says_so():
    h = host("yes")
    events = []
    h.run.ev = lambda name, **f: events.append((name, f)) or {}
    assert drive(h.ask("mute")) is True
    assert events == [("clarify", {"question": "<q_mute>", "round": 0})]
    assert h.run.world.facts == {"answered": {("mute", "yes")}, "answer_text": set()}


def test_ask_with_a_pack_question_on_a_world_with_no_fact_store_still_reads_the_answer():
    h = host("go ahead", facts=False)
    assert drive(h.ask("clip")) is True and not hasattr(h.run.world, "facts")
    assert h.cond("answered", "clip", "use") is False


def test_a_tree_branches_on_the_content_of_an_answer():
    tree = ["sequence", ["ask", "clip"], ["fallback", ["cond", "answered", "clip", "use"], ["sequence", ["cond", "answered", "clip", "again"],
                                                                                           ["act", "Find", "x"]]], ["act", "Pick", "x"]]
    for said, steps in (("use the measured", ["Pick"]), ("measure again", ["Find", "Pick"]), ("leave it", None)):
        h = host(said)
        assert drive(bt.tick(tree, h)) == (bt.SUCCESS if steps else bt.FAILURE)
        assert [s["action"] for s in h.svc.steps] == (steps or [])


def test_the_ask_leaf_with_a_phrase_key_is_not_changed_by_the_pack_questions():
    for said, want in (("the direct way", True), ("nothing", False)):
        h = host(said)
        h.svc.robot.pack = NS(table=lambda name: {"route": {"phrase": None, "answer": {"type": "choice", "choices": {"shortcut": {"words": {"en": ["direct"]}}}}}}
                              if name == "questions" else {})
        events = []
        h.run.ev = lambda name, **f: events.append((name, f)) or {}
        assert drive(h.ask("q_x", "route.shortcut")) is want
        assert events == [("clarify", {"question": "<q_x>", "round": 0, "kind": "bt_goal"})] and h.run.world.facts == {}
    h = host("direct")
    h.svc.robot = NS()
    assert drive(h.ask("q_x", "ask_next.retry")) is False and h.run.world.facts == {}


# ── which trees a robot can use ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_has_phrase_is_the_robots_phrase_or_the_phrase_of_a_pack_question():
    said = {"q_clip", "q_plain"}.__contains__
    assert tree_host.has_phrase(said, questions, "q_plain") and tree_host.has_phrase(said, questions, "clip")
    assert not tree_host.has_phrase(said, questions, "mute") and not tree_host.has_phrase(said, questions, "nophrase")
    assert not tree_host.has_phrase(said, questions, "nope") and not tree_host.has_phrase(said, lambda: 1 / 0, "clip")
    calls = []
    assert tree_host.has_phrase(said, lambda: calls.append(1) or QUESTIONS, "q_plain") and calls == []      # the pack is read only when needed


def usable(tree, can_ask=True):
    """The tree filled for GOAL when a robot that says only ``q_clip`` and has the pack question ``clip`` can use it, else None."""
    says = lambda k: tree_host.has_phrase(lambda x: x == "q_clip", questions, k)                      # noqa: E731
    return B._filled({"tree": tree, "assumed": True}, GOAL, ACTS, can_ask, says, {"from": SHELF})


def test_a_tree_may_use_the_answered_condition_and_an_ask_of_a_pack_question():
    tree = ["sequence", ["ask", "clip"], ["cond", "answered", "clip", "use"], ["act", "Seat", "{to}", {"arm": "left"}]]
    assert usable(tree) == ["sequence", ["ask", "clip"], ["cond", "answered", "clip", "use"], ["act", "Seat", BENCH, {"arm": "left"}]]
    assert usable(tree, can_ask=False) is None                                                    # the robot cannot ask
    assert usable(["act", "Fly", "{to}", {"arm": "left"}]) is None                                # an action the robot lacks, mapping or not
    assert usable(["cond", "nothing", "x"]) is None                                               # the other conditions stay as they were
    assert B.CONDITIONS == ("holding", "hand_free", "at", "placed", "opened", "closed")           # the closed list is not extended: only the host reads the fact


def test_the_closed_list_of_conditions_and_the_default_keys_are_as_before():
    assert tree_host.DEFAULT_KEYS == {"ladder": True, "events": False}
    assert (tree_host.ANSWERED, tree_host.ANSWER_TEXT) == ("answered", "answer_text")


@pytest.mark.parametrize("n", range(3))
def test_every_combination_of_one_act_and_a_mapping_is_valid_only_when_well_formed(n):
    for args in itertools.product(["x", {"a": 1}, {"a": [1]}, 5], repeat=n + 1):
        node = ["act", *args]
        want = ref_valid(node) or (len(args) > 1 and isinstance(args[-1], dict) and all(isinstance(v, int) and not isinstance(v, bool) for v in args[-1].values())
                                   and all(isinstance(a, str) for a in args[:-1]))
        assert bt.valid(node) == want, node
