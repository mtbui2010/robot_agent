"""The ``pack:`` section of a manifest: a robot's vocabulary and rules as declared data (pack schema 2).

What: the WHOLE schema of a pack declaration (a header and eleven data sections: goal kinds, predicates, actions, verifiers,
recovery, questions, phrases, trees, constants, options, fleet), parsed into :class:`PackDecl`; every bad field is reported by
its path in one :class:`PackError`. Why: the engine reads its vocabulary from declared data instead of naming one kind of robot
(docs/proposals/generalize_core_design.md, "Pack declaration"). Off: a manifest with no ``pack:`` key is schema 1; nothing
here runs for it, and the engine's loader (``robot_agent/core/pack``) gives it the standard pack with what it declares today.

Contract layer: plain data, no engine import. Whether a NAME exists (a registered callable, a predicate of the base pack) is
the loader's check: one pack alone cannot know its base. An unknown key is an error, so a mistyped rule is never silently off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

SUPPORTED = (1, 2)                          # the pack schemas this core loads; no `pack:` key at all = schema 1
LABELS = ("source", "assumed")              # where a fact comes from: `source: "<text>"` or `assumed: true`
SECTIONS = ("goal_kinds", "predicates", "actions", "verifiers", "recovery", "questions", "phrases", "trees", "constants",
            "options", "fleet")
HEADER = ("schema", "name", "version", "extends", "file", "dir", "callables", "remove", "questions_default_kind") + LABELS
NAME_RE = re.compile(r"[a-z][a-z0-9_]*$")                         # a pack name: the namespace of what it registers
REF_RE = re.compile(r"[a-z][a-z0-9_]*\.[A-Za-z_]\w*$")            # <pack>.<name>: a registered callable
MODULE_RE = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
# at(?place) / holding(*) / not opened(?c) / hand_free() / hand_free: the predicate spelling of the manifest's skills
PRED_RE = re.compile(r"(not\s+|!)?[A-Za-z_]\w*(\s*\([^()]*\))?$")
KEY_HINT = 'a key must be a string; YAML reads a bare on / off / yes / no as true / false, so write it in quotes ("on":)'


def _text(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_SCALARS = {                                                      # type name -> (test, what the error says)
    "str": (_text, "a non-empty string"),
    "bool": (lambda v: isinstance(v, bool), "true / false"),
    "int": (lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0, "a count (0, 1, 2 ...)"),
    "num": (_number, "a number"),
    "any": (lambda v: True, "anything"),
    "map": (lambda v: isinstance(v, dict), "a mapping"),
    "list": (lambda v: isinstance(v, list), "a list"),
    "ref": (lambda v: isinstance(v, str) and bool(REF_RE.match(v)), "a registered name <pack>.<name>"),
    "pred": (lambda v: isinstance(v, str) and bool(PRED_RE.match(v.strip())), "a predicate such as at(?place) or hand_free()"),
    "need": (lambda v: _text(v) or isinstance(v, list) and bool(v) and all(_text(x) for x in v),
             "an action, or a list of actions of which any one will do"),
}


class _Or(tuple):
    """A value that may follow any one of several specs."""


# A spec: a scalar name ("str?" = may be null), a tuple = one of these values, [spec] = a list of spec, a dict = a mapping with
# these keys ("*" = every other key; without it an unknown key is an error; a label is always allowed), _Or = alternatives.
_SLOT = {"type": ("object", "place", "place_or_person", "container", "text", "person"), "required": "bool"}
_ARG = {"type": ("string", "number", "integer", "boolean", "enum"), "values": "list", "min": "num", "max": "num", "unit": "str",
        "default": "any", "required": "bool", "from_option": "str"}
_ANSWER = {"type": ("yes_no", "yes_no_correction", "place", "choice", "instance", "free_text", "number"),
           "choices": {"*": {"words": {"*": ["str"]}, "lists": ["str"], "implies": ("yes", "no")}},
           "extract": {"*": {"type": ("number",), "unit": "str"}}, "keep_text": "bool", "unclear": ("reask", "model", "fail"),
           "reasks": "int", "then": ("place",)}
_TREE = {"tree": "list", "events": "bool", "ladder": "bool", "*": "any"}
_RULE = {"action": "str", "retries": "int", "then": "str?", "retry_allowed": "bool", "mark_searched": "bool", "report": "bool",
         "note": "str"}                                           # the fields of contract.recovery.RecoveryRule
# table -> the spec of ONE entry. A table is a mapping name -> entry; the merge of a pack chain replaces an entry whole.
TABLES = {
    "goal_kinds": {"slots": {"*": _SLOT}, "needs": ["need"], "understood": ("always", "capable"), "prompt": "str",
                   "planned_by": ("planner", "template", "tree"), "rules": "ref", "holds": ["pred"], "observe": "str?",
                   "unknown_is": ("belief", "failure"), "phrase": "str", "subgoal": "str", "conflicts": ("default", "none"),
                   "limits": ["str"]},
    "predicates": {"eval": "ref", "kind": ("fact",), "args": ["str"], "scopes": [("tree", "effect", "pre", "goal")]},
    "actions": {"class": ("move", "look", "grasp", "release", "open", "close", "speak", "wait", "sense"),
                "arg": {"name": "str", "type": ("place", "object", "container", "text", "seconds", "none")},
                "args": {"*": _ARG}, "plan_step": "bool", "rules": "str", "pre": ["pred"],
                "effects": {"add": ["pred"], "del": ["pred"]}, "releases": _Or(("bool", ("from_effects",))), "post": ["str"],
                "recovery": "str", "errors": [_Or(("str", {"code": "str", "subcode": "str"}))],
                "duration_s": {"typical": "num", "max": "num"}, "verb": "str", "ability": "str", "describe": "str"},
    "verifiers": {"callable": "ref", "kind": ("sensor", "vlm"), "skill": "str", "expect": "bool", "prompt": "str",
                  "params": "map", "*": "any"},
    "recovery.codes": _Or(("str", _RULE)),
    "recovery.policies": {"callable": "ref", "ladder": [{"do": "str", "times": "int", "question": "str", "with": "map"}],
                          "*": "any"},
    "recovery.by_action": "str",
    "recovery.by_kind": "str",
    "questions": {"phrase": "str?", "kind": "str?", "answer": _ANSWER, "silence_retries": "int"},
    "phrases": {"slots": ["str"], "file": "str", "*": "str"},     # <language code>: the text with {slots}
    "trees": _Or((_TREE, [_TREE])),                               # one tree, or alternatives (the first usable one is taken)
    "constants": {"value": "any"},
    "options": {"*": "any"},
    "fleet.kinds": {"footprint": {"objects": ["str"], "places": ["str"]}},
}
# what a field means when an entry does not give it (the design's defaults; any other absent field is None)
DEFAULTS = {"goal_kinds": {"understood": "capable", "planned_by": "planner", "unknown_is": "belief", "conflicts": "default"},
            "actions": {"plan_step": True, "releases": "from_effects"}}


class PackError(ValueError):
    """A pack declaration that does not follow the schema. ``errors`` holds one ``'<field path>: <problem>'`` line per bad field."""

    def __init__(self, errors: list, source: Optional[str] = None):
        self.errors, self.source = list(errors), source
        super().__init__((f"{source}: " if source else "") + "invalid pack:\n  " + "\n  ".join(self.errors))


@dataclass
class PackDecl:
    """One pack as declared (not merged with its base). ``tables``: table name -> {entry name -> the entry as written}."""
    schema: int = 1
    name: str = ""
    version: str = "1.0"
    extends: Optional[str] = None
    callables: Optional[str] = None                               # ONE module of the pack; importing it registers its names
    source: Optional[str] = None                                  # the default label of entries that carry none
    assumed: bool = False
    remove: dict = field(default_factory=dict)                    # table -> names of the base this pack does not have
    questions_default_kind: Optional[str] = None
    tables: dict = field(default_factory=dict)
    failure_patterns: list = field(default_factory=list)          # recovery.failure_patterns, as written
    where: str = "pack"

    def header(self) -> dict:
        return {"schema": self.schema, "name": self.name, "version": self.version, "extends": self.extends}

    def label(self, entry=None) -> Optional[dict]:
        """The label of ``entry``: its own ``source`` / ``assumed``, else the pack's default; None = it carries none."""
        for d in (entry if isinstance(entry, dict) else {}, {"source": self.source, "assumed": self.assumed}):
            if _text(d.get("source")):
                return {"source": d["source"]}
            if d.get("assumed") is True:
                return {"assumed": True}
        return None


def default(table: str, name: str):
    """What the field ``name`` of an entry of ``table`` means when the entry does not give it."""
    return DEFAULTS.get(table, {}).get(name)


def pred_parts(text: str) -> tuple:
    """``'not at(?place, *)'`` -> ``(True, 'at', ['?place', '*'])``: negated, the predicate's name, its arguments."""
    s = text.strip()
    neg = s.startswith("!") or s[:4].lower() == "not "
    s = s[1:].strip() if s.startswith("!") else s[4:].strip() if neg else s
    name, _, rest = s.partition("(")
    return neg, name.strip(), [a.strip() for a in rest.rstrip(")").split(",") if a.strip()]


def _check(v, spec, where: str, errs: list) -> None:
    """Add to ``errs`` what is wrong with ``v`` under ``spec`` (see the comment above ``_SLOT`` for the spec forms)."""
    if isinstance(spec, _Or):
        tries = [[] for _ in spec]
        for s, e in zip(spec, tries):
            _check(v, s, where, e)
        if all(tries):
            errs.extend(min(tries, key=len))
    elif isinstance(spec, str):
        ok, what = _SCALARS[spec.rstrip("?")]
        if not (v is None and spec.endswith("?")) and not ok(v):
            errs.append(f"{where}: must be {what}, got {v!r}")
    elif isinstance(spec, tuple):
        if isinstance(v, bool) or v not in spec:
            errs.append(f"{where}: must be one of {', '.join(map(str, spec))}, got {v!r}")
    elif isinstance(spec, list):
        if not isinstance(v, list):
            errs.append(f"{where}: must be a list, got {type(v).__name__}")
        for i, x in enumerate(v if isinstance(v, list) else ()):
            _check(x, spec[0], f"{where}[{i}]", errs)
    elif not isinstance(v, dict):
        errs.append(f"{where}: must be a mapping, got {type(v).__name__}")
    else:
        for k, x in v.items():
            sub = spec.get(k, spec.get("*")) if isinstance(k, str) else None
            if not isinstance(k, str):
                errs.append(f"{where}: key {k!r}: {KEY_HINT}")
            elif k in LABELS:
                _check(x, "bool" if k == "assumed" else "str", f"{where}.{k}", errs)
            elif sub is None:
                errs.append(f"{where}: unknown key {k!r} (allowed: {', '.join(sorted(x for x in spec if x != '*'))})")
            else:
                _check(x, sub, f"{where}.{k}", errs)


def _entry_rules(table: str, e, where: str, errs: list) -> None:
    """The rules that tie the fields of ONE entry together (design: "Goal kinds", "Predicates", "Actions", "Answers")."""
    if not isinstance(e, dict):
        return
    if table == "goal_kinds":
        if not e.get("rules") and not e.get("holds"):
            errs.append(f"{where}: needs `rules` (a registered goal.rules name) or `holds` (a list of predicates)")
        if e.get("unknown_is") == "failure" and not e.get("observe"):
            errs.append(f"{where}: `unknown_is: failure` needs an `observe` verifier (with none it could never succeed)")
    elif table == "predicates" and bool(e.get("eval")) == (e.get("kind") == "fact"):
        errs.append(f"{where}: needs exactly one of `eval` (a registered predicate) and `kind: fact`")
    elif table == "actions" and e.get("rules") != "declared" and (e.get("pre") or e.get("effects")):
        errs.append(f"{where}: `pre` and `effects` need `rules: declared` (the standard rules come from the action model)")
    elif table == "verifiers" and (e.get("callable") and e.get("kind") or e.get("kind") == "sensor" and not e.get("skill")):
        errs.append(f"{where}: is either `callable: <name>` or a declared check (`kind: sensor` names its `skill`)")
    elif table == "recovery.policies" and e.get("callable") and e.get("ladder"):
        errs.append(f"{where}: is either `callable: <name>` or a declared `ladder`, not both")
    elif table == "questions" and (e.get("answer") or {}).get("type") == "choice" and not (e.get("answer") or {}).get("choices"):
        errs.append(f"{where}.answer: `type: choice` needs `choices`")
    for n, a in (e.get("args") or {}).items() if table == "actions" and isinstance(e.get("args"), dict) else ():
        if isinstance(a, dict) and a.get("type") == "enum" and not a.get("values"):
            errs.append(f"{where}.args.{n}: `type: enum` needs `values`")


def _header(raw: dict, where: str, errs: list) -> PackDecl:
    schema = raw.get("schema")
    if isinstance(schema, bool) or schema not in SUPPORTED:
        errs.append(f"{where}.schema: required, an integer; {schema!r} is not supported (this core loads pack schema "
                    f"{SUPPORTED[0]} to {SUPPORTED[-1]})")
        schema = SUPPORTED[-1]
    d = PackDecl(schema=schema, where=where)
    for key, test, what in (("name", lambda v: isinstance(v, str) and bool(NAME_RE.match(v)), "a lower-case name such as my_robot"),
                            ("extends", lambda v: v is None or _text(v), "the name of ONE base pack, or null"),
                            ("callables", lambda v: v is None or isinstance(v, str) and bool(MODULE_RE.match(v)), "one module name"),
                            ("source", lambda v: v is None or _text(v), "a string that says where the facts come from"),
                            ("assumed", lambda v: isinstance(v, bool), "true / false"),
                            ("questions_default_kind", lambda v: v is None or _text(v), "a question kind, or null")):
        if key in raw or key == "name" and schema >= 2:
            if test(raw.get(key)):
                setattr(d, key, raw[key])
            else:
                errs.append(f"{where}.{key}: must be {what}, got {raw.get(key)!r}")
    v = raw.get("version", "1.0")
    if not (_text(v) or _number(v)):
        errs.append(f'{where}.version: must be a string such as "1.0", got {v!r}')
    d.version = str(v).strip()
    rm = raw.get("remove") or {}
    _check(rm, {t: ["str"] for t in TABLES}, f"{where}.remove", errs)
    d.remove = {t: list(ns) for t, ns in rm.items() if t in TABLES and isinstance(ns, list)} if isinstance(rm, dict) else {}
    return d


def parse(raw, where: str = "pack") -> PackDecl:
    """A ``pack:`` mapping (the header and the sections inline) -> :class:`PackDecl`; ``None`` is schema 1 (nothing declared).
    Raises :class:`PackError` with every bad field. The files a pack names are read by ``pack_files.read``, not here."""
    if raw is None:
        return PackDecl(where=where)
    if not isinstance(raw, dict):
        raise PackError([f"{where}: must be a mapping, got {type(raw).__name__}"])
    errs: list = [f"{where}: unknown key {k!r} (allowed: {', '.join(HEADER + SECTIONS)})" if isinstance(k, str) else
                  f"{where}: key {k!r}: {KEY_HINT}" for k in raw if k not in HEADER + SECTIONS]
    d = _header(raw, where, errs)
    flat = {s: raw[s] for s in SECTIONS if s not in ("recovery", "fleet") and raw.get(s) is not None}
    for sec, keys in (("recovery", ("codes", "policies", "by_action", "by_kind", "failure_patterns")), ("fleet", ("kinds",))):
        sub = raw.get(sec) if raw.get(sec) is not None else {}
        _check(sub, {k: "any" for k in keys}, f"{where}.{sec}", errs)
        flat.update({f"{sec}.{k}": v for k, v in sub.items() if k in keys and v is not None} if isinstance(sub, dict) else {})
    d.failure_patterns = flat.pop("recovery.failure_patterns", [])
    _check(d.failure_patterns, [{"*": "any"}], f"{where}.recovery.failure_patterns", errs)
    for table, entries in flat.items():
        if not isinstance(entries, dict):
            errs.append(f"{where}.{table}: must be a mapping of names, got {type(entries).__name__}")
            continue
        for n, e in entries.items():
            w = f"{where}.{table}.{n}"
            if not isinstance(n, str):
                errs.append(f"{where}.{table}: key {n!r}: {KEY_HINT}")
                continue
            _check(e, TABLES[table], w, errs)
            _entry_rules(table, e, w, errs)
            for x in (e if isinstance(e, list) else [e]) if d.label() is None else ():
                if isinstance(x, dict) and d.label(x) is None:
                    errs.append(f'{w}: needs `source: "<where the fact comes from>"` or `assumed: true` (or give the pack one)')
        d.tables[table] = {n: e for n, e in entries.items() if isinstance(n, str)}
    if d.schema < 2 and (d.tables or d.remove or d.callables):
        errs.append(f"{where}: sections, `remove` and `callables` need `schema: 2`")
    if errs:
        raise PackError(errs)
    return d


__all__ = ["SUPPORTED", "SECTIONS", "TABLES", "DEFAULTS", "LABELS", "PRED_RE", "PackDecl", "PackError", "parse", "default",
           "pred_parts"]
