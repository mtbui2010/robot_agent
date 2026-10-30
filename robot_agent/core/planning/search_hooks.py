"""AssistLoop's hooks of the search belief (``search.py``, ``docs/search.md``), as a mixin.

The belief itself is pure (``search.SearchBelief``); these methods connect it to a run: seed it from
the object memory, ask the LLM ranking (``understand.ask_json`` under the run's budget), mark a Find
miss, steer a replan's suffix, update it after a step. ``AssistLoop`` calls them at its hook points
(grounding, plan start, ``_recover_find``, ``_replan_suffix``, ``_step_succeeded``, ``_obs``).
"""

from __future__ import annotations

import time
from typing import Optional

from . import search as search_belief
from .understand import LLMBadOutput, LLMBudgetExceeded, LLMUnavailable, ask_json


def _words(name) -> str:
    from .assist_loop import words
    return words(name)


class SearchHooks:
    """Mixin of :class:`~robot_agent.core.planning.assist_loop.AssistLoop` (needs ``cfg["search"]``,
    ``_llm()``, ``_llm_unreachable``, ``_ground_all``)."""

    def _remembered(self, memory, obj) -> Optional[str]:
        """Where the object memory puts `obj` when that sighting is still trusted (its confidence decays,
        search.confidence); a stale one is left to the search ranking (_search_start)."""
        place, sc = memory.where(obj), self.cfg["search"]
        if not place or not sc.enabled or sc.rank != "llm":
            return place
        e = memory.data["objects"].get(_words(obj)) or {}
        conf = search_belief.confidence(time.time() - float(e.get("ts") or 0), sc.half_life_s)
        return place if conf >= sc.trust else None

    def _search_start(self, r):
        """The run's SearchBelief, seeded from the object memory; an object with only stale sightings gets
        its source from the belief after the LLM ranked its places (one call)."""
        sc, r.search, r.steered = self.cfg["search"], None, []
        if not sc.enabled:
            return
        fn = getattr(r.robot.adapter, "containers", None)
        r.search = search_belief.SearchBelief(
            r.places.keys, sc, openable=r.places.openable, can_open="Open" in r.robot.plan_actions,
            containers=(lambda k: fn(k, r.places.env.get(k) or {})) if callable(fn)
            else search_belief.containers_from_env(r.places.env, r.places.openable))
        for g in r.goals:
            e = r.memory.data["objects"].get(_words(g.get("object"))) if g.get("object") else None
            if e:
                r.search.see(g["object"], e.get("place"), e.get("ts"), e.get("how") or "")
        r.goals = [dict(g) for g in r.goals]           # the understand event keeps what was understood
        for g in r.goals:
            o = g.get("object")
            if g.get("type") in ("on", "in") and not g.get("from") and not g.get("from_any") \
                    and r.search.needs_rank_first(o):
                yield from self._search_rank(r, o)
                k = r.search.first_place(o)
                if k and k != g.get("to"):
                    g["from"] = k

    def _search_rank(self, r, obj):
        """The LLM ranks the places for `obj` (once per object and run; a failed call: the prior alone)."""
        if not r.search.wants_rank(obj) or self._llm_unreachable:
            return
        from ..run_control import call_cancellable, is_cancel

        def ask(prompt):
            try:
                return ask_json(self._llm(), prompt, search_belief.RANK_SCHEMA, budget=r.llm_budget, retries=0,
                                where="search", call=call_cancellable, passthrough=is_cancel)
            except (LLMUnavailable, LLMBudgetExceeded, LLMBadOutput):
                return None
        ranked = r.search.rank(obj, ask, ground=lambda n: r.places.ground(n, n))
        yield r.ev("search", object=obj, ranked=ranked, prior=r.search.prior(obj),
                   order=r.search.order(obj)[:5])

    def _search_missed(self, r, o, rule):
        """A Find of `o` saw nothing here: the belief marks the place searched (absence only) and gives
        the next look (a closed container here first), None when nowhere is left."""
        s = getattr(r, "search", None)
        if s is None or not rule.mark_searched:
            return None
        s.missed(o, r.world.arrived, r.world.opened)
        yield from self._search_rank(r, o)
        return s.next_leg(o, r.world.arrived, r.world.opened)

    def _search_told(self, r, o, k) -> None:
        if getattr(r, "search", None) is not None:
            r.search.see(o, k, how="user said"); r.search.unsearch(o, k)

    def _search_effect(self, r, st) -> None:
        """A step succeeded: a Find ends the object's search; moving an object at a place voids the
        searches there (something changed)."""
        s = getattr(r, "search", None)
        if s is None:
            return
        if st["action"] == "Find":
            s.found(st["object"], r.world.arrived)
        elif st["action"] in ("Pick", "Place", "PutIn"):
            s.changed(r.world.arrived)
        elif st["action"] == "Open":
            r.doubted.add(st["object"])                  # opened just now: a miss inside is no stale belief

    def _search_steer(self, r, suffix) -> list:
        """A replan's suffix with every searched-for object's next look on the belief's leg (never a
        place searched already)."""
        s = getattr(r, "search", None)
        if s is None or not suffix:
            return suffix
        new, r.steered = s.steer(suffix, r.world.arrived, r.world.opened)
        return self._ground_all(r, new) if r.steered else suffix

    @staticmethod
    def _search_obs(r) -> str:
        s, out = getattr(r, "search", None), ""
        for o in (s.under_search() if s is not None else []):
            leg = s.next_leg(o, r.world.arrived, r.world.opened)
            if leg is not None:
                out += f"\nSearch for the {o}: look next at {leg.text()}"
        return out
