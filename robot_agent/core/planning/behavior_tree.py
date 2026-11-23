"""A tiny behaviour-tree runner (docs/bt_goal.md): a tree is DATA, a nested list ``[type, *args]``; the host gives every leaf its meaning.

What: ``tick(node, host)`` makes one pass through the tree (a generator: it yields the host's events and returns a status). Types: ``sequence``
(stops at the first child that does not succeed), ``fallback`` (the next child after a failure), ``retry n child`` (up to n passes of the child),
and the leaves ``cond name *args`` (``host.cond``, a plain bool: it never acts), ``act`` / ``ask`` / ``confirm`` / ``reply`` (``host.<type>(*args)``,
generators returning True, False or None = the run ended). ``act`` may take ONE optional mapping as its last argument (typed arguments of the
action, ``[act, Seat, "{to}", {arm: left}]``: strings, numbers or booleans; the host gets it as the third argument). ``fill`` puts a goal's
values into ``{slot}`` arguments (also the string values of that mapping); ``leaves`` lists what a tree needs, so a caller can refuse a tree it cannot run. Why: a declared control flow per goal kind instead of a plan written by an LLM each time.
No clock, no thread, no domain word. ``running`` is never returned: the robot's skills block (one call, one result). After each node the
host's ``note(path, node, status)`` is called when it has one (a trace per node). Used by option ``bt_goal``.
"""

from __future__ import annotations

from typing import Iterator, Optional

SUCCESS, FAILURE, RUNNING, STOPPED = "success", "failure", "running", "stopped"
COMPOSITES = ("sequence", "fallback")
LEAVES = ("cond", "act", "ask", "confirm", "reply")


def _typed(arg) -> bool:
    """The optional mapping of an ``act`` leaf: string keys, string / number / boolean values."""
    return isinstance(arg, dict) and all(isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in arg.items())


def valid(node) -> bool:
    """Is ``node`` a well-formed tree: known types, composites with children, ``retry`` with a count >= 1, leaves with string arguments
    (an ``act`` after at least one string may end with one mapping of typed arguments)."""
    if not isinstance(node, list) or not node or not isinstance(node[0], str):
        return False
    kind, args = node[0], node[1:]
    if kind in COMPOSITES:
        return bool(args) and all(valid(c) for c in args)
    if kind == "retry":
        n = args[0] if len(args) == 2 else None
        return isinstance(n, int) and not isinstance(n, bool) and n >= 1 and valid(args[1])
    if kind == "act" and len(args) > 1 and _typed(args[-1]):
        args = args[:-1]
    return kind in LEAVES and bool(args) and all(isinstance(a, str) for a in args)


def leaves(node) -> Iterator[list]:
    """Every leaf of a valid tree, in order."""
    if node[0] in COMPOSITES:
        for c in node[1:]:
            yield from leaves(c)
    elif node[0] == "retry":
        yield from leaves(node[2])
    else:
        yield node


def fill(node, values: dict) -> Optional[list]:
    """``node`` with each argument ``"{name}"`` replaced by ``values[name]`` (also in the values of an ``act``'s mapping); None when a named
    value is missing or empty."""
    out = [node[0]]
    for a in node[1:]:
        a = fill(a, values) if isinstance(a, list) else _fill_map(a, values) if isinstance(a, dict) else _slot(a, values)
        if a is None:
            return None
        out.append(a)
    return out


def _slot(a, values: dict):
    """One argument: ``"{name}"`` becomes ``values[name]`` (None when missing or empty), anything else stays."""
    return (values.get(a[1:-1]) or None) if isinstance(a, str) and len(a) > 2 and a[0] == "{" and a[-1] == "}" else a


def _fill_map(m: dict, values: dict) -> Optional[dict]:
    """The mapping with each ``"{name}"`` value filled; None when one is missing or empty."""
    out = {k: _slot(v, values) for k, v in m.items()}
    return None if any(v is None for v in out.values()) else out


def tick(node, host, path: str = "0"):
    """Generator: one pass through ``node`` (a valid, filled tree). Returns SUCCESS, FAILURE or STOPPED (the run ended: stop at once).
    After each node, ``host.note(path, node, status)`` when the host has it (``path``: the node's place, "0.1.2")."""
    status = yield from _tick(node, host, path)
    note = getattr(host, "note", None)
    if note is not None:
        note(path, node, status)
    return status


def _tick(node, host, path):
    kind, args = node[0], node[1:]
    if kind in COMPOSITES:
        stop_on = FAILURE if kind == "sequence" else SUCCESS
        for i, c in enumerate(args):
            status = yield from tick(c, host, f"{path}.{i}")
            if status in (stop_on, STOPPED):
                return status
        return SUCCESS if kind == "sequence" else FAILURE
    if kind == "retry":
        for _ in range(args[0]):
            status = yield from tick(args[1], host, f"{path}.0")
            if status != FAILURE:
                return status
        return FAILURE
    if kind == "cond":
        return SUCCESS if host.cond(*args) else FAILURE
    out = yield from getattr(host, kind)(*args)
    return STOPPED if out is None else SUCCESS if out else FAILURE


__all__ = ["SUCCESS", "FAILURE", "RUNNING", "STOPPED", "valid", "leaves", "fill", "tick"]
