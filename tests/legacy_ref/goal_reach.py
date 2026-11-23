"""Reference copy for Step S05: ``_releases``, ``_simulate_goals``, ``_on_reached``, ``_missing``, ``_reaches`` and
``_goals_hold`` as they were in robot_agent/core/planning/assist_loop.py lines 1248-1349 at commit 2d57c99 (the tip of
generalize-core before S05; the same bodies as 8002347), copied verbatim as the methods of a class ``Old``. What differs
from the file: the class is named ``Old`` (the bodies say ``AssistLoop``, bound to ``Old`` below); the relative imports are
absolute; ``_releases`` tests ``isinstance(self, AssistLoop)`` against the REAL AssistLoop class, so a loop given to the
copy behaves as it did; ``_missing`` calls ``Old._simulate_goals(self, ...)`` / ``Old._on_reached(self, ...)`` where the
file says ``self._simulate_goals(...)`` / ``self._on_reached(...)``, because the test hands the copy a real loop whose own
methods are the new ones. Never edited: it is the old behaviour that tests/test_goal_semantics_diff.py compares the
pack-chosen rules with."""

from __future__ import annotations

from robot_agent.core.planning import exec_integrity, persons
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning.assist_loop import AssistLoop as _RealLoop


class Old:
    def _releases(self) -> set:
        """The robot's own actions that release the held object (RobotView.release_actions: a hand-over)."""
        if not isinstance(self, _RealLoop):
            return set()
        robot = getattr(self, "robot", None)
        try:
            return set(robot.release_actions()) if robot is not None else set()
        except Exception:
            return set()

    def _simulate_goals(self, steps, world) -> tuple:  # self: the loop, or None (no release actions)
        """(opened, on): what the steps leave open and where each object ends: on[obj] = (action, where);
        a release action (hand-over) is ("release", the person / place the robot is at)."""
        holding, opened, on, at = world.holding, set(world.opened), {}, world.arrived
        rel = AssistLoop._releases(self) if self is not None else set()
        for st in steps:
            a, o = st.get("action"), st.get("object")
            if a == "MoveTo":
                at = o
            elif a == "Pick":
                holding = o
            elif a in ("Place", "PutIn") and holding:
                on[holding] = (a, o); holding = None
            elif a in rel and holding:
                on[holding] = ("release", o or at); holding = None
            elif a == "Open":
                opened.add(o)
            elif a == "Close":
                opened.discard(o)
        return opened, on

    def _on_reached(self, g, got) -> bool:
        """Is the ``on`` goal ``g`` reached by ``got`` = (action, where)? A Place there; for a goal to a person
        (F2.2) also the robot's hand-over there, and only the hand-over when the goal asks for the hand."""
        if not got or got[1] != g.get("to"):
            return False
        places = getattr(self, "_env_places", None) if self is not None else None
        to_person = places is not None and persons.is_person(places, g.get("to"))
        if g.get("hand") and to_person:
            return got[0] == "release"
        return got[0] == "Place" or (got[0] == "release" and to_person)

    def _missing(self, steps, world, goals):
        opened, on = Old._simulate_goals(self, steps, world)
        out = []
        for g in goals:
            t = g["type"]
            if t == "on" and not Old._on_reached(self, g, exec_integrity.lookup_object(on, g["object"])):
                out.append(f"{'Hand over' if g.get('hand') else 'Place'} the {g['object']} "
                           f"{'to' if g.get('hand') else 'on'} {g['to']}")
            elif t == "in" and exec_integrity.lookup_object(on, g["object"]) != ("PutIn", g["to"]):
                out.append(f"PutIn the {g['object']} into {g['to']}")
            elif t == "open" and g["object"] not in opened:
                out.append(f"Open {g['object']}")
            elif t == "close" and g["object"] in opened:
                out.append(f"Close {g['object']}")
            elif t in goal_kinds.TEMPLATE_KINDS and not goal_kinds.reached(g, steps, world.arrived,
                                                                           last=g is goals[-1]):
                out.append(goal_kinds.summary(g))
        return out

    @staticmethod
    def _reaches(steps, world, goals, loop=None) -> bool:
        """Do ``steps`` from ``world`` reach ``goals``? With ``loop`` (bound calls pass it: ``self._reaches``
        is wrapped in ``__init__``) a hand-over to a person counts too (F2.2)."""
        opened, on = AssistLoop._simulate_goals(loop, steps, world)
        for g in goals:
            t = g["type"]
            if t == "on" and not AssistLoop._on_reached(loop, g, exec_integrity.lookup_object(on, g["object"])):
                return False
            if t == "in" and exec_integrity.lookup_object(on, g["object"]) != ("PutIn", g["to"]):
                return False
            if t == "open" and g["object"] not in opened:
                return False
            if t == "close" and g["object"] in opened:
                return False
            if t in goal_kinds.TEMPLATE_KINDS and not goal_kinds.reached(g, steps, world.arrived,
                                                                         last=g is goals[-1]):
                return False
        return bool(goals) or bool(steps)

    def _goals_hold(self, goals, world, placed, done=(), at=None) -> bool:
        """Every goal holds in the belief: objects placed, containers open / closed, and the go / say /
        deliver goals reached by the steps `done` from `at` (the run's start)."""
        for g in goals:
            t = g["type"]
            if t == "go" and g is goals[-1]:
                # the last go goal holds where the plan's steps end (not where it once passed, or started); the goal
                # checks' own looks do not count (_observe_goals goes back there)
                if not goal_kinds.reached(g, list(done), at, last=True) and world.arrived != g.get("to"):
                    return False
            elif t in goal_kinds.TEMPLATE_KINDS and not goal_kinds.reached(g, list(done), at):
                return False
            if t in ("on", "in") and (placed.get(g["object"]) != g["to"] or world.holding == g["object"]):
                return False
            if t == "on" and g.get("hand") and g["object"] not in getattr(self, "_handed", set()):
                return False                                  # a hand delivery holds only after a hand-over
            if t == "open" and g["object"] not in world.opened:
                return False
            if t == "close" and g["object"] in world.opened:
                return False
        return True


AssistLoop = Old          # the bodies above name the class ``AssistLoop`` (as in the file); here it is the copy
