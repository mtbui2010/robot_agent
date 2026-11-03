"""The typed goals of the assistive loop: what each goal kind needs from the robot, how it is checked.

A goal is the understand step's ``{type, object, from, to}``. Its kind says which abstract
actions (the manifest's ``SkillSpec.action``) it needs, so a robot is never asked for a goal
it has no skill for, and how the goal is reached by a plan / holds after a run:

  on / in        move an object onto a surface / into a container    Pick + Place / PutIn
  open / close   a container ends open / closed                      Open / Close
  go             the robot goes to a place (nothing to move)        MoveTo
  say            the robot says a sentence (at a place, to someone) Say
  deliver        the robot takes what it carries to a place and
                 waits until a person takes it                      MoveTo + WaitForUnload

The manipulation kinds are planned by the planner (SAGE or a plug-in); the others (``TEMPLATE_KINDS``) are a fixed
sequence of steps, compiled here without an LLM (``compile_steps``). Nothing in this module
knows a robot: the actions a robot has come from its manifest (``RobotView.plan_actions``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


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


def kinds_for(actions: Iterable[str]) -> list:
    """The goal kinds the understand step may use for a robot with `actions`: the manipulation
    kinds always (so a request the robot cannot do is understood, then refused with a reason),
    the others only when the robot has their actions."""
    acts = set(actions)
    return list(MANIPULATION) + [k for k in TEMPLATE_KINDS if not missing_actions({"type": k}, acts)]


def prompt_lines(kinds: Iterable[str]) -> str:
    lines = [GOAL_KINDS[k].prompt for k in kinds if k in GOAL_KINDS and GOAL_KINDS[k].prompt]
    return "".join(f"\n  {line}" for line in lines)


def is_template(goals: list) -> bool:
    return bool(goals) and all(g.get("type") in TEMPLATE_KINDS for g in goals)


def conflicts(goals: list) -> tuple:
    """(goals, question|None): the goal validator. Two destinations for one object is a question
    for the person; an open and a close of one container keep the last one (the final state)."""
    q, dest = None, {}
    for g in goals:
        if g.get("type") in ("on", "in") and g.get("object"):
            dest.setdefault(g["object"], [])
            if g.get("to") and g["to"] not in dest[g["object"]]:
                dest[g["object"]].append(g["to"])
    for obj, tos in dest.items():
        if len(tos) > 1:
            nice = [t.replace("@", " in the ") for t in tos]
            q = f"Where should the {obj} go in the end: {' or '.join(nice)}? It cannot be in two places."
            break
    last = {}
    for k, g in enumerate(goals):
        if g.get("type") in ("open", "close"):
            last[g.get("object")] = k
    kept = [g for k, g in enumerate(goals)
            if g.get("type") not in ("open", "close") or last.get(g.get("object")) == k]
    return kept, q


# words for the person speaking; "me" is a destination (deliver to the speaker), never a source
SPEAKER_WORDS = frozenset({"me", "user", "here", "you", "myself", "person", "the user", "the person"})


def is_speaker(v) -> bool:
    return " ".join(str(v or "").lower().replace("_", " ").split()) in SPEAKER_WORDS


def speaker_slots(goals: list) -> list:
    """The understood goals with "me" kept to the destination (FROM_ME): a model that put the speaker in
    `from` ("bring me the cup" -> from "me") gets it moved to an empty `to`, else dropped (the source is
    then searched for / taken from the memory), so the robot never asks "Where are you?" for the pick."""
    out = []
    for g in goals:
        g = dict(g)
        if g.get("type") in ("on", "in") and is_speaker(g.get("from")):
            if not g.get("to"):
                g["to"] = g["from"]
            g["from"] = ""
        out.append(g)
    return out


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


def summary(g: dict) -> str:
    """The read-back phrase of a template goal."""
    nice = (g.get("to") or "").replace("@", " in the ")
    if g["type"] == "go":
        return f"go to the {nice}"
    if g["type"] == "say":
        where = f" in the {nice}" if g.get("to") else (f" to {g['person']}" if g.get("person") else "")
        return f"say \"{g.get('object')}\"{where}"
    return f"deliver {('the ' + g['object']) if g.get('object') else 'what I carry'} to the {nice}"


def compile_steps(goals: list, at: Optional[str], delivery_text: str = "", can_say: bool = False) -> list:
    """The plan of template goals, in goal order, from the robot at `at`."""
    out = []

    def goto(p):
        nonlocal at
        if p and p != at:
            out.append({"action": "MoveTo", "object": p})
            at = p
    for g in goals:
        t = g.get("type")
        if t == "go":
            out.append({"action": "MoveTo", "object": g["to"]}); at = g["to"]
        elif t == "say":
            goto(g.get("to"))
            out.append({"action": "Say", "object": g.get("object") or ""})
        elif t == "deliver":
            goto(g.get("to"))
            if can_say and delivery_text:
                out.append({"action": "Say", "object": delivery_text})
            out.append({"action": "WaitForUnload", "object": ""})
    return out


def reached(g: dict, steps: list, at: Optional[str], last: bool = False) -> bool:
    """Does running `steps` from `at` reach the template goal `g`? A ``go`` goal that is the ``last`` goal is
    judged where the steps END ("go back and charge": the robot must be at the dock afterwards; a robot that
    started there and left has not reached it, one that never left has); an earlier ``go`` is reached by a
    MoveTo there in the steps, or by starting there."""
    t, here = g.get("type"), at
    if t == "go" and last:
        for st in steps:
            if st.get("action") == "MoveTo":
                here = str(st.get("object") or "")
        return bool(g.get("to")) and here == g.get("to")
    if t == "go" and here == g.get("to"):
        return True                                   # already there
    for st in steps:
        a, o = st.get("action"), st.get("object") or ""
        if a == "MoveTo":
            here = o
            if t == "go" and o == g.get("to"):
                return True
        elif a == "Say" and t == "say" and _same_text(o, g.get("object")) and (not g.get("to") or here == g["to"]):
            return True
        elif a == "WaitForUnload" and t == "deliver" and here == g.get("to"):
            return True
    return False


def _same_text(a, b) -> bool:
    norm = lambda s: " ".join(str(s or "").lower().replace(",", " ").replace(".", " ").split())
    return norm(a) == norm(b)


__all__ = ["GoalKind", "GOAL_KINDS", "MANIPULATION", "TEMPLATE_KINDS", "missing_actions", "kinds_for",
           "prompt_lines", "is_template", "conflicts", "SPEAKER_WORDS", "is_speaker", "speaker_slots", "describe", "subgoal_text", "summary", "compile_steps", "reached"]
