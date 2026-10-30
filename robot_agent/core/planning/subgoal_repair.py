"""The certified gate's sub-goal repair: the checker's critical findings go back to the planner's own
sub-goal-local repair (the optional ``SubgoalRepair`` capability of ``base.py``; SAGE's Editing through
``backends/pyplanner_grace.py`` is one). Planner-neutral: only the capability's methods are called.

Each round, every sub-goal block that owns a step with a critical finding is re-done by
``planner.repair_block`` with that finding as feedback; a block whose sub-goal the earlier blocks already
achieved is asked for no steps. A planner without the capability gets ``(None, [])`` and the gate falls
back to one fresh plan with the findings (AssistLoop._refine_plan). See docs/planners.md.
"""

from __future__ import annotations

from . import action_model
from .base import subgoal_repair


def _block_ranges(loop, blocks, join, ground, world, goals):
    """{block index: range of its steps in the flat plan}, and the first block from which on the goals
    are already reached by the blocks before it (None if none)."""
    per_block, n_before, done_at = {}, 0, None
    for k in range(len(blocks)):
        flat_k = ground(join(blocks[:k + 1]))
        if done_at is None and blocks[k].steps and k > 0 and loop._reaches(ground(join(blocks[:k])), world, goals):
            done_at = k
        per_block[k] = range(n_before, len(flat_k))
        n_before = len(flat_k)
    return per_block, done_at


def _findings_per_block(loop, blocks, join, ground, world, places, goals, goal_txt) -> dict:
    """{block index: [the checker's critical findings that block must fix]} for the current blocks."""
    per_block, done_at = _block_ranges(loop, blocks, join, ground, world, goals)
    probs = [p_ for p_ in loop._check(ground(join(blocks)), world, places, goals) if p_[2]]
    todo = {}
    for k in range(len(blocks)):
        if done_at is not None and k >= done_at and blocks[k].steps:
            todo[k] = [f"the goals ({goal_txt}) are already achieved by the earlier sub-goals, so this "
                       f'sub-goal needs no steps: return {{"steps": []}}']
    for i_, txt, _c in probs:
        if txt.startswith("the goals are done after step"):
            continue                             # handled per block above
        k = next((k for k, r in per_block.items() if i_ in r), len(blocks) - 1)
        if txt.startswith("the plan does not achieve"):
            k = max((k for k in range(len(blocks)) if blocks[k].steps), default=len(blocks) - 1)
            txt = txt.replace("the plan does not achieve", "this sub-goal must also achieve")
        todo.setdefault(k, []).append(txt)
    return todo


def _repair_round(loop, rep, blocks, todo, task, obs, goal_txt, notes) -> None:
    """One call of ``rep.repair_block`` per block in ``todo``; the blocks' steps are replaced in place."""
    from ..run_control import call_cancellable, is_cancel
    for k, items in sorted(todo.items()):
        prev = [s_ for b in blocks[:k] for s_ in b.steps]
        state = action_model.simulate(prev, stop_on_error=False,
                                      capabilities=loop.robot.model_capabilities()).final_state
        fb = ("Checker findings (fix them; places must be exactly the listed ones):\n"
              + "\n".join(f"- {t}" for t in items) + f"\nGoals of the whole task: {goal_txt}")
        try:
            new_steps = call_cancellable(rep.repair_block, blocks[k], fb, task=task, state=state, obs=obs)
        except Exception as e:
            if is_cancel(e):
                raise
            notes.append(f"sub-goal {k + 1} edit failed: {e}")
            continue
        empty_ok = any('"steps": []' in t for t in items)
        if new_steps or empty_ok:
            blocks[k].steps = new_steps
        notes.append(f"sub-goal {k + 1} '{blocks[k].name}': " + "; ".join(items))


def repair_plan(loop, planner, task, obs, ground, world, places, goals, goal_txt):
    """The planner's sub-goal repair driven by ``loop``'s checker, at most ``loop.cfg['edit_rounds']``
    rounds. Returns ``(plan, [edit notes])``; the plan is the planner's ``join_blocks`` of its blocks,
    grounded. ``(None, [])`` when the planner has no ``SubgoalRepair`` or no blocks."""
    rep = subgoal_repair(planner)
    blocks = rep.blocks() if rep is not None else None
    if not blocks:
        return None, []
    notes: list = []
    for _round in range(loop.cfg["edit_rounds"]):
        todo = _findings_per_block(loop, blocks, rep.join_blocks, ground, world, places, goals, goal_txt)
        if not todo:
            break
        _repair_round(loop, rep, blocks, todo, task, obs, goal_txt, notes)
    return ground(rep.join_blocks(blocks)), notes
