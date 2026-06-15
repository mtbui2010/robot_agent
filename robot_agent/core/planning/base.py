# robot_agent/robot_agent/core/planning/base.py
"""Core data structures and the planner Protocol for the planning layer.

Defines the two robot-agnostic abstractions shared by the closed-loop
driver, the action mapper, and every planner backend:

* :class:`WorldState` -- the symbolic state the mapper maintains in
  lock-step with the executed plan (mirrors ``pyplanner.verifier``'s
  ``SymbolicState`` / ``_apply``; see ``ACTION_MAPPER_SPEC.md`` section 3).
* :class:`Planner` -- a structural ``typing.Protocol`` describing the
  ``generate_plan`` / ``replan`` surface that GRACE and any other backend
  expose (see ``ACTION_MAPPER_SPEC.md`` section 1).

PlanStep
--------
A plan is ``list[dict]``; each step is a plain dict (NOT a class), shaped::

    {
        "action": str,            # one GRACE action, CamelCase
        "object": str,            # target, CamelCase (Apple, CoffeeMachine)
        "target": str,            # optional, rarely used
        "reason": str,            # optional, ignored by the verifier
    }

GRACE action vocabulary (14 actions, see ``pyplanner/base.py`` ROBOT_ACTIONS):
    MoveTo, Find, Pick, Place, PutIn, Open, Close,
    TurnOn, TurnOff, Wash, Sit, LieOn, Serve, Wait

This module deliberately does NOT import ``pyplanner`` -- the contract is
duck-typed so the executor side stays independent of the planner library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

# A PlanStep is a plain dict; this alias documents the expected shape.
PlanStep = dict


@dataclass
class WorldState:
    """Symbolic world state, updated only from skills that actually succeeded.

    Mirrors ``pyplanner.verifier.SymbolicState`` so that, on a skill failure,
    the mapper can hand GRACE the exact recovered state for suffix repair
    (``ACTION_MAPPER_SPEC.md`` section 3). It is intentionally a self-contained
    dataclass -- no ``pyplanner`` import.

    Attributes
    ----------
    arrived:
        Name of the location the robot is currently at (``None`` if unknown).
    found:
        Name of the object most recently located and not yet picked.
    holding:
        Name of the object currently grasped (``None`` if the gripper is empty).
    opened:
        Set of container names currently open.
    on:
        Set of appliance names currently switched on.
    """

    arrived: Optional[str] = None
    found: Optional[str] = None
    holding: Optional[str] = None
    opened: set[str] = field(default_factory=set)
    on: set[str] = field(default_factory=set)
    # Epoch seconds when ``holding`` was last set (None if empty). There is no
    # gripper width/force sensor on this robot, so ``holding`` is a belief; the
    # timestamp lets the UI flag a stale grasp the operator may need to correct.
    holding_since: Optional[float] = None
    # Geometric memory of the currently-``found`` object (None if nothing found).
    # Shape: {loc_3d, pose_3d, side_pose?, grasppose?, ts, robot_pose:[x,y,rz]}.
    # ``robot_pose`` is the base pose at detection time; loc_3d/pose_3d are in the
    # base frame *then*, so this goes stale once the robot moves (see
    # ``found_pose_is_stale``). Persisted for display, but never trusted blindly
    # for grasping after motion.
    found_pose: Optional[dict] = None
    # Grasp actually used to pick the currently-``holding`` object (None if the
    # hand is empty). Shape: {grasppose:[dx,dy,dz,angle,width], ts,
    # robot_pose:[x,y,rz]}. A historical record of *how/where* it was grasped —
    # not a live target (the object now moves with the gripper).
    holding_pose: Optional[dict] = None

    def copy(self) -> "WorldState":
        """Return a deep-enough copy (sets are duplicated) of this state."""
        return WorldState(
            arrived=self.arrived,
            found=self.found,
            holding=self.holding,
            opened=set(self.opened),
            on=set(self.on),
            holding_since=self.holding_since,
            found_pose=dict(self.found_pose) if self.found_pose else None,
            holding_pose=dict(self.holding_pose) if self.holding_pose else None,
        )

    def found_pose_is_stale(self, robot_xy, threshold: float = 0.25) -> bool:
        """True if the stored ``found_pose`` can no longer be trusted: there is
        no pose, or the base has moved more than ``threshold`` metres from where
        the object was detected. ``robot_xy`` is the current ``(x, y)`` (or None
        → treated as stale)."""
        fp = self.found_pose
        if not fp or not fp.get("robot_pose") or not robot_xy:
            return True
        rx, ry = fp["robot_pose"][0], fp["robot_pose"][1]
        return ((rx - robot_xy[0]) ** 2 + (ry - robot_xy[1]) ** 2) ** 0.5 > threshold

    def to_dict(self) -> dict:
        """JSON-serializable snapshot for the UI / edit endpoint (sets -> sorted lists)."""
        return {
            "arrived": self.arrived,
            "found": self.found,
            "holding": self.holding,
            "opened": sorted(self.opened),
            "on": sorted(self.on),
            "holding_since": self.holding_since,
            "found_pose": self.found_pose,
            "holding_pose": self.holding_pose,
        }

    def update_from_dict(self, d: dict) -> None:
        """Apply an operator edit in place (only the keys present are touched).

        ``opened``/``on`` accept any iterable and are coerced to sets. Editing
        ``holding`` also refreshes ``holding_since`` so the staleness clock
        restarts from the correction (cleared to None when the hand is emptied).
        """
        if "arrived" in d:
            self.arrived = d["arrived"] or None
        if "found" in d:
            new_found = d["found"] or None
            # Clearing/changing the found object invalidates its cached pose
            # unless the caller supplies a fresh one in the same edit.
            if new_found != self.found and "found_pose" not in d:
                self.found_pose = None
            self.found = new_found
        if "found_pose" in d:
            self.found_pose = d["found_pose"] or None
        if "holding" in d:
            new_holding = d["holding"] or None
            if new_holding != self.holding:
                self.holding_since = d.get("holding_since")
                # Releasing/changing the held object drops its grasp record
                # unless the caller supplies a fresh one in the same edit.
                if "holding_pose" not in d:
                    self.holding_pose = None
            self.holding = new_holding
        if "holding_pose" in d:
            self.holding_pose = d["holding_pose"] or None
        if "opened" in d:
            self.opened = set(d["opened"] or [])
        if "on" in d:
            self.on = set(d["on"] or [])
        if "holding_since" in d and "holding" not in d:
            self.holding_since = d["holding_since"]

    def as_text(self) -> str:
        """Render a compact, human-readable one-line summary of the state.

        Useful for logging and for feeding a textual state snapshot into an
        observation string.
        """
        opened = ", ".join(sorted(self.opened)) if self.opened else "-"
        on = ", ".join(sorted(self.on)) if self.on else "-"
        return (
            f"arrived={self.arrived or '-'} "
            f"found={self.found or '-'} "
            f"holding={self.holding or '-'} "
            f"opened=[{opened}] "
            f"on=[{on}]"
        )


@runtime_checkable
class Planner(Protocol):
    """Structural interface every planner backend must satisfy.

    Both methods return ``(steps, metrics)`` where ``steps`` is a list of
    PlanStep dicts and ``metrics`` is an arbitrary dict (may be empty ``{}``).
    See ``ACTION_MAPPER_SPEC.md`` section 1 and ``pyplanner/base.py``'s
    ``BasePlanner`` for the reference signatures.
    """

    def generate_plan(
        self,
        task: str,
        obs: str,
        visible_objects: list[str],
    ) -> tuple[list[dict], dict]:
        """Produce a full plan for ``task`` given the current observation."""
        ...

    def replan(
        self,
        task: str,
        completed: list[dict],
        failed_step: dict,
        failure_reason: str,
        obs: str,
        visible_objects: list[str],
    ) -> tuple[list[dict], dict]:
        """Produce a suffix plan recovering from ``failed_step``.

        ``completed`` is the prefix already executed successfully; the returned
        steps are spliced after it by the driver.
        """
        ...
