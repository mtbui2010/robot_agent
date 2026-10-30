"""Contract data types shared by the core and every robot package.

Pure stdlib: no fastapi / ROS / embodiplan import here, so a robot package, a
test or a CLI can use these without booting the runtime.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FailureCode(str, Enum):
    """Why a skill failed, the same for every robot. An adapter maps its
    device errors onto these; the recovery policy (``recovery.py``) is keyed
    on them."""
    ABSENT = "absent"                                   # looked, the object is not there
    PERCEPTION_UNAVAILABLE = "perception_unavailable"   # could not look (camera / detector down)
    UNREACHABLE = "unreachable"                         # seen, but out of reach from here
    BLOCKED = "blocked"                                 # path / spot occupied
    GRASP_FAILED = "grasp_failed"                       # gripper closed (or opened) on the wrong thing
    DROPPED = "dropped"                                 # held object lost
    HW_FAULT = "hw_fault"                               # device / server fault
    ESTOP = "estop"                                     # emergency stop pressed
    TIMEOUT = "timeout"                                 # no answer in time
    CANCELLED = "cancelled"                             # the user stopped it
    SOFTWARE = "software"                               # a bug in the skill: retrying cannot help
    UNKNOWN = "unknown"                                 # legacy result with no usable reason

    @classmethod
    def parse(cls, value) -> "FailureCode":
        """``'grasp_failed'`` / ``'GRASP_FAILED'`` / ``FailureCode.GRASP_FAILED``
        -> the member. ValueError for anything else."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            s = value.strip()
            try:
                return cls(s.lower())
            except ValueError:
                pass
            if s.upper() in cls.__members__:
                return cls.__members__[s.upper()]
        raise ValueError(f"{value!r} is not a FailureCode (valid: {', '.join(c.value for c in cls)})")

    @classmethod
    def try_parse(cls, value) -> Optional["FailureCode"]:
        try:
            return cls.parse(value)
        except ValueError:
            return None


# keys a legacy result dict uses for status / reason; everything else is data
_LEGACY_STATUS_KEYS = ("isdone", "ok", "msg", "code", "device")


@dataclass
class SkillResult:
    """What a skill returns. Replaces the ``{'isdone', 'msg'}`` dict; old
    results go through :meth:`from_legacy`, and :meth:`to_legacy` gives the
    dict the current loops read."""
    ok: bool
    code: Optional[FailureCode] = None
    msg: str = ""
    device: Optional[str] = None
    data: dict = field(default_factory=dict)          # pose, conf, image ref ... as evidence

    def __post_init__(self):
        self.ok = bool(self.ok)
        if self.code is not None and not isinstance(self.code, FailureCode):
            self.code = FailureCode.parse(self.code)
        if not self.ok and self.code is None:
            from .failures import classify_legacy_failure
            self.code = classify_legacy_failure(self.msg)
        if self.ok:
            self.code = None
        self.msg = "" if self.msg is None else str(self.msg)

    @classmethod
    def success(cls, msg: str = "", **data) -> "SkillResult":
        return cls(True, None, msg, data=data)

    @classmethod
    def failure(cls, code, msg: str = "", device: Optional[str] = None, **data) -> "SkillResult":
        return cls(False, FailureCode.parse(code), msg, device, data)

    @classmethod
    def from_legacy(cls, raw: Any, strict: bool = False) -> "SkillResult":
        """Any skill return value -> SkillResult.

        * ``SkillResult``                  -> itself
        * ``{'isdone': b, 'msg': s, ...}`` -> ok=b; on failure the code is the
          dict's own ``code`` when it is a FailureCode, else
          :func:`classify_legacy_failure` of the msg
        * ``{'ok': b, 'code': c, ...}``    -> ok=b (switchbot style); a non-
          FailureCode ``code`` (an int) is kept as ``data['legacy_code']`` and
          ``result`` doubles as the msg
        * ``bool``                         -> ok=bool
        * ``str``                          -> ok=True, the text in msg (a reply)
        * ``None`` / a dict with neither flag / anything else -> ok=True, as the
          direct path (``unified_agent``) reads it. ``strict=True`` makes these
          a failure (code UNKNOWN), as the planner's verdict does today.
        """
        if isinstance(raw, cls):
            return raw
        if raw is None:
            return cls(False, FailureCode.UNKNOWN, "skill returned nothing") if strict else cls(True)
        if isinstance(raw, bool):
            return cls(raw, None if raw else FailureCode.UNKNOWN)
        if isinstance(raw, str):
            return cls(True, None, raw)
        if not isinstance(raw, dict):
            return cls(True, data={"result": raw})

        data = {k: v for k, v in raw.items() if k not in _LEGACY_STATUS_KEYS}
        if "isdone" in raw:
            ok = bool(raw.get("isdone"))
        elif "ok" in raw:
            ok = bool(raw.get("ok"))
        else:
            ok = not strict
        msg = raw.get("msg")
        if msg is None and "ok" in raw and isinstance(raw.get("result"), str):
            msg = raw["result"]
        msg = "" if msg is None else str(msg)
        code = FailureCode.try_parse(raw.get("code")) if "code" in raw else None
        if "code" in raw and code is None:
            data["legacy_code"] = raw.get("code")
        device = raw.get("device")
        if not ok and code is None:
            from .failures import classify_legacy_failure
            code = classify_legacy_failure(msg)
        return cls(ok, code if not ok else None, msg, None if device is None else str(device), data)

    def to_legacy(self) -> dict:
        """The ``{'isdone', 'msg', ...}`` dict the current loops and skills
        read; ``code`` / ``device`` ride along for whoever knows them."""
        out = dict(self.data)
        out["isdone"] = self.ok
        out["msg"] = self.msg
        if self.code is not None:
            out["code"] = self.code.value
        if self.device is not None:
            out["device"] = self.device
        return out

    def __bool__(self) -> bool:
        return self.ok


VERIFY_KINDS = ("symbolic", "sensor", "vlm")


@dataclass
class VerifySpec:
    """One post-check of a skill: ``symbolic`` (the declared effects hold in the
    world model, always cheap), ``sensor`` (another skill of the robot that
    reads the outcome) or ``vlm`` (a yes/no question on a camera frame).

    A sensor whose result carries ``pose`` checks the base against the target's
    ENV ``loc`` (``Geometry.arrive_tol_m``); any other sensor passes when its
    ``isdone`` equals ``expect`` (default True: "the hand holds it"; a release
    declares ``expect: false``)."""
    kind: str
    skill: Optional[str] = None
    prompt: Optional[str] = None
    expect: Optional[bool] = None

    @property
    def expected(self) -> bool:
        return True if self.expect is None else bool(self.expect)

    def to_dict(self) -> dict:
        d = {"kind": self.kind}
        if self.skill is not None:
            d["skill"] = self.skill
        if self.prompt is not None:
            d["prompt"] = self.prompt
        if self.expect is not None:
            d["expect"] = self.expect
        return d


@dataclass
class SkillSpec:
    """One skill as the robot's manifest declares it."""
    name: str
    action: str                                       # MoveTo / Find / Pick / Place / Open / Close / Say / Ask ...
    params: dict = field(default_factory=dict)        # JSON schema of the kwargs
    pre: list = field(default_factory=list)           # predicates that must hold before
    hold: list = field(default_factory=list)          # ... while it runs
    effects: list = field(default_factory=list)       # ... after it succeeded
    verify: list = field(default_factory=list)        # list[VerifySpec]
    interruptible: bool = True                        # False: holding / pulling -> finish or release safely
    errors: list = field(default_factory=list)        # list[FailureCode] it may return
    version: str = "1.0"
    extra: dict = field(default_factory=dict)         # keys the contract does not know (description ...)

    @property
    def param_names(self) -> list:
        return list((self.params or {}).get("properties") or {})

    @property
    def required_params(self) -> list:
        return list((self.params or {}).get("required") or [])

    def to_dict(self) -> dict:
        d = {"name": self.name, "action": self.action, "params": self.params, "pre": list(self.pre),
             "hold": list(self.hold), "effects": list(self.effects),
             "verify": [v.to_dict() for v in self.verify], "interruptible": self.interruptible,
             "errors": [e.value for e in self.errors], "version": self.version}
        d.update(self.extra)
        return d


@dataclass
class Geometry:
    """Robot-specific distances (metres) the core used to hard-code."""
    arrive_tol_m: float = 0.3         # MoveTo: farther than this from the ENV loc = not arrived
    reach_m: float = 0.78             # Pick: a target farther to the side is not retried
    reach_far_m: float = 1.0          # Find: beyond this a detection is "over there"
    workspace: Optional[dict] = None  # fixed arm: the reachable box instead of places
    extra: dict = field(default_factory=dict)

    _KEYS = ("arrive_tol_m", "reach_m", "reach_far_m")

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Geometry":
        d = dict(d or {})
        g = cls()
        for k in cls._KEYS:
            if d.get(k) is not None:
                setattr(g, k, float(d.pop(k)))
            else:
                d.pop(k, None)
        g.workspace = d.pop("workspace", None)
        g.extra = d
        return g

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self._KEYS}
        if self.workspace is not None:
            d["workspace"] = self.workspace
        d.update(self.extra)
        return d


@dataclass
class Messages:
    """What the robot says, per language. ``phrases`` = {key: {lang: text}};
    a missing language falls back to English, then to the key itself."""
    language: str = "en"
    phrases: dict = field(default_factory=dict)

    def text(self, key: str, lang: Optional[str] = None, **kw) -> str:
        by_lang = self.phrases.get(key) or {}
        lang = (lang or self.language or "en")[:2]
        s = by_lang.get(lang) or by_lang.get("en") or key
        try:
            return s.format(**kw) if kw else s
        except (KeyError, IndexError, ValueError):
            return s


@dataclass
class Observation:
    """One look at the robot: where it is, what it holds, how its sensors are."""
    text: str = ""                                   # human-readable scene note for the planner
    visible: list = field(default_factory=list)      # labels seen now
    pose: Optional[list] = None                      # base [x, y, yaw]
    arrived: Optional[str] = None                    # ENV place it stands at
    hands: dict = field(default_factory=dict)        # hand -> held object (None = empty)
    opened: set = field(default_factory=set)         # containers seen open
    sensor_health: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
