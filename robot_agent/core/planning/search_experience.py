"""Where the robot's own past episodes picked an object up: the search belief's experience prior.

The search ranking (``search.RANK_PROMPT``) is an LLM's commonsense guess ("an apple is in the
kitchen"); in a given home the robot's experience can say otherwise (the apple is kept on the
desk). The planner reads that experience from its live memory (SAGE retrieves the episodes), so
a belief that ranked places by commonsense alone overrode a planner that knew better
(T2 S06 / H04 / H07 / M04 after the v2 search). This module reads the same episodes, planner
neutral: each record's plan (``steps``, or ``plan_text`` JSON ``{"steps": [...]}``) is walked
with the robot's position (``MoveTo``), and a ``Pick <obj>`` at a place counts as "found there".

Records the episode recording marked ``verified: false`` are skipped (docs/memory.md); legacy
records without the flag count, as the planner's retriever counts them. Only the last
``max_records`` lines are read (the newest experience). Pure: no robot, no LLM. See docs/search.md.
"""

from __future__ import annotations

import json
import os
from collections import deque
from typing import Callable, Iterable, Optional

MAX_RECORDS = 2000


def _steps(rec: dict) -> list:
    st = rec.get("steps")
    if st is None:
        try:
            st = (json.loads(rec.get("plan_text") or "{}") or {}).get("steps")
        except (TypeError, ValueError, AttributeError):
            st = None
    return [s for s in st or [] if isinstance(s, dict)]


def _norm(s) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").split())


def picks(steps: Iterable[dict], places: Iterable[str]) -> list:
    """[(object, place)] of each Pick in a plan, the place being where the last MoveTo took the robot
    (a place not in ``places`` gives no pair)."""
    keys, at, out = set(places), None, []
    for s in steps:
        a, o = s.get("action"), s.get("object")
        if a == "MoveTo":
            at = o
        elif a == "Pick" and o and at in keys:
            out.append((_norm(o), at))
    return out


def picked_at(path: Optional[str], places: Iterable[str], max_records: int = MAX_RECORDS) -> dict:
    """{object: {place: times picked there}} over the episodes of the JSONL at ``path`` ({} when
    there is no file)."""
    if not path or not os.path.isfile(str(path)):
        return {}
    keys = list(places)
    out: dict = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = deque(f, maxlen=max(1, int(max_records)))
    except OSError:
        return {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("verified") is False:
            continue
        for o, p in picks(_steps(rec), keys):
            d = out.setdefault(o, {})
            d[p] = d.get(p, 0) + 1
    return out


def lookup(counts: dict, obj: str, same: Optional[Callable[[str, str], bool]] = None) -> dict:
    """The place counts of ``obj`` (its normalized name, else the first name ``same`` matches)."""
    k = _norm(obj)
    if k in counts:
        return dict(counts[k])
    if same is not None:
        for name, d in counts.items():
            if same(name, k):
                return dict(d)
    return {}


__all__ = ["MAX_RECORDS", "picks", "picked_at", "lookup"]
