"""Step S07 made the goal-kind table pack data: needs, the understand-prompt line, understood (always / capable), planned_by,
the conflicts flag and the sub-goal phrase of each kind are read from robot_agent/packs/household/goal_kinds.yaml
(robot_agent/core/pack/kinds.py; goals.GOAL_KINDS is the standard table built from it). These tests prove nothing changed for
the standard pack: (1) its data equals the old constants (tests/legacy_ref/goal_tables.py: GOAL_KINDS, MANIPULATION,
TEMPLATE_KINDS, OPTIONAL_KINDS); (2) the sub-goal phrase equals the old if-chain on every row of tests/test_subgoal_phrases.py
and on a grid of made-up goals; (3) a differential grid: missing_actions, kinds_for, domain_actions, prompt_lines,
is_template, conflicts and the loop's _kinds / _lacking / _cannot_do equal the reference copy (same value, or the same
exception) over made-up action sets and goals; (4) the standard table stays the seam (a patch of goals.GOAL_KINDS reaches
the loop); (5) opt-in: a robot pack's own kind is understood, prompted and phrased from its entry, a removed kind is
refused as an unknown kind always was, and `conflicts: none` takes a kind out of the goal validator."""

import itertools

import pytest

from legacy_ref import goal_tables as old
from robot_agent.contract import pack as schema
from robot_agent.core.pack import default_pack
from robot_agent.core.pack import kinds as K
from robot_agent.core.pack.loader import Pack
from robot_agent.core.planning import goals
from robot_agent.core.planning.assist_loop import AssistLoop

DT, DR, DS = "dining table@kitchen", "drawer@kitchen", "desk@study room"
VOCAB = ("Pick", "Place", "PutIn", "Open", "Close", "MoveTo", "Say", "WaitForUnload", "Fasten")
ACTION_SETS = [set(c) for n in range(len(VOCAB) + 1) for c in itertools.combinations(VOCAB, n)]


def _same(new, ref, *args, **kw):
    """new(*args) == ref(*args), or both raise the same exception type."""
    try:
        want = ("ok", ref(*args, **kw))
    except Exception as e:                                           # noqa: BLE001 - the old exception is the behaviour
        want = ("raise", type(e))
    try:
        got = ("ok", new(*args, **kw))
    except Exception as e:                                           # noqa: BLE001
        got = ("raise", type(e))
    assert got == want, (args, kw)


# ── (1) the data equals the old constants ──
def test_the_standard_table_equals_the_old_constants_in_the_old_order():
    assert list(goals.GOAL_KINDS) == list(old.GOAL_KINDS)
    for n, k in old.GOAL_KINDS.items():
        new = goals.GOAL_KINDS[n]
        assert (new.name, new.needs, new.prompt) == (k.name, k.needs, k.prompt), n
    assert goals.MANIPULATION == old.MANIPULATION and goals.TEMPLATE_KINDS == old.TEMPLATE_KINDS
    assert {n: (k.needs, k.prompt) for n, k in goals.OPTIONAL_KINDS.items()} == \
        {n: (k.needs, k.prompt) for n, k in old.OPTIONAL_KINDS.items()}


def test_understood_and_planned_by_say_what_manipulation_and_template_kinds_said():
    for n, k in goals.GOAL_KINDS.items():
        assert (k.understood == "always") == (n in old.MANIPULATION), n
        assert (k.planned_by == "template") == (n in old.TEMPLATE_KINDS), n
        assert k.planned_by in ("planner", "template") and k.conflicts == "default", n


def test_every_kind_of_the_file_is_labelled_and_follows_the_schema():
    raw = K.standard_entries()
    for n, e in raw.items():
        assert str(e.get("source") or "").strip(), f"{n} has no source label"
        assert e.get("needs") and e.get("subgoal"), n
    assert K.standard() == goals.GOAL_KINDS


def test_an_entry_without_a_field_takes_the_schema_default():
    k = K.from_entry("x", {"needs": ["A", ["B", "C"]]})
    assert k == K.GoalKind("x", ("A", ("B", "C")))
    assert (k.understood, k.planned_by, k.conflicts) == tuple(schema.default("goal_kinds", f)
                                                              for f in ("understood", "planned_by", "conflicts"))
    assert K.from_entry("y", None) == K.GoalKind("y", ())


# ── (2) the sub-goal phrase ──
def _subgoal_rows():
    import test_subgoal_phrases as T
    return T.test_each_kind_has_its_subgoal_phrase.pytestmark[0].args[1]


@pytest.mark.parametrize("goal, want", _subgoal_rows())
def test_the_subgoal_phrase_equals_the_old_text_on_every_row_of_the_phrase_test(goal, want):
    assert goals.subgoal_text(goal) == old.subgoal_text(goal) == want


_OBJ = ["apple", "", None, 7]
_TO = ["me", DT, "", None, "@MISSING"]
_FROM = [{}, {"from": DS}, {"from_any": [DS, "counter@kitchen"]}, {"from": DS, "from_any": ["x"]}, {"from_any": []},
         {"from": "", "from_any": ["y"]}, {"from_any": [1, 2]}]
_EXTRA = [{}, {"hand": True}, {"hand": 0}, {"person": "lena"}, {"hand": True, "person": "bo"}]


def _goals():
    for t in ["on", "in", "open", "close", "go", "say", "deliver", "give", "inspect", "", None, ["on"]]:
        for o, to, src, extra in itertools.product(_OBJ, _TO, _FROM, _EXTRA):
            g = {"type": t, "object": o, **src, **extra}
            if to != "@MISSING":
                g["to"] = to
            yield g
    yield {}
    yield {"object": "x", "to": "y"}


def test_the_subgoal_phrase_equals_the_old_if_chain_on_a_grid_of_made_up_goals():
    n = 0
    for g in _goals():
        _same(goals.subgoal_text, old.subgoal_text, g)
        _same(lambda x: goals.subgoal_text(x, None, goals.kinds_of(None)), old.subgoal_text, g)
        n += 1
    assert n == 12 * 4 * 5 * 7 * 5 + 2


def test_the_template_renderer():
    s = {"a": "A", "b": "", "c": None}
    assert K.render("x {a} y", s) == "x A y"
    assert K.render("x[ {a}][ {b}][ {c}]", s) == "x A"
    assert K.render("?b: one || ?a: two {a} || three", s) == "two A"
    assert K.render("?b: one", s) == "" and K.render("{c}", s) == "None"


# ── (3) the differential grid ──
def test_needs_kinds_and_the_refusal_vocabulary_equal_the_old_code_for_every_action_set():
    goal_list = [{"type": t, "to": to} for t in [*old.GOAL_KINDS, "give", "polish", None] for to in ("", "kitchen")]
    for acts in ACTION_SETS:
        for opt in [(), ("give",), ("give", "nope")]:
            assert goals.kinds_for(acts, opt) == old.kinds_for(acts, opt)
            assert goals.kinds_for(acts, opt, goals.kinds_of(None)) == old.kinds_for(acts, opt)
        for g in goal_list:
            assert goals.missing_actions(g, acts) == old.missing_actions(g, acts)
        assert goals.lacking(acts) == old.lacking(acts)
        assert goals.cannot_do(goal_list, acts) == old.cannot_do(goal_list, acts)
    assert goals.domain_actions() == old.domain_actions() and goals.lacking(None) == old.lacking(None)
    _same(goals.missing_actions, old.missing_actions, {"type": ["on"]}, ())


def test_prompt_lines_and_is_template_equal_the_old_code():
    names = [*old.GOAL_KINDS, "give", "polish"]
    for n in range(4):
        for ks in itertools.permutations(names, n):
            assert goals.prompt_lines(ks) == old.prompt_lines(ks)
            gs = [{"type": k} for k in ks]
            assert goals.is_template(gs) == old.is_template(gs)
            assert goals.is_template(gs, goals.kinds_of(None)) == old.is_template(gs)
    assert goals.is_template([{"type": ["go"]}]) == old.is_template([{"type": ["go"]}]) is False


def _conflict_lists():
    base = [{"type": "on", "object": "cup", "to": DT}, {"type": "in", "object": "cup", "to": DR},
            {"type": "on", "object": "cup", "to": DT}, {"type": "open", "object": DR}, {"type": "close", "object": DR},
            {"type": "open", "object": "fridge"}, {"type": "go", "to": DT}, {"type": "on", "object": "", "to": DS},
            {"type": "on", "object": "mug", "to": ""}, {"type": "close", "object": None}, {"type": "give", "object": "cup", "to": "me"}]
    for n in range(1, 5):
        yield from (list(c) for c in itertools.permutations(base, n))


def test_the_goal_validator_equals_the_old_code():
    def norm(r):
        q = r[1]
        return r[0], (type(q).__name__, str(q), getattr(q, "ask", None)) if q is not None else None
    for gl in _conflict_lists():
        for name, declare in [(None, False), (lambda k: k.upper(), False), (None, True)]:
            assert norm(goals.conflicts(gl, name, declare)) == norm(old.conflicts(gl, name, declare))
            assert norm(goals.conflicts(gl, name, declare, kinds=goals.kinds_of(None))) == norm(old.conflicts(gl, name, declare))


@pytest.fixture(scope="module", params=["fakebot", "fakehome", "fakeline"])
def view_loop(request):
    from robot_agent.core.planning.robot import RobotView
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot, lp.cfg = RobotView.load(request.param), {}
    return lp


def test_the_loop_s_kinds_lacking_and_cannot_do_equal_the_old_code_for_the_made_up_robots(view_loop):
    acts = view_loop.robot.plan_actions
    for give in (False, True):
        view_loop.cfg = {"understand_give_kind": give}
        assert view_loop._kinds() == old.kinds_for(acts, ("give",) if give else ())
        assert goals.prompt_lines(view_loop._kinds(), goals.kinds_of(view_loop)) == old.prompt_lines(view_loop._kinds())
    assert view_loop._lacking() == old.lacking(acts)
    for t in [*old.GOAL_KINDS, "give", "polish"]:
        for gl in ([{"type": t, "to": ""}], [{"type": t, "to": "kitchen"}, {"type": "on"}]):
            assert view_loop._cannot_do(gl) == old.cannot_do(gl, acts)


# ── (4) the seam ──
def test_the_standard_table_is_what_the_loop_reads_so_a_patch_reaches_it(view_loop, monkeypatch):
    table = goals.kinds_of(view_loop)
    assert all(table[n] is goals.GOAL_KINDS[n] for n in goals.GOAL_KINDS) and list(table) == list(goals.GOAL_KINDS)
    monkeypatch.setitem(goals.GOAL_KINDS, "in", goals.GoalKind("in", ("Fly",), understood="always"))
    view_loop.cfg = {}
    assert goals.kinds_of(view_loop)["in"].needs == ("Fly",)
    assert view_loop._cannot_do([{"type": "in"}]) == "Fly"


# ── (5) opt-in: a robot pack's own kinds ──
INSTALL = {"needs": ["Pick", "Fasten"], "understood": "capable", "planned_by": "planner", "conflicts": "none",
           "prompt": '"install" = mount a part: object = the part, from "", to = the place.',
           "subgoal": "install the {object}{src} at {to}", "rules": "line.install", "source": "made up for this test"}


class _Robot:
    def __init__(self, pack, acts):
        self.pack, self.plan_actions = pack, set(acts)


def _pack_loop(acts, drop=(), **replace):
    std = default_pack().table("goal_kinds")
    kinds = {n: e for n, e in std.items() if n not in drop}
    kinds.update(replace)
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot, lp.cfg = _Robot(Pack(schema=2, name="line", tables={"goal_kinds": dict(kinds, install=INSTALL)}), acts), {}
    return lp


def test_a_pack_s_own_kind_is_understood_prompted_and_phrased_from_its_entry():
    lp = _pack_loop({"Pick", "Place", "Fasten"})
    assert lp._kinds() == ["on", "in", "open", "close", "install"]
    assert goals.prompt_lines(lp._kinds(), goals.kinds_of(lp)) == "\n  " + INSTALL["prompt"]
    assert lp._cannot_do([{"type": "install", "object": "pipe", "to": "wall"}]) is None
    assert "Fasten" not in lp._lacking() and "Fasten" in goals.lacking(set(), goals.kinds_of(lp))
    g = {"type": "install", "object": "pipe", "from": "rack", "to": "wall"}
    assert goals.subgoal_text(g, None, goals.kinds_of(lp)) == "install the pipe (it is at rack) at wall"
    assert goals.subgoal_text(g) == "install pipe from rack to wall", "the standard table has no install kind: describe()"
    assert "install" not in _pack_loop({"Pick", "Place"})._kinds(), "capable: only with its actions"
    assert goals.kinds_of(lp)["on"] is goals.GOAL_KINDS["on"], "the kept standard kinds are the standard table's"


def test_a_removed_kind_leaves_the_enum_and_is_refused_as_an_unknown_kind():
    lp = _pack_loop({"Pick", "Place", "PutIn", "Fasten"}, drop=("in",))
    assert "in" not in lp._kinds() and lp._cannot_do([{"type": "in", "object": "x", "to": DR}]) == "in"
    assert goals.subgoal_text({"type": "in", "object": "x", "to": DR}, None, goals.kinds_of(lp)) == f"in x to {DR}"


def test_conflicts_none_takes_a_kind_out_of_the_goal_validator():
    two = [{"type": "on", "object": "cup", "to": DT}, {"type": "on", "object": "cup", "to": DS},
           {"type": "open", "object": DR}, {"type": "close", "object": DR}]
    on_off = {**default_pack().table("goal_kinds")["on"], "conflicts": "none"}
    close_off = {**default_pack().table("goal_kinds")["close"], "conflicts": "none"}
    kept, q = goals.conflicts(two)
    assert q and kept == [two[0], two[1], two[3]]
    kept, q = goals.conflicts(two, kinds=goals.kinds_of(_pack_loop(set(), on=on_off)))
    assert q is None and kept == [two[0], two[1], two[3]], "two destinations of an `on` goal are no question any more"
    kept, q = goals.conflicts(two, kinds=goals.kinds_of(_pack_loop(set(), close=close_off)))
    assert q and kept == two, "a close that the validator does not look at keeps the open before it"
