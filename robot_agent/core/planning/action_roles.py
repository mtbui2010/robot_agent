"""Action roles: which of the robot's actions plays a role the lab options need (round 16).

The options ``reach_delivery`` and ``relook`` have to splice or recognise a few steps: the put-down of the held object, a move
to a place, opening and closing a container. The core did that with action names written in its code. They are now data: the
robot pack's manifest ``planner.action_roles`` maps a role to the action of the pack that plays it::

    planner:
      action_roles: {put_down: Place, move: MoveTo, open: Open, close: Close}

* Roles: ``put_down``, ``move``, ``open``, ``close``, ``find``, ``pick``, ``say`` (a speech to a listener). A role is a name of an action in the pack's ``plan_actions``.
* A role the pack does not declare, or declares as an action the robot does not have, is ``None``: the part of the option that
  needs it does nothing (fail open). The core never falls back to a name of its own.
* A hand-over (an action that empties the hand to a person) is not a role: it comes from the skills' effects
  (``RobotView.release_actions``).

Pure functions on the manifest's ``planner`` mapping and a set of the robot's actions.
"""

from __future__ import annotations

from typing import Optional

KEY = "action_roles"
ROLES = ("put_down", "move", "open", "close", "find", "pick", "say")


def declared(planner) -> dict:
    """``{role: action}`` the pack declares (only known roles with a non-empty string; anything else is ignored)."""
    v = (planner or {}).get(KEY) if isinstance(planner, dict) else None
    if not isinstance(v, dict):
        return {}
    return {r: str(a) for r, a in v.items() if r in ROLES and isinstance(a, str) and a.strip()}


def action_for(planner, role: str, plan_actions=None) -> Optional[str]:
    """The pack's action for ``role``; None when it declares none, or (``plan_actions`` given) the robot has no such action."""
    a = declared(planner).get(role)
    if a is None:
        return None
    if plan_actions is not None and a not in set(plan_actions):
        return None
    return a


__all__ = ["KEY", "ROLES", "declared", "action_for"]
