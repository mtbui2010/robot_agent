"""Option ``partial_end`` (default off): a run whose goals hold while an item of the request stays open ends ``partial`` and names what was
done and what is missing, instead of saying done.

What: at the end of a run whose goals were seen to hold (hook ``on_goals_met``, after ``hazard``'s partial-refusal sentence), the engine
service ``open_items(run)`` lists the request items the options report as left open (service ``ledger_open``; today ``goal_ledger``: a
``go`` goal the engine's goal filter dropped with no evidence). An item the end state already satisfies (one of the run's goals, a ``go``
place the robot stands at, an object the run put at its place) is not missing. When one is missing the run ends with status ``partial``,
the ``done`` event's field ``partial`` ({done, missing}) and the language pack's phrase ``say_partial`` with the done goals and the missing
items (the robot's i18n wins; a language without the phrase says the English one through the translator).
Why: the failure class "the run said done with an item of the request unresolved" (goal_dropped: the read-back never named the dropped
goal and the run claimed a success it did not have). A complete success is never changed: no open item, no done goal, or no phrase in
any pack = the run ends as before.
Switch off: key ``enabled`` False (the default; run option ``partial_end``). Lab flag ``partial_end``: ``{done, missing}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import speech

PHRASE = "say_partial"
STATUS = "partial"

SPEC = OptionSpec(
    name="partial_end",
    keys=(Key("enabled", "bool", False, aliases=("partial_end",)),),
    hooks=("on_goals_met",),
    order=96,
    enabled="enabled",
    doc="A run whose goals hold while a request item stays open ends 'partial', naming the done and the missing items (the language pack's phrase).",
)


def same_goal(a: dict, b: dict) -> bool:
    """The same goal: type, object and destination ('' and None are the same)."""
    return all((a.get(k) or "") == (b.get(k) or "") for k in ("type", "object", "to"))


def satisfied(item: dict, run) -> bool:
    """Does the end state satisfy ``item``: it is one of the run's goals, a ``go`` place the robot stands at, an object the run put there?"""
    if any(same_goal(item, g) for g in getattr(run, "goals", None) or []):
        return True
    to = item.get("to")
    if not to:
        return False
    if item.get("type") == "go":
        return getattr(getattr(run, "world", None), "arrived", None) == to
    return (getattr(run, "placed", None) or {}).get(item.get("object")) == to


def missing(items, run) -> list:
    """The open ``items`` (goal dicts) the end state does not satisfy, each once, in their order."""
    out = []
    for i in items or []:
        if isinstance(i, dict) and not satisfied(i, run) and not any(same_goal(i, o) for o in out):
            out.append(i)
    return out


def sentence(robot, lang, done, miss, phrase_of):
    """The ``say_partial`` phrase as a LocalText with the slots ``done`` and ``missing`` (the goals' read-back phrases in a row), or None
    when no pack has the phrase in ``lang`` or in English. ``phrase_of(goal)``: the read-back phrase of one goal."""
    tpl = speech.phrase(robot, PHRASE, lang) or speech.phrase(robot, PHRASE, "en")
    if not tpl:
        return None
    slots = {"done": speech.LocalText.seq([phrase_of(g) for g in done]),
             "missing": speech.LocalText.seq([phrase_of(g) for g in miss])}
    en = speech.phrase(robot, PHRASE, "en") or tpl
    try:
        text = en.format(**{k: str(v) for k, v in slots.items()})
    except (KeyError, IndexError, ValueError):
        return None
    return speech.LocalText(PHRASE, slots, text=text)


class PartialEnd(Option):
    spec = SPEC

    def on_goals_met(self, run):
        """The goals hold: end ``partial`` when an open item of the request is missing, else None (the run ends as before)."""
        done = list(getattr(run, "goals", None) or [])
        miss = missing(self.svc.open_items(run), run)
        if not done or not miss:
            return None
        say = sentence(self.svc.robot, getattr(run, "lang", None) or "en", done, miss,
                       lambda g: self.svc.goal_phrase(g, run.places))
        if say is None:
            return None
        part = {"done": [goal_kinds.describe(g) for g in done], "missing": [goal_kinds.describe(g) for g in miss]}
        self.svc.lab(SPEC.name, **part)
        return self.svc.finish(run, STATUS, msg=STATUS + ": " + "; ".join(part["missing"]), say=say, partial=part)


register(SPEC, PartialEnd)
