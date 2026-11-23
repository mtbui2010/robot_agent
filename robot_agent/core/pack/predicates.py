"""One predicate table for the conditions of a behaviour tree and the effects a skill declares (pure readers of the pack's data).

What: ``holds_in_tree`` answers a tree ``cond`` leaf, ``holds_in_effect`` one declared effect of a skill; both look the name up in
the pack's ``predicates`` table and call the evaluator it names (``(belief, args) -> True / False / None``, registered in
namespace ``predicate``). ``names(scope)`` lists the names a scope may use. Why: the two used to keep two closed tables of six
names (``options/bt_goal.py``, ``core/planning/verify.py``) that did not know each other; a pack can now declare more.
Each consumer keeps its own rule, because they differ today: a tree reads an unknown name, a name outside its scope, a wrong number
of arguments and an argument it cannot compare as False (every argument is required); an effect reads a name outside its scope or
an evaluator that cannot tell as None ("not checked, the step passes"), accepts any number of arguments (``()`` is the
wildcard) and lets an evaluator's error pass through.
How to switch off: a pack that declares no predicates has no conditions and no tracked effects. Table = the default pack's unless
a ``pack`` is given.
"""

from __future__ import annotations

import functools
from typing import Optional

TREE, EFFECT = "tree", "effect"          # the scopes this module reads (the schema also knows pre and goal)


class View:
    """The belief a tree condition reads: the world's own fields and the run's ``placed`` map (object -> place)."""

    __slots__ = ("world", "placed")

    def __init__(self, world, placed) -> None:
        self.world, self.placed = world, placed

    def __getattr__(self, name):                       # only for a name that is no slot: the world's field, else AttributeError
        if name in View.__slots__:
            raise AttributeError(name)
        return getattr(self.world, name)


@functools.lru_cache(maxsize=1)
def default():
    """The pack of code that has no robot at hand (the core pack and the standard pack), resolved once."""
    from .loader import default_pack
    return default_pack()


def table(pack=None) -> dict:
    return (pack or default()).table("predicates")


def _usable(decl, scope: str) -> bool:
    return isinstance(decl, dict) and scope in (decl.get("scopes") or ())


def names(scope: str, pack=None) -> tuple:
    """The predicate names a scope may use, in the table's order (a tuple: a name is compared, never hashed)."""
    return tuple(n for n, d in table(pack).items() if _usable(d, scope))


def _evaluator(decl, pack):
    return (pack or default()).named("predicate", decl.get("eval"))


def holds_in_tree(name, args, world, placed, pack=None) -> bool:
    """A tree condition (pure): False for an unknown name, a name outside the tree scope, a wrong number of arguments, an
    argument the evaluator cannot compare (a TypeError or KeyError), an evaluator that is not registered or cannot tell."""
    try:
        decl = table(pack).get(name)
        if not _usable(decl, TREE) or len(args) != len(decl.get("args") or ()):
            return False
        fn = _evaluator(decl, pack)
        return bool(fn(View(world, placed), tuple(args))) if fn else False
    except (KeyError, TypeError):
        return False


def holds_in_effect(name, arg, world, pack=None) -> Optional[bool]:
    """One declared effect (pure): None when ``name`` is no predicate of the effect scope or its evaluator cannot tell (not
    checked); ``arg`` None is the wildcard. An evaluator's error is not caught."""
    decl = table(pack).get(name)
    fn = _evaluator(decl, pack) if _usable(decl, EFFECT) else None
    return fn(world, () if arg is None else (arg,)) if fn else None


__all__ = ["TREE", "EFFECT", "View", "default", "table", "names", "holds_in_tree", "holds_in_effect"]
