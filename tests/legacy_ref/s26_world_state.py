"""Reference copy for Step S26: the belief and the action model wrapper as they were at commit 9f1786d (the tip of
generalize-core before S26), copied verbatim: ``WorldState`` from robot_agent/core/planning/base.py lines 48-243 and
``check`` / ``apply`` from robot_agent/core/planning/action_model.py lines 30-37 (with ``_model`` of lines 18-27).
Never edited: it is the old behaviour that tests/test_world_facts.py and tests/test_pack_action_rules.py compare with."""

from __future__ import annotations

import copy as _copy
import functools
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WorldState:
    """Symbolic world state, updated only from skills that actually succeeded.

    Mirrors ``pyplanner.verifier.SymbolicState`` so that, on a skill failure,
    the mapper can hand SAGE the exact recovered state for suffix repair
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
    # Where each field's value came from (docs/guard.md): ``{field: {source, ts, value, ...}}`` with
    # source ``sensor`` (read off the robot at ``ts``) / ``operator`` (PUT /agent/world) / ``belief``,
    # and ``opened`` per container (``containers: {key: {source, ts, open}}``). An entry counts only
    # while the field still has the value it was recorded for; any other value is a belief (a
    # skill's effect, a load from disk). Written by :meth:`mark`, shown by :meth:`provenance_view`.
    provenance: dict = field(default_factory=dict)

    # ── provenance ─────────────────────────────────────────────────────
    _PROV_FIELDS = ("arrived", "found", "holding", "opened", "on")

    def _value(self, key: str):
        v = getattr(self, key)
        return sorted(v) if isinstance(v, set) else v

    def mark(self, key: str, source: str, ts: Optional[float] = None, **extra) -> None:
        """Record that ``key``'s current value comes from ``source`` (sensor / operator / belief / unknown)."""
        old = self.provenance.get(key) or {}
        e = {"source": source, "ts": time.time() if ts is None else ts, "value": self._value(key), **extra}
        if key == "opened" and old.get("containers"):
            e["containers"] = dict(old["containers"])
        self.provenance[key] = e

    def mark_container(self, key: str, is_open: bool, source: str = "sensor", ts: Optional[float] = None) -> None:
        """Record a container's open / closed state as read (``source``) at ``ts``."""
        op = self.provenance.setdefault("opened", {"source": "belief", "ts": None, "value": self._value("opened")})
        op.setdefault("containers", {})[key] = {"source": source, "ts": time.time() if ts is None else ts,
                                                "open": bool(is_open)}

    def mark_edited(self, keys, source: str = "operator") -> None:
        """An edit of ``keys`` (the dashboard's PUT /agent/world): their values are the operator's."""
        for k in keys:
            if k in self._PROV_FIELDS:
                self.mark(k, source)
            elif k == "holding_since" and self.holding:
                self.mark("holding", source)

    def provenance_of(self, key: str) -> dict:
        """``{source, ts?, ...}`` of ``key``'s current value (``belief`` when nothing recorded it)."""
        e = self.provenance.get(key)
        if not e or e.get("value") != self._value(key):
            return {"source": "belief", "ts": None}
        return {k: v for k, v in e.items() if k not in ("value", "containers")}

    def container_provenance(self, key: str) -> dict:
        """A container's ``{source, ts, open}``; ``belief`` when no sensor read agrees with the belief."""
        c = ((self.provenance.get("opened") or {}).get("containers") or {}).get(key)
        if not c or bool(c.get("open")) != (key in self.opened):
            return {"source": "belief", "ts": None, "open": key in self.opened}
        return dict(c)

    def provenance_view(self) -> dict:
        """JSON view per field (sensor-confirmed or not); ``opened`` lists its containers and the
        open ones no sensor confirmed (``unconfirmed``)."""
        out = {k: self.provenance_of(k) for k in self._PROV_FIELDS}
        conts = ((self.provenance.get("opened") or {}).get("containers") or {})
        keys = sorted(set(conts) | set(self.opened))
        out["opened"]["containers"] = {k: self.container_provenance(k) for k in keys}
        out["opened"]["unconfirmed"] = [k for k in sorted(self.opened)
                                        if out["opened"]["containers"][k]["source"] != "sensor"]
        return out

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
            provenance=_copy.deepcopy(self.provenance),
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
            # added in SAGE-v2 (docs/guard.md); old clients ignore it, as_text() (the planner's) has none
            "provenance": self.provenance_view(),
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


@functools.lru_cache(maxsize=None)
def _model():
    try:
        from embodiplan import action_model as am
    except ImportError as e:  # pragma: no cover - depends on install
        raise ImportError(
            "robot_agent needs embodiplan>=0.2.1,<0.3 with embodiplan.action_model "
            "(pip install -e ../embodiplan)"
        ) from e
    return am


def check(step, world, **kw):
    """embodiplan.action_model.check."""
    return _model().check(step, world, **kw)


def apply(step, world, **kw):
    """embodiplan.action_model.apply."""
    return _model().apply(step, world, **kw)
