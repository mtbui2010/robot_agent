"""Option ``reach_delivery`` (default off): a delivery to a person ends within their reach, or in their hand.

The problem (round 14, dev 033 EN, dev 047 KO): a person entry of the ENV (``persons.py``) is a SNAPSHOT: ``loc`` is where
the robot last saw the person and ``spot`` the support surface beside them then. The plan puts the object down at the spot
(``Place <person>``), the put-down succeeds, the goal check finds the object at the spot, and the run says done; the person
had left the spot meanwhile (to another place in the room, to another room), so the object is not within their reach. Nothing
the robot can read says so: the ENV of a person is not updated while the robot works, and a put-down is a success on its own
terms.

What the option does, all from data (the ENV entry and the pack's manifest ``planner.person_snapshot``; no name of an object,
place or person, no word list):

* a snapshot is trusted for ``trust_s`` seconds (pack data; none declared = no limit, as before). A put-down for a person
  whose snapshot is older than that is done by the robot's hand-over action instead (a hand-over reaches the person wherever
  they are in the room); a robot with no hand-over action keeps the put-down;
* a person entry with a position but no ``spot`` field (the pack cannot say where a reachable surface is) is a hand-over
  too, never "the support surface nearest to them", when the robot can hand over;
* the end-of-run goal check reads the person's entry again: when the entry no longer has the spot the object was put down
  at (a pack that updates its entries) or has none, the object is not within reach and the goal is NOT met.

``planner.person_snapshot`` (a mapping, may be empty; absent = the option does nothing): ``trust_s`` (seconds, > 0: how long a
person entry is trusted).
"""

from __future__ import annotations

from typing import Optional

from robot_agent.contract import FailureCode
from robot_agent.core.options import NEXT, STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import persons, speech

FLAG = "reach_delivery"
DATA = "person_snapshot"         # the pack's manifest planner.person_snapshot: {trust_s: seconds}


def config(planner_data) -> Optional[dict]:
    """The pack's declaration, ``{"trust_s": float | None}``, or None when it declares none (the option then does nothing)."""
    v = (planner_data or {}).get(DATA) if isinstance(planner_data, dict) else None
    if not isinstance(v, dict):
        return None
    try:
        t = float(v.get("trust_s"))
    except (TypeError, ValueError):
        t = None
    return {"trust_s": t if t is not None and t > 0 else None}


def recipient(places, step: dict, goals, put_down: Optional[str] = None) -> Optional[str]:
    """The person entry a put-down step is for: the step's own object when it is a person entry, else the person
    of the ``on`` goal that the put-down at a place serves (``near_person``). None when the step is for nobody.
    ``put_down``: the pack's put-down action (``action_roles``); none declared = no step is a put-down (fail open)."""
    if put_down is None or step.get("action") != put_down:
        return None
    o = step.get("object")
    if persons.is_person(places, o):
        return o
    for g in goals or ():
        if g.get("type") == "on" and g.get("to") == o and g.get("near_person") and persons.is_person(places, g["near_person"]):
            return g["near_person"]
    return None


def stale(cfg: Optional[dict], elapsed_s: float) -> bool:
    """Is a person snapshot taken ``elapsed_s`` ago too old to put an object down by? Never without a declared ``trust_s``."""
    return bool(cfg) and cfg["trust_s"] is not None and elapsed_s > cfg["trust_s"]


def spot_of(places, key) -> Optional[str]:
    """The reachable surface the person's entry declares now (None: it declares none or has no such field)."""
    return (places.env.get(key) or {}).get("spot") or None


def has_spot_field(places, key) -> bool:
    return "spot" in (places.env.get(key) or {})


def hand_step(release_action: str, key: str) -> dict:
    return {"action": release_action, "object": key, "grounded": True, "reach_hand": True}


def left_spot(places, key, put_at: Optional[str]) -> Optional[str]:
    """Why a delivery put down at ``put_at`` (the person's declared spot at that time) no longer holds, from the
    person's entry now; None when nothing says it does not hold (no spot field: nothing to compare)."""
    if put_at is None or not has_spot_field(places, key):
        return None
    now = spot_of(places, key)
    if now == put_at:
        return None
    return (f"their reachable surface is now {now}, not {put_at}" if now else
            f"they have no reachable surface now (it was {put_at})")


# ── the option: the hooks that call the functions above ──────────────────────────────────────────────────────────────
SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", False, aliases=(FLAG,)),),
    hooks=("on_start", "on_ground", "on_step", "on_step_done", "on_goal_check", "on_step_failed"),
    services=("no_refetch",),
    order=30,
    enabled="enabled",
    doc="A delivery to a person ends within their reach or in their hand (needs the pack's planner.person_snapshot).",
)


class ReachDelivery(Option):
    """Hooks (docs/options.md): ``on_start`` stamps the run, ``on_ground`` turns a put-down for a person without a declared
    surface into a hand-over, ``on_step`` replaces a put-down for a stale snapshot, ``on_step_done`` remembers where the object
    was put down, ``on_goal_check`` reads the person's entry again, ``on_step_failed`` asks where a person who left is."""

    @property
    def rc(self):
        """The pack's declaration (``planner.person_snapshot``) as it is now, None when it declares none (read each time: the
        manifest is the pack's, not a copy)."""
        try:
            return config(getattr(self.svc.robot.manifest, "planner", None))
        except Exception:                                  # noqa: BLE001 - no robot / manifest: no declaration, the option does nothing
            return None

    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(FLAG, {})

    def no_refetch(self):
        """Service: the plan keeps an object it already holds instead of fetching it again (the gate's completion)."""
        return True if self.rc is not None else None

    def on_start(self, run) -> None:
        self._state(run)["t0"] = self.svc.now()             # the person entries of the ENV are as of now

    def on_ground(self, mode, places, to):
        if mode == "place" and self.rc is not None and not has_spot_field(places, to) and self.svc.hand_action():
            self.svc.lab(FLAG, why="no_declared_spot", person=to)
            return "hand"                 # no declared reachable surface, so a hand-over, not the nearest furniture
        return None

    def on_step(self, run, st):
        """A put-down for a person whose ENV snapshot is older than ``trust_s`` becomes the robot's hand-over (the person may have
        left the spot; a hand-over reaches them wherever they are in the room). The new step, or None (unchanged)."""
        put_down = self.svc.role("put_down")
        if st["action"] != put_down:
            return None
        key = recipient(run.places, st, run.goals, put_down) if self.rc is not None else None
        if key is None:
            return None
        age = self.svc.now() - self._state(run).get("t0", self.svc.now())
        if not stale(self.rc, age):
            return None
        rel = self.svc.hand_action()
        if rel is None:
            self.svc.lab(FLAG, why="stale_no_handover", person=key, age_s=round(age))
            return None
        new = hand_step(rel, key)
        if run.i < len(run.steps) and run.steps[run.i] is st:
            run.steps[run.i] = new
        self.svc.lab(FLAG, why="stale", person=key, age_s=round(age), spot=spot_of(run.places, key))
        yield run.ev("recover", at_index=run.i, kind=FLAG,
                     msg=f"{key} may have moved since I last saw them ({age:.0f} s): handing it over instead of putting it down")
        return new

    def on_step_done(self, run, st, held) -> None:
        put_down = self.svc.role("put_down")
        if st["action"] == put_down and held and self.rc is not None:
            key = recipient(run.places, st, run.goals, put_down)
            if key:                                      # the person's spot as it was when the object was put down
                self._state(run).setdefault("put", {})[held] = (key, spot_of(run.places, key))

    def on_goal_check(self, run, obj):
        key, put_at = self._state(run).get("put", {}).get(obj, (None, None))
        why = left_spot(run.places, key, put_at) if key and self.rc is not None else None
        if why:                                          # the person's entry says the object is out of reach now
            return False, f"the {obj} is not within reach of {key}: {why}", None
        return None

    def on_step_failed(self, run, st, code, reason, a, o):
        """A hand-over that replaced a put-down fails ``unreachable`` (the person left): ask where they are, go there, hand over
        again; no place to go: no same-step retries, the replan / give-up path."""
        if not (st.get("reach_hand") and code is FailureCode.UNREACHABLE):
            return None
        out = yield from self._ask_where(run, st)
        if out is not None:
            return out
        return (yield from self.svc.replan_or_fail(run, st, reason, f"{a} {o} failed: {reason} (replan budget used)"))

    def _ask_where(self, r, st):
        """Ask once where the person is; the place they name (``persons.place_in_answer``) gets a move and the hand-over again.
        NEXT, STOP, or None (no question possible, no answer, no place in it: the failure goes the usual way)."""
        key = st.get("object")
        mv = self.svc.role("move")
        if mv is None or not persons.is_person(r.places, key):         # no declared move action: nowhere to go (fail open)
            return None
        state = self._state(r)
        where, asked = state.setdefault("where", {}), state.setdefault("asked_keys", set())
        known = where.get(key)
        if known and r.world.arrived != known:                # asked before for this person: go there, no second question
            r.steps[r.i:r.i + 1] = [{"action": mv, "object": known, "grounded": True}, dict(st)]
            self.svc.lab(FLAG, why="known_where", person=key, place=known)
            return NEXT
        if key in asked:
            return None
        asked.add(key)
        if r.places.env.get(key, {}).get("speaker") or key == persons.speaker_key(r.places):
            q = speech.LocalText("q_where_are_you")
        else:
            q = speech.LocalText("q_where_is_person", {"person": key}, text=f"Where is {self.svc.say_name(r.places, key)}?")
        yield r.ev("clarify", question=q, round=r.asked + 1, kind=FLAG)
        ans = self.svc.ask(r, q)
        stop = self.svc.cancelled(r)
        if stop:
            yield stop
            return STOP
        at = persons.place_in_answer(r.places, ans) if ans else None
        self.svc.lab(FLAG, why="asked_where", person=key, answered=bool(ans), place=at)
        if not ans or not at or at.startswith("@"):
            return None
        r.dialogue += [("robot", q), ("user", ans)]
        where[key] = at
        r.steps[r.i:r.i + 1] = [{"action": mv, "object": at, "grounded": True}, dict(st)]
        return NEXT


register(SPEC, ReachDelivery)
__all__ = ["FLAG", "DATA", "SPEC", "ReachDelivery", "config", "recipient", "stale", "spot_of", "has_spot_field", "hand_step", "left_spot"]
