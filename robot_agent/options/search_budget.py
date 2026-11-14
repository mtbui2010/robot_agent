"""Option ``search_budget`` (default off): each object has its own search budget, and a search stops by the pack's own numbers.

The failure it closes: one budget for the whole run (the places a first object used up are charged to the next one), a search that goes on over
places no belief points to, and a robot that gives up without telling the person, or asks again after "I don't know". With the option on:

* the looks for an object are the places the search belief marked searched FOR THAT OBJECT, against ``looks_per_object`` (in place of ``search_total``);
* the search is over when the share of the belief still on the places not yet looked at falls under ``stop_probability``;
* when it is over, the person is asked ONCE where the object is (the engine's own where-question, one per object); the answer is looked at; a
  second miss, or "I don't know", ends the run with a report of the places looked at (an "I don't know" asked earlier does not end the search:
  the robot goes on alone until the budget is over). ``ask: "never"`` ends the run without the question.

Pack data ``planner.options.search_budget.budget`` (every field optional; a bad one is ignored)::

    {"looks_per_object": int >= 1, "stop_probability": 0 < p < 1, "ask": "once" | "never", "source": "<label>"}    # or "assumed": true

The remaining share is the belief's score (+ its floor) of the places not yet searched over that of all places; it falls as the likely places are used up.
No data, no search belief or off: the engine's own behaviour, byte for byte.
The stop rule of a search without legs (``search_max``) stays the engine's; use the option with search legs. Hook ``on_start`` (no effect). Services ``looks_made``, ``search_limit``, ``search_over``. Keys: ``enabled``, ``budget`` (dict, from the manifest).
Lab flag ``search_budget``: ``{object, looks, remaining, why}`` once per object.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.options.config import resolve_key
from robot_agent.core.planning.pack_data import labelled

SPEC = OptionSpec(
    name="search_budget",
    keys=(Key("enabled", "bool", False, aliases=("search_budget",)), Key("budget", "dict", {}, sources=("manifest",))),
    hooks=("on_start",),
    services=("looks_made", "search_limit", "search_over"),
    order=70,
    enabled="enabled",
    doc="Search budget per object (looks, remaining probability: pack data); when over, ask once where it is, then report the places looked at and stop.",
)


def looks_of(data):
    """The declared ``looks_per_object`` (an int >= 1), else None."""
    n = data.get("looks_per_object") if isinstance(data, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else None


def stop_of(data):
    """The declared ``stop_probability`` (0 < p < 1), else None."""
    p = data.get("stop_probability") if isinstance(data, dict) else None
    return float(p) if isinstance(p, (int, float)) and not isinstance(p, bool) and 0 < p < 1 else None


def asks(data) -> bool:
    """Does the pack let the search ask once when it is over? ("never" = no; anything else, or nothing, = once)."""
    return not (isinstance(data, dict) and data.get("ask") == "never")


def remaining(bel, obj) -> float:
    """The share of the belief for ``obj`` on the places not searched yet (1.0 before the first look, 0.0 when every place is searched)."""
    sc = bel.scores(obj)
    w = {p: s + bel.POSTERIOR_FLOOR for p, s in sc.items()}
    tot = sum(w.values())
    done = bel.searched.get(obj, {})
    return sum(v for p, v in w.items() if p not in done) / tot if tot > 0 else 1.0


class SearchBudget(Option):
    spec = SPEC

    def on_start(self, run) -> None:
        """No state to set up: the option answers three services; the registry asks every option to declare a hook."""

    def _data(self) -> dict:
        planner = getattr(getattr(self.svc.robot, "manifest", None), "planner", None)
        d = self.cfg.get("budget") or resolve_key(SPEC, SPEC.key("budget"), {}, {}, planner)
        return d if labelled(d) and (looks_of(d) or stop_of(d)) else {}

    def looks_made(self, run, obj):
        """Looks made for ``obj`` (places marked searched for it) when a budget is declared; None = the engine counts."""
        bel = getattr(run, "search", None)
        if bel is None or not looks_of(self._data()):
            return None
        return len(bel.searched.get(obj, {}))

    def search_limit(self, run, obj):
        """The declared looks per object, or None (the engine's ``search_total`` stands)."""
        return looks_of(self._data())

    def search_over(self, run, obj, ask=False):
        """Is the search for ``obj`` over (looks used up, or the remaining share under the stop probability)? With ``ask``: is the
        where-question due (False when the pack says ``ask: never``). None = nothing declared or no belief (the engine decides)."""
        data, bel = self._data(), getattr(run, "search", None)
        if bel is None or not data:
            return None
        n, left, why = len(bel.searched.get(obj, {})), remaining(bel, obj), ""
        if looks_of(data) and n >= looks_of(data):
            why = "looks"
        elif stop_of(data) and left < stop_of(data):
            why = "probability"
        if why and not run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {}).setdefault("said", set()) & {obj}:
            run.opt[SPEC.name]["said"].add(obj)
            self.svc.lab(SPEC.name, object=obj, looks=n, remaining=round(left, 3), why=why)
        return bool(why) and (asks(data) or not ask)


register(SPEC, SearchBudget)
