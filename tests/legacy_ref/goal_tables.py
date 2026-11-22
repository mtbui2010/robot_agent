"""Reference copy for Step S07: the goal-kind table and the functions that read it, as they were in
robot_agent/core/planning/goals.py lines 25-106 (``GoalKind``, ``MANIPULATION``, ``GOAL_KINDS``, ``TEMPLATE_KINDS``,
``OPTIONAL_KINDS``, ``missing_actions``, ``kinds_for``, ``domain_actions``, ``canonical``, ``prompt_lines``, ``is_template``),
lines 122-148 (``conflicts``) and lines 174-200 (``subgoal_text``, ``describe``) at commit 96c17e1 (the tip of generalize-core
before S07; the same bodies as 8002347), copied verbatim. What differs from the file: the relative import of ``LocalText`` in
``conflicts`` is absolute. Never edited: it is the old behaviour that tests/test_pack_goal_kinds.py compares the pack's
goal-kind table with."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class GoalKind:
    name: str
    needs: tuple            # the actions the kind needs; a tuple inside = any one of them
    prompt: str = ""        # the understand prompt's line for it ('' = described in the base prompt)


MANIPULATION = ("on", "in", "open", "close")
GOAL_KINDS: dict = {
    "on": GoalKind("on", ("Pick", "Place")),
    "in": GoalKind("in", ("Pick", ("PutIn", "Place"))),
    "open": GoalKind("open", ("Open",)),
    "close": GoalKind("close", ("Close",)),
    "go": GoalKind("go", ("MoveTo",),
                   '"go" = only go somewhere, nothing to move: object "", from "", to = the place.'),
    "say": GoalKind("say", ("Say",),
                    '"say" = tell someone something: object = the sentence to say, as the robot should say it; '
                    'from ""; to = the place to say it at, or the person, or "".'),
    "deliver": GoalKind("deliver", ("MoveTo", "WaitForUnload"),
                        '"deliver" = take what the robot carries to a place and hand it over: object = the item '
                        '(or ""), from "", to = the place.'),
}
TEMPLATE_KINDS = ("go", "say", "deliver")
# Optional kinds of the understand schema (option ``understand_give_kind``): not part of the table above, so the default
# schema and prompt do not change. A ``give`` goal is an ``on`` goal whose destination is a person (``canonical``).
OPTIONAL_KINDS: dict = {
    "give": GoalKind("give", ("Pick", "Place"),
                     '"give" = hand an object to a person: object = the item, from = where it is now or "", to = the '
                     'person ("me" when the speaker receives it). Not for putting an object on a place.'),
}


def missing_actions(goal: dict, actions: Iterable[str]) -> list:
    """The actions `goal` needs that are not in `actions` (an any-of group by its first name).
    An unknown kind needs an action nobody has: [its type]."""
    acts = set(actions)
    kind = GOAL_KINDS.get(goal.get("type"))
    if kind is None:
        return [str(goal.get("type"))]
    out = []
    for need in kind.needs:
        alts = need if isinstance(need, tuple) else (need,)
        if not any(a in acts for a in alts):
            out.append(alts[0])
    if goal.get("type") == "say" and goal.get("to") and "MoveTo" not in acts:
        out.append("MoveTo")
    return out


def kinds_for(actions: Iterable[str], optional: Iterable[str] = ()) -> list:
    """The goal kinds the understand step may use for a robot with `actions`: the manipulation
    kinds always (so a request the robot cannot do is understood, then refused with a reason),
    the others only when the robot has their actions. ``optional``: names from ``OPTIONAL_KINDS`` the caller switched on,
    added (as the manipulation kinds are) at the end."""
    acts = set(actions)
    return list(MANIPULATION) + [k for k in TEMPLATE_KINDS if not missing_actions({"type": k}, acts)] \
        + [k for k in optional if k in OPTIONAL_KINDS]


def domain_actions() -> list:
    """Every abstract action the goal tables name (sorted): the vocabulary a refusal may say a robot lacks."""
    out = set()
    for kind in list(GOAL_KINDS.values()) + list(OPTIONAL_KINDS.values()):
        for need in kind.needs:
            out.update(need if isinstance(need, tuple) else (need,))
    return sorted(out)


def canonical(goals: list) -> list:
    """The goals with the optional kinds mapped onto the table's: ``give`` -> ``on`` (its destination is the person, the
    path of "bring me X" / a hand-over). Other goals are passed on unchanged."""
    return [{**g, "type": "on"} if g.get("type") == "give" else g for g in goals]


def prompt_lines(kinds: Iterable[str]) -> str:
    table = {**GOAL_KINDS, **OPTIONAL_KINDS}
    lines = [table[k].prompt for k in kinds if k in table and table[k].prompt]
    return "".join(f"\n  {line}" for line in lines)


def is_template(goals: list) -> bool:
    return bool(goals) and all(g.get("type") in TEMPLATE_KINDS for g in goals)


def conflicts(goals: list, name=None, declare: bool = False) -> tuple:
    """(goals, question|None): the goal validator. Two destinations for one object is a question
    for the person; an open and a close of one container keep the last one (the final state). ``name``: how a
    place key is said (default: 'shelf@kitchen' -> 'shelf in the kitchen'). ``declare`` (option ``dialog_agree``): the question
    comes as a ``speech.LocalText`` with the same English text that declares a choice between the places (intent
    ``choose_place``, the keys as options), so that the answer can settle the option not taken."""
    q, dest = None, {}
    for g in goals:
        if g.get("type") in ("on", "in") and g.get("object"):
            dest.setdefault(g["object"], [])
            if g.get("to") and g["to"] not in dest[g["object"]]:
                dest[g["object"]].append(g["to"])
    for obj, tos in dest.items():
        if len(tos) > 1:
            nice = [name(t) if name is not None else t.replace("@", " in the ") for t in tos]
            q = f"Where should the {obj} go in the end: {' or '.join(nice)}? It cannot be in two places."
            if declare:
                from robot_agent.core.planning.speech import LocalText
                q = LocalText("_two_places_q", text=q, ask={"intent": "choose_place", "options": list(tos), "about": str(obj)})
            break
    last = {}
    for k, g in enumerate(goals):
        if g.get("type") in ("open", "close"):
            last[g.get("object")] = k
    kept = [g for k, g in enumerate(goals)
            if g.get("type") not in ("open", "close") or last.get(g.get("object")) == k]
    return kept, q


def subgoal_text(g: dict, places=None) -> str:
    """One goal as the sub-goal phrase the planner expands (``AssistLoop._subgoals``, SAGE-v2 F1.2): what to do
    with which object, from where (when known) to where, with the ENV keys the plan must use."""
    t, o = g.get("type"), g.get("object") or ""
    src = f" (it is at {g['from']})" if g.get("from") else (
        f" (look for it at {' or '.join(g['from_any'])})" if g.get("from_any") else "")
    if t == "on":
        verb = "hand" if g.get("hand") else "put"
        return f"{verb} the {o}{src} " + (f"to {g['to']}" if g.get("hand") else f"on {g.get('to')}")
    if t == "in":
        return f"put the {o}{src} into {g.get('to')}"
    if t in ("open", "close"):
        return f"{t} {o}"
    if t == "go":
        return f"go to {g.get('to')}"
    if t == "say":
        who = g.get("to") or g.get("person") or ""
        return f"say \"{o}\"" + (f" at {who}" if who else "")
    if t == "deliver":
        return f"deliver the {o} to {g.get('to')}" if o else f"deliver what the robot carries to {g.get('to')}"
    return describe(g)


def describe(g: dict) -> str:
    """The goal in the obs / goal-text form: '<type> <object>[ from <from>][ to <to>]'."""
    return (f"{g['type']} {g.get('object') or ''}".rstrip() + (f" from {g['from']}" if g.get("from") else "")
            + (f" to {g['to']}" if g.get("to") else ""))
