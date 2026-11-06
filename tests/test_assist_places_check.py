"""assist_loop: Places grounding on the sim_house ENV, AssistLoop._check on constructed
worlds, and the small pure helpers (_missing, _reaches, _goals_hold, _ground_step, yes_no, summary).

These pin the current behaviour before the refactor (roadmap step 2): a change
in a message or a verdict here is a behaviour change and must be deliberate.
"""

import pytest

from fakes import steps
from robot_agent.core.planning.assist_loop import AssistLoop, Places, words
from robot_agent.core.planning.base import WorldState

DR, DT, DS = "drawer@kitchen", "dining table@kitchen", "desk@study room"


def G(t, o, f, to):
    return {"type": t, "object": o, "from": f, "to": to}


def W(**kw):
    w = WorldState()
    w.update_from_dict(kw)
    return w


@pytest.fixture
def robot():
    from robot_agent.core.planning.robot import RobotView
    return RobotView.load("fakebot")


@pytest.fixture
def places(env, robot):
    """The ENV places with the robot's rule for what opens (the sim house marks no drawer)."""
    return Places(env, openable=robot.openable)


@pytest.fixture
def loop(robot):
    """An AssistLoop with only what _check and the pure helpers read (no agent, no planner)."""
    lp = AssistLoop.__new__(AssistLoop)
    lp.robot = robot
    lp._namemap = robot.adapter.namemap
    return lp


# ── names ──
@pytest.mark.parametrize("raw, out", [("TennisBall", "tennis ball"), ("tennis_ball", "tennis ball"),
                                      (" Tennis  ball ", "tennis ball"), ("RGBCamera", "rgb camera"), (None, "")])
def test_words(raw, out):
    assert words(raw) == out


# ── Places ──
@pytest.mark.parametrize("name, hint, cands", [
    ("dining table", "", [DT]),
    ("the desk", "", [DS]),
    ("식탁", "", [DT]),                                   # alias (ko)
    ("kitchen drawer", "", [DR]),
    ("drawer", "", [DR]),
    ("counter@kitchen", "", ["counter@kitchen"]),         # an ENV key as-is
    ("shelf", "", ["shelf@kitchen", "shelf@restroom"]),   # ambiguous
    ("shelf", "the one in the restroom", ["shelf@restroom"]),
    ("shelf in the restroom", "", ["shelf@restroom"]),
    ("table", "", ["side table@living room", DT]),
    ("kitchen", "", ["@kitchen"]),                        # a room, not a place
    ("bedroom", "", ["@bedroom"]),
    ("the moon", "", []),
])
def test_places_candidates(places, name, hint, cands):
    assert places.candidates(name, hint) == cands
    assert places.ground(name, hint) == (cands[0] if len(cands) == 1 and not cands[0].startswith("@") else None)


def test_places_misc(places):
    assert places.rooms() == {"bedroom", "kitchen", "laundry room", "living room", "restroom", "study room"}
    assert places.openable(DR) and not places.openable("chest@bedroom")
    assert places.said(DT, "put it on the 식탁") and places.said("shelf@kitchen", "the shelf")
    assert not places.said(DT, "the counter")
    assert places.in_room("bedroom") == ["chest@bedroom", "stool@bedroom"]
    assert places.describe().splitlines()[0] == "- sofa@living room (also: 소파)"
    assert Places.head("shelf@restroom") == "shelf" and Places.room("shelf@restroom") == "restroom"
    assert Places({"a": {}, "junk": 3}).keys == ["a"]          # non-dict ENV entries are ignored


# ── _check: the plan simulated as written ──
CHECK_CASES = {
    "good": (steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"), {},
             [G("on", "apple", DS, DT)], []),
    "pick_without_find": (steps(f"MoveTo {DS}", "Pick apple", f"MoveTo {DT}", f"Place {DT}"), {},
                          [G("on", "apple", DS, DT)],
                          [(1, "step 2 'Pick apple': needs 'Find apple' just before it, at the same place", True)]),
    "open_with_full_hand": (steps(f"MoveTo {DS}", "Find orange", "Pick orange", f"MoveTo {DR}", f"Open {DR}",
                                  f"PutIn {DR}"), {}, [G("in", "orange", "", DR)],
                            [(4, "step 5 'Open drawer@kitchen': the robot cannot open it while holding the orange; "
                                 "do it before the Pick", True)]),
    "putin_closed": (steps("Find orange", "Pick orange", f"MoveTo {DR}", f"PutIn {DR}"), {},
                     [G("in", "orange", "", DR)],
                     [(3, "step 4 'PutIn drawer@kitchen': drawer in the kitchen is closed; Open it before the Pick",
                       True)]),
    "unknown_action": (steps("Teleport apple"), {}, [G("on", "apple", "", DT)],
                       [(0, "step 1 'Teleport apple': the robot has no action Teleport (use MoveTo, Find, Pick, "
                            "Place, PutIn, Open, Close)", True),
                        (1, "the plan does not achieve: Place the apple on dining table@kitchen", True)]),
    "room_not_place": ([{"action": "MoveTo", "object": "@kitchen", "grounded": False}], {}, [G("open", DR, "", DR)],
                       [(0, "step 1 'MoveTo @kitchen': kitchen is not a place the robot can go to; use one of the "
                            "listed places", True),
                        (1, "the plan does not achieve: Open drawer@kitchen", True)]),
    "wrong_destination": (steps("Find apple", "Pick apple", f"MoveTo {DS}", f"Place {DS}"), {},
                          [G("on", "apple", "", DT)],
                          [(3, "step 4 'Place desk@study room': the apple must go to dining table@kitchen, not "
                               "desk@study room", True),
                           (4, "the plan does not achieve: Place the apple on dining table@kitchen", True)]),
    "steps_after_goal": (steps(f"MoveTo {DR}", f"Open {DR}", f"Close {DR}", f"Open {DR}"), {},
                         [G("open", DR, "", DR)],
                         [(2, "step 3 'Close drawer@kitchen': no goal needs it; remove it", True),
                          (2, "the goals are done after step 2; remove the steps after it (do not repeat the task)",
                           True)]),
    "already_there": (steps(f"MoveTo {DR}", f"Open {DR}"), {"arrived": DR}, [G("open", DR, "", DR)],
                      [(0, "step 1 'MoveTo drawer@kitchen': the robot is already there", False)]),
    "open_not_needed": (steps(f"MoveTo {DR}", f"Open {DR}", f"MoveTo {DS}", "Find apple", "Pick apple",
                              f"MoveTo {DT}", f"Place {DT}"), {}, [G("on", "apple", DS, DT)],
                        [(1, "step 2 'Open drawer@kitchen': no goal needs it; remove it", True)]),
    "find_away_from_source": (steps(f"MoveTo {DT}", "Find apple", "Pick apple", f"Place {DT}"), {},
                              [G("on", "apple", DS, DT)],
                              [(1, "step 2 'Find apple': the user said it is on desk in the study room; "
                                   "MoveTo desk@study room first", True)]),
    "pick_already_held": (steps("Pick apple", f"MoveTo {DT}", f"Place {DT}"), {"holding": "apple"},
                          [G("on", "apple", "", DT)],
                          [(0, "step 1 'Pick apple': already done by an earlier step (the apple is in the hand); "
                               "delete this step", True)]),
    "not_openable": (steps("MoveTo counter@kitchen", "Open counter@kitchen"), {}, [G("open", DR, "", DR)],
                     [(1, "step 2 'Open counter@kitchen': counter in the kitchen cannot be opened or closed", True),
                      (2, "the plan does not achieve: Open drawer@kitchen", True)]),
    "drawer_already_open": (steps("Find orange", "Pick orange", f"MoveTo {DR}", f"PutIn {DR}"), {"opened": [DR]},
                            [G("in", "orange", "", DR)], []),
}


@pytest.mark.parametrize("name", list(CHECK_CASES))
def test_check(loop, places, name):
    plan, world, goals, expected = CHECK_CASES[name]
    w = W(**world)
    before = w.to_dict()
    assert loop._check(plan, w, places, goals) == expected
    assert w.to_dict() == before                          # the check never touches the belief


# ── goal bookkeeping ──
def test_missing_reaches_goals_hold(loop):
    plan, _, goals, _ = CHECK_CASES["wrong_destination"]
    assert loop._missing(plan, W(), goals) == ["Place the apple on dining table@kitchen"]
    good, _, ggoals, _ = CHECK_CASES["good"]
    assert loop._missing(good, W(), ggoals) == [] and loop._reaches(good, W(), ggoals)
    assert not loop._reaches(good[:3], W(), ggoals)
    assert loop._missing([], W(opened=[DR]), [G("close", DR, "", DR)]) == ["Close drawer@kitchen"]
    on = [G("on", "apple", "", DT)]
    assert loop._goals_hold(on, W(), {"apple": DT})
    assert not loop._goals_hold(on, W(holding="apple"), {"apple": DT})
    assert not loop._goals_hold(on, W(), {"apple": DS})
    assert not loop._goals_hold([G("close", DR, "", DR)], W(opened=[DR]), {})
    assert loop._goals_hold([G("open", DR, "", DR)], W(opened=[DR]), {})


def test_ground_step(loop, places):
    goals = [G("on", "red cup", "", DT)]
    task = "put the red cup on the dining table"
    out = [loop._ground_step(s, places, goals, task)
           for s in steps("MoveTo dining table", "Find the cup", "MoveTo kitchen", "Place table")]
    assert out == [{"action": "MoveTo", "object": DT, "grounded": True},
                   {"action": "Find", "object": "red cup"},                  # the goal's 'red cup' for 'cup'
                   {"action": "MoveTo", "object": "@kitchen", "grounded": True},
                   {"action": "Place", "object": DT, "grounded": True}]      # several tables: the goal's one


@pytest.mark.parametrize("text, out", [
    ("yes please", (True, "")), ("네", (True, "")),
    ("no, the restroom one", (False, "the restroom one")), ("아니요", (False, "")),
    ("the restroom one", (False, "the restroom one")), ("", (None, "")),
])
def test_yes_no(text, out):
    assert AssistLoop.yes_no(text) == out


def test_summary():
    s = AssistLoop.summary([G("on", "apple", DS, DT), G("in", "orange", "", DR), G("open", DR, "", DR),
                            G("close", DR, "", DR)])
    assert s == ("I will put the apple from the desk in the study room on the dining table in the kitchen, then put "
                 "the orange in the drawer in the kitchen, then open the drawer in the kitchen, then close the drawer "
                 "in the kitchen.")
