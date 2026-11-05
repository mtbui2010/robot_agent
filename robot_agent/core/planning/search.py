"""Search with a belief (SAGE-v2 roadmap P1.13): where to look for an object next, robot-agnostic.

One :class:`SearchBelief` per run (``AssistLoop`` keeps it on the run context). Per object it holds

* a **location prior**: sightings (where it was last seen / placed / the user said, from the object
  memory and this run) with a confidence that halves every ``half_life_s`` seconds;
* an **LLM ranking** of the candidate places, asked at most ONCE per object per run and cached (the
  loop passes ``ask(prompt) -> dict | None``; this module never calls a model itself);
* the **places already searched** in this run: a hard constraint. A place is searched again only when
  something changed there: a container at it was opened since (``opened``), or the robot moved an
  object there (:meth:`changed`).

From these it chooses the next :class:`Leg` (go there, open what is closed there when the robot can
open it, look) and steers a planner's suffix onto it (:meth:`steer`): a search step aimed at a place
already searched, or away from the belief's best place, is replaced by the belief's leg.

Nothing here knows a robot: places are the ENV keys, ``openable(key)`` / ``containers(key)`` come
from the robot's adapter, ``can_open`` from its manifest (an Open action). See ``docs/search.md``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# steps a planner may put between the MoveTo of a search leg and its Find
_BETWEEN = {"Open", "Close"}
# steps that act at the robot's place (a MoveTo there must come first)
_AT_PLACE = {"Place", "PutIn", "Open", "Close"}
_NOT_A_PLACE = {"robot", "floor", "", None}

RANK_SCHEMA = {
    "type": "object",
    "properties": {"places": {"type": "array", "items": {"type": "string"}}},
    "required": ["places"],
}

RANK_PROMPT = """A household robot is looking for the {obj}. First think of the room or rooms where the {obj} is usually \
kept in a home, then of the furniture there. Rank the places where the {obj} is most likely to be, most likely \
first, over the whole home (not only the room the robot is in). Leave out places where it is never \
kept. Use only these place names, grouped by room:
{places}
{known}
Return JSON: {{"places": [<place name>, ...]}} (the likely places only, most likely first)."""


@dataclass
class SearchConfig:
    """Knobs of the search (env ``ROBOT_AGENT_SEARCH*``, or per-run overrides)."""
    enabled: bool = True
    rank: str = "llm"                 # llm: the LLM ranks the places once per object; prior: no LLM call
    half_life_s: float = 6 * 3600.0   # a sighting's confidence halves every half_life_s
    trust: float = 0.5                # a sighting at least this confident is believed without a ranking
    rank_weight: float = 0.5          # the ranking's weight against the prior (top ranked = rank_weight)
    habit_weight: float = 0.75        # where past episodes picked the object up (all of them there = habit_weight)
    miss_rate: float = 0.1            # P(a look at the right place does not see the object): the detector's false-negative rate

    @classmethod
    def from_env(cls, overrides: Optional[dict] = None, env: Callable = os.environ.get,
                 miss_default: float = 0.1) -> "SearchConfig":
        """``miss_default``: the robot pack's own value (manifest ``planner.detector_miss_rate``), the fallback of
        the option ``detector_miss_rate`` and the env ``ROBOT_AGENT_DETECTOR_MISS_RATE``."""
        o, miss_default = overrides or {}, clamp_miss_rate(miss_default)
        get = lambda k, e, d: o[k] if k in o else env(e, d)            # noqa: E731
        on = get("search", "ROBOT_AGENT_SEARCH", "1")
        return cls(enabled=on if isinstance(on, bool) else str(on).lower() not in ("0", "off", "false", "no"),
                   rank=str(get("search_rank", "ROBOT_AGENT_SEARCH_RANK", "llm")).lower(),
                   half_life_s=float(get("search_half_life_s", "ROBOT_AGENT_SEARCH_HALF_LIFE_S", "21600")),
                   trust=float(get("search_trust", "ROBOT_AGENT_SEARCH_TRUST", "0.5")),
                   rank_weight=float(get("search_rank_weight", "ROBOT_AGENT_SEARCH_RANK_WEIGHT", "0.5")),
                   habit_weight=float(get("search_habit_weight", "ROBOT_AGENT_SEARCH_HABIT_WEIGHT", "0.75")),
                   miss_rate=clamp_miss_rate(get("detector_miss_rate", "ROBOT_AGENT_DETECTOR_MISS_RATE", miss_default),
                                             miss_default))


def clamp_miss_rate(v, default: float = 0.1) -> float:
    """A false-negative rate in [0, 0.9] (a bad value: ``default``). 1 would make a look worthless."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return min(max(x, 0.0), 0.9) if x == x else default


def confidence(age_s: float, half_life_s: float) -> float:
    """1.0 for a sighting made now, 0.5 after one half-life, ... (``half_life_s`` <= 0: never decays)."""
    if half_life_s <= 0:
        return 1.0
    return 0.5 ** (max(0.0, float(age_s)) / float(half_life_s))


@dataclass
class Leg:
    """One look: go to ``place``, open ``open`` (closed containers there), Find ``obj``."""
    obj: str
    place: str
    open: list = field(default_factory=list)

    def steps(self, at: Optional[str]) -> list:
        """The look as plan steps, from the robot standing at ``at``."""
        out, pos = [], at
        for c in dict.fromkeys([self.place] + list(self.open)):
            if c != pos:
                out.append({"action": "MoveTo", "object": c, "grounded": True}); pos = c
            if c in self.open:
                out.append({"action": "Open", "object": c, "grounded": True})
        return out + [{"action": "Find", "object": self.obj}]

    def text(self) -> str:
        if self.open:
            return f"{self.place} ({', '.join(self.open)} is closed: Open it, then Find the {self.obj} inside)"
        return self.place

    def end(self) -> str:
        """Where the robot stands after the look."""
        return (self.open or [self.place])[-1]


class SearchBelief:
    """See the module docstring. ``places``: the ENV keys in their order (the tie-break)."""

    def __init__(self, places: Iterable[str], cfg: Optional[SearchConfig] = None,
                 openable: Optional[Callable[[str], bool]] = None, can_open: bool = False,
                 containers: Optional[Callable[[str], list]] = None, now: Callable[[], float] = time.time):
        self.places = list(places)
        self.cfg = cfg or SearchConfig()
        self._openable = openable or (lambda k: False)
        self._containers = containers
        self.can_open = bool(can_open)
        self.now = now
        self.sightings: dict = {}      # obj -> {place: (ts, how)}
        self.ranked: dict = {}         # obj -> [places] (asked once; [] = no usable answer)
        self.searched: dict = {}       # obj -> {place: set of containers there that were closed}
        self.habits: dict = {}         # obj -> {place: times past episodes picked it up there}
        self._before_found: dict = {}  # obj -> the search state before its last Find saw it
        self.stale: dict = {}          # obj -> {place: (ts, how)} sightings a miss removed (the posterior's prior)
        self.misses: dict = {}         # obj -> {place: looks there that did not see it} (kept when a place is looked at again)
        self.rank_calls = 0

    # ── what is where ───────────────────────────────────────────────────
    def containers(self, place: str) -> list:
        """Containers at ``place`` (ENV keys): the adapter's, else the place itself when it opens."""
        if self._containers is not None:
            try:
                return [c for c in (self._containers(place) or []) if c]
            except Exception:
                pass
        return [place] if self._openable(place) else []

    def see(self, obj: str, place: Optional[str], ts: Optional[float] = None, how: str = "") -> None:
        """A sighting of ``obj`` at ``place`` (memory, the user, a Find)."""
        if place in _NOT_A_PLACE or place not in self.places:
            return
        self.sightings.setdefault(obj, {})[place] = (self.now() if ts is None else float(ts), how)
        self.stale.get(obj, {}).pop(place, None)

    def learn(self, obj: str, counts: dict) -> None:
        """Experience: ``counts`` {place: times the robot's past episodes picked ``obj`` up there}
        (search_experience.picked_at). Not a sighting: it does not decay and never sets a source."""
        c = {p: int(n) for p, n in (counts or {}).items() if p in self.places and int(n) > 0}
        if c:
            self.habits[obj] = c

    def habit(self, obj: str) -> dict:
        """{place: share of the object's past picks made there}."""
        c = self.habits.get(obj) or {}
        n = sum(c.values())
        return {p: k / n for p, k in c.items()} if n else {}

    def prior(self, obj: str) -> dict:
        """{place: confidence} of the object's sightings, decayed by their age."""
        now = self.now()
        return {p: confidence(now - ts, self.cfg.half_life_s) for p, (ts, _h) in self.sightings.get(obj, {}).items()}

    # ── the LLM ranking (once per object) ─────────────────────────────────
    def wants_rank(self, obj: str) -> bool:
        return self.cfg.rank == "llm" and obj not in self.ranked

    def rank_prompt(self, obj: str) -> str:
        now, known = self.now(), []
        for p, (ts, how) in sorted(self.sightings.get(obj, {}).items(), key=lambda kv: -kv[1][0]):
            known.append(f"The {obj} was {how or 'seen'} at {p} {_age(now - ts)} ago.")
        for p, k in sorted((self.habits.get(obj) or {}).items(), key=lambda kv: -kv[1]):
            known.append(f"In past tasks in this home the robot picked the {obj} up at {p} "
                         f"({k} time{'s' if k != 1 else ''}).")
        done = list(self.searched.get(obj, {}))
        if done:
            known.append(f"Already searched (not there): {', '.join(done)}.")
        return RANK_PROMPT.format(obj=obj, places=self._layout(), known="\n".join(known))

    def _layout(self) -> str:
        """The places grouped by the room their ENV key names (``place@room``): the house layout the ranking
        reasons over (a model that sees only a flat list ranks the room the robot is in)."""
        rooms: dict = {}
        for k in self.places:
            rooms.setdefault(k.split("@", 1)[1] if "@" in k else "", []).append(k)
        return "\n".join(f"{room or 'elsewhere'}: {', '.join(ks)}" for room, ks in rooms.items())

    def rank(self, obj: str, ask: Callable[[str], Optional[dict]],
             ground: Optional[Callable[[str], Optional[str]]] = None) -> list:
        """The LLM's ranking of the places for ``obj``: asked once, then cached (also when it failed).
        ``ask(prompt)`` returns the model's JSON object, or None when the call failed."""
        if not self.wants_rank(obj):
            return self.ranked.get(obj, [])
        self.rank_calls += 1
        self.ranked[obj] = []
        d = ask(self.rank_prompt(obj))                    # None when the model failed (the loop's ask says)
        names = d.get("places") if isinstance(d, dict) else None
        out = []
        for n in names if isinstance(names, list) else []:
            k = str(n).strip() if str(n).strip() in self.places else (ground(str(n)) if ground else None)
            if k in self.places and k not in out:
                out.append(k)
        self.ranked[obj] = out
        return out

    # ── the order ───────────────────────────────────────────────────────
    def scores(self, obj: str) -> dict:
        """{place: prior confidence + habit_weight * share of past picks there
        + rank_weight * (1 for the top ranked ... 1/n for the last)}."""
        s = dict.fromkeys(self.places, 0.0)
        for p, c in self.prior(obj).items():
            s[p] = s.get(p, 0.0) + c
        for p, h in self.habit(obj).items():
            s[p] = s.get(p, 0.0) + self.cfg.habit_weight * h
        r = self.ranked.get(obj) or []
        for i, p in enumerate(r):
            s[p] = s.get(p, 0.0) + self.cfg.rank_weight * (len(r) - i) / len(r)
        return s

    def order(self, obj: str) -> list:
        s, idx = self.scores(obj), {p: i for i, p in enumerate(self.places)}
        return sorted(s, key=lambda p: (-s[p], idx.get(p, 1 << 20)))

    def first_place(self, obj: str) -> Optional[str]:
        """Where to look first: the best scored place, None when the belief knows nothing."""
        s = self.scores(obj)
        best = self.order(obj)[0] if s else None
        return best if best is not None and s[best] > 0 else None

    def needs_rank_first(self, obj: str) -> bool:
        """Only stale sightings (none at least ``trust`` confident): ask the ranking before the first look."""
        pr = self.prior(obj)
        return bool(pr) and max(pr.values()) < self.cfg.trust and self.wants_rank(obj)

    # ── searched places (the hard constraint) ─────────────────────────────
    def missed(self, obj: str, place: Optional[str], opened: Iterable[str], count: bool = True) -> None:
        """``obj`` was looked for at ``place`` and not seen (with ``opened`` open at the time). ``count`` False:
        only the place is marked searched again (a look that was taken back), not one more miss."""
        if place not in self.places:
            return
        op = set(opened or ())
        self.searched.setdefault(obj, {})[place] = {c for c in self.containers(place) if c not in op}
        m = self.misses.setdefault(obj, {})
        m[place] = m.get(place, 0) + (1 if count else 0)
        gone = self.sightings.get(obj, {}).pop(place, None)
        if gone is not None:
            self.stale.setdefault(obj, {})[place] = gone       # the posterior still weighs what pointed here

    def is_searched(self, obj: str, place: Optional[str], opened: Iterable[str] = ()) -> bool:
        e = self.searched.get(obj, {}).get(place)
        if e is None:
            return False
        return not (e & set(opened or ()))               # a container closed then is open now: look again

    def searched_places(self, obj: str, opened: Iterable[str] = ()) -> list:
        return [p for p in self.searched.get(obj, {}) if self.is_searched(obj, p, opened)]

    def under_search(self) -> list:
        return [o for o, d in self.searched.items() if d]

    def found(self, obj: str, place: Optional[str]) -> None:
        # what the search knew is kept for one step: a phantom (a Find that saw it, a Pick that finds nothing)
        # takes it back (restore_search)
        self._before_found[obj] = ({p: set(c) for p, c in self.searched.get(obj, {}).items()},
                                   dict(self.misses.get(obj, {})), dict(self.stale.get(obj, {})),
                                   dict(self.sightings.get(obj, {})))
        self.searched.pop(obj, None)
        self.misses.pop(obj, None)
        self.stale.pop(obj, None)
        self.see(obj, place, how="found")

    def restore_search(self, obj: str) -> bool:
        """The Find that ended ``obj``'s search was a false positive: the places searched and the misses from
        before it are back (the caller then marks the place of the false positive as a miss). False when
        there is nothing to take back."""
        snap = self._before_found.pop(obj, None)
        if snap is None:
            return False
        searched, misses, stale, sightings = snap
        self.searched[obj], self.misses[obj], self.stale[obj] = searched, misses, stale
        self.sightings[obj] = sightings
        return True

    def unsearch(self, obj: str, place: Optional[str]) -> None:
        self.searched.get(obj, {}).pop(place, None)

    def changed(self, place: Optional[str]) -> None:
        """The robot moved an object at ``place``: every search there is void."""
        for d in self.searched.values():
            d.pop(place, None)

    # ── one miss is not proof of absence (the detector's false-negative rate) ──
    POSTERIOR_FLOOR = 0.02      # a place nothing points to is still possible (a share of a sighting's confidence)

    def posterior(self, obj: str) -> dict:
        """{place: P(the object is there | the misses so far)}, summing to 1. The prior is the score of the place
        (sightings, habits, ranking; a small floor for every place) and each look that missed multiplies it by the
        false-negative rate: a place looked at once keeps ``miss_rate`` of its weight, twice ``miss_rate`` squared."""
        w, now = {}, self.now()
        for p, sc in self.scores(obj).items():
            ts_how = self.stale.get(obj, {}).get(p)
            if ts_how is not None:
                sc += confidence(now - ts_how[0], self.cfg.half_life_s)
            w[p] = (sc + self.POSTERIOR_FLOOR) * self.cfg.miss_rate ** (self.misses.get(obj, {}).get(p, 0))
        tot = sum(w.values())
        return {p: v / tot for p, v in w.items()} if tot > 0 else {}

    def relook_budget(self) -> int:
        """How many places may get a second look once the search is used up: 0 for a detector that never misses,
        else about 20 x the false-negative rate (at least 1, at most 4; the default rate 0.1 gives 2)."""
        m = self.cfg.miss_rate
        return 0 if m <= 0 else max(1, min(4, int(m * 20 + 0.5)))

    def relook_order(self, obj: str, exclude: Iterable[str] = ()) -> list:
        """The places already looked at, most probable first by the posterior (equal: the one looked at first;
        a place looked at twice has lost the rate's factor again)."""
        post, ex = self.posterior(obj), set(exclude or ())
        done = [p for p in self.misses.get(obj, {}) if p not in ex]       # in the order they were looked at
        return sorted(done, key=lambda p: (-post.get(p, 0.0), done.index(p)))

    def relook_beats_next(self, obj: str, here: Optional[str], opened: Iterable[str] = ()) -> Optional[Leg]:
        """A place already looked at whose posterior is higher than that of every place not yet looked at (a
        strong sighting there against a flat rest): the look at it, else None. Never the place the robot is at
        and not a place that cannot be looked at (a closed container the robot cannot open)."""
        op, post = set(opened or ()), self.posterior(obj)
        rest = [post.get(p, 0.0) for p in self.places if not self.is_searched(obj, p, op) and p not in self.misses.get(obj, {})
                and self.leg_for(obj, p, op) is not None]
        for p in self.relook_order(obj, exclude=[here]):
            if post.get(p, 0.0) > max(rest or [0.0]):
                leg = self.leg_for(obj, p, op)
                if leg is not None:
                    return leg
            break                                     # the order is by posterior: no later place beats it either
        return None

    # ── the next look ───────────────────────────────────────────────────
    def _closed_here(self, obj: str, place: str, opened: set) -> list:
        """Containers at a searched ``place`` that were closed when it was searched, are still closed
        and the robot can open: look inside before moving on."""
        then = self.searched.get(obj, {}).get(place, set())
        if not self.can_open:
            return []
        return [c for c in self.containers(place) if c in then and c not in opened
                and (c == place or not self.is_searched(obj, c, opened))]

    def leg_for(self, obj: str, place: str, opened: set) -> Optional[Leg]:
        """How to look at ``place``: open it first when it is itself a closed container (None when the
        robot cannot open it: it cannot look inside)."""
        if place in self.containers(place) and place not in opened:
            return Leg(obj, place, [place]) if self.can_open else None
        return Leg(obj, place)

    def next_leg(self, obj: str, here: Optional[str], opened: Iterable[str] = ()) -> Optional[Leg]:
        """The next look for ``obj``: a closed container at the place just searched (when the robot can
        open it), else the best place not searched yet; None when nowhere is left."""
        op = set(opened or ())
        if here and here in self.searched.get(obj, {}):
            inside = self._closed_here(obj, here, op)
            if inside:
                return Leg(obj, inside[0], [inside[0]])
        for p in self.order(obj):
            if not self.is_searched(obj, p, op):
                leg = self.leg_for(obj, p, op)
                if leg is not None:
                    return leg
        return None

    def informed(self, obj: str, opened: Iterable[str] = ()) -> bool:
        """Does the belief prefer a place not searched yet (a prior or a ranking for it)?"""
        s, op = self.scores(obj), set(opened or ())
        return any(v > 0 and not self.is_searched(obj, p, op) and self.leg_for(obj, p, op) is not None
                   for p, v in s.items())

    # ── steering a planner's steps ────────────────────────────────────────
    def steer(self, steps: list, at: Optional[str], opened: Iterable[str] = ()) -> tuple:
        """The planner's ``steps`` with each searched-for object's first look put on the belief's leg:
        (steps, notes). A look is moved when it goes to a place already searched (nothing changed
        there), into a closed container without opening it, or (the belief knows better) elsewhere
        than the belief's best place. Steps acting at the planner's place after the look get their
        MoveTo back."""
        out, notes = [dict(s) for s in steps], []
        for obj in self.under_search():
            out, note = self._steer_one(out, obj, at, set(opened or ()))
            if note:
                notes.append(note)
        return out, notes

    def _steer_one(self, steps: list, obj: str, at: Optional[str], opened: set) -> tuple:
        j = next((i for i, s in enumerate(steps) if s.get("action") == "Find" and s.get("object") == obj), None)
        if j is None:
            return steps, ""
        pos, op, k = at, set(opened), None
        for i, s in enumerate(steps[:j]):
            a = s.get("action")
            if a == "MoveTo":
                pos, k = s.get("object"), i
            elif a == "Open":
                op.add(s.get("object"))
            elif a not in _BETWEEN:
                k = None
        leg = self.next_leg(obj, at, op) if self.is_searched(obj, pos, op) or self.informed(obj, op) else None
        if leg is None and pos in self.containers(pos) and pos not in op and self.can_open:
            leg = Leg(obj, pos, [pos])                     # into a closed container: open it first
        if leg is None or (leg.place == pos and not (set(leg.open) - op)):
            return steps, ""
        start = k if k is not None else j
        seg = leg.steps(_pos_after(steps[:start], at))
        new = steps[:start] + seg + steps[j + 1:]
        _restore_place(new, start + len(seg), leg.end(), pos)
        return new, f"look for the {obj} at {leg.text()} instead of {pos}"

    # ── a run's record ──────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"sightings": {o: {p: list(v) for p, v in d.items()} for o, d in self.sightings.items()},
                "ranked": dict(self.ranked), "rank_calls": self.rank_calls,
                "habits": {o: dict(d) for o, d in self.habits.items()},
                "misses": {o: dict(d) for o, d in self.misses.items()},
                "stale": {o: {p: list(v) for p, v in d.items()} for o, d in self.stale.items()},
                "searched": {o: {p: sorted(c) for p, c in d.items()} for o, d in self.searched.items()}}

    def load(self, d: dict) -> "SearchBelief":
        """Take a ``to_dict`` back (a resumed run keeps its searched places and rankings)."""
        for o, dd in (d.get("sightings") or {}).items():
            self.sightings[o] = {p: (float(v[0]), str(v[1])) for p, v in dd.items()}
        self.ranked.update({o: list(v) for o, v in (d.get("ranked") or {}).items()})
        for o, dd in (d.get("habits") or {}).items():
            self.learn(o, dd)
        for o, dd in (d.get("stale") or {}).items():
            self.stale[o] = {p: (float(v[0]), str(v[1])) for p, v in dd.items()}
        for o, dd in (d.get("misses") or {}).items():
            self.misses[o] = {p: int(n) for p, n in dd.items()}
        for o, dd in (d.get("searched") or {}).items():
            self.searched[o] = {p: set(c) for p, c in dd.items()}
        self.rank_calls = int(d.get("rank_calls") or 0)
        return self


def skip_relook(steps: list, i: int, obj: str, here: Optional[str]) -> tuple:
    """``steps[i]`` (a Find of ``obj``) saw it at ``here``: a look for it right after, elsewhere and before
    its Pick (``MoveTo Q``, Open / Close, ``Find obj``) would walk away from it (T2 S02: the banana seen
    on the dining table, then looked for again on the counter, not there, the run gave up). That look is
    removed; a step after it acting at Q gets its MoveTo back. Returns (steps, note)."""
    moved_to, j = None, i + 1
    while j < len(steps) and steps[j].get("action") in ("MoveTo",) + tuple(_BETWEEN):
        if steps[j].get("action") == "MoveTo":
            moved_to = steps[j].get("object")
        j += 1
    if (j >= len(steps) or moved_to in (None, here) or steps[j].get("action") != "Find"
            or steps[j].get("object") != obj):
        return steps, ""
    new = [dict(s) for s in steps[:i + 1] + steps[j + 1:]]
    _restore_place(new, i + 1, here, moved_to)
    return new, f"the {obj} was seen at {here}: the look at {moved_to} is skipped"


def _restore_place(steps: list, i: int, now_at: str, planned_at: Optional[str]) -> None:
    """After a moved look the robot stands at ``now_at``: the first step acting at a place before the
    next MoveTo gets a MoveTo there (the planner had the robot at ``planned_at``)."""
    for m in range(i, len(steps)):
        a, o = steps[m].get("action"), steps[m].get("object")
        if a == "MoveTo":
            return
        if a in _AT_PLACE:
            if o and o != now_at and o == planned_at:
                steps.insert(m, {"action": "MoveTo", "object": o, "grounded": True})
            return


def _pos_after(steps: list, at: Optional[str]) -> Optional[str]:
    for s in steps:
        if s.get("action") == "MoveTo":
            at = s.get("object")
    return at


def _age(s: float) -> str:
    s = max(0.0, s)
    if s < 120:
        return f"{int(s)} s"
    if s < 7200:
        return f"{int(s // 60)} min"
    if s < 172800:
        return f"{int(s // 3600)} h"
    return f"{int(s // 86400)} days"


def containers_from_env(env: dict, openable: Callable[[str], bool]) -> Callable[[str], list]:
    """Containers at a place from the ENV: an entry's ``containers`` list (ENV keys), else the place
    itself when it opens."""
    def at(place: str) -> list:
        e = env.get(place) if isinstance(env.get(place), dict) else {}
        listed = [c for c in (e.get("containers") or []) if c in env]
        return listed + ([place] if openable(place) and place not in listed else [])
    return at


__all__ = ["SearchBelief", "SearchConfig", "Leg", "clamp_miss_rate", "confidence", "containers_from_env", "skip_relook", "RANK_PROMPT",
           "RANK_SCHEMA"]
