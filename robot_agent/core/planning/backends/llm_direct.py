# robot_agent/robot_agent/core/planning/backends/llm_direct.py
"""Direct-LLM planner backend — wraps the legacy open-loop planning flow
(``UnifiedAgent.run``) behind the Planner interface so it is selectable as a
drop-in alternative to GRACE.

This reproduces exactly what unified_agent.py does today:
    1. init_llm_client(cfg=llm_cfg)
    2. try the structured guide (guide_struct + llm.chat(prompt, guide, format)),
       falling back to the freeform guide (guide + llm.chat_guide)
    3. reconstruct via pyconnect.ros.node_taskmanager.recontruct_plan
    4. parse the ``action::inputs && …`` plan string into PlanStep dicts.

All ``pyconnect`` imports are kept lazy (inside methods) because pyconnect is a
heavy ROS-adjacent dependency and the module must import without it.
"""

from __future__ import annotations

import importlib
from typing import Any


class LlmDirectBackend:
    """Planner backend that wraps the existing direct-LLM planning behaviour."""

    def __init__(self, llm_cfg: dict, robot_pkg: str) -> None:
        self._llm_cfg = llm_cfg or {}
        self._robot_pkg = robot_pkg

    # ── core: produce the raw `action::inputs && …` plan string ───────
    def _plan_string(self, task: str) -> str:
        # Lazy imports — pyconnect is heavy / ROS-adjacent.
        from pyconnect.utils import init_llm_client

        llm = init_llm_client(cfg=self._llm_cfg)

        # Active versioned guide (UI-editable), else the robot's guide modules —
        # exactly as UnifiedAgent.run resolves it.
        from robot_agent.core.guide_manager import resolve_guide
        guide, fmt = resolve_guide(self._robot_pkg)
        try:
            if fmt is not None:
                plan_raw = llm.chat(prompt=task, guide=guide, format=fmt)
            else:
                plan_raw = llm.chat_guide(prompt=task, guide=guide, reuse=False)
        except Exception:
            plan_raw = llm.chat_guide(prompt=task, guide=guide, reuse=False)

        from pyconnect.ros.node_taskmanager import recontruct_plan

        try:
            plan_dict = eval(str(plan_raw))  # noqa: S307 - mirrors legacy behaviour
            plan = recontruct_plan(plan_dict)
        except Exception:
            plan = str(plan_raw)

        return plan if isinstance(plan, str) else str(plan)

    # ── parse `action::inputs && …` → list[PlanStep] ──────────────────
    @staticmethod
    def _parse_steps(plan: str) -> list[dict]:
        """Convert the legacy plan string into canonical PlanStep dicts.

        Lines are separated by '\\n'; items within a line by '&&' (a parallel
        group). Each item is ``action::inputs``. We flatten parallel groups into
        sequential steps, tagging each emitted step with ``_parallel_group`` so a
        consumer that cares about concurrency can reconstruct it. The raw
        ``action::inputs`` text is preserved in ``reason``.
        """
        steps: list[dict] = []
        group_idx = 0
        for line in plan.replace("\\n", "\n").split("\n"):
            line = line.strip()
            if "::" not in line:
                continue
            items = [el for el in line.split("&&") if len(el.split("::")) == 2]
            if not items:
                continue
            parallel = len(items) > 1
            for el in items:
                action, inputs = (p.strip() for p in el.split("::", 1))
                step: dict[str, Any] = {
                    "action": action,
                    "object": inputs,
                    "reason": el.strip(),
                }
                if parallel:
                    step["_parallel_group"] = group_idx
                steps.append(step)
            if parallel:
                group_idx += 1
        return steps

    # ── Planner interface ─────────────────────────────────────────────
    def generate_plan(
        self,
        task: str,
        obs: str,
        visible_objects: list[str],
    ) -> tuple[list[dict], dict]:
        # obs / visible_objects are unused: the legacy flow plans purely from the
        # NL task + the robot's guide. They are accepted to satisfy the interface.
        plan = self._plan_string(task)
        steps = self._parse_steps(plan)
        return steps, {"raw": plan}

    def replan(
        self,
        task: str,
        completed: list[dict],
        failed_step: dict,
        failure_reason: str,
        obs: str,
        visible_objects: list[str],
    ) -> tuple[list[dict], dict]:
        # NOTE: the direct-LLM flow has no true local/suffix replan. We naively
        # re-run a full generate_plan with the failure context appended to the
        # task and let the LLM re-plan from scratch.
        fs = f"{failed_step.get('action', '')} {failed_step.get('object', '')}".strip()
        augmented = (
            f"{task}\n"
            f"(The previous attempt failed at step '{fs}': {failure_reason}. "
            f"Produce a corrected full plan.)"
        )
        return self.generate_plan(augmented, obs, visible_objects)
