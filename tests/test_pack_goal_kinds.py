"""Step S07 made the goal-kind table pack data: needs, the understand prompt line, understood, planned_by, the conflicts flag
and the sub-goal phrase of each kind are fields of ``goal_kinds`` in the robot's pack (the household values:
robot_agent/packs/household/goal_kinds.yaml; the reader: robot_agent/core/pack/kind_table.py). These tests prove nothing
changed for the standard pack: (1) its data equals the old constants (GOAL_KINDS, MANIPULATION, TEMPLATE_KINDS); (2) the
sub-goal phrase of every row of tests/test_subgoal_phrases.py is the old text; (3) a differential grid: the functions of
core/planning/goals.py and the reference copy of the old ones (tests/legacy_ref/goal_tables.py) give the same value, or raise
the same exception; (4) every robot with no pack of its own reads the module view (so a patch of it still bites); (5) opt-in:
a pack's own kinds, their needs, prompt line, sub-goal phrase and ``conflicts: none``."""
import itertools
from types import SimpleNamespace

import pytest

from legacy_ref import goal_tables as old
from robot_agent.core.pack import default_pack, kind_table
from robot_agent.core.pack.loader import Pack
from robot_agent.core.planning import goals as new
from robot_agent.core.planning.assist_loop import AssistLoop


def outcome(fn, *a, **kw):
    try:
        return ("value", fn(*a, **kw))
    except Exception as e:                     # noqa: BLE001
        return ("raises", type(e).__name__)


def plain(table) -> dict:
    return {k: (v.name, v.needs, v.prompt) for k, v in table.items()}


# ── (1) the data equals the old constants ──
def test_the_standard_pack_declares_the_old_kinds_needs_and_prompt_lines_in_the_old_order():
    assert plain(new.GOAL_KINDS) == plain(old.GOAL_KINDS) and list(new.GOAL_KINDS) == list(old.GOAL_KINDS)
    assert new.MANIPULATION == old.MANIPULATION and new.TEMPLATE_KINDS == old.TEMPLATE_KINDS
    assert plain(new.OPTIONAL_KINDS) == plain(old.OPTIONAL_KINDS)
    assert {k for k, v in new.GOAL_KINDS.items() if v.understood == "always"} == set(old.MANIPULATION)
    assert {k for k, v in new.GOAL_KINDS.items() if v.planned_by == "template"} == set(old.TEMPLATE_KINDS)
    assert {v.conflicts for v in new.GOAL_KINDS.values()} == {"default"}, "no household kind leaves the goal validator"
    entries = default_pack().table("goal_kinds")
    assert all(e.get("source") and e.get("subgoal") for e in entries.values()), "every entry is labelled and has its phrase"
    assert kind_table.is_default(entries) and kind_table.default_entries() == entries


def _subgoal_rows():
    import test_subgoal_phrases as rows
    return rows.test_each_kind_has_its_subgoal_phrase.pytestmark[0].args[1]


# ── (2) the sub-goal phrases of tests/test_subgoal_phrases.py ──
@pytest.mark.parametrize("goal, want", _subgoal_rows())
def test_the_subgoal_field_says_each_row_as_the_old_if_chain_did(goal, want):
    assert new.subgoal_text(goal) == old.subgoal_text(goal) == want


# ── (3) the differential grid ──
TYPES = ["on", "in", "open", "close", "go", "say", "deliver", "give", "inspect", "", None, 3, ["on"]]
ABSENT = object()
SLOTS = {"object": [ABSENT, None, "", "cup", 0, 5], "from": [ABSENT, "", "desk@study"], "from_any": [ABSENT, [], ["a", "b"], "ab"],
         "to": [ABSENT, None, "", "kitchen"], "hand": [ABSENT, True, False], "person": [ABSENT, "lena"]}


def _goals():
    for t, *vals in itertools.product(TYPES, *SLOTS.values()):
        g = {"type": t}
        g.update({k: v for k, v in zip(SLOTS, vals) if v is not ABSENT})
        yield g
    yield {"object": "cup"}                                # no type at all


def _old_crash_only(g) -> bool:
    """The one input where the old if-chain raised and the template says a text: a hand-over `on` goal with no `to` key
    (old: ``g['to']`` -> KeyError; new: 'to None'). ``hand`` is only ever set on a goal whose `to` is a person key
    (assist_loop.py ``_ground_person``), so no run reaches it."""
    return g.get("type") == "on" and bool(g.get("hand")) and "to" not in g


def test_the_subgoal_phrase_equals_the_old_code_on_every_goal_of_the_grid():
    n = 0
    for g in _goals():
        got, want = outcome(new.subgoal_text, g), outcome(old.subgoal_text, g)
        if _old_crash_only(g):
            assert want == ("raises", "KeyError") and got[0] == "value" and got[1].endswith(" to None"), (g, got, want)
            continue
        assert got == want, g
        n += 1
    assert n > 20000


ACTION_SETS = [set(), {"Pick", "Place"}, {"Pick", "PutIn"}, {"MoveTo"}, {"Say"}, {"MoveTo", "Say"}, {"MoveTo", "WaitForUnload"},
               {"Pick", "Place", "PutIn", "Open", "Close", "MoveTo", "Say", "WaitForUnload"}, {"Open"}, {"Close", "Find"}]


@pytest.mark.parametrize("acts", ACTION_SETS)
def test_needs_and_the_understood_kinds_equal_the_old_code(acts):
    for t, to in itertools.product(TYPES, [ABSENT, "", "kitchen"]):
        g = {"type": t} if to is ABSENT else {"type": t, "to": to}
        assert outcome(new.missing_actions, g, acts) == outcome(old.missing_actions, g, acts), g
    for opt in [(), ("give",), ("give", "nope"), ("nope",)]:
        assert new.kinds_for(acts, opt) == old.kinds_for(acts, opt)
        assert new.prompt_lines(new.kinds_for(acts, opt)) == old.prompt_lines(old.kinds_for(acts, opt))
    assert new.domain_actions() == old.domain_actions()


def test_the_loop_s_kinds_lacking_and_cannot_do_equal_the_old_bodies():
    lists = [[], [{"type": t} for t in ("on", "in")], [{"type": "say", "to": "x"}, {"type": "on"}], [{"type": "polish"}],
             [{"type": "deliver"}, {"type": "deliver"}]]
    for acts, give in itertools.product(ACTION_SETS + [None], (False, True)):
        lp = AssistLoop.__new__(AssistLoop)
        lp.robot, lp.cfg = SimpleNamespace(plan_actions=acts), {"understand_give_kind": give}
        have = set(acts or ())
        assert lp._lacking() == [a for a in old.domain_actions() if a not in have]
        if acts is None:
            continue
        assert lp._kinds() == old.kinds_for(acts, ("give",) if give else ())
        for goals in lists:
            missing = list(dict.fromkeys(a for g in goals for a in old.missing_actions(g, acts)))
            assert lp._cannot_do(goals) == (", ".join(missing) if missing else None)


def G(t, o="", to="", **kw):
    return dict({"type": t, "object": o, "from": "", "to": to}, **kw)


GOAL_LISTS = [[], [G("on", "cup", "a")], [G("on", "cup", "a"), G("on", "cup", "b")], [G("in", "cup", "a"), G("on", "cup", "b@k")],
              [G("on", "cup", "a"), G("on", "cup", "a")], [G("open", "d"), G("close", "d")], [G("close", "d"), G("open", "d")],
              [G("open", "d"), G("go", to="x"), G("open", "d")], [G("go", to="x")], [G("say", "hi")], [G("deliver", "", "x")],
              [G("on", "", "a"), G("on", "", "b")], [{"type": ["on"], "object": "cup", "to": "a"}], [G("polish", "cup", "a")],
              [G("on", "cup", "a"), G("in", "cup", "b"), G("open", "d"), G("close", "e"), G("close", "d")]]


@pytest.mark.parametrize("goals", GOAL_LISTS)
def test_the_goal_validator_and_is_template_equal_the_old_code(goals):
    assert new.is_template(goals) == old.is_template(goals)
    for name, declare in itertools.product([None, lambda k: k.upper()], [False, True]):
        (k1, q1), (k2, q2) = new.conflicts(goals, name, declare), old.conflicts(goals, name, declare)
        assert k1 == k2 and str(q1) == str(q2) and type(q1).__name__ == type(q2).__name__
        assert getattr(q1, "ask", None) == getattr(q2, "ask", None)


# ── (4) the robots of today read the module view ──
def test_a_robot_without_a_pack_of_its_own_reads_the_module_view():
    assert new.table_of(None) is new.GOAL_KINDS
    assert new.table_of(SimpleNamespace(robot=SimpleNamespace(plan_actions=set()))) is new.GOAL_KINDS
    from robot_agent.core.planning.robot import RobotView
    assert new.table_of(SimpleNamespace(robot=RobotView.load("fakebot"))) is new.GOAL_KINDS


def test_a_patch_of_the_module_view_still_changes_what_the_loop_reads(monkeypatch):
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot, lp.cfg = SimpleNamespace(plan_actions={"Place"}), {}
    assert lp._cannot_do([{"type": "in"}]) == "Pick"
    monkeypatch.setitem(new.GOAL_KINDS, "in", new.GoalKind("in", (("PutIn", "Place"),), understood="always"))
    assert lp._cannot_do([{"type": "in"}]) is None


# ── (5) opt-in: a pack's own kinds ──
LINE = {"on": {"needs": ["Pick", "Place"], "understood": "always", "conflicts": "none", "subgoal": "put {object} on {to}"},
        "install": {"needs": ["Measure", ["Seat", "Press"]], "prompt": '"install" = mount a part.', "planned_by": "tree",
                    "subgoal": "install the {object} on {to}[{?from} (from {from})]"},
        "beep": {"needs": ["Beep"], "planned_by": "template"}}


def _line_loop(acts):
    pack = Pack(schema=2, name="line", tables={"goal_kinds": LINE})
    return SimpleNamespace(robot=SimpleNamespace(pack=pack, plan_actions=acts))


def test_a_pack_s_own_kinds_are_understood_when_the_robot_has_their_actions():
    t = new.table_of(_line_loop(set()))
    assert t is not new.GOAL_KINDS and list(t) == ["on", "install", "beep"]
    assert new.kinds_for(set(), (), t) == ["on"] and new.kinds_for({"Measure", "Press", "Beep"}, ("give",), t) == ["on", "install", "beep", "give"]
    assert new.cannot_do([{"type": "install"}], {"Seat"}, t) == "Measure" and new.cannot_do([{"type": "in"}], {"Pick"}, t) == "in"
    assert new.prompt_lines(["on", "install"], t) == '\n  "install" = mount a part.'
    assert new.lacking({"Pick"}, t) == ["Beep", "Measure", "Place", "Press", "Seat"]
    assert new.is_template([{"type": "beep"}], t) and not new.is_template([{"type": "go"}], t)


def test_a_pack_s_own_subgoal_template_says_its_goals_and_a_kind_without_one_is_described():
    t = new.table_of(_line_loop(set()))
    assert new.subgoal_text({"type": "install", "object": "pipe", "to": "wall a"}, None, t) == "install the pipe on wall a"
    assert new.subgoal_text({"type": "install", "object": "pipe", "from": "rack", "to": "w"}, None, t) == "install the pipe on w (from rack)"
    assert new.subgoal_text({"type": "beep", "object": "x"}, None, t) == "beep x"
    assert new.subgoal_text({"type": "go", "to": "x"}, None, t) == "go to x"         # not a kind of this pack: describe()


def test_conflicts_none_takes_a_kind_out_of_the_goal_validator():
    t = new.table_of(_line_loop(set()))
    two = [G("on", "pipe", "wall a"), G("on", "pipe", "wall b")]
    assert new.conflicts(two)[1] is not None, "the standard table asks"
    assert new.conflicts(two, table=t) == (two, None), "a kind with conflicts: none is never a question"
    quiet = {"open": {"needs": ["Open"], "conflicts": "none"}, "close": {"needs": ["Close"]}}
    oc = [G("open", "d"), G("close", "d"), G("open", "d")]
    assert new.conflicts(oc, table=kind_table.table(quiet))[0] == [G("open", "d"), G("close", "d"), G("open", "d")]
    assert new.conflicts(oc)[0] == [G("open", "d")]


# ── the template language ──
@pytest.mark.parametrize("template, goal, want", [
    ("go to {to}", {"to": "x"}, "go to x"),
    ("go to {to}", {}, "go to None"),
    ("[{?a}A {a}|{?b}B {b}|C]", {"a": "1", "b": "2"}, "A 1"),
    ("[{?a}A {a}|{?b}B {b}|C]", {"a": "", "b": "2"}, "B 2"),
    ("[{?a}A {a}|{?b}B {b}|C]", {}, "C"),
    ("x[{?a} {a}]y", {}, "xy"),
    ("{l: or }", {"l": ["p", "q", "r"]}, "p or q or r"),
    ("{l:}", {"l": ["p", "q"]}, "pq"),
    ("[{?o}{o}]", {"o": 0}, ""),
    ("[{?o}{o}]", {"o": 7}, "7"),
    ("{v}", {"v": "[{?x}y]"}, "[{?x}y]"),                 # a value is never read as a template
    ("plain text", {"a": 1}, "plain text"),
])
def test_render_reads_slots_alternatives_and_list_joins(template, goal, want):
    assert kind_table.render(template, goal) == want


def test_an_entry_without_fields_takes_the_schema_defaults():
    k = kind_table.kind("x", {"needs": ["A", ["B", "C"]]})
    assert k == kind_table.GoalKind("x", ("A", ("B", "C")), "", "capable", "planner", "default", "")
    assert kind_table.table(None) == {} and kind_table.kind("y", None).needs == ()
