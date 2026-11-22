"""The standard pack's predicate evaluators ``std.*`` (namespace ``predicate``): ``(belief, args) -> True / False / None``.

What: the tests of the belief that used to be written twice, as a dict of lambdas in ``options/bt_goal.py`` (tree conditions) and as
an if-chain in ``core/planning/verify.py`` (a skill's declared effects). Why: one table (``predicates.yaml``) names them and each
consumer reads the same evaluator (``robot_agent/core/pack/predicates.py``). How they read ``args``: ``()`` is the wildcard (an
effect such as ``holding(*)``: anything at all); one value is compared as it is, ``None`` included (a tree leaf whose slot is empty
tests for None, as before). The belief is a world (``holding``, ``arrived``, ``found``, ``opened``, ``on``), plus ``placed`` (object ->
place) for a tree; a field the belief lacks reads as nothing. Never None here: every household predicate can tell.
Off: a robot whose pack removes these names has no conditions and no tracked effects (nothing is checked).
"""

from robot_agent.core.pack import register


def _set(belief, field: str) -> set:
    return set(getattr(belief, field, None) or ())


@register("predicate", "std.holding")
def holding(belief, args):
    held = getattr(belief, "holding", None)
    return held == args[0] if args else held is not None


@register("predicate", "std.hand_free")
def hand_free(belief, args):
    return getattr(belief, "holding", None) is None


@register("predicate", "std.at")
def at(belief, args):
    arrived = getattr(belief, "arrived", None)
    return arrived == args[0] if args else arrived is not None


@register("predicate", "std.found")
def found(belief, args):
    seen = getattr(belief, "found", None)
    return seen == args[0] if args else seen is not None


@register("predicate", "std.placed")
def placed(belief, args):
    """Put at the place and not in the hand (the run's ``placed`` map, not a field of the world)."""
    obj, place = args
    return (getattr(belief, "placed", None) or {}).get(obj) == place and getattr(belief, "holding", None) != obj


@register("predicate", "std.opened")
def opened(belief, args):
    cur = _set(belief, "opened")
    return args[0] in cur if args else bool(cur)


@register("predicate", "std.closed")
def closed(belief, args):
    return args[0] not in _set(belief, "opened")


@register("predicate", "std.switched_on")
def switched_on(belief, args):
    cur = _set(belief, "on")
    return args[0] in cur if args else bool(cur)
