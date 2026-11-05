"""Domain profiles: the knowledge about WHAT a robot is asked to do, as data a robot pack selects.

A profile is a folder ``robot_agent/domains/<name>/`` of YAML files:

  goals.yaml     goal kinds (the actions each needs, the understand-prompt line) and the read-back phrases
  hazards.yaml   hazard classes and the (hazard x place class) rules the robot refuses
  places.yaml    place classes inferred from a place's names
  lexicon.yaml   word lists (put-in / on-top / hand-over / first person / recipient / tell words, Korean particles)
  refusal.yaml   the words that make a refusal after a heard answer genuine (safety / capability)

A robot pack picks one with the manifest key ``domain: <name>``; without it the profile is ``household``, so a pack
that does not declare one behaves as before. ``household`` is what the core always knew; ``delivery`` is for
base-only robots. Nothing here knows a robot or a benchmark. See docs/domain.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT = "household"
ROOT = Path(__file__).resolve().parent.parent / "domains"
_FILES = ("goals", "hazards", "places", "lexicon", "refusal")


class DomainError(ValueError):
    """An unknown or malformed domain profile."""


@dataclass(frozen=True)
class Domain:
    name: str
    goals: dict = field(default_factory=dict)
    hazards: dict = field(default_factory=dict)
    places: dict = field(default_factory=dict)
    lexicon: dict = field(default_factory=dict)
    refusal: dict = field(default_factory=dict)

    @property
    def manipulation(self) -> tuple:
        return tuple(self.goals.get("manipulation") or ())

    @property
    def template_kinds(self) -> tuple:
        return tuple(self.goals.get("template_kinds") or ())

    @property
    def readback(self) -> dict:
        return self.goals.get("readback") or {}

    @property
    def hazard_classes(self) -> dict:
        return self.hazards.get("hazards") or {}

    @property
    def hazard_rules(self) -> list:
        return list(self.hazards.get("rules") or [])

    @property
    def place_classes(self) -> dict:
        return self.places.get("classes") or {}

    def words(self, table: str) -> Any:
        """The lexicon list ``table`` (PUTIN, TELL, ...); None when this profile does not define it."""
        return self.lexicon.get(table)

    def refusal_mark(self, kind: str):
        """The compiled refusal-words regex of ``kind`` (safety | capability); None when the profile has none."""
        return _marks(self).get(kind)


@lru_cache(maxsize=None)
def _marks_cached(name: str) -> dict:
    d = load(name)
    return {k: re.compile(v, re.I) for k, v in (d.refusal or {}).items() if v}


def _marks(d: Domain) -> dict:
    return _marks_cached(d.name)


def available() -> list:
    return sorted(p.name for p in ROOT.iterdir() if p.is_dir() and (p / "goals.yaml").exists()) if ROOT.exists() else []


@lru_cache(maxsize=None)
def load(name: str = DEFAULT) -> Domain:
    """The profile ``name`` (cached). Raises :class:`DomainError` when it does not exist."""
    name = str(name or DEFAULT)
    folder = ROOT / name
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not folder.is_dir():
        raise DomainError(f"unknown domain {name!r} (available: {', '.join(available()) or 'none'})")
    import yaml
    parts = {}
    for f in _FILES:
        p = folder / f"{f}.yaml"
        try:
            parts[f] = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
        except yaml.YAMLError as e:
            raise DomainError(f"{p}: not valid YAML: {e}") from e
    return Domain(name=name, **parts)


def name_of(robot: Any) -> str:
    """The domain a robot selects: its manifest's ``domain`` (``robot`` may be a RobotView, an adapter, a manifest
    or None), else the default."""
    m = getattr(robot, "manifest", robot)
    n = getattr(m, "domain", "")
    return n if isinstance(n, str) and n else DEFAULT


def of(robot: Any = None) -> Domain:
    """The :class:`Domain` of ``robot`` (see :func:`name_of`)."""
    return load(name_of(robot))


__all__ = ["DEFAULT", "Domain", "DomainError", "available", "load", "name_of", "of"]
