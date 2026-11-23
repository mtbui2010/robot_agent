"""The recovery policies of the standard (household) pack: ``std.find_search``, ``std.pick_ladder``, ``std.move_ladder``,
``std.release_refind`` and ``std.open_in_search``.

What: each is the loop's own ladder of one household action, looked up on ``ctx.loop`` when it runs (so a patched method is
the one that runs) and called with the same arguments; its outcome is returned unchanged. The ladders stay mixins of
AssistLoop: ``_recover_find`` (find_ladder.py), ``_recover_pick``, ``_recover_move``, ``_recover_place`` (step_ladder.py),
``_recover_open`` (search_hooks.py).
Why: the loop chooses them from a dict keyed by six action names (assist_loop.py at 3deb5c5 lines 3150-3152); recovery.yaml
next to pack.yaml names them per action so core/pack/recovery.py can pick them (Step S14).
Off: registered names do nothing until a policy table names them and the loop asks for one (it does not yet, see
core/pack/recovery.py); a robot pack that maps an action to another policy (or to ``planner``) does not run these for it.
"""

from __future__ import annotations

from robot_agent.core.pack import register


@register("recovery", "std.find_search")
def find_search(run, step, reason, rule, ctx):
    """A failed Find: look again, mark the place searched, search legs, ask where, second looks."""
    return (yield from ctx.loop._recover_find(run, step, reason, rule))


@register("recovery", "std.pick_ladder")
def pick_ladder(run, step, reason, rule, ctx):
    """A failed Pick: look and grasp again in place, ask the person, then the planner or a stop."""
    return (yield from ctx.loop._recover_pick(run, step, reason, rule))


@register("recovery", "std.move_ladder")
def move_ladder(run, step, reason, rule, ctx):
    """A failed MoveTo: move again, ask the person to clear the way, else stop."""
    return (yield from ctx.loop._recover_move(run, step, reason, rule))


@register("recovery", "std.release_refind")
def release_refind(run, step, reason, rule, ctx):
    """A failed Place / PutIn: an empty hand means the object fell (refind it on the floor, else stop); else the planner."""
    return (yield from ctx.loop._recover_place(run, step, reason, rule))


@register("recovery", "std.open_in_search")
def open_in_search(run, step, reason, rule, ctx):
    """A failed Open during a search: the search gives that container up."""
    return (yield from ctx.loop._recover_open(run, step, reason, rule))
