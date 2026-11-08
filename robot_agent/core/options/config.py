"""Resolve the keys of an option: run option > environment > the robot's manifest, kept per key (docs/options.md).

The result is what the hand-written ``_v2_cfg_base`` entries used to compute, entry for entry: the same names, the same
precedence, the same parsing, so a run with an old option name behaves as before.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .spec import Key, OptionSpec

_TRUE = ("1", "true", "yes", "on")
_MISSING = object()


def coerce(key: Key, value: Any) -> Any:
    """``value`` as the key's type; a bool is read as the loop always read its flags (``"1" | "true" | "yes" | "on"``)."""
    if key.type == "bool":
        return str(value).lower() in _TRUE
    if key.type == "str":
        return str(value)
    if key.type == "int":
        return int(value)
    if key.type == "float":
        return float(value)
    return value if isinstance(value, dict) else {}


def _env_name(spec: OptionSpec, key: Key) -> str:
    return "ROBOT_AGENT_" + (key.aliases[0] if key.aliases else f"{spec.name}_{key.name}").upper()


def _from_manifest(spec: OptionSpec, key: Key, planner: Optional[Mapping]):
    """``planner.options.<option>.<key>`` first, then the flat legacy ``planner.<alias>``. None / "" = not declared."""
    planner = planner if isinstance(planner, Mapping) else {}
    nested = planner.get("options")
    nested = (nested.get(spec.name) if isinstance(nested, Mapping) else None)
    v = nested.get(key.name) if isinstance(nested, Mapping) else None
    if v is None or v == "":
        for alias in key.aliases:
            v = planner.get(alias)
            if v is not None and v != "":
                break
    return _MISSING if v is None or v == "" else v


def resolve_key(spec: OptionSpec, key: Key, overrides: Mapping, environ: Mapping, planner: Optional[Mapping]):
    names = (*key.aliases, f"{spec.name}.{key.name}")
    for source in key.sources:
        if source == "run":
            hit = next((n for n in names if n in overrides), None)
            if hit is not None:
                return coerce(key, overrides[hit])
        elif source == "env" and _env_name(spec, key) in environ:
            return coerce(key, environ[_env_name(spec, key)])
        elif source == "manifest":
            v = _from_manifest(spec, key, planner)
            if v is not _MISSING:
                return coerce(key, v)
    return coerce(key, key.default)


class OptionConfig(dict):
    """The resolved keys of one option, by key name. ``mirror()``: the same values by legacy name, for ``AssistLoop.cfg``."""

    def __init__(self, spec: OptionSpec, values: Mapping):
        super().__init__(values)
        self.spec = spec

    def mirror(self) -> dict:
        return {(k.aliases[0] if k.aliases else f"{self.spec.name}.{k.name}"): self[k.name] for k in self.spec.keys}

    @property
    def on(self) -> bool:
        return not self.spec.enabled or bool(self[self.spec.enabled])


def resolve(spec: OptionSpec, overrides: Mapping, environ: Mapping, planner: Optional[Mapping]) -> OptionConfig:
    """The option's keys. ``overrides``: the run options; ``environ``: ``os.environ``; ``planner``: the manifest's ``planner`` section."""
    vals = {k.name: resolve_key(spec, k, overrides, environ, planner) for k in spec.keys}
    return OptionConfig(spec, vals)
