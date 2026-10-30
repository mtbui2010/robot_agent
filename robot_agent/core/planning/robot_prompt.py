"""The robot as a planner prompt describes it: its body and its plan actions, from the manifest.

One generator for every planner (docs/planner_guide.md):

* SAGE (``backends/pyplanner_grace.py``) gets :func:`action_lines` (``style="action"``:
  ``"MoveTo <place>: move to a place"``) and :func:`describe_robot` as embodiplan's
  ``SAGEPlanner(actions=..., robot_description=...)``;
* the planner guide of the direct path (``core/guide_manager.py``) fills its ``{ROBOT}`` /
  ``{ACTIONS}`` placeholders with :func:`describe_robot` and ``style="skill"`` lines
  (``"- move::<inputs> (MoveTo): move to a place"``), the ``action::inputs`` words it plans in.

Nothing here names a robot: the words come from the manifest (``robot``, ``description``,
``capabilities``, the skills' ``action`` / ``params`` / ``description``).
"""

from __future__ import annotations

import os
from typing import Any, Optional

# canonical order of the abstract actions in a prompt (the robot's others follow, sorted)
ORDER = ("MoveTo", "Find", "Pick", "Place", "PutIn", "Open", "Close", "Say", "WaitForUnload", "Dock")

# what an abstract action does, and the word for its argument (robots override it with a
# skill-level manifest `description`)
ACTION_TEXT = {
    "MoveTo": ("place", "move to a place"),
    "Find": ("object", "look for an object where the robot is"),
    "Pick": ("object", "pick up the found object"),
    "Place": ("place", "put the held object on a place the robot is at"),
    "PutIn": ("container", "put the held object into an open container the robot is at"),
    "Open": ("container", "open a container the robot is at"),
    "Close": ("container", "close a container the robot is at"),
    "Say": ("text", "say a sentence to the person"),
    "WaitForUnload": ("item", "wait until a person takes the carried item"),
    "Dock": ("dock", "drive onto the charging dock"),
}

# capability key -> how the description says it (value true / > 0)
_CAPS = (("base", "drives on a mobile base"), ("lift", "has a lift"), ("head", "has a movable head camera"),
         ("drawer", "can open and close drawers"), ("voice", "can speak and listen"),
         ("speaker", "can speak"), ("tray", "carries items on a tray"))

ENV_SWITCH = "ROBOT_AGENT_SAGE_ROBOT_PROMPT"


def robot_prompt_enabled() -> bool:
    """SAGE gets the manifest's actions and description unless ``ROBOT_AGENT_SAGE_ROBOT_PROMPT=0``
    (then embodiplan's default household prompts, as before)."""
    return os.environ.get(ENV_SWITCH, "1") != "0"


def _count(v: Any) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, (list, tuple, set)):
        return len(v)
    return 0


def _body(caps: dict) -> list:
    """The capability phrases of the description."""
    out = []
    arms, hands = _count(caps.get("arms")), caps.get("hands")
    if arms or _count(hands):
        n = arms or _count(hands)
        side = f" ({', '.join(map(str, hands))})" if isinstance(hands, (list, tuple)) and hands else ""
        out.append(f"has {n} arm{'s' if n != 1 else ''} with a hand{side}, so it holds one object at a time"
                   if n == 1 else f"has {n} arms{side}")
    elif "arms" in caps or "hands" in caps:
        out.append("has no arm, so it cannot pick up, put down or open anything")
    for key, text in _CAPS:
        if caps.get(key) is True or (not isinstance(caps.get(key), bool) and _count(caps.get(key)) > 0):
            out.append(text)
    return out


def describe_robot(view) -> str:
    """The robot's description: the manifest's ``description`` when it has one, else made
    from its ``robot`` name and ``capabilities``."""
    m = view.manifest
    own = getattr(m, "description", None) or (getattr(m, "extra", {}) or {}).get("description")
    if isinstance(own, str) and own.strip():
        return " ".join(own.split())
    caps = dict(getattr(m, "capabilities", None) or {})
    body = _body(caps)
    name = getattr(m, "robot", "") or "the robot"
    if not body:
        return f"{name}: a robot."
    head = ", ".join(body[:-1]) + (" and " if len(body) > 1 else "") + body[-1]
    return f"{name}: a robot that {head}."


def ordered_actions(view) -> list:
    """The robot's plan actions (``RobotView.plan_actions``) in prompt order."""
    acts = set(view.plan_actions)
    return [a for a in ORDER if a in acts] + sorted(acts - set(ORDER))


def _skill_text(spec, action: str) -> tuple:
    """(argument word, what it does) of ``action`` done by a manifest skill: the skill's own
    ``description`` wins when the skill is declared for that action (not as an alias)."""
    arg, text = ACTION_TEXT.get(action, ("", ""))
    extra = getattr(spec, "extra", None) or {}
    if (getattr(spec, "action", None) == action and isinstance(extra.get("description"), str)
            and extra["description"].strip()):
        text = " ".join(extra["description"].split())
    if not text and spec is not None:
        props = (spec.params or {}).get("properties") or {}
        text = str((props.get("inputs") or {}).get("description") or "")
    return arg, text


def action_line(view, action: str, style: str = "action") -> Optional[str]:
    """One prompt line for ``action``: ``"MoveTo <place>: move to a place"`` (style
    ``action``), ``"- move::<place> (MoveTo): move to a place"`` (style ``skill``, the
    direct path's ``skill::inputs`` words); None when the robot has no skill for it in
    style ``skill``."""
    spec = view.spec(action)
    arg, text = _skill_text(spec, action) if spec is not None else ACTION_TEXT.get(action, ("", ""))
    if style == "skill":
        if spec is None:
            return None
        return f"- {spec.name}::{f'<{arg}>' if arg else '<inputs>'} ({action})" + (f": {text}" if text else "")
    return f"{action}{f' <{arg}>' if arg else ''}" + (f": {text}" if text else "")


def action_lines(view, style: str = "action") -> list:
    """One line per plan action of the robot (:func:`action_line`), in prompt order."""
    lines = (action_line(view, a, style) for a in ordered_actions(view))
    return [ln for ln in lines if ln]


def sage_prompt_kwargs(view) -> dict:
    """``{actions, robot_description}`` for SAGE from the manifest ({} when switched off)."""
    if not robot_prompt_enabled():
        return {}
    try:
        return {"actions": action_lines(view), "robot_description": describe_robot(view)}
    except Exception:
        return {}
