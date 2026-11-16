"""Engine goal kinds ``find`` / ``pick`` / ``put_here`` (``goals.SINGLE_KINDS``, reached by the option ``single_goals``): their validation, their
check by the steps that ran, their compiled steps and phrases, on small made-up cases (objects ``zorblet``, ``quib``). No real robot or task id."""
import pytest

from fakes import steps
from robot_agent.core.planning import goals as G

ACTS = ("MoveTo", "Find", "Pick", "Place")
DESK, SIDE, COUNTER = "desk@study room", "side table@living room", "counter@kitchen"


def goal(t, o="", frm="", to=""):
    return {"type": t, "object": o, "from": frm, "to": to}


@pytest.fixture
def zq(monkeypatch):
    monkeypatch.setitem(langdata._CACHE, "zq", ZQ)
    return "zq"


# ── goals.py: the kinds, their validation and their check by the steps ─────────────────────────────────────────────────────
@pytest.mark.parametrize("g,acts,missing", [
    (goal("find", "zorblet"), ACTS, []), (goal("pick", "zorblet"), ACTS, []), (goal("put_here"), ACTS, []),
    (goal("find", "zorblet"), ("MoveTo", "Pick"), ["Find"]), (goal("put_here"), ("Find", "Pick"), ["Place"]),
    (goal("fetchy", "zorblet"), ACTS, ["fetchy"]),                       # an unknown kind still needs an action nobody has
])
def test_goal_validation_table_single_kinds_need_their_action(g, acts, missing):
    assert G.missing_actions(g, acts) == missing


def test_single_kinds_stay_out_of_the_default_kinds_and_vocabulary():
    assert not set(G.SINGLE_KINDS) & set(G.kinds_for(ACTS + ("Say",))) and "Find" not in G.domain_actions()
    assert G.has_destination("on") and not G.has_destination("find") and G.by_steps("go") and G.by_steps("pick") and not G.by_steps("on")


@pytest.mark.parametrize("g,plan,at,want", [
    (goal("find", "zorblet"), (f"MoveTo {DESK}", "Find zorblet"), "entrance", True),
    (goal("find", "zorblet"), (f"MoveTo {DESK}", "Find quib"), "entrance", False),
    (goal("pick", "zorblet"), ("Find zorblet", "Pick zorblet"), "entrance", True),
    (goal("pick", "zorblet"), ("Pick zorblet", f"MoveTo {SIDE}", f"Place {SIDE}"), "entrance", False),     # released after
    (goal("pick", "zorblet"), ("Pick zorblet", f"Place {SIDE}", "Pick zorblet"), "entrance", True),        # taken again
    (goal("put_here"), (f"Place {SIDE}",), SIDE, True),
    (goal("put_here"), (f"MoveTo {DESK}", f"Place {DESK}"), SIDE, False),                                 # left the place first
    (goal("put_here"), (f"Place {SIDE}",), None, False),
])
def test_single_reached_table(g, plan, at, want):
    assert G.reached(g, steps(*plan), at) is want


def test_compile_put_here_is_a_place_where_the_robot_is_and_other_kinds_keep_their_steps():
    assert G.is_template([goal("put_here")]) and not G.is_template([goal("find", "zorblet")])
    assert G.compile_steps([goal("put_here")], SIDE) == [{"action": "Place", "object": SIDE}]
    assert G.compile_steps([goal("put_here")], None) == []
    assert G.subgoal_text(goal("find", "zorblet", DESK)) == f"find the zorblet (it is at {DESK})"
    assert G.summary(goal("pick", "zorblet")) == "pick up the zorblet"
