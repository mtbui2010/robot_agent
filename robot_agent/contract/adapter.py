"""RobotAdapter: everything the core needs from one robot, as a Protocol.

Replaces the duck-typed ``<pkg>.configs.grace_namemap`` module. A robot
package ships ``configs/adapter.py`` with a class that has these members (it
does not need to inherit from anything); until it does,
:class:`~robot_agent.contract.legacy_adapter.NamemapAdapter` wraps the old
namemap module.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from .manifest import Manifest
from .types import Geometry, Messages, Observation


class Unbindable(LookupError):
    """The step's abstract action has no skill on this robot (or lacks the
    argument it needs). The caller turns it into a replan reason."""


@runtime_checkable
class RobotAdapter(Protocol):
    manifest: Manifest          # skills + capabilities
    geometry: Geometry          # arrive tolerance, reach, workspace (not in the core)
    i18n: Messages              # phrases + default language

    def bind(self, step: dict, world: Any = None) -> Optional[tuple]:
        """Abstract step ``{'action': 'Pick', 'object': 'Cup'}`` ->
        ``(skill_name, params)``; None for a no-op step (Wait, Sit ...).
        Raises :class:`Unbindable` when this robot cannot do it."""
        ...

    def observe(self, node: Any = None) -> Observation:
        """Pose, hands, containers, sensor health — best effort, never raises."""
        ...

    def reconcile(self, world: Any, node: Any = None) -> Any:
        """Bring the belief ``world`` in line with the robot (at start / after
        a crash) and return it."""
        ...

    # Optional (not Protocol members, so older adapters still match; docs/guard.md):
    #   pose(node) -> {x, y, yaw?, ts? | age_s?}   the base pose; {} when it cannot be read
    #   container_states(node) -> {key: True | False | None}   open / closed as read (None: not read)

    def health(self, node: Any = None) -> dict:
        """Frame age, FPS, depth, pose timestamp, e-stop, battery ... Keys are
        the adapter's; ``ok`` (bool | None) is the summary."""
        ...
