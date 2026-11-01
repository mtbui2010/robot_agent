# robot_agent/robot_agent/core/planning/backends/llm_direct.py
"""Direct-LLM planner backend: one LLM call turns the command into a ``skill::input`` program.

The prompt is built by code each time it plans (``core/planning/direct_prompt.py``,
docs/direct_prompt.md): the robot and its skills from the manifest, the ENV places, the robot's
``planner.notes`` and the examples most similar to the command. The open-loop direct path of
``UnifiedAgent.run`` asks through the same :func:`direct_prompt.plan_answer`.

LLM imports are kept lazy (inside methods) so this module imports cleanly in
environments without the provider SDKs installed.
"""

from __future__ import annotations

from typing import Any, Optional
from robot_agent.spoken import SpokenError, split_skill, split_value


class LlmDirectBackend:
    """Planner backend: the direct planner (dashboard choice ``direct``)."""

    uses_direct_prompt = True       # plans with core/planning/direct_prompt.py (docs/direct_prompt.md)

    def __init__(self, llm_cfg: dict, robot_pkg: str, live_path: Optional[str] = None) -> None:
        self._llm_cfg = llm_cfg or {}
        self._robot_pkg = robot_pkg
        self._live_path = live_path          # None: the runtime's planner memory
        self._view = None

    def _robot(self):
        if self._view is None:
            from robot_agent.core.planning.robot import RobotView
            self._view = RobotView.load(self._robot_pkg)
        return self._view

    # ── core: the raw `skill::inputs` program text ─────────────────────
    def _plan_string(self, task: str) -> str:
        # Lazy import — the LLM backends pull in optional SDKs.
        from robot_agent.connect.llm import init_llm_client
        from robot_agent.core.planning.direct_prompt import plan_answer

        llm = init_llm_client(cfg=self._llm_cfg)
        kw = {} if self._live_path is None else {"live_path": self._live_path}
        return plan_answer(llm, self._robot(), task, **kw)

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
                # 이동->move::식탁 앞->table_top: the mapper, verifier and world
                # state work with the real names; the spoken ones go in "say"
                # for the narration. A k=v argument list is left whole (the
                # skill registry splits its values).
                say: dict[str, str] = {}
                try:
                    said, action = split_skill(action)
                    if said:
                        say["action"] = said
                    if "=" not in inputs and "->" in inputs:
                        said, inputs = split_value(inputs)
                        say["object"] = said.split(">>")[0]
                except SpokenError:
                    pass                    # left as written; the skill call reports it
                step: dict[str, Any] = {
                    "action": action,
                    "object": inputs,
                    "reason": el.strip(),
                }
                if say:
                    step["say"] = say
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
        # obs / visible_objects are unused: the direct planner plans from the command and the
        # robot's prompt (manifest, places, examples). They are accepted to satisfy the interface.
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
