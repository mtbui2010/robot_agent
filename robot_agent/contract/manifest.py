"""manifest.yaml -> :class:`Manifest`: what a robot can do, declared as data.

Format (``<pkg>/configs/manifest.yaml``)::

    robot: my_robot
    version: "1.0"
    language: en
    capabilities: {base: true, arms: 1, hands: [right], lift: true, head: true, drawer: true}
    geometry: {arrive_tol_m: 0.3, reach_m: 0.78}
    planner: {seed_memory: ../planner/seed_memory.jsonl}   # optional, paths relative to this file
    #        notes: ["short line for the direct planner's prompt", ...]   # optional
    skills:
      - name: move
        action: MoveTo
        params: {type: object, properties: {inputs: {type: string}}, required: [inputs]}
        pre: []
        hold: []
        effects: ["arrived(?inputs)"]
        verify: [{kind: symbolic}]
        interruptible: true
        errors: [unreachable, blocked, timeout]
        version: "1.0"

Loading validates everything and raises one :class:`ManifestError` listing
every bad field by path (``skills[2].errors[1]: ...``). Keys the contract does
not know are kept (``Manifest.extra`` / ``SkillSpec.extra``), not rejected, so
a robot can carry notes; ``check_manifest`` reports them as warnings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .types import VERIFY_KINDS, FailureCode, Geometry, SkillSpec, VerifySpec

_TOP_KEYS = {"robot", "version", "language", "capabilities", "geometry", "planner", "skills", "limits", "safety"}
# keys of the optional `planner:` section (planner data the robot ships; docs/env.md):
#   seed_memory, notes; llm_budget (the run's cap on core LLM calls), translate_model / translate_url (the model
#   that translates what the robot says into the user's language, default the planner's), translate_budget (its
#   own cap per run)
PLANNER_KEYS = {"seed_memory", "notes", "llm_budget", "translate_model", "translate_url", "translate_budget"}
# keys of the optional `limits:` section (what the body can carry; checked against the ENV object estimates)
LIMIT_KEYS = {"payload_kg", "max_object_size_m"}
# planner.notes: short lines for the direct planner's prompt (docs/direct_prompt.md)
NOTE_MAX_CHARS = 300
_SKILL_KEYS = {"name", "action", "params", "pre", "hold", "effects", "verify", "interruptible", "errors", "version"}
_ACTION_RE = re.compile(r"[A-Z][A-Za-z0-9]*$")
# at(?obj) / holding(none) / not opened(?c) / !free(table) / hand_empty() / hand_empty / not found(*)
_PRED_RE = re.compile(r"(not\s+|!)?[A-Za-z_]\w*(\s*\([^()]*\))?$")
# numeric geometry fields (metres); any other geometry key is free-form robot data
_GEOM_NUMERIC = {"arrive_tol_m", "reach_m", "reach_far_m"}
# extension key: further abstract actions a skill also realises (PutIn on place)
ALIAS_ACTIONS_KEY = "alias_actions"
_JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}


class ManifestError(ValueError):
    """A manifest that does not follow the contract. ``errors`` holds one
    ``'<field path>: <problem>'`` line per bad field."""

    def __init__(self, errors: list, source: Optional[str] = None):
        self.errors = list(errors)
        self.source = source
        where = f"{source}: " if source else ""
        super().__init__(where + ("invalid manifest:\n  " + "\n  ".join(self.errors)
                                  if len(self.errors) > 1 else f"invalid manifest: {self.errors[0]}"))


@dataclass
class Manifest:
    robot: str
    version: str = "1.0"
    capabilities: dict = field(default_factory=dict)
    language: str = "en"
    geometry: dict = field(default_factory=dict)
    skills: list = field(default_factory=list)        # list[SkillSpec]
    # optional planner data: {seed_memory: <path to a JSONL seed pool, relative to the manifest>}
    planner: dict = field(default_factory=dict)
    # optional body limits {payload_kg, max_object_size_m} and safety add-ons {hazards: [...]} (docs/env.md)
    limits: dict = field(default_factory=dict)
    safety: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    source: Optional[str] = None                      # file it was read from

    # ── loading ──────────────────────────────────────────────────────────
    @classmethod
    def from_yaml(cls, path) -> "Manifest":
        try:
            import yaml
        except ImportError as e:            # pragma: no cover - pyyaml ships with every env we run in
            raise ImportError("reading manifest.yaml needs pyyaml (pip install pyyaml)") from e
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ManifestError([f"not valid YAML: {e}"], str(path)) from e
        return cls.from_dict(data, source=str(path))

    @classmethod
    def from_dict(cls, d, source: Optional[str] = None) -> "Manifest":
        errs: list = []
        if not isinstance(d, dict):
            raise ManifestError([f"top level must be a mapping, got {type(d).__name__}"], source)

        robot = d.get("robot")
        if not isinstance(robot, str) or not robot.strip():
            errs.append("robot: required, a non-empty string")
        version = _version(d.get("version", "1.0"), "version", errs)
        language = d.get("language", "en")
        if not isinstance(language, str) or not language.strip():
            errs.append("language: must be a language code such as 'en' or 'ko'")
        caps = d.get("capabilities") or {}
        if not isinstance(caps, dict):
            errs.append(f"capabilities: must be a mapping, got {type(caps).__name__}")
            caps = {}
        else:
            _check_caps(caps, errs)
        geom = d.get("geometry") or {}
        if not isinstance(geom, dict):
            errs.append(f"geometry: must be a mapping, got {type(geom).__name__}")
            geom = {}
        else:
            for k, v in geom.items():
                if k == "workspace":
                    if v is not None and not isinstance(v, dict):
                        errs.append("geometry.workspace: must be a mapping")
                elif (k in _GEOM_NUMERIC or k.endswith("_m")) and not _is_number(v):
                    errs.append(f"geometry.{k}: must be a number (metres), got {v!r}")

        planner = _planner(d.get("planner"), errs)
        limits = _limits(d.get("limits"), errs)
        safety = _safety(d.get("safety"), errs)

        raw_skills = d.get("skills")
        if raw_skills is None:
            raw_skills = []
        if not isinstance(raw_skills, list):
            errs.append(f"skills: must be a list, got {type(raw_skills).__name__}")
            raw_skills = []
        skills, seen = [], {}
        for i, s in enumerate(raw_skills):
            spec = _skill(s, f"skills[{i}]", errs)
            if spec is None:
                continue
            if spec.name in seen:
                errs.append(f"skills[{i}].name: {spec.name!r} is already declared at skills[{seen[spec.name]}]")
            seen.setdefault(spec.name, i)
            skills.append(spec)

        if errs:
            raise ManifestError(errs, source)
        return cls(robot=robot.strip(), version=version, capabilities=dict(caps), language=language.strip(),
                   geometry=dict(geom), skills=skills, planner=planner, limits=limits, safety=safety,
                   extra={k: v for k, v in d.items() if k not in _TOP_KEYS}, source=source)

    def to_dict(self) -> dict:
        d = {"robot": self.robot, "version": self.version, "language": self.language,
             "capabilities": dict(self.capabilities), "geometry": dict(self.geometry),
             "skills": [s.to_dict() for s in self.skills]}
        if self.planner:
            d["planner"] = dict(self.planner)
        if self.limits:
            d["limits"] = dict(self.limits)
        if self.safety:
            d["safety"] = dict(self.safety)
        d.update(self.extra)
        return d

    # ── lookups ──────────────────────────────────────────────────────────
    def skill_for(self, action: str) -> Optional[SkillSpec]:
        """The (first) skill realising abstract ``action`` — exact, then
        case-insensitive, then a skill listing it in ``alias_actions`` — or
        None when this robot has none."""
        for s in self.skills:
            if s.action == action:
                return s
        a = (action or "").lower()
        for s in self.skills:
            if s.action.lower() == a:
                return s
        for s in self.skills:
            if any(str(x).lower() == a for x in s.extra.get(ALIAS_ACTIONS_KEY) or []):
                return s
        return None

    def skill(self, name: str) -> Optional[SkillSpec]:
        return next((s for s in self.skills if s.name == name), None)

    @property
    def actions(self) -> set:
        """Every abstract action some skill realises (``alias_actions`` included)."""
        out = {s.action for s in self.skills}
        for s in self.skills:
            out |= {str(x) for x in s.extra.get(ALIAS_ACTIONS_KEY) or []}
        return out

    @property
    def description(self) -> Optional[str]:
        """The optional top-level ``description:`` (a sentence about the robot's body, used by
        the planner prompts instead of the one made from ``capabilities``), None when absent."""
        d = self.extra.get("description")
        return d.strip() if isinstance(d, str) and d.strip() else None

    @property
    def skill_names(self) -> list:
        return [s.name for s in self.skills]

    # ── capabilities ─────────────────────────────────────────────────────
    def has(self, capability: str) -> bool:
        """``has('base')``, ``has('drawer')``, ``has('arms')`` (at least one),
        ``has('hands')`` (at least one)."""
        v = self.capabilities.get(capability)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v > 0
        if isinstance(v, (list, tuple, set, dict, str)):
            return len(v) > 0
        return bool(v)

    @property
    def hands(self) -> list:
        """Hand names, e.g. ``['right']``; ``hands: 2`` -> ``['left', 'right']``."""
        v = self.capabilities.get("hands")
        if isinstance(v, (list, tuple)):
            return [str(h) for h in v]
        if isinstance(v, bool) or v is None:
            return ["hand"] if v else []
        if isinstance(v, int):
            return ["hand"] if v == 1 else ["left", "right"][:v] if v == 2 else [f"hand{i}" for i in range(v)]
        return [str(v)]

    @property
    def arms(self) -> int:
        v = self.capabilities.get("arms", self.capabilities.get("arm", 0))
        if isinstance(v, bool):
            return int(v)
        return int(v) if isinstance(v, (int, float)) else 0

    def geometry_obj(self) -> Geometry:
        return Geometry.from_dict(self.geometry)

    # ── planner data ─────────────────────────────────────────────────────
    def planner_path(self, key: str) -> Optional[Path]:
        """``planner.<key>`` as a path: relative paths are relative to the manifest
        file (the working directory for a manifest not read from a file). None when
        the robot declares none."""
        v = (self.planner or {}).get(key)
        if not v:
            return None
        p = Path(str(v)).expanduser()
        if not p.is_absolute() and self.source:
            p = (Path(self.source).resolve().parent / p).resolve()
        return p

    @property
    def seed_memory(self) -> Optional[Path]:
        """The robot's curated seed pool for the planner's retrieval memory (None = none)."""
        return self.planner_path("seed_memory")


def load_manifest(path_or_pkg) -> Manifest:
    """A manifest from a file, a package directory (``<dir>/configs/manifest.yaml``)
    or an importable package name, without importing the package."""
    p = Path(str(path_or_pkg))
    if p.is_file():
        return Manifest.from_yaml(p)
    if p.is_dir():
        for cand in (p / "configs" / "manifest.yaml", p / "manifest.yaml", p / p.name / "configs" / "manifest.yaml"):
            if cand.is_file():
                return Manifest.from_yaml(cand)
        raise FileNotFoundError(f"no configs/manifest.yaml under {p}")
    from importlib.util import find_spec
    spec = find_spec(str(path_or_pkg))
    if spec is None or not spec.submodule_search_locations:
        raise FileNotFoundError(f"{path_or_pkg!r} is neither a manifest file, a directory nor a package")
    pkg_dir = Path(list(spec.submodule_search_locations)[0])
    f = pkg_dir / "configs" / "manifest.yaml"
    if not f.is_file():
        raise FileNotFoundError(f"{path_or_pkg}: no {f}")
    return Manifest.from_yaml(f)


# ─────────────────────────────────────────────────────────────────────────────
# validation helpers
# ─────────────────────────────────────────────────────────────────────────────
def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _version(v, where, errs) -> str:
    if isinstance(v, str) and v.strip():
        return v.strip()
    if _is_number(v):          # version: 1.0 unquoted
        return str(v)
    errs.append(f"{where}: must be a string such as \"1.0\", got {v!r}")
    return "1.0"


def _planner(v, errs) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict):
        errs.append(f"planner: must be a mapping, got {type(v).__name__}")
        return {}
    sm = v.get("seed_memory")
    if sm is not None and (not isinstance(sm, str) or not sm.strip()):
        errs.append("planner.seed_memory: must be a file path (relative to the manifest)")
    for k in ("llm_budget", "translate_budget"):
        x = v.get(k)
        if x is not None and not (isinstance(x, int) and not isinstance(x, bool) and x >= 0):
            errs.append(f"planner.{k}: must be a count of LLM calls (0 = no cap), got {x!r}")
    for k in ("translate_model", "translate_url"):
        x = v.get(k)
        if x is not None and (not isinstance(x, str) or not x.strip()):
            errs.append(f"planner.{k}: must be a non-empty string")
    notes = v.get("notes")
    if notes is not None:
        if not isinstance(notes, list):
            errs.append(f"planner.notes: must be a list of strings, got {type(notes).__name__}")
        else:
            for i, n in enumerate(notes):
                if not isinstance(n, str) or not n.strip():
                    errs.append(f"planner.notes[{i}]: must be a non-empty string")
                elif len(n) > NOTE_MAX_CHARS:
                    errs.append(f"planner.notes[{i}]: longer than {NOTE_MAX_CHARS} characters")
    return dict(v)


def _limits(v, errs) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict):
        errs.append(f"limits: must be a mapping, got {type(v).__name__}")
        return {}
    for k, x in v.items():
        if k in LIMIT_KEYS and not (_is_number(x) and x > 0):
            errs.append(f"limits.{k}: must be a positive number, got {x!r}")
    return dict(v)


def _safety(v, errs) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict):
        errs.append(f"safety: must be a mapping, got {type(v).__name__}")
        return {}
    hz = v.get("hazards")
    if hz is not None and not (isinstance(hz, list) and all(isinstance(h, dict) for h in hz)):
        errs.append("safety.hazards: must be a list of mappings {objects, places, say}")
    return dict(v)


def _check_caps(caps: dict, errs: list) -> None:
    for k in ("base", "lift", "head", "drawer"):
        if k in caps and not isinstance(caps[k], bool):
            errs.append(f"capabilities.{k}: must be true / false, got {caps[k]!r}")
    for k in ("arms", "arm"):
        if k in caps and not (isinstance(caps[k], int) and caps[k] >= 0):
            errs.append(f"capabilities.{k}: must be a count (0, 1, 2 ...), got {caps[k]!r}")
    if "hands" in caps:
        h = caps["hands"]
        ok = (isinstance(h, list) and all(isinstance(x, str) and x for x in h)) or \
             (isinstance(h, int) and h >= 0)
        if not ok:
            errs.append(f"capabilities.hands: must be a list of hand names such as [right], got {h!r}")


def _str_list(v, where, errs, pred=False) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        errs.append(f"{where}: must be a list of strings, got {type(v).__name__}")
        return []
    out = []
    for j, x in enumerate(v):
        if not isinstance(x, str) or not x.strip():
            errs.append(f"{where}[{j}]: must be a non-empty string, got {x!r}")
        elif pred and not _PRED_RE.match(x.strip()):
            errs.append(f"{where}[{j}]: {x!r} is not a predicate like 'at(?obj)' or 'not opened(?c)'")
        else:
            out.append(x.strip())
    return out


def _params(v, where, errs) -> dict:
    if v is None:
        return {}
    if not isinstance(v, dict):
        errs.append(f"{where}: must be a JSON schema mapping, got {type(v).__name__}")
        return {}
    if "type" in v and v["type"] != "object":
        errs.append(f"{where}.type: must be 'object' (skill kwargs), got {v['type']!r}")
    props = v.get("properties", {})
    if props is None:
        props = {}
    if not isinstance(props, dict):
        errs.append(f"{where}.properties: must be a mapping of param name -> schema")
        props = {}
    for k, sch in props.items():
        if sch is None:
            continue
        if not isinstance(sch, dict):
            errs.append(f"{where}.properties.{k}: must be a schema mapping such as {{type: string}}")
            continue
        t = sch.get("type")
        types = t if isinstance(t, list) else [t] if t is not None else []
        bad = [x for x in types if x not in _JSON_TYPES]
        if bad:
            errs.append(f"{where}.properties.{k}.type: {bad[0]!r} is not a JSON schema type "
                        f"({', '.join(sorted(_JSON_TYPES))})")
    req = v.get("required", [])
    if req is None:
        req = []
    if not isinstance(req, list) or not all(isinstance(x, str) for x in req):
        errs.append(f"{where}.required: must be a list of param names")
    else:
        for x in req:
            if x not in props:
                errs.append(f"{where}.required: {x!r} is not in {where}.properties")
    return dict(v)


def _verify(v, where, errs) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        v = [v]
    out = []
    for j, x in enumerate(v):
        w = f"{where}[{j}]"
        if isinstance(x, str):            # shorthand: verify: [symbolic]
            x = {"kind": x}
        if not isinstance(x, dict):
            errs.append(f"{w}: must be a mapping such as {{kind: sensor, skill: <a skill that checks>}}")
            continue
        kind = x.get("kind")
        if kind not in VERIFY_KINDS:
            errs.append(f"{w}.kind: {kind!r} is not one of {', '.join(VERIFY_KINDS)}")
            continue
        unknown = set(x) - {"kind", "skill", "prompt", "expect"}
        if unknown:
            errs.append(f"{w}: unknown key(s) {', '.join(sorted(unknown))} (allowed: kind, skill, prompt, expect)")
        skill, prompt, expect = x.get("skill"), x.get("prompt"), x.get("expect")
        if expect is not None and not isinstance(expect, bool):
            errs.append(f"{w}.expect: must be true / false (what the sensor's isdone must be)")
            continue
        if kind == "sensor" and (not isinstance(skill, str) or not skill.strip()):
            errs.append(f"{w}.skill: a sensor verify names the robot skill that checks the outcome")
            continue
        if skill is not None and not isinstance(skill, str):
            errs.append(f"{w}.skill: must be a skill name")
            continue
        if prompt is not None and not isinstance(prompt, str):
            errs.append(f"{w}.prompt: must be a string")
            continue
        out.append(VerifySpec(kind=kind, skill=skill.strip() if skill else None, prompt=prompt, expect=expect))
    return out


def _errors(v, where, errs) -> list:
    if v is None:
        return []
    if not isinstance(v, list):
        v = [v]
    out = []
    for j, x in enumerate(v):
        c = FailureCode.try_parse(x)
        if c is None:
            errs.append(f"{where}[{j}]: {x!r} is not a FailureCode "
                        f"(valid: {', '.join(c.value for c in FailureCode)})")
        elif c not in out:
            out.append(c)
    return out


def _skill(s, where, errs) -> Optional[SkillSpec]:
    if not isinstance(s, dict):
        errs.append(f"{where}: must be a mapping with at least name and action, got {type(s).__name__}")
        return None
    n0 = len(errs)
    name = s.get("name")
    if not isinstance(name, str) or not name.strip():
        errs.append(f"{where}.name: required, the robot's skill name (as in skills_config.py)")
        name = ""
    where_n = f"{where}" + (f" ({name})" if name else "")
    action = s.get("action")
    if not isinstance(action, str) or not _ACTION_RE.match(action.strip()):
        errs.append(f"{where_n}.action: required, a CamelCase abstract action such as MoveTo / Pick, got {action!r}")
        action = ""
    params = _params(s.get("params"), f"{where_n}.params", errs)
    pre = _str_list(s.get("pre"), f"{where_n}.pre", errs, pred=True)
    hold = _str_list(s.get("hold"), f"{where_n}.hold", errs, pred=True)
    effects = _str_list(s.get("effects"), f"{where_n}.effects", errs, pred=True)
    verify = _verify(s.get("verify"), f"{where_n}.verify", errs)
    interruptible = s.get("interruptible", True)
    if not isinstance(interruptible, bool):
        errs.append(f"{where_n}.interruptible: must be true / false, got {interruptible!r}")
        interruptible = True
    errors = _errors(s.get("errors"), f"{where_n}.errors", errs)
    version = _version(s.get("version", "1.0"), f"{where_n}.version", errs)
    aliases = s.get(ALIAS_ACTIONS_KEY)
    if aliases is not None and (not isinstance(aliases, list)
                                or not all(isinstance(x, str) and _ACTION_RE.match(x) for x in aliases)):
        errs.append(f"{where_n}.{ALIAS_ACTIONS_KEY}: must be a list of CamelCase actions such as [PutIn]")
    if len(errs) > n0:
        return None
    return SkillSpec(name=name.strip(), action=action.strip(), params=params, pre=pre, hold=hold,
                     effects=effects, verify=verify, interruptible=interruptible, errors=errors,
                     version=version, extra={k: v for k, v in s.items() if k not in _SKILL_KEYS})
