"""The constants of the default pack: the texts the engine says when a robot's own i18n has none.

What: ``default_table()`` is the ``constants`` section of the packs a robot with no pack of its own gets (the root pack, then
the standard pack: robot_agent/packs/household/constants.yaml); ``texts(table, prefix)`` is {key: value} of the entries named
``<prefix><key>``, in the table's order. ``core/planning/robot.py`` reads ``CORE_TEXT`` through ``texts(..., 'core_text.')``.
Why: CORE_TEXT was a constant of the core with English sentences in it (docs/proposals/generalize_core_design.md, Step S10);
it is declared, labelled data of the standard pack now and the old name stays a loaded view of it.
Off: delete an entry from the file and the engine falls back to the key as text, as it does for any unknown key.
"""

from __future__ import annotations

import functools
from typing import Mapping

from . import loader
from .phrases import default_chain

TABLE = "constants"


@functools.lru_cache(maxsize=None)
def default_table(root=None) -> dict:
    """{name: entry as declared} of the default chain's ``constants`` section. Read once; callers must not change it."""
    return loader.merge(list(default_chain(root)))[0][TABLE]


def texts(table: Mapping, prefix: str) -> dict:
    """{key: value} of the entries whose name starts with ``prefix`` (the prefix removed), in the table's order."""
    return {n[len(prefix):]: e["value"] for n, e in table.items() if n.startswith(prefix) and isinstance(e, dict) and "value" in e}


__all__ = ["TABLE", "default_table", "texts"]
