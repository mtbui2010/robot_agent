# robot_agent/robot_agent/core/planning/__init__.py
"""Robot-agnostic planning layer for the closed-loop GRACE driver.

This package bridges abstract GRACE/SAGE (embodiplan) symbolic plans to concrete
skill calls of the robot, which it reaches only through its ``RobotAdapter``
(``robot_agent.contract``, wrapped by :class:`.robot.RobotView`). See the specs:

* ``docs/contract.md`` (this repo) and the robot package's
  ``docs/CLOSED_LOOP_ARCHITECTURE.md`` / ``docs/ACTION_MAPPER_SPEC.md``

Public surface (the only names downstream code should import):

* :class:`WorldState`  -- mapper-owned symbolic state (mirrors GRACE).
* :class:`Planner`     -- structural Protocol every backend satisfies.
* :class:`ActionMapper`-- GRACE step -> (skill, params) + state effects.
* :class:`Unmappable`  -- raised for actions this robot cannot execute.
* :func:`get_planner`  -- registry; lazily constructs a backend by name.

Nothing here imports ``embodiplan`` -- backends that do are loaded lazily
through :func:`get_planner`.
"""

from __future__ import annotations

from .base import Planner, WorldState
from .mapper import ActionMapper, Unmappable
from .registry import get_planner

__all__ = [
    "WorldState",
    "Planner",
    "ActionMapper",
    "Unmappable",
    "get_planner",
]
