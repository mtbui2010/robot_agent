"""The AssistLoop's hooks of the search belief (search.py): grounding from a decayed memory, the
ranking once per object, searched places never again, closed containers opened before moving on."""

import json
import time

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DR, DT, DS, CT = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen"
SK = "shelf@kitchen"
HOUSE = {"apple": DS, "mug": DS, "banana": DT, "spoon": DR + "/in"}
RANK = r"Rank the places where the apple"
DESK = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


APPLE = understood(("on", "apple", "", "dining table"))


class ObsPlanner(ScriptedPlanner):
    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        self.calls.append(("obs", obs, failure_reason))
        return super().replan(task, completed, failed_step, failure_reason, obs, visible_objects)


def finds(robot):
    return [o for s, o, *_ in robot.trace if s == "find"]


def looked_at(robot):
    """The place of every Find, from the moves before it."""
    at, out = None, []
    for s, o, ok, _m in robot.trace:
        if s == "move" and ok:
            at = o
        elif s == "find":
            out.append(at)
    return out


def ranks(llm):
    return sum(1 for p, _f in llm.calls if "Rank the places where" in p)


def write_memory(tmp_path, **objs):
    d = {"objects": {o: {"place": p, "ts": time.time() - age, "how": "placed"} for o, (p, age) in objs.items()},
         "answers": []}
    (tmp_path / "object_memory.json").write_text(json.dumps(d))


def test_a_searched_place_is_not_looked_at_again(make_assist, env):
    robot = FakeRobot(env, dict(HOUSE, apple=CT))
    planner = ObsPlanner(DESK, replans=[DESK, DESK])           # the planner insists on the desk
    llm = FakeLLM(APPLE, (RANK, {"places": [CT]}), default=APPLE)
    evs = run_task(make_assist(robot, llm, planner), "Bring the apple to the dining table.")
    assert done(evs)["status"] == "success"
    assert looked_at(robot)[:2] == [DS, CT]
    rp = [e for e in evs if e["event"] == "replan"]
    assert rp[0]["search"] and "instead of desk@study room" in rp[0]["search"][0]
    # the planner is told where to look next, in the reason and the observation
    obs, reason = planner.calls[1][1], planner.calls[1][2]
    assert "Look next at counter@kitchen" in reason and "Search for the apple: look next at counter@kitchen" in obs
    assert ranks(llm) == 1
    ev = next(e for e in evs if e["event"] == "search")
    assert ev["object"] == "apple" and ev["ranked"] == [CT]


def test_search_off_is_the_old_behaviour(make_assist, env):
    robot = FakeRobot(env, dict(HOUSE, apple=CT))
    planner = ObsPlanner(DESK, replans=[steps(f"MoveTo {CT}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")])
    llm = FakeLLM(APPLE, default=APPLE)
    evs = run_task(make_assist(robot, llm, planner, search=False), "Bring the apple to the dining table.")
    assert done(evs)["status"] == "success" and ranks(llm) == 0
    assert "Choose where to look next" in planner.calls[1][2]
    assert "Search for the apple" not in planner.calls[1][1]


def test_ranking_once_per_object_over_many_replans(make_assist, env):
    robot = FakeRobot(env, dict(HOUSE, apple=SK))
    planner = ScriptedPlanner(DESK, replans=[DESK] * 4)
    llm = FakeLLM(APPLE, (RANK, {"places": [CT, "dining table", SK]}), default=APPLE)
    evs = run_task(make_assist(robot, llm, planner), "Bring the apple to the dining table.")
    assert done(evs)["status"] == "success"
    assert looked_at(robot)[:4] == [DS, CT, DT, SK]
    assert ranks(llm) == 1 and len([c for c in planner.calls if c[0] == "replan"]) == 3


def test_closed_drawer_here_is_opened_and_looked_into(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    spoon = understood(("on", "spoon", "", "dining table"))
    planner = ScriptedPlanner(steps(f"MoveTo {DR}", "Find spoon", "Pick spoon", f"MoveTo {DT}", f"Place {DT}"),
                              replans=[steps("Find spoon", "Pick spoon", f"MoveTo {DT}", f"Place {DT}")])
    evs = run_task(make_assist(robot, FakeLLM(spoon, default=spoon), planner), "Bring the spoon to the dining table.")
    assert done(evs)["status"] == "success"
    assert [(s, o) for s, o, *_ in robot.trace][:4] == [("move", DR), ("find", "spoon"), ("open_drawer", DR),
                                                        ("find", "spoon")]


def test_nowhere_left_asks_then_fails_absent(make_assist, env):
    robot = FakeRobot(env, dict(HOUSE, apple="nowhere"))
    planner = ScriptedPlanner(DESK, replans=[DESK] * 20)
    llm = FakeLLM(APPLE, default=APPLE)
    evs = run_task(make_assist(robot, llm, planner, search_max=99, max_replans=30, llm_budget=0),
                   "Bring the apple to the dining table.")
    d = done(evs)
    assert d["status"] == "failed" and d["code"] == "absent" and "could not find the apple" in d["msg"]
    places = looked_at(robot)
    assert len(places) == len(set(places))           # every place once, then the robot gave up
    assert robot.asked and "where is it" in robot.asked[-1].lower()


def test_fresh_memory_is_the_source_a_stale_one_is_ranked(make_assist, env, tmp_path):
    write_memory(tmp_path, apple=(SK, 60))
    robot = FakeRobot(env, dict(HOUSE, apple=SK))
    planner = ScriptedPlanner(steps(f"MoveTo {SK}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"))
    llm = FakeLLM(APPLE, default=APPLE)
    evs = run_task(make_assist(robot, llm, planner), "Bring the apple to the dining table.")
    und = next(e for e in evs if e["event"] == "understand")
    assert und["goals"][0]["from"] == SK and ranks(llm) == 0

    write_memory(tmp_path, apple=(DS, 5 * 86400))                  # 5 days old: decayed
    llm = FakeLLM(APPLE, (RANK, {"places": [SK]}), default=APPLE)
    evs = run_task(make_assist(FakeRobot(env, dict(HOUSE, apple=SK)), llm, ScriptedPlanner([])),
                   "Bring the apple to the dining table.", plan_only=True)
    plan_ev = next(e for e in evs if e["event"] == "understand")
    assert plan_ev["goals"][0]["from"] == ""                       # not trusted at grounding ...
    assert ranks(llm) == 1                                          # ... the ranking decides
    assert next(e for e in evs if e["event"] == "search")["order"][0] == SK


def test_stale_memory_in_prior_mode_is_used_as_before(make_assist, env, tmp_path):
    write_memory(tmp_path, apple=(DS, 5 * 86400))
    llm = FakeLLM(APPLE, default=APPLE)
    evs = run_task(make_assist(FakeRobot(env, HOUSE), llm, ScriptedPlanner([]), search_rank="prior"),
                   "Bring the apple to the dining table.", plan_only=True)
    assert next(e for e in evs if e["event"] == "understand")["goals"][0]["from"] == DS and ranks(llm) == 0
