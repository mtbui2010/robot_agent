"""Which runs the planner's live memory learns from (SAGE_v2 report P0.8).

A planner with a retrieval memory (SAGE) copies its past plans into new ones, so a
false success in memory is repeated. A run is recorded only when its goals were
**seen** to hold after the task, not when the belief says so:

* every ``on`` / ``in`` goal has a goal check (``AssistLoop._observe_goals``) whose
  latest verdict is ``ok: True`` (a goal that could not be looked at, e.g. inside a
  closed drawer, is not verified);
* a state goal (``open`` / ``close``) has no sensor: its latest check must not be
  ``ok: False`` (the skill's own verify layers stand for it);
* no goal check at all (``goal_check=belief``) is not verified.

The planner applies its own gate on top (embodiplan: the plan passes the action
model). ``ROBOT_AGENT_MEMORY_RECORD`` (option ``memory_record``) picks the policy:
``observed`` (default), ``belief`` (the old rule: any success, also the plain
ClosedLoop's), ``off`` (never record). See docs/memory.md.
"""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

RECORD_MODES = ("observed", "belief", "off")
_OBSERVABLE = ("on", "in")


def record_mode(value: Optional[str] = None) -> str:
    """The recording policy: ``value`` (a loop option), else ``$ROBOT_AGENT_MEMORY_RECORD``,
    else ``observed``. An unknown value is ``observed``."""
    v = (value or os.environ.get("ROBOT_AGENT_MEMORY_RECORD") or "observed").strip().lower()
    return v if v in RECORD_MODES else "observed"


def goal_key(g: dict) -> str:
    """The key AssistLoop's goal checks use for a goal (``"on apple dining table@kitchen"``)."""
    return f"{g.get('type')} {g.get('object')} {g.get('to', '') or ''}".strip()


def goals_verified(goals: Iterable[dict], goal_checks: Iterable[dict]) -> tuple:
    """``(ok, why)``: were the run's goals seen to hold? The latest check of each goal counts
    (a goal seen missing, then repaired and seen again, is verified)."""
    goals = list(goals or [])
    if not goals:
        return False, "the run has no goals to check"
    latest = {}
    for c in goal_checks or []:
        latest[c.get("goal")] = c
    for g in goals:
        c = latest.get(goal_key(g))
        if c is None:
            return False, f"goal {goal_key(g)!r} was not looked at"
        if c.get("ok") is False:
            return False, f"goal {goal_key(g)!r} was seen not to hold"
        if g.get("type") in _OBSERVABLE and c.get("ok") is not True:
            return False, f"goal {goal_key(g)!r} could not be seen ({c.get('evidence')})"
    return True, ""


def record(planner: Any, task: str, completed: list, meta: Optional[dict] = None) -> bool:
    """``planner.record_episode(task, completed[, meta=])`` when the planner has it (best-effort:
    a memory error never fails a run). True when the planner recorded it."""
    fn = getattr(planner, "record_episode", None)
    if not callable(fn):
        return False
    try:
        try:
            takes_meta = "meta" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            takes_meta = False
        out = fn(task, completed, meta=meta) if takes_meta and meta else fn(task, completed)
        return out is not False
    except Exception as e:                      # never fail a finished run on its memory
        log.warning("episode not recorded (%s): %r", e, task)
        return False


def record_if_verified(planner: Any, task: str, completed: list, goals: Iterable[dict],
                       goal_checks: Iterable[dict], *, mode: Optional[str] = None,
                       run_id: Optional[str] = None) -> tuple:
    """Record a successful run under the policy (``record_mode(mode)``). Returns
    ``(recorded, why_not)``."""
    m = record_mode(mode)
    if m == "off":
        return False, "memory recording is off"
    meta = {"evidence": "goal_observation" if m == "observed" else "belief"}
    if run_id:
        meta["run_id"] = run_id
    if m == "observed":
        ok, why = goals_verified(goals, goal_checks)
        if not ok:
            log.info("episode not recorded: %s: %r", why, task)
            return False, why
    if not record(planner, task, completed, meta):
        return False, "the planner did not record it"
    return True, ""
