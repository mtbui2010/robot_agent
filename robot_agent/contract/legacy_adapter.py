"""RobotAdapter on top of an existing ``<pkg>.configs.grace_namemap`` module.

The shim for step 4 of the roadmap: robots keep their namemap (``SKILL_MAP``,
``to_loc`` / ``to_obj``, ``build_params``, ``observe``, ``vlm_hook`` ...), the
core talks to a :class:`RobotAdapter`. Binding follows what the loops do today
(``ActionMapper.to_skill`` + ``ClosedLoop._build_params``), so wrapping changes
nothing for an existing robot.

:func:`load_adapter` is the one entry point: ``<pkg>.configs.adapter`` when a
robot ships one, else manifest.yaml + this shim.
"""

from __future__ import annotations

import importlib
from importlib.util import find_spec
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from .adapter import RobotAdapter, Unbindable
from .manifest import Manifest
from .types import Geometry, Messages, Observation, SkillSpec

# same split as ActionMapper: these name a place, the rest an object
_LOCATION_ACTIONS = {"MoveTo", "Place", "PutIn"}


def _step_get(step, key: str) -> str:
    v = step.get(key) if isinstance(step, dict) else getattr(step, key, None)
    return (v or "").strip() if isinstance(v, str) else (v or "")


class NamemapAdapter:
    """A :class:`RobotAdapter` made of a namemap module and (optionally) a
    manifest. Without a manifest one is synthesised from ``SKILL_MAP`` so
    ``adapter.manifest`` is always there."""

    def __init__(self, module, manifest: Optional[Manifest] = None):
        if isinstance(module, str):
            module = importlib.import_module(module)
        self.namemap: ModuleType = module
        self.manifest: Manifest = manifest if manifest is not None else self._manifest_from_namemap(module)
        self.geometry: Geometry = Geometry.from_dict(self.manifest.geometry)
        self.i18n: Messages = Messages(language=self.manifest.language or "en",
                                       phrases=dict(getattr(module, "PHRASES", {}) or {}))
        ov = getattr(module, "RECOVERY_OVERRIDES", None)
        if ov:
            self.recovery_overrides = ov

    @staticmethod
    def _manifest_from_namemap(module) -> Manifest:
        name = (getattr(module, "__name__", "") or "robot").split(".")[0]
        skills, seen = [], set()
        for action, skill in (getattr(module, "SKILL_MAP", {}) or {}).items():
            if skill in seen:           # PutIn -> place: one spec per skill, the first action wins
                continue
            seen.add(skill)
            skills.append(SkillSpec(name=skill, action=action))
        return Manifest(robot=name, skills=skills, source=f"{getattr(module, '__name__', '?')} (no manifest)")

    # ── action sets (what ActionMapper reads off the namemap) ─────────────
    @property
    def skill_map(self) -> dict:
        """Abstract action -> skill name: the manifest's, then the namemap's
        for actions the manifest does not list (e.g. PutIn -> place)."""
        out = dict(getattr(self.namemap, "SKILL_MAP", {}) or {})
        for action in self.manifest.actions:
            out[action] = self.manifest.skill_for(action).name
        return out

    @property
    def noop_actions(self) -> set:
        return set(getattr(self.namemap, "NOOP_ACTIONS", set()) or set())

    @property
    def supported_actions(self) -> set:
        sup = getattr(self.namemap, "SUPPORTED_ACTIONS", None)
        base = set(sup) if sup is not None else set(self.skill_map)
        return base | set(self.skill_map) | self.noop_actions

    @property
    def vlm_actions(self) -> set:
        return set(getattr(self.namemap, "VLM_ACTIONS", set()) or set())

    # ── RobotAdapter ─────────────────────────────────────────────────────
    def bind(self, step, world: Any = None) -> Optional[tuple]:
        action = _step_get(step, "action")
        obj = _step_get(step, "object")
        if not action:
            raise Unbindable("step has no action")
        if action in self.noop_actions:
            return None
        skill = self.skill_map.get(action)
        if not skill or action not in self.supported_actions:
            raise Unbindable(f"no skill for action {action!r} on object {obj!r} on robot {self.manifest.robot!r}")
        if action == "Pick" and not obj:
            found = getattr(world, "found", None) if world is not None else None
            if not found:
                raise Unbindable("Pick requested but nothing has been found")
            obj = found
        if not obj:
            raise Unbindable(f"action {action!r} requires an object")

        nm = self.namemap
        to = getattr(nm, "to_loc" if action in _LOCATION_ACTIONS else "to_obj", None)
        params = {"inputs": to(obj) if callable(to) else obj}
        if hasattr(nm, "build_params"):
            try:
                extra = nm.build_params(action, obj, world)
                if extra:
                    params.update(extra)
            except Exception:
                pass
        return skill, params

    def observe(self, node: Any = None) -> Observation:
        text, visible = "", []
        fn = getattr(self.namemap, "observe", None)
        if callable(fn):
            try:
                text, visible = fn(node)
            except Exception:
                pass
        return Observation(text=text or "", visible=list(visible or []))

    def reconcile(self, world: Any, node: Any = None) -> Any:
        fn = getattr(self.namemap, "reconcile_world", None)
        try:
            if callable(fn):
                fn(node, world)
            else:
                # the core's generic ENV + base-pose reconcile (lazy: needs the runtime)
                from robot_agent.core.planning.loop import reconcile_world
                reconcile_world(node, world)
        except Exception:
            pass
        return world

    def health(self, node: Any = None) -> dict:
        fn = getattr(self.namemap, "health", None)
        if callable(fn):
            try:
                h = fn(node)
                return dict(h) if isinstance(h, dict) else {"ok": bool(h)}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": None, "note": "the robot reports no health (namemap has no health())"}

    # ── pass-throughs the loops use today ───────────────────────────────
    def to_loc(self, name: str) -> str:
        """The robot's name for a place (namemap ``to_loc``; else unchanged)."""
        fn = getattr(self.namemap, "to_loc", None)
        return fn(name) if callable(fn) else name

    def to_obj(self, name: str) -> str:
        """The robot's name for an object (namemap ``to_obj``; else unchanged)."""
        fn = getattr(self.namemap, "to_obj", None)
        return fn(name) if callable(fn) else name

    def vlm_hook(self, step: dict, node: Any = None):
        fn = getattr(self.namemap, "vlm_hook", None)
        if not callable(fn):
            return True, None, "vlm not configured (no vlm_hook)", 0.0
        return fn(step, node)

    def apply_skill_effect(self, world, skill, params=None, result=None, node=None) -> None:
        fn = getattr(self.namemap, "apply_skill_effect", None)
        if callable(fn):
            fn(world, skill, params, result, node)

    def __repr__(self) -> str:
        return f"NamemapAdapter({getattr(self.namemap, '__name__', '?')}, robot={self.manifest.robot!r})"


# ─────────────────────────────────────────────────────────────────────────────
# loading
# ─────────────────────────────────────────────────────────────────────────────
def _pkg_dir(robot_pkg: str) -> Optional[Path]:
    spec = find_spec(robot_pkg)           # top-level: located, not executed
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def _has_module(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _from_adapter_module(mod) -> RobotAdapter:
    """``<pkg>.configs.adapter`` may expose ``ADAPTER`` (an instance),
    ``make_adapter()`` / ``get_adapter()`` (a factory) or a class ``Adapter``."""
    for attr in ("ADAPTER", "adapter"):
        obj = getattr(mod, attr, None)
        if obj is not None and not isinstance(obj, ModuleType) and not callable(obj):
            return obj
    for attr in ("make_adapter", "get_adapter", "Adapter"):
        fn = getattr(mod, attr, None)
        if callable(fn):
            return fn()
    raise ImportError(f"{mod.__name__} defines none of ADAPTER / make_adapter() / get_adapter() / Adapter")


def load_adapter(robot_pkg: str) -> RobotAdapter:
    """The robot's adapter: ``<pkg>.configs.adapter`` if present, else
    ``configs/manifest.yaml`` (when there is one) wrapped around
    ``<pkg>.configs.grace_namemap``."""
    if _has_module(f"{robot_pkg}.configs.adapter"):
        return _from_adapter_module(importlib.import_module(f"{robot_pkg}.configs.adapter"))
    manifest = None
    d = _pkg_dir(robot_pkg)
    if d is not None and (d / "configs" / "manifest.yaml").is_file():
        manifest = Manifest.from_yaml(d / "configs" / "manifest.yaml")
    if not _has_module(f"{robot_pkg}.configs.grace_namemap"):
        raise ImportError(f"{robot_pkg}: no configs/adapter.py and no configs/grace_namemap.py")
    return NamemapAdapter(importlib.import_module(f"{robot_pkg}.configs.grace_namemap"), manifest)
