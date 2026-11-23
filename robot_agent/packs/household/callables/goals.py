"""The goal-reach rules of the standard (household) pack: one rules object per goal kind, ``std.on`` ... ``std.deliver``.

What: for each household kind, ``reached`` (do the plan's steps reach the goal), ``holds`` (does the goal hold in the
belief) and ``missing`` (the planner's text for an unmet goal), moved from the if-chains of ``AssistLoop._reaches``,
``_goals_hold`` and ``_missing`` (robot_agent/core/planning/assist_loop.py:1248-1349 at 2d57c99, the same code as
8002347), one branch per kind, conditions and texts unchanged; ``phrase`` (Step S08: the read-back phrase of a goal, the
branches of ``AssistLoop._goal_phrase`` at ef42ad3, one per kind: a ``LocalText`` with the key, the slots and the English
text); plus what every household kind shares: ``simulate`` (what
the steps leave open, where each object ends) and ``reached_by`` (is an ``on`` goal reached by that end position), the
bodies of ``_simulate_goals`` and ``_on_reached``.
Why: the loop names no goal kind (docs/proposals/generalize_core_design.md, "Goal kinds", Step S05). ``goal_kinds.yaml``
next to ``pack.yaml`` names these objects per kind; ``robot_agent/core/pack/goal_semantics.py`` asks them.
How: the template kinds keep calling ``goals.reached`` / ``goals.summary`` through the module (a test that patches them
still reaches them); an ``on`` goal asks ``ctx.reached_by`` (the loop's seam ``_on_reached``), which comes back here.
Off: a robot pack that removes a kind (``remove: {goal_kinds: [...]}``) or names other rules for it does not use these for
that kind; its goals of that kind are then vacuous (reached, holding, no text), as an unknown kind always was.
"""

from __future__ import annotations

from robot_agent.core.pack import register
from robot_agent.core.planning import exec_integrity, persons, speech
from robot_agent.core.planning import goals as goal_kinds

_L = speech.LocalText


def _fmt_en(key: str, **slots) -> str:
    return speech.CORE_PHRASES[key].format(**slots)


class _Household:
    """The rules every household kind shares. A kind's subclass gives ``_unmet`` (the steps leave the goal unmet),
    ``_text`` (the planner's line for it) and ``holds``."""

    def simulate(self, steps, world, released) -> tuple:
        """(opened, on): what the steps leave open and where each object ends: on[obj] = (action, where);
        a release action (hand-over) is ("release", the person / place the robot is at)."""
        holding, opened, on, at = world.holding, set(world.opened), {}, world.arrived
        for st in steps:
            a, o = st.get("action"), st.get("object")
            if a == "MoveTo":
                at = o
            elif a == "Pick":
                holding = o
            elif a in ("Place", "PutIn") and holding:
                on[holding] = (a, o); holding = None
            elif a in released and holding:
                on[holding] = ("release", o or at); holding = None
            elif a == "Open":
                opened.add(o)
            elif a == "Close":
                opened.discard(o)
        return opened, on

    def reached_by(self, g, got, places) -> bool:
        """Is the ``on`` goal ``g`` reached by ``got`` = (action, where)? A Place there; for a goal to a person
        (F2.2) also the robot's hand-over there, and only the hand-over when the goal asks for the hand."""
        if not got or got[1] != g.get("to"):
            return False
        to_person = places is not None and persons.is_person(places, g.get("to"))
        if g.get("hand") and to_person:
            return got[0] == "release"
        return got[0] == "Place" or (got[0] == "release" and to_person)

    def reached(self, goal, steps, world, ctx) -> bool:
        return not self._unmet(goal, steps, world, ctx)

    def missing(self, goal, steps, world, ctx):
        return self._text(goal) if self._unmet(goal, steps, world, ctx) else None


class On(_Household):
    def _unmet(self, g, steps, world, ctx) -> bool:
        return not ctx.reached_by(g, exec_integrity.lookup_object(ctx.sim[1], g["object"]))

    def _text(self, g) -> str:
        return (f"{'Hand over' if g.get('hand') else 'Place'} the {g['object']} "
                f"{'to' if g.get('hand') else 'on'} {g['to']}")

    def holds(self, g, belief, ctx) -> bool:
        if belief.placed.get(g["object"]) != g["to"] or belief.world.holding == g["object"]:
            return False
        return not (g.get("hand") and g["object"] not in ctx.handed)       # a hand delivery holds only after a hand-over

    def phrase(self, g, places, nice):
        obj = g.get("object") or ""
        o = g.get("said") or g.get("name_local") or obj        # name_local: the object's name in the user's language (F4.8)
        if places is not None and persons.is_person(places, g.get("to")):
            you = bool((places.env.get(g["to"]) or {}).get("speaker"))
            key = ("rb_hand_you" if you else "rb_hand_person") if g.get("hand") else ("rb_to_you" if you else "rb_to_person")
            return _L(key, {"object": o, "person": g["to"]}, text=_fmt_en(key, object=obj, person=nice(g["to"])))
        if g.get("near_person") and places is not None:
            you = bool((places.env.get(g["near_person"]) or {}).get("speaker"))
            key = "rb_on_near_you" if you else "rb_on_near_person"
            return _L(key, {"object": o, "place": g["to"], "person": g["near_person"]},
                      text=_fmt_en(key, object=obj, place=nice(g["to"]), person=nice(g["near_person"])))
        if g.get("from") and not (places is not None and persons.is_person(places, g["from"])):
            return _L("rb_on_from", {"object": o, "source": g["from"], "place": g["to"]},
                      text=f"put the {obj} from the {nice(g['from'])} on the {nice(g['to'])}")
        return _L("rb_on", {"object": o, "place": g["to"]}, text=f"put the {obj} on the {nice(g['to'])}")


class In(_Household):
    def _unmet(self, g, steps, world, ctx) -> bool:
        return exec_integrity.lookup_object(ctx.sim[1], g["object"]) != ("PutIn", g["to"])

    def _text(self, g) -> str:
        return f"PutIn the {g['object']} into {g['to']}"

    def holds(self, g, belief, ctx) -> bool:
        return not (belief.placed.get(g["object"]) != g["to"] or belief.world.holding == g["object"])

    def phrase(self, g, places, nice):
        obj = g.get("object") or ""
        o = g.get("said") or g.get("name_local") or obj
        return _L("rb_in", {"object": o, "place": g["to"]}, text=f"put the {obj} in the {nice(g['to'])}")


class Open(_Household):
    def _unmet(self, g, steps, world, ctx) -> bool:
        return g["object"] not in ctx.sim[0]

    def _text(self, g) -> str:
        return f"Open {g['object']}"

    def holds(self, g, belief, ctx) -> bool:
        return g["object"] in belief.world.opened

    def phrase(self, g, places, nice):
        obj = g.get("object") or ""
        return _L("rb_open", {"place": obj}, text=f"open the {nice(obj)}")


class Close(_Household):
    def _unmet(self, g, steps, world, ctx) -> bool:
        return g["object"] in ctx.sim[0]

    def _text(self, g) -> str:
        return f"Close {g['object']}"

    def holds(self, g, belief, ctx) -> bool:
        return g["object"] not in belief.world.opened

    def phrase(self, g, places, nice):
        obj = g.get("object") or ""
        return _L("rb_close", {"place": obj}, text=f"close the {nice(obj)}")


class Template(_Household):
    """A kind of fixed steps (go / say / deliver): reached by the steps themselves (``goals.reached``)."""

    def _unmet(self, g, steps, world, ctx) -> bool:
        return not goal_kinds.reached(g, steps, world.arrived, last=ctx.last)

    def _text(self, g) -> str:
        return goal_kinds.summary(g)

    def holds(self, g, belief, ctx) -> bool:
        return bool(goal_kinds.reached(g, list(belief.done), belief.at))

    def phrase(self, g, places, nice):
        """The ``say`` phrase (``go`` and ``deliver`` give their own); with no places, the template's plain summary."""
        if places is None:
            return goal_kinds.summary(g)
        obj, who = g.get("object") or "", g.get("to") or ""
        if who and persons.is_person(places, who):
            return _L("rb_say", {"person": who, "text": obj}, text=f"tell {nice(who)}: \"{obj}\"")
        if who:
            return _L("rb_say_at", {"place": who, "text": obj}, text=f"say \"{obj}\" in the {nice(who)}")
        if g.get("person"):
            return _L("rb_say", {"person": g["person"], "text": obj}, text=f"tell {g['person']}: \"{obj}\"")
        return _L("rb_say_here", {"text": obj}, text=f"say \"{obj}\"")


class Go(Template):
    def holds(self, g, belief, ctx) -> bool:
        if not ctx.last:
            return Template.holds(self, g, belief, ctx)
        # the last go goal holds where the plan's steps end (not where it once passed, or started); the goal
        # checks' own looks do not count (_observe_goals goes back there)
        return not (not goal_kinds.reached(g, list(belief.done), belief.at, last=True) and belief.world.arrived != g.get("to"))

    def phrase(self, g, places, nice):
        if places is None:
            return goal_kinds.summary(g)
        return _L("rb_go", {"place": g.get("to")}, text=f"go to the {nice(g.get('to'))}")


class Deliver(Template):
    def phrase(self, g, places, nice):
        return goal_kinds.summary(g)


RULES = {"on": On(), "in": In(), "open": Open(), "close": Close(), "go": Go(), "say": Template(), "deliver": Deliver()}
for _kind, _rules in RULES.items():
    register("goal.rules", f"std.{_kind}", _rules)

__all__ = ["RULES", "On", "In", "Open", "Close", "Template", "Go", "Deliver"]
