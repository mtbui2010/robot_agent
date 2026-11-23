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
The table above is pack data (Step S07): each kind's needs, prompt line, ``understood``, ``planned_by``, ``conflicts`` and
sub-goal phrase come from the robot's pack (``goal_kinds`` section; the standard pack's file reproduces the old constants),
passed as ``pack`` (None = the default table). What stays code here, with one user each: the extra MoveTo of a ``say`` goal
with a place, ``canonical`` (give -> on) and the optional kind ``give``, ``compile_steps`` and the validator's body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..pack import goal_kinds as kind_data


@dataclass(frozen=True)
class GoalKind:
    name: str
    needs: tuple            # the actions the kind needs; a tuple inside = any one of them
    prompt: str = ""        # the understand prompt's line for it ('' = described in the base prompt)
    understood: str = "capable"     # "always": in the understand schema even when the robot lacks the actions
    planned_by: str = "planner"     # "template": a fixed sequence of steps (compile_steps), no model
    conflicts: str = "default"      # "none": the goal validator (conflicts) does not look at the kind
    subgoal: str = ""               # the sub-goal template (core/pack/goal_kinds.render); '' = describe()


def _table(entries: dict) -> dict:
    return {n: GoalKind(n, **kind_data.fields(e)) for n, e in entries.items()}


# The table of a robot with no goal kinds of its own: the default pack's (robot_agent/packs/household/goal_kinds.yaml), read
# once. A view, as speech.CORE_PHRASES is: a pack whose table equals the default one is read through it (``table``).
GOAL_KINDS: dict = _table(kind_data.default_table())
MANIPULATION = tuple(n for n, k in GOAL_KINDS.items() if k.understood == "always")
TEMPLATE_KINDS = tuple(n for n, k in GOAL_KINDS.items() if k.planned_by == "template")
# Optional kinds of the understand schema (option ``understand_give_kind``): not part of the table above, so the default
# schema and prompt do not change. A ``give`` goal is an ``on`` goal whose destination is a person (``canonical``).
OPTIONAL_KINDS: dict = {
    "give": GoalKind("give", ("Pick", "Place"),
                     '"give" = hand an object to a person: object = the item, from = where it is now or "", to = the '
                     'person ("me" when the speaker receives it). Not for putting an object on a place.'),
}


def table(pack=None) -> dict:
    """The goal-kind table of a resolved pack (``RobotView.pack``): ``GOAL_KINDS`` for no pack or a pack with the default
    table, else the pack's own kinds, in its order."""
    return GOAL_KINDS if kind_data.is_default(pack) else _table(pack.table(kind_data.TABLE))


def missing_actions(goal: dict, actions: Iterable[str], pack=None) -> list:
    """The actions `goal` needs that are not in `actions` (an any-of group by its first name).
    An unknown kind needs an action nobody has: [its type]. ``pack``: the robot's resolved pack (None: the default table)."""
    acts = set(actions)
    kind = table(pack).get(goal.get("type"))
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


def kinds_for(actions: Iterable[str], optional: Iterable[str] = (), pack=None) -> list:
    """The goal kinds the understand step may use for a robot with `actions`: the kinds understood ``always`` (the
    manipulation kinds: a request the robot cannot do is understood, then refused with a reason), then the others, in
    the table's order, only when the robot has their actions. ``optional``: names from ``OPTIONAL_KINDS`` the caller
    switched on, added (as the manipulation kinds are) at the end."""
    acts, kinds = set(actions), table(pack)
    return [k for k, v in kinds.items() if v.understood == "always"] \
        + [k for k, v in kinds.items() if v.understood != "always" and not missing_actions({"type": k}, acts, pack)] \
        + [k for k in optional if k in OPTIONAL_KINDS]


def domain_actions(pack=None) -> list:
    """Every abstract action the goal tables name (sorted): the vocabulary a refusal may say a robot lacks."""
    out = set()
    for kind in list(table(pack).values()) + list(OPTIONAL_KINDS.values()):
        for need in kind.needs:
            out.update(need if isinstance(need, tuple) else (need,))
    return sorted(out)


def lacking(actions: Iterable[str], pack=None) -> list:
    """The abstract actions the goal tables name that a robot with ``actions`` does not have (the missing_action enum)."""
    have = set(actions or ())
    return [a for a in domain_actions(pack) if a not in have]


def cannot_do(goals: list, actions: Iterable[str], pack=None) -> Optional[str]:
    """The actions the goals need that are not in ``actions``, as one text ('Pick, Open'), or None."""
    missing = list(dict.fromkeys(a for g in goals for a in missing_actions(g, actions, pack)))
    return ", ".join(missing) if missing else None


def canonical(goals: list) -> list:
    """The goals with the optional kinds mapped onto the table's: ``give`` -> ``on`` (its destination is the person, the
    path of "bring me X" / a hand-over). Other goals are passed on unchanged."""
    return [{**g, "type": "on"} if g.get("type") == "give" else g for g in goals]


def prompt_lines(kinds: Iterable[str], pack=None) -> str:
    table_ = {**table(pack), **OPTIONAL_KINDS}
    lines = [table_[k].prompt for k in kinds if k in table_ and table_[k].prompt]
    return "".join(f"\n  {line}" for line in lines)


def _kind(t, kinds: dict):
    return kinds.get(t) if isinstance(t, str) else None


def is_template(goals: list, pack=None) -> bool:
    """Every goal is of a kind planned by ``template`` (a fixed sequence of steps, ``compile_steps``)."""
    kinds = table(pack)
    return bool(goals) and all(getattr(_kind(g.get("type"), kinds), "planned_by", None) == "template" for g in goals)


def dedupe(goals: list) -> list:
    """The goals without an exact repeat (same type, object, from, to), the first one kept: a read-back patch that
    puts the whole list in twice does not make the robot do everything twice."""
    seen, out = set(), []
    for g in goals:
        k = tuple(str(g.get(f) or "") for f in ("type", "object", "from", "to"))
        if k in seen:
            continue
        seen.add(k)
        out.append(g)
    return out


def conflicts(goals: list, name=None, declare: bool = False, pack=None) -> tuple:
    """(goals, question|None): the goal validator. Two destinations for one object is a question
    for the person; an open and a close of one container keep the last one (the final state). ``name``: how a
    place key is said (default: 'shelf@kitchen' -> 'shelf in the kitchen'). ``declare`` (option ``dialog_agree``): the question
    comes as a ``speech.LocalText`` with the same English text that declares a choice between the places (intent
    ``choose_place``, the keys as options), so that the answer can settle the option not taken. A kind whose table entry
    says ``conflicts: none`` (``pack``: the robot's resolved pack) is not looked at."""
    q, dest, off = None, {}, {n for n, k in table(pack).items() if k.conflicts == "none"}
    for g in goals:
        if g.get("type") in ("on", "in") and g.get("object") and g.get("type") not in off:
            dest.setdefault(g["object"], [])
            if g.get("to") and g["to"] not in dest[g["object"]]:
                dest[g["object"]].append(g["to"])
    for obj, tos in dest.items():
        if len(tos) > 1:
            nice = [name(t) if name is not None else t.replace("@", " in the ") for t in tos]
            q = f"Where should the {obj} go in the end: {' or '.join(nice)}? It cannot be in two places."
            if declare:
                from .speech import LocalText
                q = LocalText("_two_places_q", text=q, ask={"intent": "choose_place", "options": list(tos), "about": str(obj)})
            break
    last = {}
    for k, g in enumerate(goals):
        if g.get("type") in ("open", "close") and g.get("type") not in off:
            last[g.get("object")] = k
    kept = [g for k, g in enumerate(goals)
            if g.get("type") not in ("open", "close") or g.get("type") in off or last.get(g.get("object")) == k]
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


class _Slots(dict):
    def __missing__(self, key):            # a slot the goal does not have reads as ''
        return ""


def subgoal_text(g: dict, places=None, pack=None) -> str:
    """One goal as the sub-goal phrase the planner expands (``AssistLoop._subgoals``, SAGE-v2 F1.2): what to do
    with which object, from where (when known) to where, with the ENV keys the plan must use. The phrase is the
    kind's ``subgoal`` template (``pack``: the robot's resolved pack); a kind with none, or none that holds: ``describe``."""
    t, o = g.get("type"), g.get("object") or ""
    src = f" (it is at {g['from']})" if g.get("from") else (
        f" (look for it at {' or '.join(g['from_any'])})" if g.get("from_any") else "")
    kind = _kind(t, table(pack))
    if kind is not None and kind.subgoal:
        slots = _Slots({k: v for k, v in g.items() if isinstance(k, str)})
        slots.update(object=o, src=src, to=g.get("to"), who=g.get("to") or g.get("person") or "")
        text = kind_data.render(kind.subgoal, slots)
        if text is not None:
            return text
    return describe(g)


def subgoals(goals: list, places=None, pack=None) -> list:
    """The sub-goal phrase of each goal, in goal order (``subgoal_text``; a pack with the default table goes through the
    module's ``subgoal_text`` as it is called, so that it stays the one place a test patches)."""
    if table(pack) is GOAL_KINDS:
        return [subgoal_text(g, places) for g in goals]
    return [subgoal_text(g, places, pack) for g in goals]


def describe(g: dict) -> str:
    """The goal in the obs / goal-text form: '<type> <object>[ from <from>][ to <to>]'."""
    return (f"{g['type']} {g.get('object') or ''}".rstrip() + (f" from {g['from']}" if g.get("from") else "")
            + (f" to {g['to']}" if g.get("to") else ""))


def goal_text(goals) -> str:
    """The goals as the planner reads them: 'on apple from desk to dining table; ...'."""
    return "; ".join(describe(g) for g in goals)


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


def ends_at(goal: dict, obj: str, place: str, ground=None, langs=None) -> bool:
    """Pure: ``goal`` (an ``on`` / ``in`` goal) puts ``obj`` at ``place``: its object and ``obj`` are one name (the words of one are whole
    words of the other, ``names.has``) and its destination is ``place`` or grounds to it (``ground``: text -> place key or None)."""
    from . import names
    o, to = str(goal.get("object") or ""), str(goal.get("to") or "")
    if goal.get("type") not in ("on", "in") or not place or not to or not (names.has(o, obj, langs) or names.has(obj, o, langs)):
        return False
    return to == place or (ground is not None and ground(to) == place)


def _same_text(a, b) -> bool:
    norm = lambda s: " ".join(str(s or "").lower().replace(",", " ").replace(".", " ").split())
    return norm(a) == norm(b)


__all__ = ["GoalKind", "GOAL_KINDS", "MANIPULATION", "TEMPLATE_KINDS", "table", "missing_actions", "kinds_for", "lacking",
           "cannot_do", "subgoals", "prompt_lines", "is_template", "dedupe", "conflicts", "SPEAKER_WORDS", "is_speaker", "speaker_slots", "describe", "goal_text", "subgoal_text", "summary", "compile_steps", "reached", "ends_at"]
