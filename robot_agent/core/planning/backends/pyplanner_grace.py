# robot_agent/robot_agent/core/planning/backends/pyplanner_grace.py
"""GRACE planner backend — adapts pyplanner's GRACEPlanner to the Planner
interface.

``pyplanner`` is an OPTIONAL dependency: it is imported lazily so this module
imports cleanly even when pyplanner is absent. The first time a GraceBackend is
constructed without pyplanner installed, a clear RuntimeError is raised.

GRACE already returns steps in the canonical PlanStep shape
(``{"action", "object", "target"?, "reason"?}``), so generate_plan / replan
delegate directly and only coerce the returned PlanMetrics dataclass to a plain
dict for transport across the Planner boundary.
"""

from __future__ import annotations

import dataclasses
from typing import Any


# Kwargs that pyplanner.grace() / GRACEPlanner.__init__ actually accept.
# (verified against pyplanner/__init__.py::grace and pyplanner/grace.py::GRACEPlanner)
_GRACE_KWARGS = (
    "host",
    "model",
    "provider",
    "api_key",
    "gt_path",
    "live_path",
    "safe_refine",
    "top_k",
    "max_refines",
    "use_chroma",
    "chroma_path",
)


def _metrics_to_dict(metrics: Any) -> dict:
    """Best-effort coercion of a pyplanner PlanMetrics into a plain dict.

    PlanMetrics is a dataclass and also exposes a ``to_dict()`` helper; prefer
    that, then fall back to dataclasses.asdict, then a getattr sweep.
    """
    if metrics is None:
        return {}
    if isinstance(metrics, dict):
        return metrics
    # PlanMetrics.to_dict() gives the curated, JSON-friendly view.
    to_dict = getattr(metrics, "to_dict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            if isinstance(d, dict):
                # to_dict() drops `extra`; fold it back in if present.
                extra = getattr(metrics, "extra", None)
                if isinstance(extra, dict) and "extra" not in d:
                    d["extra"] = extra
                return d
        except Exception:
            pass
    if dataclasses.is_dataclass(metrics) and not isinstance(metrics, type):
        try:
            return dataclasses.asdict(metrics)
        except Exception:
            pass
    # Last resort: scrape public, non-callable attributes.
    out: dict = {}
    for k in dir(metrics):
        if k.startswith("_"):
            continue
        try:
            v = getattr(metrics, k)
        except Exception:
            continue
        if callable(v):
            continue
        out[k] = v
    return out


class GraceBackend:
    """Planner backend backed by pyplanner's GRACEPlanner."""

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        api_key: str = "",
        gt_path: str = "",
        live_path: str = "",
        safe_refine: bool = True,
        **cfg: Any,
    ) -> None:
        try:
            import pyplanner  # optional dependency — imported lazily
        except ImportError as e:  # pragma: no cover - depends on install
            raise RuntimeError(
                "pip install -e pyplanner to use the GRACE planner"
            ) from e

        # Assemble the candidate kwargs, then keep only those pyplanner accepts.
        candidate: dict[str, Any] = {
            "host": host,
            "model": model,
            "provider": provider,
            "api_key": api_key,
            "gt_path": gt_path,
            "live_path": live_path,
            "safe_refine": safe_refine,
        }
        candidate.update(cfg)
        selected = {
            k: v
            for k, v in candidate.items()
            if k in _GRACE_KWARGS and v is not None
        }

        # pyplanner.get("GRACE", ...) routes through the registry to GRACEPlanner.
        self._p = pyplanner.get("GRACE", **selected)

    # ── Planner interface ────────────────────────────────────────────
    def generate_plan(
        self,
        task: str,
        obs: str,
        visible_objects: list[str],
    ) -> tuple[list[dict], dict]:
        steps, metrics = self._p.generate_plan(task, obs, visible_objects)
        return list(steps), _metrics_to_dict(metrics)

    def replan(
        self,
        task: str,
        completed: list[dict],
        failed_step: dict,
        failure_reason: str,
        obs: str,
        visible_objects: list[str],
    ) -> tuple[list[dict], dict]:
        steps, metrics = self._p.replan(
            task,
            completed,
            failed_step,
            failure_reason,
            obs,
            visible_objects,
        )
        return list(steps), _metrics_to_dict(metrics)

    # ── Optional passthrough ─────────────────────────────────────────
    def record_episode(self, task: str, steps: list[dict]) -> None:
        """Record a successful episode into GRACE's live memory, if supported."""
        rec = getattr(self._p, "record_episode", None)
        if callable(rec):
            rec(task=task, plan=steps, success=True)
