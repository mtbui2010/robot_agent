"""Example plans similar to a command, for a planner guide's ``{EXAMPLES}`` (docs/planner_guide.md).

The pool is the robot's seed pool (manifest ``planner.seed_memory``) plus the verified episodes
of the planner's live memory (``verified: true`` records, robot_agent's episode recording);
ranking is embodiplan's ``MemoryRetriever`` (the one SAGE retrieves with), so the guide path
and SAGE see the same examples. embodiplan is imported here only (rule 3); without it the
pool is ranked by word overlap.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterable, Optional

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9]+")


def _steps(rec: dict) -> list:
    """The ``[{action, object}]`` plan of a memory record (``steps`` or ``plan_text``)."""
    st = rec.get("steps")
    if st is None:
        try:
            st = json.loads(rec.get("plan_text") or "{}").get("steps")
        except (TypeError, ValueError, AttributeError):
            st = None
    return [{"action": s.get("action", ""), "object": s.get("object", "")}
            for s in st or [] if isinstance(s, dict) and s.get("action")]


def read_pool(seed_path: Optional[str] = None, live_path: Optional[str] = None,
              verified_only: bool = True) -> list:
    """``[{task, steps, source}]``: every seed record, and the live records that are verified
    (all of them with ``verified_only=False``). Unreadable lines are skipped."""
    out = []
    for path, source in ((seed_path, "seed"), (live_path, "episode")):
        if not path or not os.path.isfile(str(path)):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or not rec.get("task"):
                    continue
                if source == "episode" and verified_only and rec.get("verified") is not True:
                    continue
                steps = _steps(rec)
                if steps:
                    out.append({"task": str(rec["task"]).strip(), "steps": steps, "source": source})
    return out


def _overlap(a: str, b: str) -> float:
    ta, tb = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _retriever(pool: list):
    """embodiplan's MemoryRetriever over ``pool`` (None without embodiplan)."""
    try:
        from embodiplan.memory_retriever import MemoryRetriever, MemoryRetrieverConfig
    except ImportError:
        return None
    r = MemoryRetriever(MemoryRetrieverConfig())
    for ex in pool:
        r.add_episode(ex["task"], ex["steps"], reasoning=ex["source"])
    return r


def rank(command: str, pool: Iterable[dict], k: int = 3, leave_one_out: bool = False) -> list:
    """The ``k`` examples of ``pool`` most similar to ``command`` (``score`` added), best
    first. ``leave_one_out``: never the command's own task or a near-duplicate of it."""
    pool = list(pool)
    if not pool or k <= 0:
        return []
    by_task = {}
    for ex in pool:
        by_task.setdefault(ex["task"], ex)
    r = _retriever(pool)
    if r is not None:
        hits = r.retrieve(command, k=k, leave_one_out=leave_one_out)
        return [dict(by_task.get(h.task, {"task": h.task, "steps": [], "source": "seed"}),
                     score=round(float(h.score), 3)) for h in hits]
    scored = sorted(({**ex, "score": round(_overlap(command, ex["task"]), 3)} for ex in pool),
                    key=lambda e: -e["score"])
    if leave_one_out:
        scored = [e for e in scored if e["score"] < 0.92]
    return scored[:k]


def similar_examples(command: str, seed_path: Optional[str] = None, live_path: Optional[str] = None,
                     k: int = 3, leave_one_out: bool = False, verified_only: bool = True) -> list:
    """:func:`rank` over :func:`read_pool` (``[]`` when there is no pool or it fails)."""
    try:
        return rank(command, read_pool(seed_path, live_path, verified_only), k, leave_one_out)
    except Exception as e:                      # a broken memory file never stops planning
        log.warning("guide examples: %s", e)
        return []
