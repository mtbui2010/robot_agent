"""The option registry: ``register(spec, factory)``, the built-in list, entry points (docs/options.md).

Built-in options are the explicit list in ``robot_agent/options/__init__.py`` (so their order is reviewable); third-party
ones are the entry point group ``robot_agent.options`` (a module or a callable that calls ``register``). The engine reaches
them only through here: this is the one place that loads the option layer, by name, never by a static import.
"""

from __future__ import annotations

import importlib
import logging
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .spec import HOOKS, Option, OptionSpec

BUILTIN = "robot_agent.options"          # the option layer; loaded by name so that the engine has no import edge to it
ENTRY_POINTS = "robot_agent.options"
Factory = Callable[..., Option]
log = logging.getLogger(__name__)


class Registry:
    def __init__(self, builtin: Optional[str] = BUILTIN, entry_points: Optional[str] = ENTRY_POINTS) -> None:
        self._items: Dict[str, Tuple[OptionSpec, Factory]] = {}
        self._builtin, self._eps, self._loaded = builtin, entry_points, False

    def register(self, spec: OptionSpec, factory: Factory) -> OptionSpec:
        if spec.name in self._items:
            raise ValueError(f"option {spec.name!r} is registered twice")
        clash = {n: o for o, (s, _f) in self._items.items() for n in s.flat_names()}
        for n in spec.flat_names():
            if n in clash:
                raise ValueError(f"option {spec.name!r}: key name {n!r} is already used by option {clash[n]!r}")
        for h in (*spec.hooks, *spec.services):
            if not callable(getattr(factory, h, None)):
                raise ValueError(f"option {spec.name!r}: declares hook / service {h!r} but {factory!r} does not define it")
        self._items[spec.name] = (spec, factory)
        return spec

    def discover(self) -> "Registry":
        """Load the built-in option modules and the entry points, once. A broken third-party option is logged and skipped."""
        if self._loaded:
            return self
        self._loaded = True
        if self._builtin:
            importlib.import_module(self._builtin)
        if self._eps:
            try:
                from importlib.metadata import entry_points
                for ep in entry_points(group=self._eps):
                    try:
                        obj = ep.load()
                        if callable(obj) and not isinstance(obj, type):
                            obj(self)
                    except Exception as e:                        # noqa: BLE001 - a plug-in must not break the robot
                        log.warning("option entry point %s failed: %s", ep.name, e)
            except Exception as e:                                # noqa: BLE001
                log.warning("option entry points unavailable: %s", e)
        return self

    def specs(self) -> List[OptionSpec]:
        """The registered specs in dispatch order (ties: registration order)."""
        self.discover()
        return sorted((s for s, _f in self._items.values()), key=lambda s: s.order)

    def factory(self, name: str) -> Factory:
        self.discover()
        return self._items[name][1]

    def get(self, name: str) -> Optional[OptionSpec]:
        self.discover()
        item = self._items.get(name)
        return item[0] if item else None

    def all_keys(self) -> frozenset:
        """Every run-option name the registered options accept (legacy names and ``<option>.<key>``)."""
        return frozenset(n for s in self.specs() for n in s.flat_names())

    def hooks(self) -> Iterable[str]:
        return HOOKS


REGISTRY = Registry()
register = REGISTRY.register
