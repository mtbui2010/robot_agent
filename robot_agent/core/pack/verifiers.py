"""The verifiers of a pack, chosen by name: the end-of-run look at one goal, and the checks after a step.

What: ``goal_verifier(pack, kind)`` is the ``verifier.goal`` callable (and its entry) that looks at a goal of ``kind``: the
kind's ``observe`` name (``goal_kinds`` section) -> an entry of the ``verifiers`` section -> its registered ``callable``.
``step_verifiers(pack, action)`` lists the ``verifier.step`` callables that check a step of ``action``: the entries with
``every_step: true`` (table order; such a check decides itself which actions it looks at), then the names the action lists
in ``post`` (each name once). ``observed_kinds(pack)`` lists the kinds a verifier looks at.
Why: the loop used to decide itself which goals are looked at after the task (only ``on`` / ``in``) and which steps get a
second look (docs/proposals/generalize_core_design.md, "Verifiers", Step S12); the standard pack now names those rules.
How: pure readers of a resolved pack (data in, callables out); the pack's callables module registered the names.
``checking_pack`` picks the pack they read: a pack with ``extends: null`` and no verifier keeps the default pack's checks.
Off: a kind with no ``observe`` (null, or a name not declared or not registered) is not looked at: the goal stays on the
belief. A step check a pack removes (``remove: {verifiers: [...]}``) is not run. A declared check with no callable
(``kind: sensor`` / ``vlm``) is parsed by the schema but not run yet: it gives no callable here.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, List, Optional, Tuple

GOAL = "verifier.goal"
STEP = "verifier.step"
TABLE = "verifiers"


@functools.lru_cache(maxsize=1)
def _default():
    from .loader import default_pack
    return default_pack()


def checking_pack(pack: Any, default: Any = None) -> Any:
    """The pack whose verifiers check a robot's run: its own when it builds on the bases of the default chain (a schema-1
    robot, a pack that extends the standard pack) or has a verifier of its own; else (``extends: null`` and no verifier,
    or no pack) the default pack (``default``, else the pack of a robot that declares nothing): before Step S12 every
    robot had the loop's checks, and such a pack keeps them."""
    default = _default() if default is None else default
    if pack is None:
        return default
    if pack.table(TABLE) or set(default.chain[:-1]) <= set(pack.chain):
        return pack
    return default


def _callable(pack: Any, namespace: str, entry) -> Optional[Callable]:
    ref = entry.get("callable") if isinstance(entry, dict) else None
    return pack.named(namespace, ref) if ref else None


def goal_verifier(pack: Any, kind) -> Tuple[Optional[Callable], Optional[dict]]:
    """(the callable that looks at a goal of ``kind``, its entry); (None, None) when the kind names no ``observe`` verifier,
    the name is not in the ``verifiers`` section, or its callable is not a registered ``verifier.goal``."""
    decl = pack.entry("goal_kinds", kind) if pack is not None and isinstance(kind, str) else None
    name = decl.get("observe") if isinstance(decl, dict) else None
    entry = pack.entry(TABLE, name) if isinstance(name, str) and name else None
    fn = _callable(pack, GOAL, entry)
    return (fn, entry) if fn is not None else (None, None)


def step_names(pack: Any, action=None) -> List[str]:
    """The names of the step checks of ``action``, in the order they run: every ``every_step: true`` entry, then the
    action's ``post`` names not already listed."""
    table = pack.table(TABLE) if pack is not None else {}
    decl = pack.entry("actions", action) if pack is not None and isinstance(action, str) else None
    names = [n for n, e in table.items() if isinstance(e, dict) and e.get("every_step") is True]
    for n in (decl.get("post") or ()) if isinstance(decl, dict) else ():
        if n not in names:
            names.append(n)
    return names


def step_verifiers(pack: Any, action=None) -> List[Tuple[Callable, dict]]:
    """[(callable, entry)] of the step checks of ``action`` (``step_names``) whose callable is a registered
    ``verifier.step``; a name with no entry or no registered callable is skipped."""
    out = []
    for name in step_names(pack, action):
        entry = pack.entry(TABLE, name)
        fn = _callable(pack, STEP, entry)
        if fn is not None:
            out.append((fn, entry))
    return out


def observed_kinds(pack: Any) -> List[str]:
    """The goal kinds of ``pack`` that a verifier looks at after the task (table order)."""
    kinds = pack.table("goal_kinds") if pack is not None else {}
    return [k for k in kinds if goal_verifier(pack, k)[0] is not None]


__all__ = ["GOAL", "STEP", "TABLE", "checking_pack", "goal_verifier", "step_names", "step_verifiers", "observed_kinds"]
