"""The robot as the planning loops see it: one ``RobotAdapter``, nothing named here.

Everything robot-specific the loops need -- which skill realises an action, its params, the
sensors that verify it, how far "arrived" may be, what opens, what the robot says -- comes from
the adapter that ``robot_agent.contract.load_adapter(robot_pkg)`` returns (the robot's
``configs/adapter.py``, or its manifest.yaml + grace_namemap shim). :class:`RobotView` adds the
lookups the loops share on top of it; :func:`run_skill` is the one place a skill's return value
becomes a :class:`~robot_agent.contract.SkillResult`.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from robot_agent.contract import (FailureCode, Geometry, Messages, SkillResult, SkillSpec, Unbindable,
                                  VerifySpec, classify_legacy_failure, load_adapter, policy_for)
from . import action_model

# what the core says when the robot's i18n has no phrase for a key (English, {placeholders})
CORE_TEXT = {
    "verify_failed": "{skill} reported success but the {sensor} check says otherwise",
    "verify_unknown": "unknown: {skill} declares no sensor that checks it",
    "sensor_unknown": "unknown: the {sensor} check could not run ({why})",
    "robot_description": "The robot can: {abilities}.",
}

# abstract action -> how the task-understanding prompt describes it (robots override the
# whole sentence with the i18n key "robot_description")
_ABILITY = {"MoveTo": "move to a place", "Find": "find an object", "Pick": "pick it up",
            "Place": "place it on a surface", "Open": "open / close a container",
            "PutIn": "put an object in an open container"}


class RobotView:
    """A :class:`~robot_agent.contract.RobotAdapter` plus the lookups the loops share."""

    def __init__(self, adapter: Any):
        self.adapter = adapter
        self.manifest = adapter.manifest
        self.geometry: Geometry = getattr(adapter, "geometry", None) or self.manifest.geometry_obj()
        self.i18n: Messages = getattr(adapter, "i18n", None) or Messages(language=self.manifest.language or "en")
        self.policy = policy_for(adapter)

    @classmethod
    def load(cls, robot_pkg: str) -> "RobotView":
        return cls(load_adapter(robot_pkg))

    # ── vocabulary ──────────────────────────────────────────────────────
    @property
    def skill_map(self) -> dict:
        sm = getattr(self.adapter, "skill_map", None)
        if isinstance(sm, dict):
            return dict(sm)
        return {s.action: s.name for s in self.manifest.skills}

    @property
    def noop_actions(self) -> set:
        return set(getattr(self.adapter, "noop_actions", set()) or set())

    @property
    def supported_actions(self) -> set:
        sup = getattr(self.adapter, "supported_actions", None)
        return set(sup) if sup is not None else set(self.skill_map) | self.noop_actions

    @property
    def vlm_actions(self) -> set:
        return set(getattr(self.adapter, "vlm_actions", set()) or set())

    @property
    def plan_actions(self) -> set:
        """Actions a plan may use: the robot's skills in the action model's vocabulary, plus
        its no-ops (sensor / dialog skills such as Verify / Ask are not plan steps)."""
        vocab = set(action_model.ROBOT_ACTIONS)
        return ({a for a in self.skill_map if a in vocab} & self.supported_actions) | self.noop_actions

    def spec(self, action: str) -> Optional[SkillSpec]:
        return self.manifest.skill_for(action) if action else None

    def interruptible(self, skill: str) -> bool:
        """May a stop cut ``skill`` mid-way? Its manifest ``interruptible`` (default True)."""
        s = next((x for x in self.manifest.skills if x.name == skill), None)
        return True if s is None else bool(s.interruptible)

    def bind(self, step: dict, world: Any = None) -> Optional[tuple]:
        """``(skill, params)`` for a plan step, None for a no-op; raises Unbindable."""
        return self.adapter.bind(step, world)

    def name_for(self, action: str, obj: str) -> str:
        """The robot's own name of a step's argument (for announcements)."""
        fn = getattr(self.adapter, "to_loc" if action == "MoveTo" else "to_obj", None)
        try:
            return fn(obj) if callable(fn) else obj
        except Exception:
            return obj

    # ── body ────────────────────────────────────────────────────────────
    def capabilities(self) -> dict:
        """The manifest capabilities with the core's defaults: one hand, and Find only looks
        (a found place still needs a MoveTo) unless the robot declares ``find_reaches``."""
        caps = dict(self.manifest.capabilities or {})
        caps.setdefault("find_reaches", False)
        if "hands" not in caps and "arms" not in caps:
            caps["hands"] = 1
        return caps

    def model_capabilities(self):
        return action_model.Capabilities.from_any(self.capabilities())

    def openable(self, key: str, entry: Optional[dict] = None) -> bool:
        """Can ``key`` be opened? The adapter's ``is_openable(key, entry)``, else the ENV
        entry's ``openable`` flag."""
        fn = getattr(self.adapter, "is_openable", None)
        if callable(fn):
            try:
                return bool(fn(key, entry or {}))
            except Exception:
                pass
        return bool((entry or {}).get("openable"))

    @property
    def quiet_skills(self) -> set:
        """Skills that speak for themselves: their steps are not narrated."""
        return set(getattr(self.adapter, "quiet_skills", ()) or ())

    # ── sensors ─────────────────────────────────────────────────────────
    def verify_specs(self, action: str) -> list:
        s = self.spec(action)
        return list(s.verify) if s is not None else []

    def sensors(self, action: str) -> list:
        return [v for v in self.verify_specs(action) if v.kind == "sensor" and v.skill]

    def holding_sensor(self) -> Optional[str]:
        """The skill that tells whether the hand holds something: the sensor that verifies a
        Pick (``isdone`` = holding)."""
        return next((v.skill for v in self.sensors("Pick") if v.expected), None)

    def pose_sensor(self) -> Optional[str]:
        """The skill that reads the base pose (``pose`` = [x, y, yaw_deg]): the sensor that
        verifies a MoveTo, else the robot's Observe skill."""
        s = next((v.skill for v in self.sensors("MoveTo")), None)
        if s is None:
            obs = self.spec("Observe")
            s = obs.name if obs is not None else None
        return s

    def ask_spec(self) -> Optional[SkillSpec]:
        """The skill that asks the user a question (the manifest's Ask action)."""
        return self.spec("Ask")

    def observe(self, node: Any = None) -> tuple:
        try:
            o = self.adapter.observe(node)
            return (o.text or "", list(o.visible or []))
        except Exception:
            return "", []

    def health(self, node: Any = None) -> dict:
        try:
            return dict(self.adapter.health(node) or {})
        except Exception as e:
            return {"ok": None, "error": str(e)}

    @property
    def vlm_hook(self):
        nm = getattr(self.adapter, "namemap", None)
        if nm is not None and not callable(getattr(nm, "vlm_hook", None)):
            return None                          # a namemap without one: no VLM layer, as before
        fn = getattr(self.adapter, "vlm_hook", None)
        return fn if callable(fn) else None

    # ── failures ────────────────────────────────────────────────────────
    @staticmethod
    def failure_code(result: SkillResult, reason: str) -> FailureCode:
        """The step's FailureCode: the skill's own when the skill failed, else (a verify layer
        failed) the code of that layer's reason."""
        if result is not None and not result.ok and result.code is not None:
            return result.code
        return classify_legacy_failure(reason)

    def stops_now(self, code) -> bool:
        """The recovery policy forbids any retry / replan (e-stop, cancelled, a skill bug)."""
        return not self.policy.rule_for(code).retry_allowed

    # ── words ───────────────────────────────────────────────────────────
    def text(self, key: str, lang: Optional[str] = None, **kw) -> str:
        """The robot's phrase for ``key`` (its i18n), else the core's English one."""
        by_lang = (self.i18n.phrases or {}).get(key) or {}
        s = by_lang.get((lang or "en")[:2]) or by_lang.get("en") or CORE_TEXT.get(key) or key
        try:
            return s.format(**kw) if kw else s
        except (KeyError, IndexError, ValueError):
            return s

    def has_text(self, key: str) -> bool:
        return bool((self.i18n.phrases or {}).get(key))

    def robot_description(self) -> str:
        """How the task-understanding prompt describes the robot."""
        if self.has_text("robot_description"):
            return self.text("robot_description")
        acts = [a for a in ("MoveTo", "Find", "Pick", "Place", "Open", "PutIn") if a in self.plan_actions]
        return CORE_TEXT["robot_description"].format(abilities=", ".join(_ABILITY[a] for a in acts) or "nothing")

    def __repr__(self) -> str:
        return f"RobotView({self.adapter!r})"


def run_skill(sr, skill: str, params: dict, node: Any = None, log_fn=None, strict: bool = True,
              raise_errors: bool = False, interruptible: bool = True) -> SkillResult:
    """Run one skill and normalise what it returns (the ONE place): an exception is a failure
    whose code is read off its text (``raise_errors``: it propagates, e.g. a sensor that could
    not be read), a legacy ``{'isdone', 'msg'}`` dict goes through ``SkillResult.from_legacy``
    (``strict``: no verdict = failure, the planner's rule). ``interruptible`` False (the
    manifest's): a stop meanwhile waits for the skill to end (``run_control.uninterruptible``)."""
    from ..run_control import uninterruptible
    try:
        with uninterruptible(skill, interruptible):
            raw = (sr.execute(skill, params, node, log_fn=log_fn) if log_fn is not None
                   else sr.execute(skill, params, node))
    except Exception as e:
        if raise_errors:
            raise
        return SkillResult.from_legacy({"isdone": False, "msg": str(e)})
    return SkillResult.from_legacy(raw, strict=strict)


# ─────────────────────────────────────────────────────────────────────────────
# the robot of the running agent (announcer, reconcile, direct path)
# ─────────────────────────────────────────────────────────────────────────────
_CACHE: dict = {}
_LOCK = threading.Lock()


def robot_for(robot_pkg: str) -> Optional[RobotView]:
    """The (cached) RobotView of a robot package; None when it cannot be loaded."""
    with _LOCK:
        if robot_pkg not in _CACHE:
            try:
                _CACHE[robot_pkg] = RobotView.load(robot_pkg)
            except Exception:
                _CACHE[robot_pkg] = None
        return _CACHE[robot_pkg]


def current_robot() -> Optional[RobotView]:
    """The RobotView of ``robot_agent.state.current().robot_pkg``, or None outside a runtime."""
    try:
        from robot_agent.state import current
        pkg = current().robot_pkg
    except Exception:
        return None
    return robot_for(pkg)


__all__ = ["RobotView", "run_skill", "robot_for", "current_robot", "CORE_TEXT", "Unbindable", "VerifySpec"]
