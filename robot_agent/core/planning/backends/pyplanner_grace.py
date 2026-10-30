# robot_agent/robot_agent/core/planning/backends/pyplanner_grace.py
"""GRACE planner backend — adapts embodiplan's GRACEPlanner to the Planner
interface.

``embodiplan`` is an OPTIONAL dependency: it is imported lazily so this module
imports cleanly even when embodiplan is absent. The first time a GraceBackend is
constructed without embodiplan installed, a clear RuntimeError is raised.

GRACE already returns steps in the canonical PlanStep shape
(``{"action", "object", "target"?, "reason"?}``), so generate_plan / replan
delegate directly and only coerce the returned PlanMetrics dataclass to a plain
dict for transport across the Planner boundary.

GraceBackend also offers the optional planner capabilities of ``base.py``
(``docs/planners.md``) on top of embodiplan's public SAGE API: ``SubgoalRepair``
(``blocks`` / ``join_blocks`` / ``repair_block`` = SAGE's ``last_blocks`` /
``flatten`` / ``edit_block`` with its memory examples), ``ProgressReporting``
(``progress``) and ``EpisodeMemory`` (``record_episode``). This is the only
place that knows those SAGE names.
"""

from __future__ import annotations

import dataclasses
from typing import Any


# Kwargs that embodiplan's SAGEPlanner (alias GRACEPlanner, registry name "GRACE")
# accepts; see embodiplan/sage.py::SAGEPlanner.__init__.
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
    # the robot's body (manifest capabilities: hands, find_reaches): SAGE's symbolic gate
    # then plans e.g. the MoveTo to a drawer before its Open
    "capabilities",
)


def _metrics_to_dict(metrics: Any) -> dict:
    """Best-effort coercion of an embodiplan PlanMetrics into a plain dict.

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


def _observe_llm(embodiplan) -> None:
    """Route embodiplan's LLM calls through ``robot_agent.observe.llm.call``:
    recorded on the current run (prompt messages, model, seed, temperature,
    latency), served from the record during a replay.

    embodiplan has no public way to inject a client: every planner builds its
    own public ``embodiplan.LLMBackend`` and calls ``LLMBackend.chat(messages,
    temperature)``. That public method is wrapped once, at the class, here (the
    only module that imports embodiplan). Calls outside a recorded run or a
    replay go straight through, unchanged.
    """
    backend = getattr(embodiplan, "LLMBackend", None)
    chat = getattr(backend, "chat", None)
    if chat is None or getattr(chat, "_robot_agent_observed", False):
        return

    def observed_chat(self, messages, temperature=None):
        from robot_agent.observe.llm import call
        temp = temperature if temperature is not None else getattr(self, "temperature", None)
        return call(
            "embodiplan",
            lambda: chat(self, messages, temperature),
            {"messages": messages, "temperature": temp},
            model=getattr(self, "model", None),
            backend=getattr(self, "provider", None),
            temperature=temp,
            seed=getattr(self, "seed", None),
            as_tuple=True,
        )

    observed_chat._robot_agent_observed = True
    observed_chat.__wrapped__ = chat
    backend.chat = observed_chat


def available() -> bool:
    """True when embodiplan (the GRACE / SAGE planner) can be imported here."""
    try:
        import embodiplan  # noqa: F401  optional dependency
    except Exception:
        return False
    return True


class GraceBackend:
    """Planner backend backed by embodiplan's GRACEPlanner."""

    _examples: tuple | None = None   # (task, SAGE memory examples block) for repair_block

    @classmethod
    def wrap(cls, planner) -> "GraceBackend":
        """A GraceBackend around an already built SAGE / GRACE planner (any object
        with embodiplan's public planner API, e.g. a scripted SAGEPlanner in tests)."""
        b = cls.__new__(cls)
        b._p = planner
        return b

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
            import embodiplan  # optional dependency — imported lazily
        except ImportError as e:  # pragma: no cover - depends on install
            raise RuntimeError(
                "pip install -e embodiplan to use the GRACE planner"
            ) from e
        _observe_llm(embodiplan)

        # Assemble the candidate kwargs, then keep only those embodiplan accepts.
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

        # embodiplan.get("GRACE", ...) routes through the registry to GRACEPlanner.
        self._p = embodiplan.get("GRACE", **selected)

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

    # ── Optional capability: SubgoalRepair (SAGE's sub-goal-local Editing) ──
    def blocks(self) -> list | None:
        """SAGE's sub-goal blocks of the last generate_plan (None when this planner
        has no editing surface). Starts a new repair session (examples re-read)."""
        self._examples = None
        if not callable(getattr(self._p, "edit_block", None)):
            return None
        return getattr(self._p, "last_blocks", None) or None

    def join_blocks(self, blocks: list) -> list[dict]:
        """SAGE's ``flatten``: the blocks' steps with the redundant ones dropped."""
        from embodiplan.sage import flatten
        return flatten(blocks)

    def repair_block(self, block, feedback: str, *, task: str, state=None, obs: str = "") -> list[dict]:
        """SAGEPlanner.edit_block with the memory examples for ``task``."""
        if self._examples is None or self._examples[0] != task:
            self._examples = (task, self._p.retrieve_examples(task)[1])
        return self._p.edit_block(block, feedback, task=task, state=state, obs=obs,
                                  examples_block=self._examples[1])

    # ── Optional capability: ProgressReporting ───────────────────────
    @property
    def progress(self):
        """SAGE's live progress hook (decompose / expand / refine)."""
        return getattr(self._p, "progress", None)

    @progress.setter
    def progress(self, fn) -> None:
        if hasattr(self._p, "progress"):
            self._p.progress = fn

    # ── Optional passthrough ─────────────────────────────────────────
    def record_episode(self, task: str, steps: list[dict]) -> None:
        """Record a successful episode into GRACE's live memory, if supported."""
        rec = getattr(self._p, "record_episode", None)
        if callable(rec):
            rec(task=task, plan=steps, success=True)
