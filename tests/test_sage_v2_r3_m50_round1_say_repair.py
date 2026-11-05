"""Gate repair: an Announce of a say goal's sentence becomes the goal's Say (synthetic sentence and places)."""
from robot_agent.core.planning import plan_repair
from robot_agent.core.planning.assist_loop import Places
from robot_agent.core.planning.base import WorldState

ENV = {"person_o": {"person": "o", "aliases": ["Oma"]}, "den@home": {"aliases": ["den"]}}
GOAL = [{"type": "say", "object": "tea is ready", "from": "", "to": "person_o"}]


def score(steps):
    return 0 if any(s["action"] == "Say" and s["object"].lower().strip(". ") == "tea is ready" for s in steps) else 1


def run(steps, actions):
    return plan_repair.repair(steps, score, WorldState(), Places(ENV), GOAL, actions=actions, release=())


def test_announce_becomes_say():
    plan = [{"action": "MoveTo", "object": "person_o"}, {"action": "Announce", "object": "Tea is ready."}]
    out, notes, counts = run(plan, {"Say", "Announce", "MoveTo"})
    assert [s["action"] for s in out] == ["MoveTo", "Say"] and counts.get("say") == 1 and notes


def test_robot_without_say_is_left_alone():
    plan = [{"action": "Announce", "object": "tea is ready"}]
    out, _n, counts = run(plan, {"Announce"})
    assert out == plan and "say" not in counts


def test_other_announcements_are_not_touched():
    plan = [{"action": "Announce", "object": "something else"}]
    out, _n, counts = run(plan, {"Say", "Announce"})
    assert out == plan and "say" not in counts


def test_a_close_of_a_container_the_goal_leaves_open_is_removed():
    env = {"box@den": {"aliases": ["box"], "openable": True}}
    goals = [{"type": "open", "object": "box@den", "from": "", "to": "box@den"}]
    score = lambda steps: sum(1 for s in steps if s["action"] == "Close")            # noqa: E731
    plan = [{"action": "Open", "object": "box@den"}, {"action": "Close", "object": "box@den"}]
    out, _n, counts = plan_repair.repair(plan, score, WorldState(), Places(env), goals, actions={"Open", "Close"}, release=())
    assert [s["action"] for s in out] == ["Open"] and counts.get("unclose") == 1
    both = goals + [{"type": "close", "object": "box@den", "from": "", "to": "box@den"}]
    out, _n, counts = plan_repair.repair(plan, score, WorldState(), Places(env), both, actions={"Open", "Close"}, release=())
    assert "unclose" not in counts
