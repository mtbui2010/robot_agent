"""Step S25a: bt_goal uses the seams that exist. The opt-in keys of a tree entry (``ladder: false``, ``events: true``) and the choice words
of a pack question (``<question>.<choice>``) are read by ``core/planning/tree_host.py``; an entry with neither key, and a ref no pack question
names, behave as before. The pure functions are compared with a reference copy of the code they replaced (copied here as it was at 96c17e1);
the leaves are driven with a stub service. The runs through the real AssistLoop are the cassettes of tests/golden/scenario_sets/s25a_*.py."""

import copy
from types import SimpleNamespace as NS
from typing import Optional

import pytest

from robot_agent.core.planning import behavior_tree as bt
from robot_agent.core.planning import langdata, tree_host
from robot_agent.options import bt_goal as B

SHELF, BENCH = "shelf@loft", "bench@den"
ACTS = {"MoveTo", "Find", "Pick", "Place", "Say"}
GOAL = {"type": "on", "object": "gizmo", "from": SHELF, "to": BENCH}
GO = ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]]
ASK = ["sequence", ["ask", "q_x", "ask_next.retry", "route.shortcut"], ["act", "Pick", "{object}"]]
TABLES = {
    "plain": {"on": {"tree": GO, "assumed": True}},
    "list": {"on": [{"tree": GO, "skip_if": ["from"], "assumed": True}, {"tree": GO, "assumed": True}]},
    "unlabelled": {"on": {"tree": GO}},
    "bad_tree": {"on": {"tree": ["act"], "assumed": True}},
    "missing_action": {"on": {"tree": ["act", "Fly", "{to}"], "assumed": True}},
    "keys_set": {"on": {"tree": GO, "assumed": True, "ladder": False, "events": True}},
    "none": {},
}


def ok(_k):
    return True


# ── the reference: tree_for / plan_trees / pack_words as they were before the step ────────────────────────────────────────────────
def ref_tree_for(goal, trees, actions, can_ask, has_phrase, source=None, extra=None, openable=None) -> Optional[list]:
    entries = trees.get(goal.get("type")) if isinstance(trees, dict) else None
    for entry in entries if isinstance(entries, list) else [entries]:
        tree = B._filled(entry, goal, actions, can_ask, has_phrase, {**(extra or {}), "from": goal.get("from") or source}, openable)
        if tree is not None:
            return tree
    return None


def ref_plan_trees(goals, trees, actions, can_ask, has_phrase, source=lambda obj: None, extra=None, openable=None, mixed=False):
    out = [ref_tree_for(g, trees, actions, can_ask, has_phrase, source(g.get("object")), extra, openable) for g in goals or ()]
    if mixed:
        return out if any(t is not None for t in out) else None
    return out if out and all(t is not None for t in out) else None


def ref_pack_words(lang, refs) -> list:
    out = []
    for ref in refs or ():
        blk, _dot, key = str(ref).partition(".")
        words = langdata.block(lang, blk).get(key)
        out += [w for w in words if isinstance(w, str) and w.strip()] if isinstance(words, list) else []
    return out


GOALS = [GOAL, {**GOAL, "from": ""}, {**GOAL, "type": "open"}, {**GOAL, "type": "say"}, {"type": "on", "object": "", "to": BENCH}]


@pytest.mark.parametrize("table", sorted(TABLES))
@pytest.mark.parametrize("can_ask", [True, False])
def test_tree_for_and_plan_trees_are_the_reference_for_every_table(table, can_ask):
    trees = TABLES[table]
    for goal in GOALS:
        for src in (None, "crate@den"):
            assert B.tree_for(goal, trees, ACTS, can_ask, ok, src) == ref_tree_for(goal, trees, ACTS, can_ask, ok, src)
    for mixed in (False, True):
        for goals in (GOALS, GOALS[:1], GOALS[:2], [], None):
            got = B.plan_trees(goals, trees, ACTS, can_ask, ok, mixed=mixed)
            assert got == ref_plan_trees(goals, trees, ACTS, can_ask, ok, mixed=mixed)


def test_plan_picks_is_plan_trees_with_the_keys_of_the_entry_that_was_used():
    picks = B.plan_picks([GOAL, {**GOAL, "from": ""}], TABLES["list"], ACTS, True, ok, mixed=True)
    assert [p[0] for p in picks] == B.plan_trees([GOAL, {**GOAL, "from": ""}], TABLES["list"], ACTS, True, ok, mixed=True)
    assert all(p[1] == tree_host.DEFAULT_KEYS for p in picks)
    keyed = B.pick(GOAL, TABLES["keys_set"], ACTS, True, ok)
    assert keyed[1] == {"ladder": False, "events": True}
    assert B.pick(GOAL, TABLES["none"], ACTS, True, ok) is None and B.pick(GOAL, TABLES["unlabelled"], ACTS, True, ok) is None


def test_the_keys_belong_to_the_entry_that_is_used_not_to_the_first_entry():
    trees = {"on": [{"tree": GO, "skip_if": ["from"], "assumed": True, "ladder": False}, {"tree": GO, "assumed": True, "events": True}]}
    assert B.pick(GOAL, trees, ACTS, True, ok)[1] == {"ladder": True, "events": True}           # the first entry is skipped (the goal has a from)
    assert B.pick({**GOAL, "from": ""}, trees, ACTS, True, ok, source=None)[1] == {"ladder": False, "events": False}


# ── the keys ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_default_keys_are_the_old_behaviour_and_only_a_literal_value_changes_them():
    assert tree_host.DEFAULT_KEYS == {"ladder": True, "events": False}       # the engine's ladder runs (ladder=True was hard-coded), no node event
    assert tree_host.tree_keys({"tree": GO}) == tree_host.DEFAULT_KEYS
    assert tree_host.tree_keys(None) == tree_host.DEFAULT_KEYS and tree_host.tree_keys("x") == tree_host.DEFAULT_KEYS
    assert tree_host.tree_keys({"ladder": False, "events": True}) == {"ladder": False, "events": True}
    for odd in (0, "false", None, [], 1, "yes"):                              # no truthy / falsy guessing: only a literal false / true
        assert tree_host.tree_keys({"ladder": odd, "events": odd}) == {"ladder": odd is not False, "events": False}


class Svc:
    """A stub of the services the leaves use: it records every ``try_step`` ladder flag, lab row and emitted event."""

    def __init__(self, ok=True):
        self.ok, self.tries, self.labs, self.events = ok, [], [], []
        self.robot = NS()

    def try_step(self, run, step, ladder=False):
        self.tries.append((step["action"], ladder))
        return self.ok
        yield

    def lab(self, flag, **info):
        self.labs.append((flag, info))

    def emit(self, run, event, **fields):
        self.events.append({"event": event, **fields})


def drive(gen):
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


def host(keys=None, svc=None):
    return B._Leaves(svc or Svc(), NS(lang="en", world=NS(holding=None)), GOAL, 3, keys)


def test_an_act_runs_the_ladder_unless_the_entry_says_ladder_false():
    for keys, want in ((None, True), ({}, True), ({"ladder": True}, True), ({"ladder": False}, False)):
        h = host(keys)
        assert drive(h.act("Pick", "gizmo")) is True and h.svc.tries == [("Pick", want)]


def test_a_node_is_an_event_only_with_events_true_and_the_lab_row_is_the_same_either_way():
    node = ["act", "Pick", "gizmo"]
    quiet, loud = host(), host({"events": True})
    quiet.note("0.1", node, bt.SUCCESS)
    loud.note("0.1", node, bt.FAILURE)
    assert quiet.svc.events == [] and quiet.svc.labs == [(B.FLAG, {"goal": 3, "node": "0.1 act Pick gizmo", "status": "success"})]
    assert loud.svc.labs == [(B.FLAG, {"goal": 3, "node": "0.1 act Pick gizmo", "status": "failure"})]
    assert loud.svc.events == [{"event": "tree_node", "goal": 3, "path": "0.1", "node": "act Pick gizmo", "status": "failure"}]


def test_the_runner_sends_every_node_of_a_tree_as_an_event_in_order():
    h = host({"events": True})
    assert drive(bt.tick(["sequence", ["act", "Find", "gizmo"], ["act", "Pick", "gizmo"]], h)) == bt.SUCCESS
    assert [(e["path"], e["node"], e["status"]) for e in h.svc.events] == [
        ("0.0", "act Find gizmo", "success"), ("0.1", "act Pick gizmo", "success"), ("0", "sequence", "success")]


# ── the choice words of a pack question ─────────────────────────────────────────────────────────────────────────────────────────
QUESTIONS = {"route": {"phrase": "q_x", "answer": {"type": "choice", "choices": {
    "shortcut": {"words": {"en": ["shortcut", "direct", 7, "", "  "], "zq": ["vrak"]}},
    "detour": {"words": {"en": ["detour"]}, "lists": ["ask_next.retry"]},
    "bare": {}}}}}


def test_the_language_pack_words_are_the_reference_for_every_ref_with_or_without_a_pack():
    refs = [["ask_next.retry"], ["dialogue.yes", "ask_next.stop"], ["nope.none"], ["noblock"], [], None, ["ask_next.retry", "dialogue.stop"]]
    for lang in ("en", "ko", "zq"):
        for r in refs:
            assert tree_host.pack_words(lang, r) == ref_pack_words(lang, r) == B.pack_words(lang, r)
            assert B.pack_words(lang, r, lambda: QUESTIONS) == ref_pack_words(lang, r)       # a ref the pack does not name: unchanged
            assert B.pack_words(lang, r, lambda: {}) == ref_pack_words(lang, r)


def test_a_ref_that_names_a_choice_of_a_pack_question_gives_its_words_in_the_language_of_the_run():
    q = lambda: QUESTIONS                                                                      # noqa: E731
    assert B.pack_words("en", ["route.shortcut"], q) == ["shortcut", "direct"]               # a non-string and an empty word are dropped
    assert B.pack_words("zq", ["route.shortcut"], q) == ["vrak"] and B.pack_words("ko", ["route.shortcut"], q) == []
    assert B.pack_words("en", ["route.detour"], q) == ["detour"] + ref_pack_words("en", ["ask_next.retry"])      # ``lists`` name language-pack lists
    assert B.pack_words("en", ["route.bare", "route.nope", "nope.shortcut"], q) == []
    assert B.pack_words("en", ["route.shortcut"]) == [] and B.pack_words("en", ["route.shortcut"], lambda: None) == []   # no pack: nothing


def test_the_pack_is_asked_only_for_a_ref_the_language_pack_has_no_list_for():
    calls = []
    assert B.pack_words("en", ["ask_next.retry"], lambda: calls.append(1) or QUESTIONS) == ref_pack_words("en", ["ask_next.retry"])
    assert calls == []
    B.pack_words("en", ["route.shortcut"], lambda: calls.append(1) or QUESTIONS)
    assert calls == [1]


def test_the_ask_leaf_reads_the_choice_of_the_robots_pack_and_a_robot_with_no_pack_is_unchanged():
    class Robot:
        pack = NS(table=lambda name: QUESTIONS if name == "questions" else {})

        def ask_spec(self):
            return None

    for robot, said, want in ((Robot(), "the direct way please", True), (Robot(), "no idea", False), (NS(), "the direct way", False)):
        svc = Svc()
        svc.robot = robot
        svc.ask = lambda run, q, said=said: said
        svc.cancelled = lambda run: None
        svc.said_of = lambda run, o: o
        h = B._Leaves(svc, NS(lang="en", world=NS(holding=None), ev=lambda *a, **k: {}), GOAL, 0)
        h._phrase = lambda key: "which way?"
        assert drive(h.ask("q_x", "route.shortcut")) is want


def test_picking_a_tree_does_not_change_the_declared_entry():
    entry = copy.deepcopy(TABLES["keys_set"]["on"])
    B.pick(GOAL, {"on": entry}, ACTS, True, ok)
    assert entry == TABLES["keys_set"]["on"]
