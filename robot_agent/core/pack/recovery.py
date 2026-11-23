"""The recovery policy of a failed step, chosen by name: by goal kind, by action, else the builtin planner.

What: ``policy_name(pack, step, kinds)`` names the policy that recovers a failed step: the step's own ``recovery`` (a tree
leaf may set it), then ``recovery.by_kind`` for the first of the run's goal kinds it lists, then ``recovery.by_action`` for
the step's action (or that action's own ``recovery`` field), else ``planner``. ``policy(pack, name)`` gives the registered
``recovery`` callable of a declared policy and its entry; (None, None) for a builtin or a name with no registered callable.
A callable is a generator ``(run, step, reason, rule, ctx) -> (outcome or None, replan hint)``; ``ctx`` is a :class:`Ctx`.
Why: the loop picks the ladder of six household actions from a dict of its own (AssistLoop._step_failed, assist_loop.py
at 3deb5c5 lines 3150-3152); the standard pack declares that table now (robot_agent/packs/household/recovery.yaml) so a
robot pack can name its own (docs/proposals/generalize_core_design.md, "Recovery", Step S14).
Builtins: ``planner`` (the planner's repair, else the run fails; a step a tree runs stays failed, service ``no_replan``),
``tree`` (the same path: the goal's tree decides, without a tree the planner), ``stop`` (the run fails at once).
Which pack: ``recovering_pack``, as for the verifiers: the robot's own when it builds on the default chain or declares a
recovery table itself, else the default pack (a pack with ``extends: null`` keeps the ladders every robot had).
Status / off: the loop does not call this yet (StepLadder._recover_by_policy is the prepared seam; _step_failed still uses
its dict because tests/test_find_ladder.py pins that dict's text). Until then nothing here changes a run.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Iterable, Optional, Tuple

from . import loader

NAMESPACE = "recovery"
PLANNER, STOP, TREE = "planner", "stop", "tree"
BUILTINS = (PLANNER, STOP, TREE)
TABLES = ("recovery.policies", "recovery.by_action", "recovery.by_kind")


class Ctx:
    """What a recovery policy may use: ``loop`` (the AssistLoop), ``run`` (the run's state), ``entry`` (its declared entry)."""
    __slots__ = ("loop", "run", "entry")

    def __init__(self, loop, run, entry=None):
        self.loop, self.run, self.entry = loop, run, entry if isinstance(entry, dict) else {}


@functools.lru_cache(maxsize=1)
def _default():
    return loader.default_pack()


def recovering_pack(pack: Any, default: Any = None) -> Any:
    """The pack whose policies recover a robot's steps: its own when it builds on the packs of the default chain or
    declares a recovery table itself; else (``extends: null`` and no recovery table, or no pack) the default pack."""
    default = _default() if default is None else default
    if pack is None:
        return default
    if any(pack.table(t) for t in TABLES) or set(default.chain[:-1]) <= set(pack.chain):
        return pack
    return default


def policy_name(pack: Any, step: Any, kinds: Iterable = ()) -> str:
    """The name of the policy for a failed ``step`` (a plan step dict) while goals of ``kinds`` run; ``planner`` when no
    table names one."""
    own = step.get("recovery") if isinstance(step, dict) else None
    if isinstance(own, str) and own:
        return own
    if pack is None:
        return PLANNER
    by_kind = pack.table("recovery.by_kind")
    for k in kinds:
        if isinstance(k, str) and by_kind.get(k):
            return by_kind[k]
    action = step.get("action") if isinstance(step, dict) else None
    if not isinstance(action, str):
        return PLANNER
    return pack.table("recovery.by_action").get(action) or pack.value("actions", action, "recovery") or PLANNER


def policy(pack: Any, name: Optional[str]) -> Tuple[Optional[Callable], Optional[dict]]:
    """(the registered ``recovery`` callable of the declared policy ``name``, its entry), or (None, None) for a builtin, an
    undeclared name or a callable nobody registered."""
    if pack is None or not name or name in BUILTINS:
        return None, None
    entry = pack.entry("recovery.policies", name)
    ref = entry.get("callable") if isinstance(entry, dict) else None
    fn = pack.named(NAMESPACE, ref) if ref else None
    return (fn, entry) if fn is not None else (None, None)


__all__ = ["NAMESPACE", "PLANNER", "STOP", "TREE", "BUILTINS", "TABLES", "Ctx", "recovering_pack", "policy_name", "policy"]
