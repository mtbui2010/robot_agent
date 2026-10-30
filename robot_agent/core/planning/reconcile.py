"""The belief checked against the robot's sensors (SAGE-v2 AssistLoop; see docs/guard.md).

Robot-agnostic. What the robot can read comes through its adapter and manifest:

* **pose** -> ``arrived``: the adapter's optional ``pose(node)`` (``{x, y, yaw?, ts? | age_s?}``),
  else the manifest's pose sensor (the skill that verifies a MoveTo, its result's ``pose``). The
  nearest ENV place within ``arrive_tol_m`` is where the robot is; farther than that from every
  place, ``arrived`` is None (between places). A pose that is missing or older than the manifest's
  ``geometry.pose_max_age_s`` (``sensor_health.pose_problem``) makes ``arrived`` unknown (None):
  never a believed place kept, never one invented.
* **containers** -> ``opened``: the adapter's optional ``container_states(node)`` (``{key: True |
  False | None}``). What it reads replaces the belief; what it cannot read stays a belief and is
  listed as unconfirmed.

Each field's provenance (``sensor`` with its time / ``operator`` / ``belief`` / ``unknown``) is
kept on the WorldState (``WorldState.mark``, ``provenance_view``).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .sensor_health import pose_problem

SENSOR, BELIEF, OPERATOR, UNKNOWN = "sensor", "belief", "operator", "unknown"

# steps whose precondition is where the robot stands (the per-step guard checks a non-sensor arrived)
AT_PLACE_ACTIONS = frozenset({"Find", "Pick", "Place", "PutIn", "Open", "Close"})


@dataclass
class PoseReading:
    x: Optional[float] = None
    y: Optional[float] = None
    ts: Optional[float] = None
    age_s: Optional[float] = None
    source: str = ""
    problem: Optional[str] = None             # why it cannot be used (missing / stale)

    @property
    def ok(self) -> bool:
        return self.problem is None and self.x is not None and self.y is not None


def _xy(p) -> Optional[tuple]:
    """(x, y) of a pose: [x, y, ...], {'x', 'y'}, {'pose': ...} or an object with ``.position``."""
    try:
        if isinstance(p, dict):
            if "x" in p and "y" in p:
                return float(p["x"]), float(p["y"])
            return _xy(p.get("pose"))
        pos = getattr(p, "position", None)
        if pos is not None:
            return float(pos.x), float(pos.y)
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            return float(p[0]), float(p[1])
    except (TypeError, ValueError, AttributeError):
        pass
    return None


def _adapter_pose(robot, node) -> dict:
    fn = getattr(robot.adapter, "pose", None)
    if not callable(fn):
        return {}
    try:
        d = fn(node)
        return dict(d) if isinstance(d, dict) else {}
    except Exception:
        return {}


def read_pose(robot, node: Any = None, sense: Optional[Callable] = None, now: Optional[float] = None) -> PoseReading:
    """The base pose now: the adapter's ``pose()``, else (or for what it lacks) the pose sensor."""
    now = time.time() if now is None else now
    d = _adapter_pose(robot, node)
    xy, src = _xy(d), "adapter"
    why = None
    skill = robot.pose_sensor()
    if xy is None and skill and sense is not None:
        src = skill
        try:
            res = sense(skill)
            xy = _xy((getattr(res, "data", None) or {}).get("pose")) if getattr(res, "ok", True) else None
            why = None if xy is not None else f"{skill} gave no pose ({getattr(res, 'msg', '')})"
        except Exception as e:                          # localization lost, no runtime ...
            why = f"{skill} could not be read: {e}"
    if xy is None:
        return PoseReading(source=src, problem=why or "no pose sensor")
    ts = d.get("ts") if isinstance(d.get("ts"), (int, float)) else None
    age = d.get("age_s") if isinstance(d.get("age_s"), (int, float)) else (now - ts if ts is not None else None)
    if age is None and not callable(getattr(robot.adapter, "pose", None)) and robot.health_limits().pose:
        age = robot.health(node).get("pose_age_s")       # an adapter without pose(): its health report
    stale = pose_problem({"pose_age_s": age}, robot.health_limits())
    return PoseReading(x=xy[0], y=xy[1], ts=ts if ts is not None else (now - age if age is not None else now),
                       age_s=age, source=src, problem=stale)


def nearest_place(x: float, y: float, env: dict, tol: float) -> tuple:
    """(ENV key within ``tol`` metres or None, distance to the nearest place or None, its key)."""
    best, best_d = None, None
    for k, spec in (env or {}).items():
        loc = spec.get("loc") if isinstance(spec, dict) else None
        if isinstance(loc, dict) and "x" in loc and "y" in loc:
            d = math.hypot(float(loc["x"]) - x, float(loc["y"]) - y)
            if best_d is None or d < best_d:
                best, best_d = k, d
    return (best if best_d is not None and best_d <= tol else None), best_d, best


def _pose_detail(p: PoseReading, env: dict, tol: float) -> tuple:
    at, d, near = nearest_place(p.x, p.y, env, tol)
    detail = (f"{d:.2f} m from {near}" if near else "no place with a loc") + ("" if at else f" (> {tol:.2f} m)")
    return at, detail


def reconcile_arrived(world, robot, node, sense, env: dict, tol: float) -> dict:
    """``arrived`` from a fresh pose (sensor), else unknown (None). Returns what was read."""
    p = read_pose(robot, node, sense)
    if not p.ok:
        world.arrived = None
        world.mark("arrived", UNKNOWN, why=p.problem)
        return {"pose": None, "why": p.problem, "source": p.source}
    world.arrived, detail = _pose_detail(p, env, tol)
    world.mark("arrived", SENSOR, ts=p.ts, detail=detail)
    return {"pose": [round(p.x, 3), round(p.y, 3)], "age_s": p.age_s, "detail": detail, "source": p.source}


def reconcile_containers(world, robot, node) -> dict:
    """``opened`` from the adapter's ``container_states(node)``; {} when it reads none."""
    fn = getattr(robot.adapter, "container_states", None)
    if not callable(fn):
        return {}
    try:
        states = dict(fn(node) or {})
    except Exception:
        return {}
    now, read = time.time(), {}
    for key, is_open in states.items():
        if is_open is None:
            continue
        (world.opened.add if is_open else world.opened.discard)(key)
        world.mark_container(key, bool(is_open), SENSOR, now)
        read[key] = bool(is_open)
    return read


def reconcile_sensors(world, robot, node, sense, env: dict, tol: float) -> dict:
    """Pose and containers into the belief; the report ``{arrived: {...}, containers: {...}, unconfirmed}``."""
    out = {"arrived": reconcile_arrived(world, robot, node, sense, env, tol),
           "containers": reconcile_containers(world, robot, node)}
    unconfirmed = world.provenance_view()["opened"]["unconfirmed"]
    world.mark("opened", BELIEF if unconfirmed else SENSOR)
    out["unconfirmed"] = unconfirmed
    return out


def guard_arrived(world, robot, node, sense, env: dict, tol: float, step: dict) -> Optional[dict]:
    """Before a step that acts at a place: an ``arrived`` no sensor confirmed (operator / belief) is
    checked against a fresh pose. Returns ``{old, new, source, detail}`` when it was wrong (fixed in
    the belief), None otherwise (confirmed, no fresh pose: left as it is, or not such a step)."""
    if step.get("action") not in AT_PLACE_ACTIONS:
        return None
    prov = world.provenance_of("arrived")
    if prov.get("source") == SENSOR:
        return None
    p = read_pose(robot, node, sense)
    if not p.ok:
        return None
    at, detail = _pose_detail(p, env, tol)
    old = world.arrived
    world.arrived = at
    world.mark("arrived", SENSOR, ts=p.ts, detail=detail)
    if at == old:
        return None
    return {"old": old, "new": at, "source": prov.get("source"), "detail": detail}


def confirm_arrival(world, verifies) -> None:
    """A MoveTo whose localization layer measured the base at its target: ``arrived`` is sensor-confirmed."""
    if any(getattr(v, "layer", "") == "localization" and v.ok and not getattr(v, "unknown", False)
           for v in verifies or ()):
        world.mark("arrived", SENSOR, detail="MoveTo localization check")


__all__ = ["SENSOR", "BELIEF", "OPERATOR", "UNKNOWN", "AT_PLACE_ACTIONS", "PoseReading", "read_pose",
           "nearest_place", "reconcile_arrived", "reconcile_containers", "reconcile_sensors", "guard_arrived",
           "confirm_arrival"]
