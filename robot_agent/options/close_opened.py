"""Option ``close_opened`` (default off): a container the plan opens is closed again once the robot is done with it, unless a goal wants it open.

What: the grounded plan (hook ``on_plan``) is replayed on the belief with the engine's action model and the robot's own capabilities. For every
container an Open step of the plan opens (the pack's ``open`` role) and the replay leaves open, and that no ``open`` goal names: a Close of it that
the model refuses because the hand is full (``hand_full``: one hand) is taken out, and ``MoveTo C`` (when the robot is elsewhere) and ``Close C``
go in at the first point after the robot's last step at C where the hand is empty again (after the release of what it took out). A plan whose
hand never empties after that is left as it is.
Why (failure class: container left open, e.g. a hand goal): the planner writes "Open C, Find X, Pick X, Close C" and the hand-over after it. The
Close cannot run while holding X; the gate says "do it before the Pick" (impossible, X is inside), the planner or the gate drops the Close and the
robot ends with C open. The gate's own hoist (``plan_repair.op_hoist``) moves such a Close only when the plan it edits holds the release (not one an
option added) and never adds a Close the plan left out.
How to switch it off: key ``enabled`` (legacy ``close_opened``), off by default. No ``open`` / ``close`` role in ``planner.action_roles``, or roles
that name actions the action model does not know, = the plan is unchanged (fail open); no ``move`` role = a Close is only added where the robot is.

Hooks ``on_start`` (one lab event per distinct edit in a run) and ``on_plan`` at order 89, after ``precond_repair``, ``plan_complete`` and
``plan_release`` (the release they add is seen). Lab flag ``close_opened``: ``{container, fix}`` (``moved``: a hand-full Close went after the
release; ``added``: the plan had no Close of it).
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import action_model

HAND_FULL = "hand_full"                 # the action model's code: Open / Close with the one hand full
KEEP_OPEN = "open"                      # the goal kind that wants a container open at the end

SPEC = OptionSpec(
    name="close_opened",
    keys=(Key("enabled", "bool", False, aliases=("close_opened",)),),
    hooks=("on_start", "on_plan"),
    order=89,
    enabled="enabled",
    doc="A container the plan opens is closed again after the robot is done with it (a hand-full Close goes after the release), unless a goal wants it open.",
)


def replay(plan, world, caps) -> list:
    """The belief before each step of ``plan`` and after the last one (len(plan) + 1 states)."""
    w, out = world.copy(), []
    for st in plan:
        out.append(w.copy())
        action_model.apply(st, w, inplace=True, capabilities=caps)
    return out + [w]


def opened_by(plan, states, open_) -> list:
    """The containers an Open step of ``plan`` opens (not already open before it), in plan order."""
    return list(dict.fromkeys(st.get("object") for st, w in zip(plan, states)
                              if st.get("action") == open_ and st.get("object") and st.get("object") not in w.opened))


class CloseOpened(Option):
    def on_start(self, run) -> None:
        self._told = set()

    def on_plan(self, steps, run):
        """``steps`` with every container the plan opened closed after its use; None when nothing changes or no role is declared."""
        roles = {r: self.svc.role(r) for r in ("move", "open", "close")}
        if not steps or not (roles["open"] and roles["close"]):
            return None
        caps = self.svc.robot.model_capabilities()
        plan = list(steps)
        for c in opened_by(plan, replay(plan, run.world, caps), roles["open"]):
            if not self._wanted_open(c, run):
                plan = self._close(plan, c, run.world, caps, roles)
        return plan if plan != list(steps) else None

    @staticmethod
    def _wanted_open(c, run) -> bool:
        head = getattr(run.places, "head", None)
        names = {c, head(c) if callable(head) else c}
        return any(isinstance(g, dict) and g.get("type") == KEEP_OPEN and g.get("object") in names for g in run.goals or [])

    def _close(self, plan, c, world, caps, roles) -> list:
        """``plan`` with the hand-full Closes of ``c`` out and one Close of ``c`` where the hand is empty after its last use; unchanged if none fits."""
        close, move = roles["close"], roles["move"]
        states = replay(plan, world, caps)
        full = {i for i, st in enumerate(plan) if st.get("action") == close and st.get("object") == c
                and HAND_FULL in {v.code for v in action_model.check(st, states[i], capabilities=caps)}}
        rest = [st for i, st in enumerate(plan) if i not in full]
        states = replay(rest, world, caps)
        if c not in states[-1].opened:
            return plan
        last = max(i for i, st in enumerate(rest) if st.get("object") == c or (states[i].arrived == c and st.get("action") != move))
        k = next((j for j in range(last + 1, len(rest) + 1) if not states[j].holding), None)
        if k is None or (states[k].arrived != c and not move):
            return plan
        add = ([{"action": move, "object": c}] if states[k].arrived != c else []) + [{"action": close, "object": c}]
        self._tell(c, "moved" if full else "added")
        return rest[:k] + add + rest[k:]

    def _tell(self, c, fix) -> None:
        """One lab event per distinct edit in a run (the gate scores many candidate plans through this hook)."""
        told = self.__dict__.setdefault("_told", set())
        if (c, fix) not in told:
            told.add((c, fix))
            self.svc.lab(SPEC.name, container=c, fix=fix)


register(SPEC, CloseOpened)
