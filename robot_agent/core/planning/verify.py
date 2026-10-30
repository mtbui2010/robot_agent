"""Layered per-step verification: isdone -> symbolic -> VLM (toggleable).

Planner/robot-agnostic. The symbolic layer decides through the shared action model
(``.action_model`` -> ``embodiplan.action_model``), the same rules the plan checker and
SAGE's gate use. See docs/TRACKING_VERIFY_VOICE.md §3 and docs/action_model_divergence.md.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import action_model
from .base import WorldState  # noqa: F401  (contract: do not redefine WorldState)
from .records import VerifyResult


# --------------------------------------------------------------------------- #
# Layer 2 — symbolic consistency
# --------------------------------------------------------------------------- #
def symbolic_check(step: dict, world: "WorldState", *, capabilities=None,
                   openable=None) -> tuple[bool, str]:
    """Is ``step`` admissible in ``world`` under the action model? ``(ok, reason)``.

    ``world`` is never mutated. ``capabilities`` defaults to
    ``action_model.ROBOT_CAPABILITIES``; ``openable`` (names or predicate) enables the
    not-openable rule. Actions outside the model's vocabulary pass: the robot's own
    vocabulary is checked by the mapper / plan checker.
    """
    for v in action_model.check(step, world, openable=openable,
                                capabilities=capabilities or action_model.ROBOT_CAPABILITIES):
        if v.is_error and v.code != "unknown_action":
            return False, v.reason
    return True, ''


# --------------------------------------------------------------------------- #
# Layered verifier
# --------------------------------------------------------------------------- #
class StepVerifier:
    """Run layered verification after a step: isdone -> symbolic -> vlm."""

    def __init__(
        self,
        vlm_enabled: bool,
        vlm_actions: set[str],
        vlm_hook: Optional[Callable[[dict, Any], tuple[bool, Optional[float], str, float]]] = None,
    ) -> None:
        self.vlm_enabled = vlm_enabled
        self.vlm_actions = set(vlm_actions or ())
        self.vlm_hook = vlm_hook

    def verify(
        self,
        step: dict,
        skill_result: dict,
        world: "WorldState",
        node: Any,
    ) -> list[VerifyResult]:
        out: list[VerifyResult] = []

        # ── Layer 1: skill isdone (always, cheapest) ──────────────────────────
        ok1 = bool(skill_result.get('isdone'))
        out.append(VerifyResult('isdone', ok1, skill_result.get('msg', '')))
        if not ok1:
            return out  # short-circuit: physical failure → caller replans

        # ── Layer 2: symbolic consistency (0 token) ───────────────────────────
        ok2, why = symbolic_check(step, world)
        out.append(VerifyResult('symbolic', ok2, why))

        # ── Layer 3: VLM look-and-confirm (toggleable, only for some actions) ──
        if self.vlm_enabled and step.get('action') in self.vlm_actions and self.vlm_hook:
            try:
                ok3, conf, why3, ms = self.vlm_hook(step, node)
                out.append(
                    VerifyResult('vlm', bool(ok3), why3 or '',
                                 confidence=conf, latency_ms=float(ms or 0.0))
                )
            except Exception as exc:  # noqa: BLE001
                # Design choice: a broken/unavailable VLM hook must NEVER hard-fail
                # an otherwise-good step. We record a passing 'vlm' result noting the
                # skip so the failure is visible in logs but doesn't trigger a replan.
                out.append(
                    VerifyResult('vlm', True, f'vlm hook error (skipped): {exc}')
                )
        return out


# --------------------------------------------------------------------------- #
# Verdict policy
# --------------------------------------------------------------------------- #
def verdict(results: list[VerifyResult]) -> tuple[bool, str]:
    """Overall verdict: ok iff every layer passed; reason = first failing detail.

    With no results the step is treated as not-verified (mirrors
    ``StepRecord.verified`` requiring at least one layer to have run).
    """
    if not results:
        return False, 'no verification ran'
    for r in results:
        if not r.ok:
            return False, r.detail or f'{r.layer} failed'
    return True, ''
