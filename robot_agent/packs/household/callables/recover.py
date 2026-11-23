"""The recovery policies of the standard (household) pack: ``std.find_search``, ``std.pick_ladder``, ``std.move_ladder``,
``std.open_in_search`` and ``std.release_refind``.

What: each policy runs one of the engine's own ladders, which the loop offers in ``ctx.ladders`` (the Find search, the
Pick and MoveTo ladders, the Open given up while searching, the release refound on the floor). The code of the ladders
stays where it is (core/planning/find_ladder.py, step_ladder.py, search_hooks.py); what moved is the CHOICE of a ladder
by action, which the loop made from a dict of six action names (robot_agent/core/planning/assist_loop.py:3150-3152 at
6f844de). ``recovery.yaml`` next to ``pack.yaml`` maps the same six actions to these names.
Why: the loop names no action for its recovery (docs/proposals/generalize_core_design.md, "Recovery", Step S14); a robot
pack picks another policy for an action (``by_action``) or for a goal kind (``by_kind``), or a builtin (planner, stop, tree).
Off: a robot pack that maps an action to another policy does not run the ladder for it.
"""

from __future__ import annotations

from robot_agent.core.pack import register


@register("recovery", "std.find_search")
def find_search(run, step, reason, rule, ctx):
    """A failed Find: look again, mark the place searched, search on, ask where (``AssistLoop._recover_find``)."""
    return (yield from ctx.ladders["find"](run, step, reason, rule))


@register("recovery", "std.pick_ladder")
def pick_ladder(run, step, reason, rule, ctx):
    """A failed Pick: grasp again in place, ask the person, replan (``AssistLoop._recover_pick``)."""
    return (yield from ctx.ladders["pick"](run, step, reason, rule))


@register("recovery", "std.move_ladder")
def move_ladder(run, step, reason, rule, ctx):
    """A failed MoveTo: move again, ask the person to clear the way, stop (``AssistLoop._recover_move``)."""
    return (yield from ctx.ladders["move"](run, step, reason, rule))


@register("recovery", "std.open_in_search")
def open_in_search(run, step, reason, rule, ctx):
    """A failed Open while searching: that container is given up (``AssistLoop._recover_open``)."""
    return (yield from ctx.ladders["search_open"](run, step, reason, rule))


@register("recovery", "std.release_refind")
def release_refind(run, step, reason, rule, ctx):
    """A failed release: an empty hand refinds the dropped object or stops, else the planner (``AssistLoop._recover_place``)."""
    return (yield from ctx.ladders["release"](run, step, reason, rule))
