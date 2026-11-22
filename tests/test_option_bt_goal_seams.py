"""Step S25a: option ``bt_goal`` uses the seams that exist (core/planning/tree_host.py, docs/bt_goal.md, "Tree keys").

What is pinned here:
* the default data equals the old behaviour (an entry with neither key: the engine's ladder runs, no node event; a language-pack ref reads
  the same words; the declared trees are the configured ones);
* ``pick`` / ``tree_for`` / ``plan_picks`` / ``plan_trees`` choose the same trees as a reference copy of the code before the step
  (differential, over a grid of goals, tables and robots);
* the host leaves: ``ladder: false`` calls ``try_step(ladder=False)``, ``events: true`` sends each node through ``emit``, the ``ask`` leaf
  reads the words of a pack question choice;
* a schema-1 manifest's pack ``trees`` table is the manifest's own trees (so reading it adds nothing for today's robots).
Made-up robot actions, a made-up language ``zq`` and a made-up question; no model, no robot. The runs through the real AssistLoop are the
cassettes of tests/golden/scenario_sets/s25a_tree_keys.py."""

import itertools
from types import SimpleNamespace as NS

import pytest

from robot_agent.core.planning import behavior_tree as bt
from robot_agent.core.planning import langdata, tree_host
from robot_agent.core.planning.robot import RobotView
from robot_agent.options import bt_goal as B

ACTS = {"MoveTo", "Find", "Pick", "Place", "Open", "Grab"}
ON = ["fallback", ["cond", "placed", "{object}", "{to}"], ["sequence", ["act", "MoveTo", "{from}"], ["act", "Pick", "{object}"], ["act", "Place", "{to}"]]]
GO = ["fallback", ["cond", "at", "{to}"], ["act", "MoveTo", "{to}"]]
OPEN_FIRST = ["sequence", ["act", "Open", "{from}"], ["act", "Pick", "{object}"]]


def ok(_key):
    return True


# ── the reference copy of the code before the step (options/bt_goal.py at generalize-core 814d212) ────────────────────────────────
def old_tree_for(goal, trees, actions, can_ask, has_phrase, source=None, extra=None, openable=None):
    entries = trees.get(goal.get("type")) if isinstance(trees, dict) else None
    for entry in entries if isinstance(entries, list) else [entries]:
        tree = B._filled(entry, goal, actions, can_ask, has_phrase, {**(extra or {}), "from": goal.get("from") or source}, openable)
        if tree is not None:
            return tree
    return None


def old_plan_trees(goals, trees, actions, can_ask, has_phrase, source=lambda obj: None, extra=None, openable=None, mixed=False):
    out = [old_tree_for(g, trees, actions, can_ask, has_phrase, source(g.get("object")), extra, openable) for g in goals or ()]
    if mixed:
        return out if any(t is not None for t in out) else None
    return out if out and all(t is not None for t in out) else None


def old_pack_words(lang, refs):
    out = []
    for ref in refs or ():
        blk, _dot, key = str(ref).partition(".")
        words = langdata.block(lang, blk).get(key)
        out += [w for w in words if isinstance(w, str) and w.strip()] if isinstance(words, list) else []
    return out


TABLES = [
    {}, None, {"on": {"tree": ON, "assumed": True}}, {"on": {"tree": ON}},                    # no label: not used
    {"on": {"tree": ON, "source": "made up"}, "go": {"tree": GO, "assumed": True}},
    {"on": [{"tree": OPEN_FIRST, "assumed": True, "needs_openable": ["from"]}, {"tree": ON, "assumed": True}]},
    {"on": {"tree": ON, "assumed": True, "skip_if": ["hand"]}, "go": {"tree": ["act", "Fly", "{to}"], "assumed": True}},
    {"on": {"tree": ON, "assumed": True, "ladder": False, "events": True}},                  # the new keys change no choice of tree
]
GOALS = [{"type": "on", "object": "gizmo", "from": "shelf", "to": "bench"}, {"type": "on", "object": "gizmo", "to": "bench"},
         {"type": "on", "object": "gizmo", "from": "shelf", "to": "bench", "hand": True}, {"type": "go", "to": "bench"},
         {"type": "go"}, {"type": "open", "object": "drawer"}, {}]


@pytest.mark.parametrize("trees,goal,openable", itertools.product(TABLES, GOALS, (None, lambda p: p == "shelf")))
def test_pick_chooses_the_trees_the_code_before_the_step_chose(trees, goal, openable):
    want = old_tree_for(goal, trees, ACTS, True, ok, "den", {"delivery": "here"}, openable)
    got = B.pick(goal, trees, ACTS, True, ok, "den", {"delivery": "here"}, openable)
    assert B.tree_for(goal, trees, ACTS, True, ok, "den", {"delivery": "here"}, openable) == want
    assert (got[0] if got else None) == want


@pytest.mark.parametrize("trees,mixed", itertools.product(TABLES, (False, True)))
def test_plan_trees_and_plan_picks_agree_with_the_code_before_the_step(trees, mixed):
    for goals in ([], GOALS[:1], GOALS[:2], GOALS[:4], GOALS[3:5], GOALS):
        args = (goals, trees, ACTS, True, ok)
        want = old_plan_trees(*args, source=lambda obj: "den", mixed=mixed)
        picks = B.plan_picks(*args, source=lambda obj: "den", mixed=mixed)
        assert B.plan_trees(*args, source=lambda obj: "den", mixed=mixed) == want
        assert (None if picks is None else [p[0] if p else None for p in picks]) == want


# ── the default data is the old behaviour ───────────────────────────────────────────────────────────────────────────────────────
def test_the_default_keys_are_the_old_behaviour():
    assert tree_host.DEFAULT_KEYS == {"ladder": True, "events": False}
    for entry in (None, {}, "tree", [], {"tree": ON}, {"tree": ON, "assumed": True}):
        assert tree_host.tree_keys(entry) == tree_host.DEFAULT_KEYS
    assert B.pick(GOALS[0], {"on": {"tree": ON, "assumed": True}}, ACTS, True, ok)[1] == tree_host.DEFAULT_KEYS


@pytest.mark.parametrize("entry,want", [({"ladder": False}, {"ladder": False, "events": False}),
                                        ({"events": True}, {"ladder": True, "events": True}),
                                        ({"ladder": False, "events": True}, {"ladder": False, "events": True}),
                                        ({"ladder": 0, "events": 1}, tree_host.DEFAULT_KEYS),      # only a literal false / true counts
                                        ({"ladder": "false", "events": "true"}, tree_host.DEFAULT_KEYS),
                                        ({"ladder": None, "events": None}, tree_host.DEFAULT_KEYS)])
def test_a_key_is_read_only_when_it_is_a_literal_boolean(entry, want):
    assert tree_host.tree_keys(entry) == want


def test_a_list_of_entries_gives_the_keys_of_the_entry_that_was_used():
    trees = {"on": [{"tree": OPEN_FIRST, "assumed": True, "needs_openable": ["from"], "ladder": False},
                    {"tree": ON, "assumed": True, "events": True}]}
    goal = GOALS[0]
    assert B.pick(goal, trees, ACTS, True, ok, openable=lambda p: True)[1] == {"ladder": False, "events": False}
    assert B.pick(goal, trees, ACTS, True, ok, openable=lambda p: False)[1] == {"ladder": True, "events": True}


# ── the words ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", {"dialogue": {"yes": ["vee"], "stop": ["vrak", " "]}, "zq_words": {"again": ["ree"], "assumed": True}})


QUESTIONS = {"clip_far": {"phrase": "q_clip_far", "answer": {"type": "choice", "choices": {
    "use_measured": {"words": {"zq": ["measured", ""], "en": ["measured clips"]}, "lists": ["zq_words.again"], "implies": "yes"},
    "stop_it": {"lists": ["dialogue.stop"]}, "bare": {}}}}}


def test_the_language_pack_words_are_the_words_before_the_step(zq):
    for lang, refs in itertools.product(("zq", "en", "ko"), ([], None, ["dialogue.yes"], ["zq_words.again", "dialogue.yes", "nope.none"],
                                                              ["ask_next.retry", "dialogue.stop"], ["nothing"], [7])):
        assert B.pack_words(lang, refs) == old_pack_words(lang, refs)
        assert B.pack_words(lang, refs, lambda: QUESTIONS) == old_pack_words(lang, refs)       # a pack with no such question adds nothing


def test_a_ref_the_language_pack_lacks_names_a_choice_of_a_pack_question(zq):
    assert B.pack_words("zq", ["clip_far.use_measured"], lambda: QUESTIONS) == ["measured", "ree"]
    assert B.pack_words("en", ["clip_far.use_measured"], lambda: QUESTIONS) == ["measured clips"]
    assert B.pack_words("zq", ["clip_far.stop_it"], lambda: QUESTIONS) == ["vrak"]
    assert B.pack_words("zq", ["clip_far.use_measured", "dialogue.yes"], lambda: QUESTIONS) == ["measured", "ree", "vee"]
    for ref in ("clip_far.bare", "clip_far.nobody", "nobody.use_measured", "clip_far", "x.y.z"):
        assert B.pack_words("zq", [ref], lambda: QUESTIONS) == []
    assert B.pack_words("zq", ["clip_far.use_measured"]) == [] and B.pack_words("zq", ["clip_far.use_measured"], lambda: {}) == []


def test_the_questions_are_asked_for_only_when_the_language_pack_has_no_such_list(zq):
    def boom():
        raise AssertionError("the pack's questions were read for a ref the language pack knows")
    assert B.pack_words("zq", ["dialogue.yes", "zq_words.again"], boom) == ["vee", "ree"]


# ── the trees a pack declares ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_trees_come_from_the_key_and_only_when_it_is_empty_from_the_pack():
    own, theirs = {"on": {"tree": ON, "assumed": True}}, {"go": {"tree": GO, "assumed": True}}
    assert tree_host.declared_trees(own, lambda: theirs) == own
    assert tree_host.declared_trees({}, lambda: theirs) == theirs and tree_host.declared_trees(None, lambda: theirs) == theirs
    assert tree_host.declared_trees({}, None) == {} and tree_host.declared_trees({}, lambda: {}) == {}
    assert tree_host.declared_trees({}, lambda: None) == {} and tree_host.declared_trees({}, lambda: [1]) == {}


def _boom():
    raise AssertionError("the pack was read although the key holds trees")


def test_the_pack_is_not_read_when_the_key_holds_trees():
    own = {"on": {"tree": ON, "assumed": True}}
    assert tree_host.declared_trees(own, _boom) is own


@pytest.mark.parametrize("trees", [{}, {"on": {"tree": ON, "assumed": True}}, {"on": [{"tree": ON, "source": "x"}], "go": {"tree": GO, "assumed": True}}])
def test_a_schema_1_manifest_has_the_pack_trees_it_declares_itself(trees):
    robot = RobotView.load("fakebot")
    robot.manifest.planner = {**(robot.manifest.planner or {}), "options": {"bt_goal": {"trees": trees}}}
    robot.use_pack(None)
    assert robot.pack.table("trees") == trees


# ── the host leaves over stub services ────────────────────────────────────────────────────────────────────────────────────────────
class Svc:
    def __init__(self, answers=(), pack=None):
        self.calls, self.events, self.labs, self.answers = [], [], [], list(answers)
        self.robot = NS(pack=pack, i18n=NS(phrases={"q_x": {"en": "Which one?", "zq": "Ree?"}}))

    def try_step(self, run, st, ladder=False):
        self.calls.append((dict(st), ladder))
        return True
        yield

    def emit(self, run, event, **fields):
        self.events.append((event, fields))

    def lab(self, flag, **info):
        self.labs.append(info)

    def said_of(self, run, name):
        return name

    def ask(self, run, question):
        return self.answers.pop(0) if self.answers else ""

    def cancelled(self, run):
        return None


class Run:
    lang = "zq"

    def ev(self, *event, **kw):
        return {"event": event[0], **kw}


def drive(gen):
    try:
        while True:
            next(gen)
    except StopIteration as e:
        return e.value


@pytest.mark.parametrize("keys,ladder", [(None, True), ({}, True), ({"ladder": True}, True), ({"ladder": False}, False)])
def test_act_runs_the_ladder_unless_the_entry_says_ladder_false(keys, ladder):
    svc = Svc()
    host = B._Leaves(svc, Run(), {"object": "gizmo"}, 0, keys)
    assert drive(host.act("Pick", "gizmo")) is True and svc.calls == [({"action": "Pick", "object": "gizmo"}, ladder)]


@pytest.mark.parametrize("keys,sent", [(None, False), ({"events": False}, False), ({"events": True}, True)])
def test_each_node_is_a_tree_node_event_only_with_events_true(keys, sent):
    svc = Svc()
    host = B._Leaves(svc, Run(), {"object": "gizmo"}, 3, keys)
    drive(bt.tick(["sequence", ["act", "Pick", "gizmo"], ["act", "Place", "bench"]], host))
    assert len(svc.labs) == 3                                                      # the lab record is the same with or without the key
    want = [("tree_node", {"goal": 3, "path": "0.0", "node": "act Pick gizmo", "status": "success"}),
            ("tree_node", {"goal": 3, "path": "0.1", "node": "act Place bench", "status": "success"}),
            ("tree_node", {"goal": 3, "path": "0", "node": "sequence", "status": "success"})]
    assert svc.events == (want if sent else [])


class NoEmit(Svc):
    emit = None                                                                   # a stub with no ``emit``: calling it would raise


def test_a_service_stub_with_no_emit_is_fine_when_the_key_is_off():
    host = B._Leaves(NoEmit(), Run(), {"object": "gizmo"}, 0, None)
    assert drive(bt.tick(["act", "Pick", "gizmo"], host)) == bt.SUCCESS


@pytest.mark.parametrize("answer,want", [("measured", True), ("use the ree", True), ("no thanks", False), ("", False)])
def test_the_ask_leaf_branches_on_the_words_of_a_pack_question_choice(zq, answer, want):
    pack = NS(table=lambda name: QUESTIONS if name == "questions" else {})
    svc = Svc([answer], pack)
    host = B._Leaves(svc, Run(), {"object": "gizmo"}, 0, None)
    tree = ["fallback", ["sequence", ["ask", "q_x", "clip_far.use_measured"], ["act", "Pick", "gizmo"]], ["act", "Grab", "gizmo"]]
    drive(bt.tick(tree, host))
    assert [c[0]["action"] for c in svc.calls] == (["Pick"] if want else ["Grab"])


def test_a_robot_with_no_pack_gives_no_question_words(zq):
    svc = Svc(["measured"], None)
    host = B._Leaves(svc, Run(), {"object": "gizmo"}, 0, None)
    assert host._questions() == {}
    assert drive(host.ask("q_x", "clip_far.use_measured")) is False
