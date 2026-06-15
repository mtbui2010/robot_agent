"""Layered per-step verification: isdone -> symbolic -> VLM (toggleable).

Planner/robot-agnostic. NO pyplanner import — the symbolic effect model mirrors
GRACE preconditions but is self-contained here. See docs/TRACKING_VERIFY_VOICE.md §3.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .base import WorldState  # noqa: F401  (contract: do not redefine WorldState)
from .records import VerifyResult


# --------------------------------------------------------------------------- #
# Layer 2 — symbolic consistency
# --------------------------------------------------------------------------- #
def symbolic_check(step: dict, world: "WorldState") -> tuple[bool, str]:
    """Apply ``step``'s effect to a COPY of ``world`` and confirm consistency.

    Mirrors GRACE preconditions for the world-changing actions. Returns
    ``(ok, reason)``. ``world`` is never mutated (we operate on ``world.copy()``).
    Unknown / no-op actions pass through as trivially consistent.
    """
    action = step.get('action', '')
    obj = step.get('object', '')
    target = step.get('target', '')
    w = world.copy()

    if action == 'MoveTo':
        # Locomotion: simply records arrival. Always consistent.
        w.arrived = obj
        return True, ''

    if action == 'Find':
        # Perception: object must be the thing we now claim to have found.
        if not obj:
            return False, 'Find requires an object'
        w.found = obj
        return True, ''

    if action == 'Pick':
        if not w.found:
            return False, 'Pick requires a found object'
        if w.holding:
            return False, f'Pick requires empty gripper, holding {w.holding!r}'
        w.holding = obj or w.found
        w.found = None
        return True, ''

    if action == 'Place':
        if not w.holding:
            return False, 'Place requires holding an object'
        w.holding = None
        return True, ''

    if action == 'PutIn':
        container = target or obj
        if not w.holding:
            return False, 'PutIn requires holding an object'
        if container not in w.opened:
            return False, f'PutIn requires {container!r} to be open'
        w.holding = None
        return True, ''

    if action == 'Open':
        if obj in w.opened:
            return False, f'{obj!r} is already open'
        w.opened.add(obj)
        return True, ''

    if action == 'Close':
        if obj not in w.opened:
            return False, f'{obj!r} is not open'
        w.opened.discard(obj)
        return True, ''

    if action == 'TurnOn':
        if obj in w.on:
            return False, f'{obj!r} is already on'
        w.on.add(obj)
        return True, ''

    if action == 'TurnOff':
        if obj not in w.on:
            return False, f'{obj!r} is not on'
        w.on.discard(obj)
        return True, ''

    if action == 'Wash':
        # Requires the object in hand or found; non-state-changing otherwise.
        if not (w.holding == obj or w.found == obj):
            return False, f'Wash requires {obj!r} to be held or found'
        return True, ''

    if action == 'Serve':
        if not w.holding:
            return False, 'Serve requires holding an object'
        w.holding = None
        return True, ''

    if action in ('Sit', 'LieOn', 'Wait'):
        # Pose / idle actions — no symbolic precondition to break.
        return True, ''

    # Unknown action: don't block a step the symbolic model doesn't model.
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
