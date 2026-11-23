"""Check a RESOLVED pack: every name its data uses exists (design, "How the core loads and validates it", step 4).

What: ``callables`` lists the registered names an entry refers to that nobody registered; ``references`` lists, for the
entries of ONE pack, a verifier / recovery policy / predicate / action / phrase that no pack of the chain declares, a
predicate used with a wrong number of arguments or outside its scopes, and a field whose reader the design defers.
Why: one pack alone cannot know its base, so this runs after the merge. Pure functions; the loader decides what a problem
means (a schema-2 robot pack: the load stops; a shipped pack: recorded; data lifted from schema 1: never checked, it fails
open as today). Off: a pack that names nothing has no problem.
"""

from __future__ import annotations

from typing import Iterator, NamedTuple

from robot_agent.contract.pack import pred_parts

BUILTIN_POLICIES = ("planner", "stop", "tree")             # recovery policies every pack has (design, "Recovery")
LEAVES = ("cond", "act", "ask", "confirm", "reply")        # the leaf types of a tree (core/planning/behavior_tree.py)
# (table, field path, the value that is deferred or None = any value): parsed by the schema, read by nothing yet
DEFERRED = (("questions", ("answer", "type"), "number"), ("questions", ("answer", "extract"), None),
            ("questions", ("answer", "unclear"), "model"), ("recovery.policies", ("ladder",), None))
# (table, field, the namespaces one of which must hold the name)
CALLABLES = (("goal_kinds", "rules", ("goal.rules",)), ("predicates", "eval", ("predicate",)),
             ("verifiers", "callable", ("verifier.goal", "verifier.step")), ("recovery.policies", "callable", ("recovery",)))


class Problem(NamedTuple):
    pack: str          # the pack the entry comes from
    kind: str          # callable | reference | predicate | deferred | remove
    path: str
    text: str


def callables(tables: dict, origin: dict, registry) -> Iterator[Problem]:
    """A callable an entry names (of any pack of the chain) that is not registered in its namespace."""
    for table, fld, spaces in CALLABLES:
        for name, e in tables.get(table, {}).items():
            ref = e.get(fld) if isinstance(e, dict) else None
            if ref and not any(registry.has(ns, ref) for ns in spaces):
                yield Problem(origin[table][name], "callable", f"{table}.{name}.{fld}",
                              f"{ref!r} is not registered in the namespace {' / '.join(spaces)}")


def leaves(node) -> Iterator[list]:
    """Every leaf ``[type, *args]`` of a tree given as nested lists (anything else is skipped)."""
    if isinstance(node, list) and node and isinstance(node[0], str):
        if node[0] in LEAVES:
            yield node
        else:
            for child in node[1:]:
                yield from leaves(child)


def _pred(name: str, args: list, scope: str, tables: dict) -> str:
    """What is wrong with the use of predicate ``name`` with ``args`` in ``scope`` ("" = nothing)."""
    decl = tables.get("predicates", {}).get(name)
    if not isinstance(decl, dict):
        return f"the predicate {name!r} is not declared"
    want = list(decl.get("args") or [])
    if scope not in (decl.get("scopes") or ()):
        return f"the predicate {name!r} may not be used as `{scope}` (its scopes: {', '.join(decl.get('scopes') or []) or 'none'})"
    if len(args) > len(want) or scope != "effect" and len(args) != len(want):
        return f"the predicate {name!r} takes {len(want)} argument(s) ({', '.join(want) or 'none'}), got {len(args)}"
    for a, w in zip(args, want):
        if a == "*" and (scope == "tree" or not str(w).endswith("?")):
            return f"the argument {w!r} of the predicate {name!r} may not be the wildcard * here"
    return ""


def _uses(table: str, name: str, e) -> Iterator[tuple]:
    """(path, what kind of name, the name or (predicate, args, scope)) for everything the entry ``e`` refers to."""
    w = f"{table}.{name}"
    if table == "recovery.by_action" or table == "recovery.by_kind":
        yield w, "action" if table.endswith("action") else "goal_kind", name
        yield w, "policy", e
    elif table == "fleet.kinds":
        yield w, "goal_kind", name
    for x in (e if isinstance(e, list) else [e]) if table == "trees" else ():
        for leaf in leaves(x.get("tree") if isinstance(x, dict) else None):
            if leaf[0] == "act" and len(leaf) > 1:
                yield f"{w}.tree", "action", leaf[1]
            elif leaf[0] == "cond" and len(leaf) > 1:
                yield f"{w}.tree", "predicate", (leaf[1], [a for a in leaf[2:] if isinstance(a, str)], "tree")
    if not isinstance(e, dict):
        return
    for fld, what in {"goal_kinds": (("observe", "verifier"),), "actions": (("recovery", "policy"),),
                      "questions": (("phrase", "phrase"),)}.get(table, ()):
        if e.get(fld):
            yield f"{w}.{fld}", what, e[fld]
    for i, need in enumerate(e.get("needs") or () if table == "goal_kinds" else ()):
        for a in need if isinstance(need, list) else [need]:
            yield f"{w}.needs[{i}]", "action", a
    for i, v in enumerate(e.get("post") or () if table == "actions" else ()):
        yield f"{w}.post[{i}]", "verifier", v
    eff = e.get("effects") if table == "actions" and isinstance(e.get("effects"), dict) else {}
    for fld, scope, preds in (("holds", "goal", e.get("holds") if table == "goal_kinds" else None),
                              ("pre", "pre", e.get("pre") if table == "actions" else None),
                              ("effects.add", "effect", eff.get("add")), ("effects.del", "effect", eff.get("del"))):
        for i, p in enumerate(preds or ()):
            _neg, pname, args = pred_parts(p)
            yield f"{w}.{fld}[{i}]", "predicate", (pname, args, scope)


def _deferred(table: str, name: str, e) -> Iterator[tuple]:
    for t, path, value in DEFERRED:
        cur = e if t == table else None
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is not None and (value is None or cur == value):
            yield f"{table}.{name}.{'.'.join(path)}", f"{'the value ' + repr(cur) if value is not None else 'this field'} is not built yet (deferred in the design)"


def references(tables: dict, origin: dict, own: str, actions=(), phrases=(), language: str = "en") -> Iterator[Problem]:
    """The problems of the entries that come from the pack ``own``. ``actions``: the abstract actions of the robot's skills;
    ``phrases``: the phrase keys the robot's own words already hold; ``language``: the robot's default language."""
    known = {"verifier": set(tables.get("verifiers", {})), "policy": set(tables.get("recovery.policies", {})) | set(BUILTIN_POLICIES),
             "action": set(tables.get("actions", {})) | set(actions), "goal_kind": set(tables.get("goal_kinds", {})),
             "phrase": {k for k, v in tables.get("phrases", {}).items() if isinstance(v, dict) and (v.get(language) or v.get("file"))}
             | set(phrases)}
    for table, entries in tables.items():
        for name, e in entries.items():
            if origin.get(table, {}).get(name) != own:
                continue
            for path, what, ref in _uses(table, name, e):
                if what == "predicate":
                    bad = _pred(ref[0], ref[1], ref[2], tables)
                    if bad:
                        yield Problem(own, "predicate", path, bad)
                elif ref not in known[what]:
                    yield Problem(own, "reference", path, f"the {what.replace('_', ' ')} {ref!r} is not declared by this pack or its base")
            for path, text in _deferred(table, name, e):
                yield Problem(own, "deferred", path, text)


__all__ = ["Problem", "callables", "references", "leaves", "BUILTIN_POLICIES", "DEFERRED"]
