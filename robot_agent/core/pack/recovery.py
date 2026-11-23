"""The recovery policies of a pack, chosen by name: what a failed step does after the recovery rule's own rung.

What: ``policy_name(pack, action, kinds)`` is the name of the policy of a failed step of ``action`` while goals of ``kinds``
run: ``by_kind`` of the first of those kinds the table lists, else ``by_action[action]``, else the action entry's own
``recovery`` field, else the builtin ``planner``. ``policy(pack, name)`` is (the builtin, None, {}) for a builtin (``planner``,
``stop``, ``tree``) and (None, the registered ``recovery`` callable, its entry) for a declared policy; a name that is neither,
whose callable is not registered, or that declares a ``ladder`` (deferred in the design) is ``planner``. ``ladder_of(pack,
action)`` is (failure code, may replan) of the step's own ladder as the action's policy declares it (``ladder_code``,
``may_replan``), or None. ``Ctx`` is what a policy callable is given: the loop, the run, its entry, the engine's ladders.
Why: the loop chose a ladder from a dict of six action names (robot_agent/core/planning/assist_loop.py:3150-3152 at
6f844de) and read the Pick / MoveTo ladder codes from contract STEP_LADDER (docs/proposals/generalize_core_design.md,
"Recovery", Step S14). The standard pack (robot_agent/packs/household/recovery.yaml) declares the same choice as data.
Which pack: ``selecting_pack``: a robot's own pack when it builds on the default chain (schema 1, or ``extends`` the standard
pack) or declares a recovery table itself; else the default pack, because the loop ran these ladders for every robot.
Off: a pack whose tables name no policy for an action sends its failed steps to the planner (the path of an action with no
ladder before this step). Pure readers of a resolved pack; nothing here runs a ladder.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional, Tuple

from robot_agent.contract import FailureCode

from . import loader
from .check import BUILTIN_POLICIES

NAMESPACE = "recovery"
PLANNER = "planner"
TABLES = ("recovery.by_action", "recovery.by_kind", "recovery.policies")


class Ctx:
    """What a policy callable may use: ``loop`` (the AssistLoop), ``run`` (the run's state), ``entry`` (its own declared entry)
    and ``ladders`` (the engine's ladders by name: ``find``, ``pick``, ``move``, ``search_open``, ``release``; each a generator method
    ``(run, step, reason, rule) -> (outcome or None, replan hint)``)."""
    __slots__ = ("loop", "run", "entry", "ladders")

    def __init__(self, loop, run, entry=None, ladders=None):
        self.loop, self.run = loop, run
        self.entry = entry if isinstance(entry, dict) else {}
        self.ladders = dict(ladders or {})


@functools.lru_cache(maxsize=1)
def _default():
    return loader.default_pack()


def selecting_pack(pack: Any, default: Any = None) -> Any:
    """The pack whose policies a robot's failed steps follow: its own when it declares a recovery table or builds on the packs
    of the default chain; else (``extends: null`` and no policy, or no pack) the default pack."""
    default = _default() if default is None else default
    if pack is None:
        return default
    if any(pack.table(t) for t in TABLES) or set(default.chain[:-1]) <= set(pack.chain):
        return pack
    return default


def policy_name(pack: Any, action, kinds=()) -> str:
    """The policy of a failed step of ``action`` while goals of ``kinds`` (in the run's order) run."""
    if pack is None:
        return PLANNER
    by_kind = pack.table("recovery.by_kind")
    for k in kinds or ():
        if isinstance(k, str) and isinstance(by_kind.get(k), str):
            return by_kind[k]
    by_action = pack.table("recovery.by_action")
    if isinstance(action, str) and isinstance(by_action.get(action), str):
        return by_action[action]
    decl = pack.entry("actions", action) if isinstance(action, str) else None
    own = decl.get("recovery") if isinstance(decl, dict) else None
    return own if isinstance(own, str) and own else PLANNER


def policy(pack: Any, name: str) -> Tuple[Optional[str], Optional[Callable], dict]:
    """(builtin name, None, {}) or (None, the registered callable, its entry) of the policy ``name``."""
    if name in BUILTIN_POLICIES:
        return name, None, {}
    entry = pack.entry("recovery.policies", name) if pack is not None else None
    if not isinstance(entry, dict) or entry.get("ladder"):
        return PLANNER, None, {}
    fn = pack.named(NAMESPACE, entry.get("callable"))
    return (None, fn, entry) if fn is not None else (PLANNER, None, {})


def ladder_of(pack: Any, action) -> Optional[Tuple[FailureCode, bool]]:
    """(the failure code whose rule is the step's own ladder, may it end in a replan) as the policy of ``action`` declares it
    (``ladder_code``, ``may_replan``); None when it declares no code or an unknown one."""
    name = policy_name(pack, action)
    entry = pack.entry("recovery.policies", name) if pack is not None else None
    code = entry.get("ladder_code") if isinstance(entry, dict) else None
    try:
        return (FailureCode(code), bool(entry.get("may_replan"))) if isinstance(code, str) else None
    except ValueError:
        return None


__all__ = ["NAMESPACE", "PLANNER", "TABLES", "Ctx", "selecting_pack", "policy_name", "policy", "ladder_of"]
