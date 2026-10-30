"""Sensor health before trusting a look or an arrival: the adapter's ``health()`` against the manifest.

Planner/robot-agnostic. ``RobotAdapter.health(node)`` reports what the robot knows about its
sensors, with these keys (any may be missing or None = not known):

* ``frame_age_s`` seconds since the last camera frame, ``fps`` its rate, ``depth`` a depth image
  is there (bool), ``pose_age_s`` seconds since the last base pose, ``ok`` the adapter's summary.

The thresholds are the robot's, in its manifest ``geometry``: ``frame_max_age_s``,
``min_fps``, ``pose_max_age_s``, ``need_depth`` (unset = not checked, so a robot without them
behaves as before). Two checks use them:

* :func:`find_precheck` before a Find: a stale / missing camera frame is a failed Find with
  FailureCode PERCEPTION_UNAVAILABLE without running the skill (the recovery policy then waits
  for health and tries again: no evidence that the object is absent);
* :func:`arrival_check` after a MoveTo reported success: a stale pose fails the localization
  layer, so the arrival is not counted (PERCEPTION_UNAVAILABLE as well).

See docs/verify.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from robot_agent.contract import FailureCode, SkillResult

from .records import VerifyResult


@dataclass(frozen=True)
class HealthLimits:
    frame_max_age_s: Optional[float] = None
    min_fps: Optional[float] = None
    pose_max_age_s: Optional[float] = None
    need_depth: bool = False

    @classmethod
    def from_geometry(cls, geometry: Any) -> "HealthLimits":
        """From a ``Geometry`` (its ``extra``) or a plain geometry dict."""
        d = dict(getattr(geometry, "extra", None) or (geometry if isinstance(geometry, dict) else {}) or {})

        def num(k):
            try:
                return None if d.get(k) is None else float(d[k])
            except (TypeError, ValueError):
                return None
        return cls(frame_max_age_s=num("frame_max_age_s"), min_fps=num("min_fps"),
                   pose_max_age_s=num("pose_max_age_s"), need_depth=bool(d.get("need_depth", False)))

    @property
    def camera(self) -> bool:
        return self.frame_max_age_s is not None or self.min_fps is not None or self.need_depth

    @property
    def pose(self) -> bool:
        return self.pose_max_age_s is not None


def _num(h: dict, k: str) -> Optional[float]:
    try:
        return None if h.get(k) is None else float(h[k])
    except (TypeError, ValueError):
        return None


def camera_problem(h: dict, lim: HealthLimits) -> Optional[str]:
    """Why the camera cannot be trusted now (worded so it classifies as perception_unavailable)."""
    age, fps = _num(h, "frame_age_s"), _num(h, "fps")
    if lim.frame_max_age_s is not None and age is not None and age > lim.frame_max_age_s:
        return f"no fresh frame: the camera frame is {age:.1f} s old (limit {lim.frame_max_age_s:.1f} s)"
    if lim.min_fps is not None and fps is not None and fps < lim.min_fps:
        return f"no fresh frame: the camera runs at {fps:.1f} fps (needs {lim.min_fps:.1f})"
    if lim.need_depth and h.get("depth") is False:
        return "no depth image from the camera"
    return None


def pose_problem(h: dict, lim: HealthLimits) -> Optional[str]:
    age = _num(h, "pose_age_s")
    if lim.pose_max_age_s is not None and age is not None and age > lim.pose_max_age_s:
        return f"no fresh pose: the base pose is {age:.1f} s old (limit {lim.pose_max_age_s:.1f} s)"
    return None


def find_precheck(robot: Any, node: Any, step: dict, enabled: bool = True) -> Optional[SkillResult]:
    """Before a Find: a failed SkillResult (PERCEPTION_UNAVAILABLE) when the camera is stale, else
    None (run the skill). The robot's health is read only when its manifest sets camera limits."""
    if not enabled or step.get("action") != "Find" or robot is None:
        return None
    lim = robot.health_limits()
    if not lim.camera:
        return None
    h = robot.health(node)
    why = camera_problem(h, lim)
    if why is None:
        return None
    return SkillResult(ok=False, code=FailureCode.PERCEPTION_UNAVAILABLE, msg=why, device="camera",
                       data={"health": h})


def arrival_check(robot: Any, node: Any, step: dict, results: list, enabled: bool = True) -> list:
    """After a MoveTo whose layers passed: a failing ``localization`` layer when the pose is stale
    (the arrival is not trusted), else []."""
    if (not enabled or step.get("action") != "MoveTo" or robot is None or not results
            or not all(v.ok for v in results)):
        return []
    lim = robot.health_limits()
    if not lim.pose:
        return []
    why = pose_problem(robot.health(node), lim)
    return [VerifyResult("localization", False, f"{why}: arrival not confirmed")] if why else []


__all__ = ["HealthLimits", "camera_problem", "pose_problem", "find_precheck", "arrival_check"]
