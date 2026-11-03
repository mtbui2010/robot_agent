"""Symbolic plan repair: the certified gate's own edits, before any LLM edit (SAGE-v2 F1.3, no LLM call).

The gate's findings used to go back to the planner as text ("do it before the Pick", "already placed; delete
the steps that move it again"), and a 7-8B model rarely fixed them: it cannot move a step from one sub-goal
block to another, and every edit cost one LLM call of the run's budget (gate30 round 1: the gate did not converge
in 9 of 53 failed episodes). These operators fix the findings whose fix is mechanical, each proved by the plan
checker: an edit is kept only when the plan's critical findings strictly decrease (``score``), so a repair never
makes a plan worse, and every kept edit is a note.

1. ``hoist``: an Open / Close with the hand full. Open C while holding X goes, with a MoveTo C, to just before
   the run that fetched X (MoveTo / Find / Pick of X); a Close C while holding X goes after the release of X.
2. ``dedupe``: a second Find / Pick ... Place of an object already at its goal place is removed with its run.
3. ``insert``: a MoveTo P before a Place / PutIn / Open / Close at P the robot is not at; a Find X before a
   Pick X that has none.
4. ``room``: a MoveTo to a room (not a place) goes to the next step's place in that room, else the goal place in
   that room, else it is removed.
5. ``action``: the release fits the goal: Place -> PutIn into an ``in`` goal's container, PutIn -> Place onto an
   ``on`` goal's surface, Place on a person -> the robot's hand-over action when the goal is a hand delivery.
6. ``complete`` (OFF by default, ``gate_complete``): a goal no step reaches gets its canonical segment at the end
   (fetch, deliver). It writes steps the planner did not, so it is counted apart (``completed_by_gate``).

Block-aware: the planner's sub-goal blocks (``SubgoalRepair``) are edited in place, so a later LLM block edit
starts from the repaired blocks (an edit of the flat plan alone would be overwritten by the planner's next
``join_blocks``). Robot-agnostic: places are the ENV keys, the rules the shared action model's, the release
action the robot's own (``Capabilities.release_actions``).
"""

from __future__ import annotations

from typing import Callable, Optional

from . import action_model

RELEASES = ("Place", "PutIn")
OPS = ("hoist", "dedupe", "insert", "room", "action", "complete")


def _st(a, o, **kw) -> dict:
    return {"action": a, "object": o, **kw}


class _Ctx:
    def __init__(self, world, places, goals, caps, actions, release, hand_goal):
        self.world, self.places, self.goals = world, places, goals
        self.caps, self.actions, self.release, self.hand_goal = caps, set(actions or ()), release, hand_goal

    def goal_of(self, obj) -> Optional[dict]:
        return next((g for g in self.goals if g.get("type") in ("on", "in") and g.get("object") == obj), None)

    def sim(self, steps):
        """[(world before step i, violation codes of step i)] for the plan as written."""
        w, out = self.world.copy(), []
        for st in steps:
            codes = {v.code for v in action_model.check(st, w, capabilities=self.caps)}
            out.append((w.copy(), codes))
            action_model.apply(st, w, inplace=True, capabilities=self.caps)
        return out


# ── operators: each returns a new owned plan [(owner, step)] or None ─────────────────────────────────────────
def _fetch_start(F, p) -> int:
    """Index where the run that fetched the object picked at ``p`` starts (its Find and the MoveTo before it)."""
    s = p
    if s > 0 and F[s - 1][1].get("action") == "Find":
        s -= 1
    if s > 0 and F[s - 1][1].get("action") == "MoveTo":
        s -= 1
    return s


def op_hoist(F, cx: _Ctx):
    if cx.caps.hands >= 2:
        return None
    sim = cx.sim([s for _o, s in F])
    for i, (w, codes) in enumerate(sim):
        a, c = F[i][1].get("action"), F[i][1].get("object")
        if a not in ("Open", "Close") or "hand_full" not in codes or not w.holding:
            continue
        held = w.holding
        if a == "Open":
            p = max((j for j in range(i) if F[j][1].get("action") == "Pick"
                     and F[j][1].get("object") in (held, "")), default=None)
            if p is None:
                return None
            s = _fetch_start(F, p)
            owner = F[s][0]
            pre = [] if sim[s][0].arrived == c else [(owner, _st("MoveTo", c, grounded=True))]
            return F[:s] + pre + [(owner, dict(F[i][1]))] + F[s:i] + F[i + 1:]
        # Close while holding: after the release of the held object, at the container
        r = next((j for j in range(i + 1, len(F)) if F[j][1].get("action") in RELEASES + tuple(cx.release)), None)
        if r is None:
            return None
        owner = F[r][0]
        rest = F[:i] + F[i + 1:r + 1]
        back = [] if F[r][1].get("object") == c else [(owner, _st("MoveTo", c, grounded=True))]
        return rest + back + [(owner, dict(F[i][1]))] + F[r + 1:]
    return None


def op_dedupe(F, cx: _Ctx):
    placed, holding = {}, cx.world.holding
    for i, (_o, st) in enumerate(F):
        a, o = st.get("action"), st.get("object")
        g = cx.goal_of(o) if a in ("Find", "Pick") else None
        if g is not None and placed.get(o) == g.get("to") and holding != o:
            end = next((j for j in range(i, len(F)) if F[j][1].get("action") in RELEASES + tuple(cx.release)),
                       len(F) - 1)
            s = i - 1 if i > 0 and F[i - 1][1].get("action") == "MoveTo" else i
            return F[:s] + F[end + 1:]
        if a == "Pick":
            holding = o
        elif (a in RELEASES or a in cx.release) and holding:
            placed[holding], holding = o, None
    return None


def op_insert(F, cx: _Ctx):
    sim = cx.sim([s for _o, s in F])
    for i, (w, codes) in enumerate(sim):
        owner, st = F[i]
        a, o = st.get("action"), st.get("object")
        if a in RELEASES + ("Open", "Close") and ({"place_wrong_location", "interact_without_find"} & codes) \
                and o in cx.places.env and w.arrived != o:
            return F[:i] + [(owner, _st("MoveTo", o, grounded=True))] + F[i:]
        if a == "Pick" and not w.holding and ({"no_find_before_pick", "pick_not_found"} & codes) and o:
            return F[:i] + [(owner, _st("Find", o))] + F[i:]
    return None


def op_room(F, cx: _Ctx):
    for i, (owner, st) in enumerate(F):
        o = str(st.get("object") or "")
        if st.get("action") != "MoveTo" or not (o.startswith("@") or not st.get("grounded", True)):
            continue
        room = o.lstrip("@")
        in_room = lambda k: cx.places.room(k) == room                       # noqa: E731
        nxt = next((s.get("object") for _o, s in F[i + 1:] if s.get("object") in cx.places.env
                    and s.get("action") in ("MoveTo",) + RELEASES + ("Open", "Close")), None)
        to = nxt if nxt and in_room(nxt) else next(
            (g.get(k) for g in cx.goals for k in ("to", "from") if g.get(k) in cx.places.env and in_room(g.get(k))),
            None)
        if to:
            return F[:i] + [(owner, _st("MoveTo", to, grounded=True))] + F[i + 1:]
        return F[:i] + F[i + 1:]
    return None


def op_action(F, cx: _Ctx):
    holding = cx.world.holding
    hand = next(iter(cx.release), None)
    for i, (owner, st) in enumerate(F):
        a, o = st.get("action"), st.get("object")
        if a == "Pick":
            holding = o
            continue
        if a not in RELEASES or not holding:
            continue
        g = cx.goal_of(holding)
        if g is not None and g.get("to") == o:
            want = None
            if g["type"] == "in" and a == "Place" and "PutIn" in cx.actions:
                want = "PutIn"
            elif g["type"] == "on" and a == "PutIn" and not cx.places.openable(o):
                want = "Place"
            elif g["type"] == "on" and a == "Place" and hand and cx.hand_goal(g):
                want = hand
            if want:
                return F[:i] + [(owner, dict(st, action=want))] + F[i + 1:]
        holding = None
    return None


def op_complete(F, cx: _Ctx, reached: Callable):
    """The canonical segment of the first goal the plan does not reach (fetch, then deliver), at the end."""
    plan = [s for _o, s in F]
    owner = F[-1][0] if F else 0
    for g in cx.goals:
        if g.get("type") not in ("on", "in") or reached(plan, cx.world, [g]):
            continue
        src = g.get("from") or (g.get("from_any") or [None])[0]
        if not src or not g.get("to"):
            continue
        to, add = g["to"], []
        if g["type"] == "in" and cx.places.openable(to):
            add += [_st("MoveTo", to, grounded=True), _st("Open", to)]
        add += [_st("MoveTo", src, grounded=True), _st("Find", g["object"]), _st("Pick", g["object"]),
                _st("MoveTo", to, grounded=True),
                _st("PutIn" if g["type"] == "in" else (next(iter(cx.release)) if cx.hand_goal(g) and cx.release
                                                       else "Place"), to)]
        return F + [(owner, s) for s in add]
    return None


# ── driver ──────────────────────────────────────────────────────────────────────────────────────────────────
def owned_plan(blocks, join, ground) -> list:
    """[(block index, grounded step)] of the planner's blocks, in plan order (the flat plan = ``join``)."""
    out, n = [], 0
    for k in range(len(blocks)):
        flat = ground(join(blocks[:k + 1]))
        out += [(k, s) for s in flat[n:]]
        n = len(flat)
    return out


def write_back(blocks, F) -> None:
    """The repaired owned plan into the blocks, in place (a block left with no steps is emptied)."""
    for k, b in enumerate(blocks):
        b.steps = [dict(s) for o, s in F if o == k]


def repair(steps: list, score: Callable, world, places, goals, *, caps=None, actions=(), release=(),
           hand_goal: Callable = lambda g: False, blocks=None, join=None, ground=None, complete: bool = False,
           reached: Optional[Callable] = None, max_edits: int = 8) -> tuple:
    """(steps, notes, counts): the plan after the operators, ``notes`` per kept edit, ``counts`` {op: n} (with
    ``completed_by_gate``). ``score(steps)``: the number of critical findings; an edit is kept only when it
    strictly lowers it. With ``blocks`` (+ ``join`` / ``ground``) the blocks are edited in place."""
    caps = action_model.Capabilities.from_any(caps) if not hasattr(caps, "hands") else caps
    cx = _Ctx(world, places, goals, caps, actions, tuple(release or ()), hand_goal)
    F = owned_plan(blocks, join, ground) if blocks else [(0, dict(s)) for s in steps]
    n, notes, counts = score([s for _o, s in F]), [], {}
    ops = [("hoist", op_hoist), ("dedupe", op_dedupe), ("insert", op_insert), ("room", op_room),
           ("action", op_action)]
    if complete and reached is not None:
        ops.append(("complete", lambda F_, c: op_complete(F_, c, reached)))
    for _k in range(max_edits):
        if n == 0:
            break
        kept = False
        for name, op in ops:
            cand = op(F, cx)
            if cand is None:
                continue
            m = score([s for _o, s in cand])
            if m < n:
                F, n, kept = cand, m, True
                counts[name] = counts.get(name, 0) + 1
                notes.append(f"gate repair ({name}): {n} critical finding(s) left")
                break
        if not kept:
            break
    if counts and blocks:
        write_back(blocks, F)
    counts["completed_by_gate"] = counts.get("complete", 0)
    return [s for _o, s in F], notes, counts


__all__ = ["repair", "owned_plan", "write_back", "OPS"]
