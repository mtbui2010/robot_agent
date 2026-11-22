"""Schema 1 -> the pack structure: what a manifest with no ``pack:`` section declares today, as a pack declaration.

What: ``lift(manifest, adapter, tables, keep)`` copies the manifest / adapter data that the pack index names
(``schema_1_lift``: pack table <- path) into pack tables, and keeps the paths of ``schema_1_keep`` as written.
Why: the engine can read ONE structure for every robot, and today's packs load unchanged
(docs/proposals/generalize_core_design.md, "Compatibility"). Pure: nothing is written to disk and nothing is added,
labelled or guessed, so lifted data keeps today's rules (unlabelled option data is not used; an unknown name does nothing).
Off: a manifest with a ``pack:`` section of schema 2 is never lifted; an index with no ``schema_1_lift`` lifts nothing.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, Mapping, Optional

from robot_agent.contract.pack import PackDecl

NAME = "manifest"                      # the origin of every lifted entry


def dig(manifest: Any, adapter: Any, path: str) -> Any:
    """The value at ``path``: ``adapter.<attribute>[.key ...]`` or ``<manifest attribute>[.key ...]``; None when absent."""
    head, *rest = str(path).split(".")
    if head == "adapter":
        cur = getattr(adapter, rest.pop(0), None) if rest else None
    else:
        cur = getattr(manifest, head, None)
    for k in rest:
        cur = cur.get(k) if isinstance(cur, Mapping) else None
    return cur


def plain(v: Any) -> Any:
    """A copy of declared data as plain data: a dataclass (a recovery rule) -> a mapping, an enum (a failure code) -> its value."""
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: plain(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, Mapping):
        return {(k.value if isinstance(k, enum.Enum) else k): plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    return v


def lift(manifest: Any, adapter: Any = None, tables: Optional[Mapping] = None, keep=()) -> tuple:
    """(the schema-1 declaration as a :class:`PackDecl` named ``manifest``, {kept path: value as written})."""
    decl = PackDecl(schema=1, name=NAME, where="manifest (schema 1)")
    for table, path in (tables or {}).items():
        v = dig(manifest, adapter, path)
        if isinstance(v, Mapping) and v:
            decl.tables[table] = plain(v)
    kept = {p: plain(v) for p in keep or () for v in [dig(manifest, adapter, p)] if v not in (None, {}, [])}
    return decl, kept


__all__ = ["NAME", "dig", "plain", "lift"]
