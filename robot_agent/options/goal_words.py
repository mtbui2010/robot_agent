"""Option ``goal_words`` (default off): the goal filter compares whole words, trusts the object catalogue first, asks when a name is both an
object and a place, and never changes the receiver of a shared object.

The filter of the understood goals (``goal_fix.drop_invented``) decides "this goal moves a place, so it is not a task" by matching the goal's
object against the ENV places as SUBSTRINGS and aliases: "book" is found inside "bookshelf", and an object that is also an alias of a place
(a "charger" that is the name of the dock) is dropped as an appliance. Three rules, all from the world the robot already has:

* whole words: a name is a place only when it equals the head or an alias of an ENV key (articles ignored), never when it is a part of one;
* catalogue first: a name the robot knows as an OBJECT (its object memory, the manifest ``limits.objects`` names) is an object, and the goal
  is kept; when the same name is also a place (head or alias), the person is asked once which one they mean (hook ``on_goals``);
* a shared object (named by two or more move goals) keeps the receiver each goal has: the receiver repairs of ``goal_fix`` skip it.

No pack data: the catalogue is the robot's own memory and limits; no catalogue = every name is judged by whole words alone. Service
``goal_guard(run, goals)`` returns the guard ``goal_fix`` asks (``place(name)``, ``frozen(goal)``). Hook ``on_goals`` (the question).
Keys: ``enabled`` (legacy ``goal_words``). Lab flag ``goal_words``: ``{rule, name}``.
"""

from __future__ import annotations

import re

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register

_ARTICLES = ("the ", "a ", "an ")
SPEC = OptionSpec(
    name="goal_words",
    keys=(Key("enabled", "bool", False, aliases=("goal_words",)),),
    hooks=("on_goals",),
    order=70,
    enabled="enabled",
    services=("goal_guard",),
    doc="The goal filter compares whole words, trusts the object catalogue first, asks when a name is both an object and a place, and keeps the receiver of a shared object.",
)


def _w(s) -> str:
    t = " ".join(re.findall(r"[\w']+", str(s or "").lower()))
    return next((t[len(a):] for a in _ARTICLES if t.startswith(a)), t)


def catalogue(run, robot) -> set:
    """The names the robot knows as objects: its object memory and the manifest's ``limits.objects``."""
    mem = getattr(getattr(run, "memory", None), "data", None) or {}
    lim = getattr(getattr(robot, "manifest", None), "limits", None) or {}
    return {_w(n) for n in list((mem.get("objects") or {})) + list(lim.get("objects") or {}) if _w(n)}


def place_keys(name: str, places) -> list:
    """ENV keys whose head or alias is, as whole words, the name."""
    n = _w(name)
    if not n:
        return []
    return [k for k, v in places.env.items()
            if n == _w(places.head(k)) or n in {_w(a) for a in (v.get("aliases") or []) if a}]


class Guard:
    """What ``goal_fix`` asks while it filters: which place a name is, and which goals keep their receiver."""

    def __init__(self, known, places, goals):
        self.known, self.places = known, places
        names = [_w(g.get("object")) for g in goals if g.get("type") in ("on", "in") and g.get("object")]
        self.shared = {n for n in names if names.count(n) > 1}

    def place(self, name) -> str:
        """The ENV key the name is (one, whole words, not a catalogue object), else ''."""
        if _w(name) in self.known:
            return ""
        keys = place_keys(name, self.places)
        return keys[0] if len(keys) == 1 else ""

    def frozen(self, goal) -> bool:
        return goal.get("type") in ("on", "in") and _w(goal.get("object")) in self.shared


class GoalWords(Option):
    spec = SPEC

    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault("goal_words", {})

    def goal_guard(self, run, goals):
        return Guard(catalogue(run, self.svc.robot), run.places, goals)

    def _both(self, run):
        """The (goal, place key) pairs whose object is in the catalogue and is also, as whole words, one place."""
        known = catalogue(run, self.svc.robot)
        out = []
        for g in run.goals:
            keys = place_keys(g.get("object"), run.places) if g.get("type") in ("on", "in") else []
            if _w(g.get("object")) in known and len(keys) == 1:
                out.append((g, keys[0]))
        return out

    def on_goals(self, run):
        asked = self._state(run).setdefault("asked", set())
        for g, key in self._both(run):
            obj = _w(g.get("object"))
            if obj in asked:
                continue
            asked.add(obj)
            from robot_agent.core.planning import speech
            head = run.places.head(key)
            q = speech.LocalText("q_which_x", {"what": obj, "options": [obj, key]}, text=f"Which {obj} do you mean: the {obj} itself, or the {head}?")
            yield run.ev("clarify", question=q, round=run.asked + 1, kind="goal_words")
            ans = self.svc.ask(run, q)
            stop = self.svc.cancelled(run)
            if stop:
                yield stop
                return STOP
            a = _w(ans)
            says_place = bool(re.search(r"(?<!\w)" + re.escape(_w(head)) + r"(?!\w)", a)) and not re.search(r"(?<!\w)" + re.escape(obj) + r"(?!\w)", a)
            self.svc.lab("goal_words", rule="ask", name=obj, place=says_place)
            if says_place:
                run.goals = [x for x in run.goals if x is not g]
                run.filtered = list(getattr(run, "filtered", [])) + [g]
        return None


register(SPEC, GoalWords)
