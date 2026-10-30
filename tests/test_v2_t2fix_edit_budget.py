"""T2 fix: the gate's sub-goal edits within the run's LLM-call budget (subgoal_repair.py). T2 H01: a
61-step plan of 22 one-word sub-goals used the whole cap of 20 on edits, then the run ended."""

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, steps

DT, DS, CT = "dining table@kitchen", "desk@study room", "counter@kitchen"
APPLE = {"status": "clear", "reason": "", "question": "",
         "goals": [{"type": "on", "object": "apple", "from": "", "to": "dining table"}]}
GOOD = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
AGAIN = steps(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}")


class Block:
    def __init__(self, name, st):
        self.name, self.steps = name, [dict(s) for s in st]


class BlockPlanner(ScriptedPlanner):
    """SubgoalRepair fake: the first plan is ``blocks``; each repair_block returns ``fix(block)``."""

    def __init__(self, blocks, fix=None):
        super().__init__([s for b in blocks for s in b.steps])
        self._blocks, self.fix, self.edits = blocks, fix, []

    def blocks(self):
        return self._blocks

    def join_blocks(self, blocks):
        return [dict(s) for b in blocks for s in b.steps]

    def repair_block(self, block, feedback, *, task, state=None, obs=""):
        self.edits.append((block.name, feedback))
        return self.fix(block) if self.fix else [dict(s) for s in block.steps]


def gate(evs, k):
    return next((e for e in evs if e["event"] == "gate" and e.get("round") == k), {})


def test_blocks_after_the_goals_are_emptied_without_a_call(make_assist, env):
    """The task done in block 1, the same task repeated in 8 more blocks: no edit call, the plan is block 1."""
    blocks = [Block("put the apple", GOOD)] + [Block(f"again {k}", AGAIN) for k in range(8)]
    planner = BlockPlanner(blocks)
    robot = FakeRobot(env, {"apple": DS})
    evs = run_task(make_assist(robot, FakeLLM(APPLE, default=APPLE), planner), "Put the apple on the dining table.")
    assert done(evs)["status"] == "success", done(evs)
    assert planner.edits == []
    assert gate(evs, 1)["plan"] == [f"{s['action']} {s['object']}" for s in GOOD]
    assert sum("emptied" in n for n in gate(evs, 1)["edits"]) == 8


def test_edits_leave_a_reserve_of_the_budget(make_assist, env):
    """9 broken blocks, a budget of 12 (2 used by understand + plan): the edits do not start (they would
    leave fewer than 4 calls); the gate's one fresh plan fixes it and the run goes on."""
    bad = steps(f"MoveTo {CT}", "Pick apple")
    blocks = [Block(f"broken {k}", bad) for k in range(9)]
    planner = BlockPlanner(blocks)
    planner.generate_plan_calls = 0
    first = planner.generate_plan

    def gen(task, obs, visible):
        planner.generate_plan_calls += 1
        return first(task, obs, visible) if planner.generate_plan_calls == 1 else (GOOD, {"method": "fresh"})
    planner.generate_plan = gen
    robot = FakeRobot(env, {"apple": DS})
    evs = run_task(make_assist(robot, FakeLLM(APPLE, default=APPLE), planner, llm_budget=12),
                   "Put the apple on the dining table.")
    assert planner.edits == []
    assert any("edits stopped" in n and "LLM calls left" in n for n in gate(evs, 1)["edits"])
    assert done(evs)["status"] == "success", done(evs)


def test_edits_made_before_the_budget_ran_out_are_kept(make_assist, env):
    """The budget runs out in the middle of an edit round (another caller used it): the blocks edited so
    far are kept, the edits stop with a note, nothing is raised."""
    from robot_agent.core.planning import subgoal_repair
    from robot_agent.core.planning.assist_loop import Places
    from robot_agent.core.planning.base import WorldState
    from robot_agent.core.planning.understand import LLMBudget

    loop = make_assist(FakeRobot(env, {"apple": DS}), FakeLLM(APPLE), ScriptedPlanner([]))
    bad = steps(f"MoveTo {CT}", "Pick apple", f"MoveTo {DT}", f"Place {DT}")
    blocks = [Block("a", bad), Block("b", bad)]
    half = steps(f"MoveTo {DS}", "Find apple", "Pick apple")
    planner = BlockPlanner(blocks, fix=lambda b: [dict(s) for s in half])
    budget = LLMBudget(1)
    budget.left = lambda: 100                                  # the reserve check passes; take() still caps
    goals = [{"type": "on", "object": "apple", "from": "", "to": DT}]
    plan, notes = subgoal_repair.repair_plan(loop, planner, "t", "", lambda s: s, WorldState(), Places(env),
                                             goals, "on apple to dining table", budget=budget)
    assert blocks[0].steps == half and len(planner.edits) == 1
    assert any("edits stopped" in n and "budget" in n for n in notes)
    assert plan == half                                       # block b was needless (emptied, no call)
