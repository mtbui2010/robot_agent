"""The safety profile: what is hazardous for ONE robot, as data (schema in docs/safety.md).

The core ships the engine only (this loader and validator, the class matchers in ``hazards.py`` / ``place_class.py`` /
``receiver_class.py``). Which product, place or person is a hazard depends on the robot and where it works: a cleaning
chemical next to food is a hazard for a household robot and none for a factory robot. A robot pack declares its profile in
the manifest (``safety.profile``: a path relative to ``manifest.yaml`` or an inline mapping). No profile = ``EMPTY`` = no
hazard rule fires (fail open), as for a language with no pack.

``load(obj, base_dir, where)`` returns ``(SafetyProfile, errors)``; ``errors`` are ``'<path>: <problem>'`` lines. A profile
with errors is never returned (``EMPTY`` and the errors). ``SafetyProfile.warnings`` holds what only ``--strict`` rejects.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TOP_KEYS = {"profile", "version", "description", "sources", "checks", "hazard_classes", "place_classes",
            "receiver_classes", "rules", "refusal_marks"}
SOURCE_KEYS = {"url", "opened", "quote", "supports", "status"}
MARK_KINDS = ("safety", "capability")
DEFAULT_CHECKS = ("on", "in")
DEFAULT_RECEIVER_FIELDS = ("age_group", "category")
_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
_LANG_RE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)?$")
_OPT_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


@dataclass(frozen=True)
class Rule:
    """One rule: an object (a hazard class, or regexes) x a place (``places``) or a receiver (``receivers``)."""
    id: str = ""
    hazard: str = ""
    objects: tuple = ()
    places: tuple = ()
    receivers: tuple = ()
    say: str = ""
    enabled_by: str = ""
    provenance: str = "assumed"

    @staticmethod
    def legacy(d: dict) -> "Rule":
        """A rule of the manifest's legacy ``safety.hazards`` list ({objects | hazard, places, say})."""
        return Rule(hazard=str(d.get("hazard") or ""), objects=tuple(str(x) for x in d.get("objects") or ()),
                    places=tuple(str(x) for x in d.get("places") or ()), say=str(d.get("say") or ""))


@dataclass(frozen=True)
class SafetyProfile:
    name: str = ""
    version: int = 0
    sha256: str = ""
    checks: tuple = DEFAULT_CHECKS
    hazard_classes: tuple = ()        # ((cls, (regex, ...)), ...) ordered; sourced words first, then assumed
    place_classes: tuple = ()         # ((cls, (regex, ...)), ...) ordered
    receiver_classes: tuple = ()      # ((cls, (regex, ...), (field, ...)), ...) ordered
    rules: tuple = ()                 # (Rule, ...) in file order
    refusal_marks: tuple = ()         # ((kind, regex), ...)
    stats: dict = field(default_factory=dict, compare=False)
    warnings: tuple = ()

    def place_names(self) -> frozenset:
        return frozenset(c for c, _w in self.place_classes)

    def mark(self, kind: str):
        """The compiled words that make a typed refusal ``kind`` believable after a heard answer, None when the profile has none."""
        src = dict(self.refusal_marks).get(kind)
        return re.compile(src, re.I) if src else None

    def summary(self) -> dict:
        return {"profile": self.name, "version": self.version, "sha256": self.sha256, **self.stats}


EMPTY = SafetyProfile()


# ── loading ──────────────────────────────────────────────────────────────────
def load(obj, base_dir=None, where: str = "safety.profile") -> tuple:
    """``(profile, errors)`` from a mapping, or a path (str / Path; relative to ``base_dir``) of a YAML / JSON file."""
    errs: list = []
    raw, digest = _read(obj, base_dir, where, errs)
    if errs:
        return EMPTY, errs
    prof = _build(raw, digest, where, errs)
    return (EMPTY, errs) if errs else (prof, errs)


def _read(obj, base_dir, where, errs):
    if isinstance(obj, dict):
        return obj, hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    if not isinstance(obj, (str, Path)) or not str(obj).strip():
        errs.append(f"{where}: must be a path or a mapping, got {type(obj).__name__}")
        return None, ""
    p = Path(str(obj))
    if not p.is_absolute():
        if base_dir is None:
            errs.append(f"{where}: relative path {str(obj)!r} needs a manifest file to be relative to")
            return None, ""
        p = Path(base_dir) / p
    if not p.is_file():
        errs.append(f"{where}: no such file {p}")
        return None, ""
    data = p.read_bytes()
    try:
        import yaml
        raw = yaml.safe_load(data.decode("utf-8"))
    except Exception as e:                                    # yaml.YAMLError, UnicodeDecodeError
        errs.append(f"{where}: {p.name} is not valid YAML / JSON: {e}")
        return None, ""
    if not isinstance(raw, dict):
        errs.append(f"{where}: {p.name} must hold a mapping at top level")
        return None, ""
    return raw, hashlib.sha256(data).hexdigest()


def _build(raw: dict, digest: str, where: str, errs: list) -> SafetyProfile:
    _keys(raw, TOP_KEYS, where, errs)
    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        errs.append(f"{where}.version: must be an integer, got {version!r}")
    name = raw.get("profile", "")
    if not isinstance(name, str) or not name.strip():
        errs.append(f"{where}.profile: required, a non-empty name")
    warns: list = []
    sources = _sources(raw.get("sources"), where, errs, warns)
    count = {"sourced": 0, "assumed": 0}
    hz = _hazards(raw.get("hazard_classes"), sources, where, errs, count)
    pl = _places(raw.get("place_classes"), sources, where, errs, count)
    rc = _receivers(raw.get("receiver_classes"), sources, where, errs, count)
    rules = _rules(raw.get("rules"), {c for c, _w in hz}, {c for c, _w in pl}, {c for c, _w, _f in rc}, sources, where, errs)
    marks = _marks(raw.get("refusal_marks"), where, errs)
    checks = raw.get("checks", list(DEFAULT_CHECKS))
    if isinstance(checks, list):                           # YAML 1.1 reads an unquoted `on` as true
        checks = ["on" if c is True else c for c in checks]
    if not (isinstance(checks, list) and checks and all(isinstance(c, str) and c for c in checks)):
        errs.append(f"{where}.checks: must be a non-empty list of goal kinds, got {checks!r}")
        checks = list(DEFAULT_CHECKS)
    stats = {"hazard_classes": len(hz), "place_classes": len(pl), "receiver_classes": len(rc), "rules": len(rules),
             "sourced_words": count["sourced"], "assumed_words": count["assumed"], "sources": len(sources)}
    return SafetyProfile(name=str(name).strip(), version=version if isinstance(version, int) else 0, sha256=digest,
                         checks=tuple(checks), hazard_classes=tuple(hz), place_classes=tuple(pl),
                         receiver_classes=tuple(rc), rules=tuple(rules), refusal_marks=tuple(marks), stats=stats,
                         warnings=tuple(warns))


def _keys(d: dict, ok: set, where: str, errs: list) -> None:
    for k in d:
        if k not in ok:
            errs.append(f"{where}.{k}: unknown key (known: {', '.join(sorted(ok))})")


def _regex(p, where: str, errs: list) -> Optional[str]:
    if not isinstance(p, str) or not p:
        errs.append(f"{where}: must be a non-empty regex string, got {p!r}")
        return None
    try:
        re.compile(p)
    except re.error as e:
        errs.append(f"{where}: bad regex {p!r}: {e}")
        return None
    return p


def _regexes(v, where: str, errs: list) -> list:
    if not isinstance(v, list):
        errs.append(f"{where}: must be a list of regexes, got {type(v).__name__}")
        return []
    return [p for i, x in enumerate(v) for p in [_regex(x, f"{where}[{i}]", errs)] if p]


def _langs(v, where: str, errs: list) -> dict:
    if not isinstance(v, dict) or not v:
        errs.append(f"{where}: must be a mapping language code -> words")
        return {}
    for lg in v:
        if not isinstance(lg, str) or not _LANG_RE.match(lg):
            errs.append(f"{where}.{lg}: not a language code (such as 'en', 'ko', 'xx')")
    return v


def _sources(v, where, errs, warns) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict):
        errs.append(f"{where}.sources: must be a mapping id -> {{url, opened, quote | supports, status}}")
        return {}
    for sid, s in v.items():
        w = f"{where}.sources.{sid}"
        if not isinstance(s, dict):
            errs.append(f"{w}: must be a mapping")
            continue
        _keys(s, SOURCE_KEYS, w, errs)
        if not str(s.get("url") or "").strip():
            errs.append(f"{w}.url: required")
        if str(s.get("status") or "") == "verify":
            warns.append(f"{w}.status: verify (open the page and quote it exactly, or mark the rule assumed)")
    return v


def _provenance(v, sources: dict, where: str, errs: list) -> str:
    """'assumed' | 'sourced' (a ``{source: id}`` mapping)."""
    if v in (None, "assumed"):
        return "assumed"
    if isinstance(v, dict) and set(v) == {"source"} and v["source"] in sources:
        return "sourced"
    errs.append(f"{where}.provenance: must be 'assumed' or {{source: <id of sources>}}, got {v!r}")
    return "assumed"


def _hazards(v, sources, where, errs, count) -> list:
    if v is None:
        return []
    if not isinstance(v, dict):
        errs.append(f"{where}.hazard_classes: must be a mapping class -> {{words, sources}}")
        return []
    out = []
    for cls, d in v.items():
        w = f"{where}.hazard_classes.{cls}"
        if not isinstance(cls, str) or not _ID_RE.match(cls) or not isinstance(d, dict):
            errs.append(f"{w}: a class name (letters, digits, _) and a mapping")
            continue
        _keys(d, {"words", "sources"}, w, errs)
        for sid in d.get("sources") or []:
            if sid not in sources:
                errs.append(f"{w}.sources: unknown source id {sid!r}")
        sourced, assumed = [], []
        for lg, lists in _langs(d.get("words"), w + ".words", errs).items():
            if not isinstance(lists, dict) or set(lists) - {"sourced", "assumed"}:
                errs.append(f"{w}.words.{lg}: must be {{sourced: [regex], assumed: [regex]}}")
                continue
            sourced += _regexes(lists.get("sourced", []), f"{w}.words.{lg}.sourced", errs)
            assumed += _regexes(lists.get("assumed", []), f"{w}.words.{lg}.assumed", errs)
        if sourced and not d.get("sources"):
            errs.append(f"{w}: has sourced words but no `sources`")
        count["sourced"] += len(sourced)
        count["assumed"] += len(assumed)
        out.append((cls, tuple(sourced + assumed)))
    return out


def _words_by_lang(d: dict, w: str, errs: list) -> list:
    out = []
    for lg, lst in _langs(d.get("words"), w + ".words", errs).items():
        out += _regexes(lst, f"{w}.words.{lg}", errs)
    return out


def _places(v, sources, where, errs, count) -> list:
    return [(c, tuple(w)) for c, w, _f in _name_classes(v, "place_classes", sources, where, errs, count, set())]


def _receivers(v, sources, where, errs, count) -> list:
    return _name_classes(v, "receiver_classes", sources, where, errs, count, {"fields"})


def _name_classes(v, key, sources, where, errs, count, extra) -> list:
    if v is None:
        return []
    if not isinstance(v, dict):
        errs.append(f"{where}.{key}: must be a mapping class -> {{words, provenance}}")
        return []
    out = []
    for cls, d in v.items():
        w = f"{where}.{key}.{cls}"
        if not isinstance(cls, str) or not _ID_RE.match(cls) or not isinstance(d, dict):
            errs.append(f"{w}: a class name (letters, digits, _) and a mapping")
            continue
        _keys(d, {"words", "provenance"} | extra, w, errs)
        words = _words_by_lang(d, w, errs)
        count[_provenance(d.get("provenance"), sources, w, errs)] += len(words)
        fields = d.get("fields", list(DEFAULT_RECEIVER_FIELDS))
        if not (isinstance(fields, list) and all(isinstance(f, str) and f for f in fields)):
            errs.append(f"{w}.fields: must be a list of ENV field names")
            fields = list(DEFAULT_RECEIVER_FIELDS)
        out.append((cls, tuple(words), tuple(fields)))
    return out


_RULE_KEYS = {"id", "hazard", "objects", "places", "receivers", "say", "enabled_by", "provenance"}


def _rules(v, hz: set, pl: set, rc: set, sources, where, errs) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        errs.append(f"{where}.rules: must be a list of mappings")
        return []
    out, seen = [], set()
    for i, d in enumerate(v):
        w = f"{where}.rules[{i}]"
        if not isinstance(d, dict):
            errs.append(f"{w}: must be a mapping")
            continue
        _keys(d, _RULE_KEYS, w, errs)
        rid = d.get("id")
        if not isinstance(rid, str) or not _ID_RE.match(rid):
            errs.append(f"{w}.id: required (letters, digits, _)")
        elif rid in seen:
            errs.append(f"{w}.id: {rid!r} is already used")
        seen.add(rid)
        out.append(_rule(d, hz, pl, rc, sources, w, errs))
    return out


def _rule(d: dict, hz: set, pl: set, rc: set, sources: dict, w: str, errs: list) -> Rule:
    hazard, objects = d.get("hazard"), d.get("objects")
    if (hazard is None) == (objects is None):
        errs.append(f"{w}: needs exactly one of `hazard` (a class) and `objects` (regexes)")
    if hazard is not None and hazard not in hz:
        errs.append(f"{w}.hazard: {hazard!r} is not a declared hazard class")
    objs = _regexes(objects, w + ".objects", errs) if objects is not None else []
    places, recv = d.get("places"), d.get("receivers")
    if (places is None) == (recv is None):
        errs.append(f"{w}: needs exactly one of `places` and `receivers`")
    pls = _regexes(places, w + ".places", errs) if places is not None else []
    rcs = list(recv) if isinstance(recv, list) else []
    for r in rcs:
        if r not in rc:
            errs.append(f"{w}.receivers: {r!r} is not a declared receiver class")
    if recv is not None and not isinstance(recv, list):
        errs.append(f"{w}.receivers: must be a list of receiver classes")
    say = d.get("say")
    if not isinstance(say, str) or not say.strip():
        errs.append(f"{w}.say: required, what the robot says (prompt language)")
    eb = d.get("enabled_by", "")
    if eb and not (isinstance(eb, str) and _OPT_RE.match(eb)):
        errs.append(f"{w}.enabled_by: must be an option key such as 'hazard.receivers', got {eb!r}")
    prov = _provenance(d.get("provenance"), sources, w, errs)
    return Rule(id=str(d.get("id") or ""), hazard=str(hazard or ""), objects=tuple(objs), places=tuple(pls),
                receivers=tuple(rcs), say=str(say or ""), enabled_by=str(eb or ""), provenance=prov)


def _marks(v, where, errs) -> list:
    if v is None:
        return []
    if not isinstance(v, dict):
        errs.append(f"{where}.refusal_marks: must be a mapping safety | capability -> language -> regexes")
        return []
    _keys(v, set(MARK_KINDS), f"{where}.refusal_marks", errs)
    out = []
    for kind in MARK_KINDS:
        pats = []
        for lg, x in sorted(_langs(v.get(kind), f"{where}.refusal_marks.{kind}", errs).items()) if kind in v else []:
            pats += _regexes([x] if isinstance(x, str) else x, f"{where}.refusal_marks.{kind}.{lg}", errs)
        if pats:
            out.append((kind, pats[0] if len(pats) == 1 else "|".join(f"(?:{p})" for p in pats)))
    return out


__all__ = ["EMPTY", "Rule", "SafetyProfile", "load"]
