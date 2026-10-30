"""Fix stream `plan`, D3: the certified gate. A plan with critical findings after SAGE's edits gets one
fresh plan and is certified again; an uncertified plan (or replan suffix) never runs."""

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DT, DS, CT = "dining table@kitchen", "desk@study room", "counter@kitchen"
HOUSE = {"apple": DS, "mug": DS}
GOOD = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
HALLUCINATED = steps("MoveTo Garage", "Find apple", "Teleport DiningTable", f"Place {DT}")
TASK = "Bring the apple from the desk to the dining table."


def understood(*goals):
    return {"status": "clear", "reason": "", "question": "",
            "goals": [{"type": t, "object": o, "from": f, "to": to} for t, o, f, to in goals]}


APPLE = understood(("on", "apple", "desk", "dining table"))


class Plans(ScriptedPlanner):
    """generate_plan returns the next of `plans` (the last one again when they run out)."""

    def __init__(self, *plans, replans=()):
        super().__init__(plans[0], replans=replans)
        self.plans = [[dict(s) for s in p] for p in plans]

    def generate_plan(self, task, obs, visible_objects):
        self.calls.append(("generate_plan", task, obs))
        p = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        return [dict(s) for s in p], {"method": "scripted"}


def motions(robot):
    return [t for t in robot.trace if t[0] in ("move", "pick", "place", "open_drawer", "close_drawer")]


def test_critical_plan_never_runs(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    planner = Plans(HALLUCINATED)
    evs = run_task(make_assist(robot, FakeLLM(default=APPLE), planner), TASK)
    d = done(evs)
    assert d["status"] == "failed" and "checks" in d["msg"] and d.get("say")
    assert motions(robot) == [], "an uncertified plan was executed"
    # refined once: a second, fresh plan, told why the first was rejected
    gens = [c for c in planner.calls if c[0] == "generate_plan"]
    assert len(gens) == 2 and "A checker rejected the previous plan" in gens[1][2]
    plan_ev = next(e for e in evs if e["event"] == "plan")
    assert plan_ev["certified"] is False and plan_ev["warnings"]


def test_refined_plan_runs_when_certified(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    planner = Plans(HALLUCINATED, GOOD)
    evs = run_task(make_assist(robot, FakeLLM(default=APPLE), planner), TASK)
    assert done(evs)["status"] == "success" and robot.obj["apple"] == DT
    assert next(e for e in evs if e["event"] == "plan")["certified"] is True
    assert [e["round"] for e in evs if e["event"] == "gate"][-1] == 2


def test_uncertified_replan_suffix_never_runs(make_assist, env):
    house = dict(HOUSE, apple=CT)                     # not at the desk: the Find misses, SAGE replans
    robot = FakeRobot(env, house)
    bad = steps("MoveTo Garage", "Find apple")
    planner = Plans(GOOD, replans=[bad, bad])
    evs = run_task(make_assist(robot, FakeLLM(default=APPLE), planner), TASK)
    d = done(evs)
    assert d["status"] == "failed" and "did not pass my checks" in d["msg"]
    assert [t[1] for t in robot.trace if t[0] == "move"] == [DS], "moved on an uncertified suffix"
    assert len([c for c in planner.calls if c[0] == "replan"]) == 2      # refined once


def test_certified_plan_runs_unchanged(make_assist, env):
    robot = FakeRobot(env, HOUSE)
    planner = Plans(GOOD)
    evs = run_task(make_assist(robot, FakeLLM(default=APPLE), planner), TASK)
    assert done(evs)["status"] == "success"
    assert len([c for c in planner.calls if c[0] == "generate_plan"]) == 1
