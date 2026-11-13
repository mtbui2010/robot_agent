"""Option ``search_per_object`` (default off): each object has its own search budget (``search_total``).

The engine counts the looks of a search in ``run.searched``, the places missed in the whole run. With two objects to fetch, the
places the first one used up are charged to the second one too, so the second search starts with less than the budget (or none)
and gives up in a place where it had looks left. With the option on, the count for an object is the places the search belief has
marked searched FOR THAT OBJECT (``SearchBelief.searched[obj]``); a search that has no belief (no search legs) keeps the run-wide count.

Generic: no object, place, number or word; the budget itself stays the existing ``search_total``. Hook ``on_start`` (a lab line that the run counts per object); service ``looks_made(run, obj)``.
Keys: ``enabled`` (legacy ``search_per_object``).
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register

SPEC = OptionSpec(
    name="search_per_object",
    keys=(Key("enabled", "bool", False, aliases=("search_per_object",)),),
    hooks=("on_start",),
    services=("looks_made",),
    order=70,
    enabled="enabled",
    doc="Each object has its own search budget (search_total) instead of one budget for the whole run.",
)


class SearchPerObject(Option):
    def on_start(self, run) -> None:
        self.svc.lab("search_per_object", on=True)

    def looks_made(self, run, obj):
        """Looks the search for ``obj`` has made (places marked searched for it); None = no belief, the engine counts the run."""
        bel = getattr(run, "search", None)
        if bel is None:
            return None
        return len(bel.searched.get(obj, {}))


register(SPEC, SearchPerObject)
