"""Step S07: the goal-kind table is pack data. ``needs``, ``prompt``, ``understood``, ``planned_by``, ``conflicts`` and
``subgoal`` of each kind come from the standard pack's robot_agent/packs/household/goal_kinds.yaml (read through
robot_agent/core/pack/goal_kinds.py); core/planning/goals.py keeps the functions that read the table, with the robot's
resolved pack as an argument. These tests prove nothing changed for a robot without kinds of its own: (1) the default data
equals the old constants (tests/legacy_ref/goal_tables.py, the old code at 96c17e1); (2) a differential grid: the new
functions and the old ones give the same answer, or raise the same exception, over made-up action sets, goal lists and
goals, with no pack and with the resolved default pack; (3) the sub-goal phrase of every row of tests/test_subgoal_phrases.py
is the old text; (4) the module views stay the place a test patches; (5) opt-in: a pack with its own kinds is read, and
``conflicts: none`` switches the goal validator off for one kind."""

import itertools

import pytest

from legacy_ref import goal_tables as old
from robot_agent.core.pack import Pack, default_pack
from robot_agent.core.pack import goal_kinds as kind_data
from robot_agent.core.planning import goals
from robot_agent.core.planning.speech import LocalText

DEFAULT = default_pack()
PACKS = (None, DEFAULT)
DT, DR, DS, CT = "dining table@kitchen", "drawer@kitchen", "desk@study room", "counter@kitchen"
ACTIONS = ("Pick", "Place", "PutIn", "Open", "Close", "MoveTo", "Say", "WaitForUnload", "Find", "Handover")
ACTION_SETS = [set(c) for n in (0, 1, 2, 3) for c in itertools.combinations(ACTIONS, n)] + [set(ACTIONS), set(ACTIONS[:8])]
TYPES = ("on", "in", "open", "close", "go", "say", "deliver", "give", "inspect", "", None, ["on"], 3)


def _outcome(fn, *a, **kw):
    try:
        out = fn(*a, **kw)
    except Exception as e:                  # the same exception type is the same behaviour
        return ("raised", type(e).__name__)
    if isinstance(out, tuple):
        return tuple(_plain(x) for x in out)
    return _plain(out)


def _plain(x):
    return ("LocalText", str(x), x.key, x.ask) if isinstance(x, LocalText) else x


# ── (1) the default data is the old constants ─────────────────────────────────────────────────────────────────────────
def test_the_default_table_is_the_old_goal_kinds():
    assert list(goals.GOAL_KINDS) == list(old.GOAL_KINDS) == ["on", "in", "open", "close", "go", "say", "deliver"]
    for n, k in old.GOAL_KINDS.items():
        assert (goals.GOAL_KINDS[n].name, goals.GOAL_KINDS[n].needs, goals.GOAL_KINDS[n].prompt) == (k.name, k.needs, k.prompt), n
    assert goals.MANIPULATION == old.MANIPULATION and goals.TEMPLATE_KINDS == old.TEMPLATE_KINDS
    assert {n: (k.name, k.needs, k.prompt) for n, k in goals.OPTIONAL_KINDS.items()} == \
        {n: (k.name, k.needs, k.prompt) for n, k in old.OPTIONAL_KINDS.items()}


def test_the_flags_reproduce_the_old_groups():
    t = goals.GOAL_KINDS
    assert tuple(n for n, k in t.items() if k.understood == "always") == old.MANIPULATION
    assert tuple(n for n, k in t.items() if k.understood == "capable") == old.TEMPLATE_KINDS
    assert tuple(n for n, k in t.items() if k.planned_by == "template") == old.TEMPLATE_KINDS
    assert all(k.conflicts == "default" for k in t.values()), "no household kind switches the goal validator off"
    assert all(k.subgoal for k in t.values()), "every household kind has its sub-goal phrase"


def test_the_household_file_is_labelled_and_the_resolved_default_pack_reads_the_view():
    table = DEFAULT.table("goal_kinds")
    assert all(isinstance(e.get("source"), str) and e["source"] for e in table.values())
    assert kind_data.is_default(DEFAULT) and kind_data.is_default(None)
    assert goals.table(DEFAULT) is goals.GOAL_KINDS and goals.table(None) is goals.GOAL_KINDS


# ── (2) the differential grid ─────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("pack", PACKS, ids=("no_pack", "default_pack"))
def test_kinds_prompt_lines_and_missing_actions_are_the_old_ones(pack):
    for acts in ACTION_SETS:
        for optional in ((), ("give",), ("give", "nope")):
            kinds = goals.kinds_for(acts, optional, pack)
            assert kinds == old.kinds_for(acts, optional), (acts, optional)
            assert goals.prompt_lines(kinds, pack) == old.prompt_lines(kinds)
        assert goals.prompt_lines(list(TYPES[:9]), pack) == old.prompt_lines(list(TYPES[:9]))
        for t in TYPES:
            for to in ("", "kitchen"):
                g = {"type": t, "to": to}
                assert _outcome(goals.missing_actions, g, acts, pack) == _outcome(old.missing_actions, g, acts), (g, acts)
        assert goals.lacking(acts, pack) == [a for a in old.domain_actions() if a not in acts]
    assert goals.domain_actions(pack) == old.domain_actions()


def _old_cannot_do(goal_list, acts):
    """AssistLoop._cannot_do at 96c17e1 (assist_loop.py:743-748), over the old table."""
    missing = list(dict.fromkeys(a for g in goal_list for a in old.missing_actions(g, acts)))
    return ", ".join(missing) if missing else None


GOAL_LISTS = [[], [{"type": "go", "to": "x"}], [{"type": "say", "object": "hi"}, {"type": "deliver", "to": "x"}],
              [{"type": "go"}, {"type": "on", "object": "cup"}], [{"type": "inspect"}], [{"type": None}], [{"type": ["go"]}],
              [{"type": "give", "object": "cup", "to": "me"}], [{"type": "say", "object": "hi", "to": "kitchen"}],
              [{"type": "on", "object": "apple", "to": DT}, {"type": "on", "object": "apple", "to": CT}],
              [{"type": "in", "object": "apple", "to": DR}, {"type": "on", "object": "apple", "to": DT},
               {"type": "on", "object": "apple", "to": DT}],
              [{"type": "open", "object": DR, "to": DR}, {"type": "close", "object": DR}, {"type": "open", "object": DR}],
              [{"type": "close", "object": DR}, {"type": "on", "object": "", "to": DT}, {"type": "on", "object": "pear", "to": ""},
               {"type": "go", "to": DT}, {"type": ["open"], "object": DR}, {"type": "open", "object": "box"}],
              [{"type": "on", "object": "apple", "to": "shelf@hall"}, {"type": "in", "object": "apple", "to": DR},
               {"type": "on", "object": "pear", "to": DT}, {"type": "on", "object": "pear", "to": CT}]]


@pytest.mark.parametrize("pack", PACKS, ids=("no_pack", "default_pack"))
def test_is_template_cannot_do_and_the_goal_validator_are_the_old_ones(pack):
    named = lambda k: k.upper()                                                     # noqa: E731
    for gl in GOAL_LISTS:
        assert _outcome(goals.is_template, gl, pack) == _outcome(old.is_template, gl), gl
        for acts in ACTION_SETS[::7]:
            assert _outcome(goals.cannot_do, gl, acts, pack) == _outcome(_old_cannot_do, gl, acts), (gl, acts)
        for name, declare in itertools.product((None, named), (False, True)):
            got = _outcome(goals.conflicts, [dict(g) for g in gl], name, declare, pack)
            assert got == _outcome(old.conflicts, [dict(g) for g in gl], name, declare), (gl, name, declare)


SUBGOAL_GOALS = [
    {"type": t, "object": o, "to": to, **extra}
    for t in TYPES for o in ("", "apple") for to in ("", DT, None)
    for extra in ({}, {"from": DS}, {"from_any": [DS, CT]}, {"person": "lena"}, {"hand": True}, {"hand": False, "from": "me"})
] + [{"type": "on", "object": "mug"}, {"type": "say"}, {"type": "go"}, {"type": "deliver", "object": None}, {"object": "x"},
     {"type": "on", "object": "{to}", "to": "{object}"}, {"type": "say", "object": 'a "b"', "to": "", "person": ""}]


@pytest.mark.parametrize("pack", PACKS, ids=("no_pack", "default_pack"))
def test_the_subgoal_phrase_is_the_old_one(pack):
    for g in SUBGOAL_GOALS:
        assert _outcome(goals.subgoal_text, g, None, pack) == _outcome(old.subgoal_text, g), g
    typed = [g for g in SUBGOAL_GOALS if "type" in g]
    assert goals.subgoals(typed, None, pack) == [old.subgoal_text(g) for g in typed]


def test_a_hand_over_with_no_destination_is_the_one_row_left_out():
    """An ``on`` goal with ``hand`` set and no ``to`` key raised KeyError in the old if-chain (``g['to']``) and reads 'to None'
    now, as every other kind's missing ``to`` did. The loop sets ``hand`` only on a goal whose ``to`` is a person
    (AssistLoop._person_destination), so no run reaches this row; it is pinned here so that the difference is a known one."""
    g = {"type": "on", "object": "mug", "hand": True}
    assert _outcome(old.subgoal_text, g) == ("raised", "KeyError")
    assert goals.subgoal_text(g) == "hand the mug to None"


# ── (3) the rows of tests/test_subgoal_phrases.py ─────────────────────────────────────────────────────────────────────
def _phrase_rows():
    import test_subgoal_phrases as rows
    marks = [m for m in rows.test_each_kind_has_its_subgoal_phrase.pytestmark if m.name == "parametrize"]
    return marks[0].args[1]


def test_every_row_of_the_subgoal_phrase_test_is_the_old_text_and_the_pack_text():
    rows = _phrase_rows()
    assert len(rows) == 15
    for goal, want in rows:
        assert old.subgoal_text(goal) == want == goals.subgoal_text(goal) == goals.subgoal_text(goal, None, DEFAULT), goal


# ── (4) the module views are what a test patches ──────────────────────────────────────────────────────────────────────
def test_a_patch_of_the_module_table_or_function_reaches_the_default_pack(monkeypatch):
    monkeypatch.setitem(goals.GOAL_KINDS, "in", goals.GoalKind("in", (("PutIn", "Place"),)))
    assert goals.missing_actions({"type": "in"}, {"PutIn"}, DEFAULT) == []
    monkeypatch.setattr(goals, "subgoal_text", lambda g, places=None: "patched")
    assert goals.subgoals([{"type": "on"}], None, DEFAULT) == ["patched"]


# ── (5) opt-in: a pack with its own kinds ─────────────────────────────────────────────────────────────────────────────
def _pack(**kinds) -> Pack:
    table = dict(DEFAULT.table("goal_kinds"))
    table.update(kinds)
    return Pack(schema=2, name="made_up", tables={"goal_kinds": table})


INSTALL = {"rules": "made_up.install", "needs": ["Measure", ["Seat", "Place"]], "understood": "capable", "planned_by": "planner",
           "conflicts": "none", "prompt": '"install" = mount a pipe on a wall: object = the pipe, to = the wall.',
           "subgoal": "install the {object} on {to}", "source": "a made-up robot of this test"}


def test_a_pack_kind_is_understood_when_the_robot_can_do_it_with_its_prompt_line_and_its_subgoal():
    p = _pack(install=INSTALL)
    assert not kind_data.is_default(p) and goals.table(p) is not goals.GOAL_KINDS
    acts = {"Pick", "Place", "Measure"}
    kinds = goals.kinds_for(acts, (), p)
    assert kinds == ["on", "in", "open", "close", "install"]
    assert goals.prompt_lines(kinds, p).endswith('\n  "install" = mount a pipe on a wall: object = the pipe, to = the wall.')
    assert goals.missing_actions({"type": "install"}, {"Pick"}, p) == ["Measure", "Seat"]
    assert goals.cannot_do([{"type": "install"}], {"Measure"}, p) == "Seat"
    assert {"Measure", "Seat"} <= set(goals.domain_actions(p)) and "Measure" not in goals.domain_actions()
    assert goals.subgoals([{"type": "install", "object": "pipe", "to": "wall A"}], None, p) == ["install the pipe on wall A"]
    assert goals.subgoal_text({"type": "install", "object": "pipe", "to": "wall A"}) == "install pipe to wall A"   # default: describe
    assert not goals.is_template([{"type": "install"}], p)
    assert goals.missing_actions({"type": "install"}, acts) == ["install"]          # the default table does not know it


def test_conflicts_none_switches_the_goal_validator_off_for_that_kind_only():
    two = [{"type": "on", "object": "pipe", "to": "wall A"}, {"type": "on", "object": "pipe", "to": "wall B"},
           {"type": "open", "object": DR}, {"type": "close", "object": DR}]
    kept, q = goals.conflicts(two)
    assert q and kept == [two[0], two[1], two[3]]
    on_off = _pack(on={**DEFAULT.table("goal_kinds")["on"], "conflicts": "none"})
    kept, q = goals.conflicts(two, pack=on_off)
    assert q is None and kept == [two[0], two[1], two[3]], "open / close keep their rule"
    both_off = _pack(open={**DEFAULT.table("goal_kinds")["open"], "conflicts": "none"},
                     close={**DEFAULT.table("goal_kinds")["close"], "conflicts": "none"})
    kept, q = goals.conflicts(two, pack=both_off)
    assert q and kept == two


def test_a_planned_by_template_flag_and_the_understood_flag_come_from_the_pack():
    go_planned = _pack(go={**DEFAULT.table("goal_kinds")["go"], "planned_by": "planner", "understood": "always"})
    assert goals.is_template([{"type": "go", "to": "x"}]) and not goals.is_template([{"type": "go", "to": "x"}], go_planned)
    assert goals.kinds_for(set(), (), go_planned) == ["on", "in", "open", "close", "go"]


def test_the_subgoal_template_rules():
    r = kind_data.render
    assert r("{a?}A {a}|B {b}", {"a": "x", "b": "y"}) == "A x" and r("{a?}A {a}|B {b}", {"a": "", "b": "y"}) == "B y"
    assert r("{a?}A", {}) is None and r("plain {b}", {"b": None}) == "plain None"
    assert kind_data.fields({"needs": ["A", ["B", "C"]]}) == {"needs": ("A", ("B", "C")), "prompt": "", "understood": "capable",
                                                             "planned_by": "planner", "conflicts": "default", "subgoal": ""}
