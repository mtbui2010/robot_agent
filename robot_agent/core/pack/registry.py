"""The registry of a pack's named callables: ``register(namespace, "<pack>.<name>", obj)``.

What: five namespaces (design, "Named registered callables"). A pack's DATA names a callable; a Python module of that pack
registers it here. Why: a pack is data plus names, never code taken from data: no ``eval``, no dotted path imported from a
value. Rules checked at registration: a name is registered once; it carries the prefix of the pack whose module registers it
(``claim`` binds a pack name to its ONE callables module, so no pack can replace another pack's callable silently); the
callable takes the number of parameters its namespace gives it.
Off: nothing is registered until a pack's callables module is imported, and a name no data refers to is never called.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable, Dict, Optional, Tuple

# namespace -> the parameters of its callable (the count is checked), or, for an object, method -> parameters
NAMESPACES: Dict[str, Any] = {
    "predicate": ("belief", "args"),                                   # -> True / False / None (cannot tell)
    "goal.rules": {"reached": ("goal", "steps", "world", "ctx"), "holds": ("goal", "belief", "ctx"),
                   "missing": ("goal", "steps", "world", "ctx"), "phrase": ("goal", "places", "nice")},
    "verifier.step": ("step", "result", "ctx"),                        # -> a list of verify results
    "verifier.goal": ("goal", "ctx"),                                  # generator -> (True / False / None, evidence, device)
    "recovery": ("run", "step", "reason", "rule", "ctx"),              # generator -> (outcome or None, replan hint)
}
OPTIONAL_METHODS = {"goal.rules": ("phrase",)}                         # a rules object may come without these
_NAME = re.compile(r"[a-z][a-z0-9_]*\.[A-Za-z_]\w*$")


def _arity(fn: Callable) -> Optional[Tuple[int, int]]:
    """(the fewest, the most) positional arguments ``fn`` takes (a bound method: without ``self``); None = any number."""
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return None
    if any(p.kind is p.VAR_POSITIONAL for p in params):
        return None
    pos = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return sum(1 for p in pos if p.default is p.empty), len(pos)


def _shape_error(namespace: str, obj: Any) -> Optional[str]:
    """What is wrong with ``obj`` for ``namespace`` (not callable, a method missing, a wrong parameter count), or None."""
    want = NAMESPACES[namespace]
    parts = [(None, obj, want)] if isinstance(want, tuple) else [(m, getattr(obj, m, None), p) for m, p in want.items()]
    for method, fn, params in parts:
        what = f"method {method}" if method else "the callable"
        if fn is None and method in OPTIONAL_METHODS.get(namespace, ()):
            continue
        if not callable(fn):
            return f"{what} is missing or not callable"
        n = _arity(fn)
        if n is not None and not n[0] <= len(params) <= n[1]:
            return f"{what} takes {n[0]} to {n[1]} parameter(s), the namespace gives it {len(params)} ({', '.join(params)})"
    return None


class Registry:
    def __init__(self) -> None:
        self._items: Dict[str, Dict[str, Tuple[Any, str]]] = {ns: {} for ns in NAMESPACES}
        self._claims: Dict[str, str] = {}                              # pack name -> its callables module

    def register(self, namespace: str, name: str, obj: Any = None, module: Optional[str] = None):
        """Register ``obj`` as ``name`` (``<pack>.<name>``) in ``namespace``; with no ``obj`` it is a decorator.
        ``module``: the module that registers it (default: where ``obj`` is defined). Raises ValueError on a broken rule."""
        if obj is None:
            return lambda fn: self.register(namespace, name, fn, module) or fn
        if namespace not in NAMESPACES:
            raise ValueError(f"pack callable {name!r}: unknown namespace {namespace!r} (known: {', '.join(NAMESPACES)})")
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"pack callable {name!r}: a name is <pack>.<name>")
        if name in self._items[namespace]:
            raise ValueError(f"pack callable {name!r} is registered twice in {namespace!r}")
        bad = _shape_error(namespace, obj)
        if bad:
            raise ValueError(f"pack callable {name!r} ({namespace}): {bad}")
        module = module or getattr(obj, "__module__", None) or type(obj).__module__
        self._owned(name, module)
        self._items[namespace][name] = (obj, module)
        return None

    def claim(self, pack: str, module: str) -> None:
        """The pack named ``pack`` registers from ``module`` (and its sub-modules) only, and that module registers no other
        pack's names. Checked against what is registered already and at every later registration."""
        if self._claims.get(pack, module) != module:
            raise ValueError(f"pack {pack!r}: its callables module is {self._claims[pack]!r}, not {module!r}")
        taken = next((p for p, m in self._claims.items() if p != pack and _inside(module, m)), None)
        if taken:
            raise ValueError(f"pack {pack!r}: the module {module!r} belongs to pack {taken!r}")
        self._claims[pack] = module
        for items in self._items.values():
            for name, (_obj, mod) in items.items():
                self._owned(name, mod)

    def _owned(self, name: str, module: str) -> None:
        pack = name.split(".", 1)[0]
        owner = self._claims.get(pack)
        if owner is not None and not _inside(module, owner):
            raise ValueError(f"pack callable {name!r}: the names of pack {pack!r} are registered by {owner!r} only, not by {module!r}")
        other = next((p for p, m in self._claims.items() if p != pack and _inside(module, m)), None)
        if other is not None:
            raise ValueError(f"pack callable {name!r}: the module {module!r} belongs to pack {other!r} and registers only {other}.* names")

    def get(self, namespace: str, name: str) -> Any:
        """The registered object, or None (an unknown name: the caller decides what that means)."""
        item = self._items.get(namespace, {}).get(name)
        return item[0] if item else None

    def has(self, namespace: str, name: str) -> bool:
        return name in self._items.get(namespace, {})

    def names(self, namespace: Optional[str] = None) -> list:
        """The registered names of ``namespace`` (sorted), or ``(namespace, name)`` pairs of all of them."""
        if namespace is not None:
            return sorted(self._items.get(namespace, {}))
        return sorted((ns, n) for ns, items in self._items.items() for n in items)


def _inside(module: str, owner: str) -> bool:
    return module == owner or module.startswith(owner + ".")


REGISTRY = Registry()
register = REGISTRY.register

__all__ = ["NAMESPACES", "Registry", "REGISTRY", "register"]
