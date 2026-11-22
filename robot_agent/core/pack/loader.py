"""Resolve a robot's pack: the chain core -> base -> robot, merged, with the callables of each pack imported.

What: ``resolve(manifest, adapter)`` gives the :class:`Pack` the engine reads its vocabulary and rules from. The shipped
packs are found by name in the pack layer's index; a manifest with no ``pack:`` section is schema 1: it gets the base the
index names (``schema_1_base``) plus what it declares today (``lift.py``). Merge rule: an entry of a later pack replaces
the entry of the same name whole (it keeps its place in the order); ``remove`` deletes names; one base only.
Why: one place builds the chain, so a step that moves a table adds a section file and edits no shared code
(docs/proposals/generalize_core_design.md, "Pack declaration"). The pack layer is loaded by NAME: no import edge to it.
Off: nothing is resolved until somebody asks (``RobotView.pack``); a section with no file declares nothing.
"""

from __future__ import annotations

import copy
import functools
import importlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from robot_agent.contract import pack as schema
from robot_agent.contract.pack import PackDecl, PackError
from robot_agent.contract.pack_files import read

from . import check
from .lift import lift
from .registry import REGISTRY

PACKS = "robot_agent.packs"            # the pack layer; loaded by name so that the engine has no import edge to it
UNSET: Any = object()


@dataclass
class Pack:
    """A resolved pack. ``tables``: table -> {name -> entry}; ``origin``: table -> {name -> the pack the entry comes from}."""
    schema: int = 1
    name: str = ""                                         # the robot pack's own name ("" for schema 1)
    version: str = ""
    extends: Optional[str] = None
    chain: tuple = ()                                      # the names of the packs, the core first
    tables: dict = field(default_factory=dict)
    origin: dict = field(default_factory=dict)
    questions_default_kind: Optional[str] = None
    failure_patterns: list = field(default_factory=list)
    kept: dict = field(default_factory=dict)               # schema 1: manifest data with no pack table yet, by its path
    problems: list = field(default_factory=list)           # check.Problem rows that did not stop the load
    registry: Any = field(default=None, compare=False, repr=False)

    def table(self, name: str) -> dict:
        return self.tables.get(name, {})

    def entry(self, table: str, name: str):
        return self.table(table).get(name)

    def value(self, table: str, name: str, key: str):
        """The field ``key`` of an entry: as declared, else the schema's default for it (None when there is none)."""
        e = self.entry(table, name)
        return e[key] if isinstance(e, dict) and key in e else schema.default(table, key)

    def named(self, namespace: str, name: Optional[str]):
        """The registered callable ``name`` of ``namespace``; None when the name is empty or not registered."""
        return self.registry.get(namespace, name) if name and self.registry is not None else None

    def header(self) -> dict:
        return {"schema": self.schema, "name": self.name, "version": self.version, "extends": self.extends}


def packs_root(root=None) -> Path:
    return Path(root) if root is not None else Path(importlib.import_module(PACKS).ROOT)


@functools.lru_cache(maxsize=None)
def index(root=None) -> dict:
    """The pack layer's ``index.yaml``: {root, packs: {name: {dir}}, schema_1_base, schema_1_lift, schema_1_keep}."""
    import yaml
    data = yaml.safe_load((packs_root(root) / "index.yaml").read_text(encoding="utf-8")) or {}
    if not isinstance(data.get("packs"), dict):
        raise PackError(["index.yaml: `packs` must map a pack name to its folder"], str(packs_root(root)))
    return data


@functools.lru_cache(maxsize=None)
def shipped(name: str, root=None) -> PackDecl:
    """The declaration of the shipped pack ``name`` (its ``pack.yaml`` and the section files next to it), read once."""
    packs = index(root)["packs"]
    if name not in packs:
        raise PackError([f"extends: no pack named {name!r} ships with the engine (known: {', '.join(sorted(packs))})"])
    folder = packs_root(root) / str((packs[name] or {}).get("dir", name))
    try:
        return read({"file": "pack.yaml", "dir": "."}, folder, where=f"pack {name}")
    except PackError as e:
        raise PackError(e.errors, str(folder)) from e


def clear_cache() -> None:
    index.cache_clear()
    shipped.cache_clear()


def chain_of(decl: PackDecl, root=None) -> list:
    """[the root pack, the base of the base ..., the base, ``decl``]: every pack ``decl`` builds on, the core first."""
    names, base = [], decl.extends
    while base:
        if base in names:
            raise PackError([f"extends: {base!r} extends itself ({' -> '.join(names[::-1] + [base])})"])
        names.append(base)
        base = shipped(base, root).extends
    first = index(root).get("root")
    order = ([first] if first and first not in names else []) + names[::-1]
    return [shipped(n, root) for n in order] + [decl]


def merge(chain: list) -> tuple:
    """(tables, origin, failure patterns, problems) of a chain: later entries replace earlier ones whole, ``remove`` deletes.
    Removing a name the base does not have is recorded as a problem and stops nothing (a base may not hold the table yet)."""
    tables: dict = {t: {} for t in schema.TABLES}
    origin: dict = {t: {} for t in schema.TABLES}
    patterns, problems = [], []
    for d in chain:
        for t, names in d.remove.items():
            for n in names:
                if tables[t].pop(n, None) is None:
                    problems.append(check.Problem(d.name, "remove", f"remove.{t}", f"{n!r} is not an entry of the base"))
                origin[t].pop(n, None)
        for t, entries in d.tables.items():
            for n, e in entries.items():
                tables[t][n] = copy.deepcopy(e)
                origin[t][n] = d.name
        patterns += copy.deepcopy(d.failure_patterns)
    return tables, origin, patterns, problems


def _own_module(module: str, manifest: Any) -> bool:
    """Is ``module`` a file of the robot's own package: under the manifest's folder, or under ``<pkg>`` for
    ``<pkg>/configs/manifest.yaml``? A pack may name no other module (never a dotted path from data run as code)."""
    src = getattr(manifest, "source", None)
    try:
        spec = importlib.util.find_spec(module) if src else None
    except (ImportError, ValueError):
        spec = None
    if spec is None or not spec.origin:
        return False
    home = Path(src).resolve().parent
    homes = (home, home.parent) if home.name == "configs" else (home,)
    return any(h in Path(spec.origin).resolve().parents for h in homes)


def _import_callables(d: PackDecl, is_shipped: bool, manifest: Any, registry) -> None:
    if not d.callables:
        return
    if is_shipped and not d.callables.startswith(PACKS + "."):
        raise PackError([f"pack {d.name}: callables {d.callables!r} is not a module of the pack layer"])
    if not is_shipped and not _own_module(d.callables, manifest):
        raise PackError([f"pack.callables: {d.callables!r} is not a module of the robot's own package (next to its manifest)"])
    try:
        registry.claim(d.name, d.callables)
        importlib.import_module(d.callables)
    except (ImportError, ValueError) as e:
        raise PackError([f"pack {d.name}: callables {d.callables!r}: {e}"]) from e


def resolve(manifest: Any = None, adapter: Any = None, raw: Any = UNSET, registry=None, root=None) -> Pack:
    """The pack of a robot. ``raw``: a ``pack:`` section to use instead of the manifest's (None = schema 1). Raises
    :class:`PackError` for a schema-2 pack that is wrong; a schema-1 manifest never fails here (its data fails open)."""
    registry = REGISTRY if registry is None else registry
    idx = index(root)
    raw = (getattr(manifest, "pack", None) or None) if raw is UNSET else raw
    src = getattr(manifest, "source", None)
    decl, kept = read(raw, Path(src).parent if src else None), {}
    if decl.schema < 2:
        decl, kept = lift(manifest, adapter, idx.get("schema_1_lift"), idx.get("schema_1_keep"))
        decl.extends = idx.get("schema_1_base")
    elif decl.name in {shipped(n, root).name for n in idx["packs"]}:
        raise PackError([f"pack.name: {decl.name!r} is the name of a pack that ships with the engine"], src)
    chain = chain_of(decl, root)
    for d in chain:
        _import_callables(d, d is not decl, manifest, registry)
    tables, origin, patterns, found = merge(chain)
    own = decl.name if decl.schema >= 2 else None
    found += list(check.callables(tables, origin, registry))
    if own:
        words = getattr(getattr(adapter, "i18n", None), "phrases", None) or {}
        found += list(check.references(tables, origin, own, getattr(manifest, "actions", None) or (), words,
                                       str(getattr(manifest, "language", None) or "en")[:2]))
        if decl.questions_default_kind == "name":
            found.append(check.Problem(own, "deferred", "questions_default_kind", "the value `name` is not built yet (deferred in the design)"))
        stop = [f"pack.{p.path}: {p.text}" for p in found if p.pack == own and p.kind != "remove"]
        if stop:
            raise PackError(stop, src)
    return Pack(schema=decl.schema, name=decl.name if own else "", version=decl.version if own else "", extends=decl.extends,
                chain=tuple(d.name for d in chain), tables=tables, origin=origin,
                questions_default_kind=next((d.questions_default_kind for d in chain[::-1] if d.questions_default_kind), None),
                failure_patterns=patterns, kept=kept, problems=found, registry=registry)


def default_pack(registry=None, root=None) -> Pack:
    """The pack of a robot that declares nothing: the core pack and the schema-1 base (for code that has no robot at hand)."""
    return resolve(None, None, raw=None, registry=registry, root=root)


__all__ = ["PACKS", "Pack", "resolve", "default_pack", "chain_of", "merge", "shipped", "index", "clear_cache"]
