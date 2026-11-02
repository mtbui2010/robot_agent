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

    # ── parse `skill::inputs && …` → list[PlanStep] ───────────────────
    @staticmethod
    def _parse_steps(plan: str, actions: Optional[dict] = None) -> list[dict]:
        """The program as PlanStep dicts, one per ``skill::inputs`` item (``concrete.parse_item``).

        Lines are separated by '\\n'; items within a line by '&&' (a parallel group), flattened into
        sequential steps tagged ``_parallel_group``. Each step keeps the robot's call (``skill``,
        ``params`` read as Structured mode reads them) and, from ``actions`` (``{skill: action}``,
        ``concrete.skill_actions`` of the robot), the abstract ``action`` the world model, the
        verifiers and the guard read (``""`` for a skill with no plan action). The raw item is in
        ``reason``. Without ``actions`` the old shape (``action`` = the skill name) comes back.
        """
        from robot_agent.core.planning.concrete import parse_item
        steps: list[dict] = []
        group_idx = 0
        for line in plan.replace("\\n", "\n").split("\n"):
            line = line.strip()
            if "::" not in line:
                continue
            items = [st for st in (parse_item(el, actions) for el in line.split("&&")) if st is not None]
            if not items:
                continue
            for step in items:
                if len(items) > 1:
                    step["_parallel_group"] = group_idx
                steps.append(step)
            if len(items) > 1:
                group_idx += 1
        return steps

    def _steps(self, plan: str) -> list[dict]:
        from robot_agent.core.planning.concrete import into_containers, skill_actions
        view = self._robot()
        try:
            from robot_agent.skill_configs import ENV as env
        except Exception:
            env = {}
        return into_containers(self._parse_steps(plan, skill_actions(view)), view, env)

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
        return self._steps(plan), {"raw": plan}

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
        # the failed step as the program wrote it (``pick::apple``), else its action and object
        fs = (failed_step.get("reason") if failed_step.get("skill") else "") or \
            f"{failed_step.get('action', '')} {failed_step.get('object', '')}".strip()
        augmented = (
            f"{task}\n"
            f"(The previous attempt failed at step '{fs}': {failure_reason}. "
            f"Produce a corrected full plan.)"
        )
        return self.generate_plan(augmented, obs, visible_objects)
