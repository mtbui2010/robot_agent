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


def test_place_into_a_closed_container_becomes_putin_with_an_open_before_the_fetch():
    from robot_agent.core.planning import action_model
    env = {"chest@den": {"aliases": ["chest"], "openable": True}, "shelf@den": {"aliases": ["shelf"]}}
    goals = [{"type": "in", "object": "ring", "from": "shelf@den", "to": "chest@den"}]
    plan = [{"action": "MoveTo", "object": "shelf@den"}, {"action": "Find", "object": "ring"},
            {"action": "Pick", "object": "ring"}, {"action": "MoveTo", "object": "chest@den"},
            {"action": "Place", "object": "chest@den"}]
    ps = Places(env, openable=lambda k, v: bool(v.get("openable")))

    def score(steps):                       # the shared action model's critical violations + goal not reached
        w, n = WorldState(), 0
        for st in steps:
            n += len([v for v in action_model.check(st, w, capabilities=None)])
            action_model.apply(st, w, inplace=True)
        return n + (0 if any(s["action"] == "PutIn" for s in steps) else 1)

    out, _n, counts = plan_repair.repair(plan, score, WorldState(), ps, goals,
                                         actions={"MoveTo", "Find", "Pick", "Place", "PutIn", "Open"}, release=("Place", "PutIn"))
    acts = [(s["action"], s["object"]) for s in out]
    assert counts.get("putin") == 1 and ("PutIn", "chest@den") in acts
    assert acts.index(("Open", "chest@den")) < acts.index(("Pick", "ring"))        # opened with an empty hand


def test_orphan_release_gets_its_fetch():
    env = {"table@den": {"aliases": ["table"]}, "shelf@den": {"aliases": ["shelf"]}}
    goals = [{"type": "on", "object": "cup", "from": "shelf@den", "to": "table@den"}]
    plan = [{"action": "MoveTo", "object": "table@den"}, {"action": "Place", "object": "table@den"}]

    def score(steps):
        have = False
        for s in steps:
            if s["action"] == "Pick":
                have = True
            if s["action"] == "Place" and not have:
                return 1
        return 0

    out, _n, counts = plan_repair.repair(plan, score, WorldState(), Places(env), goals,
                                         actions={"MoveTo", "Find", "Pick", "Place"}, release=("Place",))
    acts = [(s["action"], s["object"]) for s in out]
    assert counts.get("fetch") == 1
    assert acts == [("MoveTo", "table@den"), ("MoveTo", "shelf@den"), ("Find", "cup"), ("Pick", "cup"),
                    ("MoveTo", "table@den"), ("Place", "table@den")]
    # two goals deliver to the same place: which one is missing is not clear, nothing is written
    goals2 = goals + [{"type": "on", "object": "pen", "from": "shelf@den", "to": "table@den"}]
    out, _n, counts = plan_repair.repair(plan, score, WorldState(), Places(env), goals2,
                                         actions={"MoveTo", "Find", "Pick", "Place"}, release=("Place",))
    assert "fetch" not in counts
