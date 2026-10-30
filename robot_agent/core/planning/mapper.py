# robot_agent/robot_agent/core/planning/mapper.py
"""ActionMapper -- GRACE symbolic action -> robot skill call + state.

This is the robot-agnostic glue specified in ``ACTION_MAPPER_SPEC.md``. It
turns a GRACE plan step (abstract, CamelCase) into a concrete skill call
``(skill_name, params)`` for ``SkillRegistry.execute``, mirrors the GRACE
symbolic effects onto a :class:`WorldState`, and pre-screens plans for
actions this robot cannot perform.

All robot-specific knowledge (name maps, skill selection, the supported /
no-op / VLM action sets) is pulled from the robot -- a ``RobotAdapter``
(``robot_agent.contract``; ``to_skill`` is then its ``bind``) or a duck-typed
``<robot>.configs.grace_namemap`` module -- never hard-coded here. A namemap
module is expected to expose (see docs/contract.md):

* ``to_loc(name) -> str``           : GRACE location -> ENV/move key
* ``to_obj(name) -> str``           : GRACE object -> detector/skill string
* ``SKILL_MAP: dict[str, str]``     : GRACE action -> robot skill name
* ``SUPPORTED_ACTIONS: set[str]``   : actions this robot can execute
* ``NOOP_ACTIONS: set[str]``        : Sit / LieOn / Serve / Wait
* ``VLM_ACTIONS: set[str]``         : actions checked via a VLM hook
* ``observe(node) -> (str, list)``  : perception pass (used by the Grounder)
* ``vlm_hook(step, node) -> (...)`` : per-step VLM verification

Mapping policy (``ACTION_MAPPER_SPEC.md`` sections 2 & 6):
* No-op actions (``NOOP_ACTIONS``)            -> ``to_skill`` returns ``None``.
* Unsupported actions (action not in
  ``SUPPORTED_ACTIONS``, or no ``SKILL_MAP``
  entry)                                       -> raises :class:`Unmappable`.
* Everything else                              -> ``(skill_name, params)``.

The symbolic effects come from the shared action model (``.action_model``).
"""

from __future__ import annotations

from typing import Optional

from . import action_model
from .base import WorldState

# GRACE param fields that name a location (vs. an object) for param building.
# MoveTo / Place / PutIn target a place-like receptacle (uses ``to_loc``);
# Find / Pick target a graspable object (uses ``to_obj``).
_LOCATION_ACTIONS = {"MoveTo", "Place", "PutIn"}
_OBJECT_ACTIONS = {"Find", "Pick", "Open", "Close", "TurnOn", "TurnOff", "Wash"}


class Unmappable(Exception):
    """Raised when a GRACE step has no executable skill on this robot.

    Carries the offending action/object so the driver can turn it into a
    ``failure_reason`` for ``replan`` or escalate it to the operator
    (``ACTION_MAPPER_SPEC.md`` section 6).
    """


class ActionMapper:
    """Translate GRACE steps to skill calls and track symbolic effects.

    Parameters
    ----------
    namemap:
        The robot: a ``RobotAdapter`` / ``RobotView`` (has ``bind``), or a
        duck-typed config module (see module docstring) supplying name maps,
        the skill table, and the action category sets.
    """

    def __init__(self, namemap) -> None:
        self.namemap = namemap
        self._adapter = namemap if callable(getattr(namemap, "bind", None)) else None

    # ── helpers ──────────────────────────────────────────────────────────
    def _get(self, legacy: str, name: str, default):
        v = getattr(self.namemap, legacy, None)
        if v is None:
            v = getattr(self.namemap, name, None)
        return default if v is None else v

    def _skill_map(self) -> dict[str, str]:
        return dict(self._get("SKILL_MAP", "skill_map", {}) or {})

    def _supported(self) -> set[str]:
        return set(self._get("SUPPORTED_ACTIONS", "supported_actions", set()))

    def _noop(self) -> set[str]:
        return set(self._get("NOOP_ACTIONS", "noop_actions", set()))

    @staticmethod
    def _action(step: dict) -> str:
        return (step.get("action") or "").strip()

    @staticmethod
    def _object(step: dict) -> str:
        return (step.get("object") or "").strip()

    # ── core API ─────────────────────────────────────────────────────────
    def to_skill(
        self, step: dict, world: WorldState
    ) -> Optional[tuple[str, dict]]:
        """Map one GRACE ``step`` to ``(skill_name, params)`` or ``None``.

        Returns
        -------
        None
            If ``action`` is a no-op (``NOOP_ACTIONS``) -- the driver marks it
            done without touching the robot.
        (skill_name, params)
            For a mappable action. ``params`` is a dict of the shape the robot's
            skills expect, e.g. ``{'inputs': '<robot-name>'}``.

        Raises
        ------
        Unmappable
            If ``action`` is not in ``SUPPORTED_ACTIONS`` or has no
            ``SKILL_MAP`` entry (``ACTION_MAPPER_SPEC.md`` sections 2 & 6).
        """
        action = self._action(step)
        if not action:
            raise Unmappable("step has no action")
        if self._adapter is not None:
            from robot_agent.contract import Unbindable
            try:
                return self._adapter.bind(step, world)
            except Unbindable as e:
                raise Unmappable(str(e)) from e

        # No-op actions: succeed without a skill call.
        if action in self._noop():
            return None

        # Supported-action gate.
        if action not in self._supported():
            raise Unmappable(
                f"no skill for action {action!r} on object "
                f"{self._object(step)!r} (not in SUPPORTED_ACTIONS)"
            )

        skill_map = self._skill_map()
        if action not in skill_map:
            raise Unmappable(
                f"no skill for action {action!r} on object "
                f"{self._object(step)!r} (no SKILL_MAP entry)"
            )
        skill_name = skill_map[action]

        # Resolve the argument name.
        obj = self._object(step)

        # GRACE ``Pick`` carries no object -> take it from world.found
        # (ACTION_MAPPER_SPEC.md section 2 / table note).
        if action == "Pick" and not obj:
            if not world.found:
                raise Unmappable("Pick requested but nothing has been found")
            obj = world.found

        if not obj:
            raise Unmappable(f"action {action!r} requires an object")

        # Build params: location-like actions name a place (to_loc), object-like
        # actions name a graspable object (to_obj).
        if action in _LOCATION_ACTIONS:
            value = self.namemap.to_loc(obj)
        else:
            value = self.namemap.to_obj(obj)

        params = {"inputs": value}
        return skill_name, params

    def apply_effect(self, step: dict, world: WorldState) -> None:
        """Mutate ``world`` to mirror the GRACE symbolic effect of ``step``.

        Called by the driver only after the real skill reported success, so the
        mapper's state stays consistent with physical ground truth. The effect is
        the shared action model's (``embodiplan.action_model.apply``;
        ``ACTION_MAPPER_SPEC.md`` section 3).
        """
        action_model.apply(step, world, inplace=True)

    def screen(self, steps: list[dict]) -> list[str]:
        """Return pre-flight human warnings for non-executable steps.

        A warning is produced for every step whose action is neither in
        ``SUPPORTED_ACTIONS`` nor a no-op -- i.e. the run would hit an
        :class:`Unmappable` for it (``ACTION_MAPPER_SPEC.md`` section 6.3).
        """
        supported = self._supported()
        noop = self._noop()
        warnings: list[str] = []
        for i, step in enumerate(steps):
            action = self._action(step)
            if action in supported or action in noop:
                continue
            warnings.append(
                f"step {i + 1}: unsupported action {action!r} on object "
                f"{self._object(step)!r} -- this robot cannot execute it"
            )
        return warnings
