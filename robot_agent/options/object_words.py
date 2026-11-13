"""Option ``object_words`` (default off): the goal checks find an object the plan names in other words as the same object.

The belief of a run keeps what the plan did under the executor's name (``Pick cell phone`` puts ``cell phone`` in ``placed`` and in the set of
handed-over objects); the goal keeps the reader's name (``phone``). "Is the goal object where the goal says" asked for the exact name, found
nothing, and believed the goal NOT met after a plan that had done everything: the run looked again, replanned, fetched the object a second time
and ended ``failed`` with every goal true in the world.

With the option the two belief tables of the run (what was placed where, what was handed over) look an object up by its words: the single entry
whose word tokens contain, or are contained in, the asked name ('phone' / 'cell phone'); several fits or none stay unmatched, so 'red book' and
'green book' remain different. Only the lookup changes (writes keep the executor's name); no object name in the code. The plan-simulation
checks (``_missing``, ``_reaches``) already match this way.

Services: ``belief_table()`` and ``belief_set()`` (the run's tables). Keys: ``enabled`` (legacy ``object_words``). Lab flag ``object_words``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import exec_integrity

SPEC = OptionSpec(
    name="object_words",
    keys=(Key("enabled", "bool", False, aliases=("object_words",)),),
    hooks=("on_start",),
    services=("belief_table", "belief_set"),
    order=75,
    enabled="enabled",
    doc="The run's belief tables (placed, handed over) look an object up by its words ('phone' is the 'cell phone' the plan picked).",
)


def _name(table, obj):
    """The key of ``table`` naming ``obj``: itself, else the single entry that names it in other words, else ``obj``."""
    keys = list(table)
    if obj in keys:
        return obj
    hit = exec_integrity.lookup_object({k: k for k in keys}, obj)
    return obj if hit is None else hit


class WordTable(dict):
    """``object -> where``; a read or a pop finds the entry by its words."""

    def get(self, obj, default=None):
        return dict.get(self, _name(self, obj), default)

    def __getitem__(self, obj):
        return dict.__getitem__(self, _name(self, obj))

    def __contains__(self, obj):
        return dict.__contains__(self, _name(self, obj))

    def pop(self, obj, *default):
        return dict.pop(self, _name(self, obj), *default)


class WordSet(set):
    """A set of objects; membership is by words."""

    def __contains__(self, obj):
        return set.__contains__(self, _name(self, obj))


class ObjectWords(Option):
    def on_start(self, run) -> None:
        self.svc.lab(SPEC.name, on=True)

    def belief_table(self):
        return WordTable()

    def belief_set(self):
        return WordSet()


register(SPEC, ObjectWords)
