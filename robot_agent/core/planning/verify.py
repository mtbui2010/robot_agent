"""Layered per-step verification: isdone -> symbolic effects -> VLM (toggleable) -> sensors.

Planner/robot-agnostic. ``symbolic_check`` (the pre-step guard) decides through the shared action
model (``.action_model`` -> ``embodiplan.action_model``), the same rules the plan checker and SAGE's
gate use. After a skill reported success, preconditions are not checked again: the verifier
applies the step's effects and runs the skill's VerifySpecs from the robot's manifest (a missing
sensor gives an UNKNOWN layer, not a silent pass). See docs/TRACKING_VERIFY_VOICE.md §3,
docs/action_model_divergence.md and docs/contract.md.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Optional

from robot_agent.contract import SkillResult

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
# Post-step symbolic layer: the declared effects hold (never the preconditions)
# --------------------------------------------------------------------------- #
_PRED = re.compile(r"^(not\s+|!)?([A-Za-z_]\w*)\s*(?:\(([^()]*)\))?$")


def _holds(world, pred: str, bind) -> Optional[bool]:
    """One manifest predicate on ``world``; None when it is not one the world model tracks."""
    m = _PRED.match(pred.strip())
    if not m:
        return None
    neg, name, arg = m.group(1), m.group(2), (m.group(3) or "").strip()
    val = None if arg in ("", "*") else bind(arg)
    if name == "hand_empty":
        r = world.holding is None
    elif name in ("arrived", "found", "holding"):
        cur = getattr(world, name, None)
        r = (cur == val) if val is not None else (cur is not None)
    elif name in ("opened", "on"):
        cur = set(getattr(world, name, ()) or ())
        r = (val in cur) if val is not None else bool(cur)
    else:
        return None
    return (not r) if neg else r


def effects_check(step: dict, world: "WorldState", effects=(), *, params: Optional[dict] = None,
                  main: str = "inputs") -> tuple[bool, str]:
    """After a skill REPORTED SUCCESS: apply the step's effects (the action model) to a copy of
    ``world`` and check the skill's declared ``effects`` predicates there. ``(ok, reason)``.

    Preconditions are not checked here: they belong to the pre-step guard and the plan gate.
    ``?<main>`` binds to the step's object (a Pick without one: the found object), any other
    ``?x`` to the skill param ``x``.
    """
    obj = (step.get("object") or "").strip() or (world.found if step.get("action") == "Pick" else "") or ""
    after = action_model.apply(step, world)

    def bind(arg):
        if not arg.startswith("?"):
            return arg
        name = arg[1:]
        return obj if name == main or not params or name not in params else params.get(name)

    bad = [e for e in effects or () if _holds(after, e, bind) is False]
    if bad:
        return False, f"{step.get('action')} {obj}: effect(s) {', '.join(bad)} do not hold after it"
    return True, ''


def _xy(pose) -> Optional[tuple]:
    """(x, y) of a sensor ``pose``: [x, y, ...], {'x','y'} or an object with .position."""
    try:
        if isinstance(pose, dict):
            return float(pose["x"]), float(pose["y"])
        pos = getattr(pose, "position", None)
        if pos is not None:
            return float(pos.x), float(pos.y)
        return float(pose[0]), float(pose[1])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Layered verifier
# --------------------------------------------------------------------------- #
class StepVerifier:
    """Run layered verification after a step: isdone -> symbolic effects -> vlm -> the skill's
    sensor VerifySpecs (manifest).

    ``robot`` (a :class:`~.robot.RobotView`) supplies the skill specs and geometry, ``sensor(skill)``
    runs a sensor skill (-> SkillResult), ``locate(key)`` gives the (x, y) of a place. Without
    ``robot`` only the first three layers run (no effects declared: the symbolic layer passes).
    """

    def __init__(
        self,
        vlm_enabled: bool,
        vlm_actions: set[str],
        vlm_hook: Optional[Callable[[dict, Any], tuple[bool, Optional[float], str, float]]] = None,
        *,
        robot: Any = None,
        sensor: Optional[Callable[[str], Any]] = None,
        locate: Optional[Callable[[str], Optional[tuple]]] = None,
    ) -> None:
        self.vlm_enabled = vlm_enabled
        self.vlm_actions = set(vlm_actions or ())
        self.vlm_hook = vlm_hook
        self.robot = robot
        self.sensor = sensor
        self.locate = locate

    def verify(
        self,
        step: dict,
        skill_result,
        world: "WorldState",
        node: Any,
        params: Optional[dict] = None,
    ) -> list[VerifyResult]:
        out: list[VerifyResult] = []

        # ── Layer 1: skill isdone (always, cheapest) ──────────────────────────
        if isinstance(skill_result, SkillResult):
            ok1, msg = skill_result.ok, skill_result.msg
        else:
            ok1, msg = bool(skill_result.get('isdone')), skill_result.get('msg', '')
        out.append(VerifyResult('isdone', ok1, msg))
        if not ok1:
            return out  # short-circuit: physical failure → caller replans

        # ── Layer 2: symbolic -- the effects hold (0 token; no precondition check) ──
        spec = self.robot.spec(step.get('action', '')) if self.robot is not None else None
        main = (spec.required_params[:1] or ['inputs'])[0] if spec is not None else 'inputs'
        ok2, why = effects_check(step, world, spec.effects if spec is not None else (), params=params, main=main)
        out.append(VerifyResult('symbolic', ok2, why))

        # ── Layer 3: VLM look-and-confirm (toggleable, only for some actions) ──
        vlm_ran = False
        if self.vlm_enabled and step.get('action') in self.vlm_actions and self.vlm_hook:
            vlm_ran = True
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

        # ── Layer 4: the skill's sensor VerifySpecs; none declared = UNKNOWN ──
        if self.robot is not None:
            out.extend(self._sensors(step, spec, main, params, vlm_ran))
        return out

    def _sensors(self, step, spec, main, params, vlm_ran) -> list[VerifyResult]:
        robot, action = self.robot, step.get('action', '')
        name = spec.name if spec is not None else action
        specs = [v for v in (spec.verify if spec is not None else []) if v.kind == 'sensor' and v.skill]
        if not specs:
            return [] if vlm_ran else [VerifyResult('sensor', True, robot.text('verify_unknown', skill=name),
                                                    unknown=True)]
        out = []
        for v in specs:
            try:
                r = self.sensor(v.skill) if self.sensor is not None else None
                if r is None:
                    raise RuntimeError('no sensor runner')
            except Exception as e:  # noqa: BLE001
                out.append(VerifyResult('sensor', True, robot.text('sensor_unknown', sensor=v.skill, why=e),
                                        unknown=True))
                continue
            data = r.data if isinstance(r, SkillResult) else dict(r or {})
            ok = r.ok if isinstance(r, SkillResult) else bool(data.get('isdone'))
            if 'pose' in data:
                out.append(self._at_target(step, v.skill, data['pose'], (params or {}).get(main)))
            elif ok == v.expected:
                out.append(VerifyResult('sensor', True, f'{v.skill}: ok'))
            else:
                key = f'verify_failed.{action}'
                out.append(VerifyResult('sensor', False, robot.text(key if robot.has_text(key) else 'verify_failed',
                                                                    skill=name, sensor=v.skill)))
        return out

    def _at_target(self, step, sensor, pose, key) -> VerifyResult:
        """A pose sensor: the base within ``geometry.arrive_tol_m`` of the target's ENV loc."""
        o = step.get('object') or key or ''
        xy = _xy(pose)
        loc = self.locate(key or o) if self.locate is not None else None
        if xy is None or loc is None:
            return VerifyResult('localization', True, self.robot.text('sensor_unknown', sensor=sensor,
                                                                      why=f'no pose / no loc for {o}'), unknown=True)
        d = math.hypot(xy[0] - loc[0], xy[1] - loc[1])
        if d > self.robot.geometry.arrive_tol_m:
            return VerifyResult('localization', False, f'stopped {d:.2f} m from {o}')
        return VerifyResult('localization', True, f'{d:.2f} m from {o}')


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
