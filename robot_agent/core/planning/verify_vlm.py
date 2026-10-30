"""The manifest's VLM post-check: ``verify: [{kind: vlm, prompt: ...}]`` after a skill reported success.

Planner/robot-agnostic. For a step whose skill declares ``vlm`` VerifySpecs, :class:`VlmCheck` asks
the robot's VLM (the adapter's ``vlm_ask(prompt, step=, node=)``) each prompt, after the skill said
it succeeded and before the step's effects enter the belief:

* ``yes``  -> a passing ``vlm`` layer;
* ``no``   -> a failing ``vlm`` layer: the step is not a success, its effects are not applied and
  the loop's recovery / replan takes over (``refuted``);
* no VLM, an error, an unclear answer or a confidence below ``geometry.vlm_min_conf``
  -> the observation path: the object the step acted on is looked for with the robot's Find
  (``observe(obj, node)``, Place / PutIn: the object let go of): seen -> pass, not seen -> fail,
  nothing to look for (Open / Close / Pick) or Find cannot run -> an UNKNOWN layer (never a crash, never a pass that nothing checked).

A step without ``vlm`` specs never calls the VLM. The layer is on when the run's ``vlm_verify``
says so (``ROBOT_AGENT_VLM_VERIFY`` / the ``vlm_verify`` option), else when the manifest's
``capabilities.vlm_verify`` is not false. See docs/verify.md.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .records import VerifyResult

LAYER = "vlm"
FALLBACK = "vlm_observe"
_YES = ("yes", "true", "y", "1", "ok")
_NO = ("no", "false", "n", "0")


def vlm_enabled(setting, capabilities: Optional[dict]) -> bool:
    """The run's switch: an explicit ``setting`` (bool / '1' / '0') wins, else the manifest's
    ``capabilities.vlm_verify`` (default on: a robot that declares vlm specs wants them run)."""
    if setting is not None and setting != "":
        return setting is True or str(setting).strip().lower() in _YES
    v = (capabilities or {}).get("vlm_verify", True)
    return v is True or str(v).strip().lower() in _YES


def read_answer(raw) -> tuple[Optional[bool], Optional[float], str]:
    """A VLM answer -> (verdict True / False / None = unsure, confidence, note).

    Accepts a bool, None, a string ('yes' / 'no' / anything else = unsure), a dict
    (``answer`` / ``ok``, ``confidence``, ``note``) or the legacy ``(ok, conf, note, ms)`` tuple."""
    conf, note = None, ""
    if isinstance(raw, (tuple, list)) and raw:
        conf = raw[1] if len(raw) > 1 else None
        note = str(raw[2]) if len(raw) > 2 else ""
        raw = raw[0]
    elif isinstance(raw, dict):
        conf, note = raw.get("confidence"), str(raw.get("note") or "")
        raw = raw.get("answer", raw.get("ok"))
    if isinstance(raw, bool) or raw is None:
        verdict = raw
    else:
        s = str(raw).strip().lower().rstrip(".!")
        verdict = True if s in _YES else False if s in _NO else None
        note = note or str(raw)[:80]
    try:
        conf = None if conf is None else float(conf)
    except (TypeError, ValueError):
        conf = None
    return verdict, conf, note


class VlmCheck:
    """Runs the ``vlm`` VerifySpecs of a step (see the module docstring).

    ``robot``: a :class:`~.robot.RobotView`; ``observe(obj, node) -> bool | None``: the Find
    fallback (True seen, False not seen, None could not look); ``min_conf``: answers with a lower
    confidence are unsure."""

    def __init__(self, robot: Any, enabled: bool = True, observe: Optional[Callable] = None,
                 min_conf: float = 0.0) -> None:
        self.robot, self.enabled, self.observe, self.min_conf = robot, bool(enabled), observe, float(min_conf)
        self.calls = 0                            # VLM questions asked (tests, metrics)

    def specs(self, action: str) -> list:
        if not self.enabled or self.robot is None:
            return []
        return [v for v in self.robot.verify_specs(action) if v.kind == LAYER]

    @staticmethod
    def target(step: dict, world: Any) -> str:
        """What the Find fallback looks for: the object a Place / PutIn let go of (the effects are
        not applied yet, so it is still the held one). Other actions have nothing a Find can
        confirm (an opened drawer, a held object): ''."""
        if step.get("action") in ("Place", "PutIn"):
            return getattr(world, "holding", None) or ""
        return ""

    def run(self, step: dict, world: Any, node: Any = None) -> list:
        """The vlm layer(s) of ``step``; [] when it declares none (the VLM is not called)."""
        specs = self.specs(step.get("action", ""))
        if not specs:
            return []
        obj, place = self.target(step, world), step.get("object") or ""
        what = obj or step.get("object") or ""
        out = []
        for v in specs:
            prompt = _fill(v.prompt or "Did the robot {action} the {object}?", step, what, place)
            verdict, conf, note, ms = self._ask(prompt, step, node)
            if verdict is not None and conf is not None and conf < self.min_conf:
                verdict, note = None, f"confidence {conf:.2f} < {self.min_conf:.2f}"
            if verdict is None:
                out.append(self._fallback(obj, place, node, note))
            else:
                out.append(VerifyResult(LAYER, verdict, f"{prompt} -> {'yes' if verdict else 'no'}"
                                        + (f" ({note})" if note else ""), confidence=conf, latency_ms=ms))
            if not out[-1].ok:
                break
        return out

    def _ask(self, prompt, step, node):
        ask = getattr(self.robot, "vlm_ask", None)
        if not callable(ask):
            return None, None, "no VLM on this robot", 0.0
        t0 = time.time()
        self.calls += 1
        try:
            verdict, conf, note = read_answer(ask(prompt, step=dict(step), node=node))
        except Exception as e:  # noqa: BLE001 - an unavailable VLM falls back, it never fails the run
            verdict, conf, note = None, None, f"VLM unavailable: {e}"
        return verdict, conf, note, (time.time() - t0) * 1000.0

    def _fallback(self, obj, place, node, why) -> VerifyResult:
        """VLM unsure / down: look for the object with the robot's Find."""
        seen = None
        if obj and self.observe is not None:
            try:
                seen = self.observe(obj, node)
            except Exception as e:  # noqa: BLE001
                why, seen = f"{why}; Find failed: {e}", None
        if seen is None:
            return VerifyResult(FALLBACK, True, f"unknown: {why}; nothing could look", unknown=True)
        where = f" at {place}" if place and place != obj else ""
        return VerifyResult(FALLBACK, bool(seen), f"{why}; Find {obj}{where}: "
                            + ("seen" if seen else f"the {obj} is not seen{where}"))


def _fill(prompt: str, step: dict, obj: str, place: str) -> str:
    try:
        return prompt.format(object=obj or "object", place=(place or "").replace("@", " in the "),
                             action=str(step.get("action", "")).lower())
    except (KeyError, IndexError, ValueError):
        return prompt


def refuted(results) -> Optional[VerifyResult]:
    """The vlm / observation layer that said the step did not happen, else None."""
    return next((v for v in results or () if v.layer in (LAYER, FALLBACK) and not v.ok), None)


__all__ = ["VlmCheck", "read_answer", "refuted", "vlm_enabled", "LAYER", "FALLBACK"]
