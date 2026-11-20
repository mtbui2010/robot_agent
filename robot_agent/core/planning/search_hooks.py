"""AssistLoop's hooks of the search belief (``search.py``, ``docs/search.md``), as a mixin.

The belief itself is pure (``search.SearchBelief``); these methods connect it to a run: seed it from
the object memory, ask the LLM ranking (``understand.ask_json`` under the run's budget), mark a Find
miss, steer a replan's suffix, update it after a step. ``AssistLoop`` calls them at its hook points
(grounding, plan start, ``_recover_find``, ``_replan_suffix``, ``_step_succeeded``, ``_obs``).
"""

from __future__ import annotations

from typing import Optional

from ..options import NEXT
from . import search as search_belief
from . import search_experience, search_route
from .understand import LLMBadOutput, LLMBudgetExceeded, LLMUnavailable, ask_json


def _words(name) -> str:
    from .assist_loop import words
    return words(name)


class SearchHooks:
    """Mixin of :class:`~robot_agent.core.planning.assist_loop.AssistLoop` (needs ``cfg["search"]``, ``_now()``,
    ``_llm()``, ``_llm_unreachable``, ``_ground_all``)."""

    def _remembered(self, memory, obj) -> Optional[str]:
        """Where the object memory puts `obj` when that sighting is still trusted (its confidence decays,
        search.confidence); a stale one is left to the search ranking (_search_start)."""
        place, sc = memory.where(obj), self.cfg["search"]
        if not place or not sc.enabled or sc.rank != "llm":
            return place
        e = memory.data["objects"].get(_words(obj)) or {}
        conf = search_belief.confidence(self._now() - float(e.get("ts") or 0), sc.half_life_s)
        return place if conf >= sc.trust else None

    def _search_start(self, r):
        """The run's SearchBelief, seeded from the object memory and the planner's live-memory episodes
        (search_experience: where the robot picked each goal object up before); an object with only stale
        sightings gets its source from the belief after the LLM ranked its places (one call)."""
        sc, r.search, r.steered = self.cfg["search"], None, []
        if not sc.enabled:
            return
        fn = getattr(r.robot.adapter, "containers", None)
        # people are not places to search (SAGE-v2 F2.4: a person entry was searched like a shelf), nor is a place the ENV marks `search: false`
        keys = [k for k in r.places.keys if not (e := r.places.env.get(k) or {}).get("person") and e.get("search", True) is not False]
        r.search = search_belief.SearchBelief(
            keys, sc, now=self._now, openable=r.places.openable, can_open="Open" in r.robot.plan_actions,
            containers=(lambda k: fn(k, r.places.env.get(k) or {})) if callable(fn)
            else search_belief.containers_from_env(r.places.env, r.places.openable),
            locs=search_route.locs_from_env(r.places.env, keys))
        for o, ks in (getattr(r, "pre_prefer", None) or {}).items():      # option ask_source (use_room): the room the person named
            r.search.prefer(o, ks)
        exp = search_experience.picked_at(self.cfg.get("live_path"), r.places.keys) if sc.habit_weight > 0 else {}
        for g in r.goals:
            e = r.memory.data["objects"].get(_words(g.get("object"))) if g.get("object") else None
            if e:
                r.search.see(g["object"], e.get("place"), e.get("ts"), e.get("how") or "")
            if g.get("object") and exp:                  # where past episodes picked it up (experience prior)
                r.search.learn(g["object"], search_experience.lookup(exp, _words(g["object"])))
        r.goals = [dict(g) for g in r.goals]           # the understand event keeps what was understood
        for g in r.goals:
            o = g.get("object")
            if g.get("type") in ("on", "in") and not g.get("from") and not g.get("from_any") \
                    and (r.search.needs_rank_first(o) or o in r.search.preferred):
                yield from self._search_rank(r, o)
                k = r.search.first_place(o, r.world.arrived if o in r.search.preferred else None)
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

    def _search_total(self, r, o) -> int:
        """Looks the search for ``o`` may make: the engine's ``search_total``, or what an option (``search_budget``) declares per object."""
        return int(self.options.query("search_limit", r, o) or self.cfg.get("search_total", 8))

    def _search_over(self, r, o, ask=False) -> bool:
        """Is the search for ``o`` over by an option's own measure (``search_budget``: looks, remaining probability)? ``ask``: is the question due."""
        return bool(self.options.query("search_over", r, o, ask, default=False))

    def _missed_enough(self, r, o) -> bool:
        """Have the misses of ``o`` passed ``search_max`` (time to ask where it is)? The one miss that option ask_source's ``reask_on_miss``
        counted in advance (the person named the place) is enough only when that look was complete: a container there that was closed
        is opened first, so a first look into a closed box does not tell the person they were wrong."""
        n, primed = r.find_fails[o], (getattr(r, "pre_fails", None) or {}).get(o)
        if n <= self.cfg["search_max"]:
            return False
        s = getattr(r, "search", None)
        return not (primed is not None and n == primed + 1 and s is not None and not s.look_complete(o, r.world.arrived, r.world.opened))

    def _second_look_seen(self, r, o, place, nice, skill):
        """The goal check's first Find missed and the second saw ``o``: (ok, evidence, skill). An option (``on_second_look``) may ask for
        more before believing a sighting that followed a miss; its verdict wins, else the second look is believed."""
        own = self.options.decide("on_second_look", r, o, place)
        if own is not None:
            if own[0]:
                r.memory.see(o, place, "seen at its goal place")
            return own
        r.memory.see(o, place, "seen at its goal place")
        return True, f"the {o} is seen on the {nice} (second look)", skill

    @staticmethod
    def _search_open(r, action) -> bool:
        """An Open that failed during a search: the search gives that container up (``_recover_open``), so it is not "the same failure again"."""
        s = getattr(r, "search", None)
        return action == "Open" and s is not None and bool(s.under_search())

    def _recover_open(self, r, st, reason, rule):
        """An Open failed while searching (a locked cabinet the ENV says opens): that container is no place to look into; the
        belief gives it up and the search goes on at its next look, instead of trying the Open again until the run stops.
        (outcome, replan hint), like the other ``_recover_*``: (None, "") when no search is under way."""
        s, c = getattr(r, "search", None), st["object"]
        objs = s.under_search() if s is not None else []
        if not objs or not self.cfg.get("search_legs"):
            return None, ""
        o, s.blocked = objs[0], s.blocked | {c}
        nxt = r.steps[r.i + 1] if r.i + 1 < len(r.steps) else {}
        if nxt.get("action") == "Find" and nxt.get("object") == o:
            r.steps[r.i:r.i + 2] = [dict(nxt)]              # the Find that was to follow the Open stands in for it: the leg replaces both
        leg = s.next_leg(o, r.world.arrived, r.world.opened)
        if leg is not None and self._splice_leg(r, leg):
            yield r.ev("recover", at_index=r.i, kind="search_leg", msg=f"{c} cannot be opened ({reason}): looking for the {o} at {leg.text()}")
            return NEXT, ""
        return None, f"; {c} cannot be opened ({reason}): do not try it again, look elsewhere"

    def _search_told(self, r, o, k) -> None:
        r.__dict__.setdefault("told", set()).add((o, k))      # where the person said it is (option relook reads it)
        if getattr(r, "search", None) is not None:
            r.search.see(o, k, how="user said"); r.search.unsearch(o, k)

    def _search_effect(self, r, st) -> None:
        """A step succeeded: a Find ends the object's search (and a look for it elsewhere right after is
        skipped: search.skip_relook); moving an object at a place voids the searches there (something
        changed)."""
        s = getattr(r, "search", None)
        if s is None:
            return
        if st["action"] == "Find":
            s.found(st["object"], r.world.arrived)
            r.steps, note = search_belief.skip_relook(r.steps, r.i, st["object"], r.world.arrived)
            if note:
                r.steered = list(getattr(r, "steered", None) or []) + [note]
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
