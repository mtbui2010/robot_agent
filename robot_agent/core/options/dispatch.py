"""Hook dispatch: instantiate the enabled options of one loop and call their hooks in ``order`` (docs/options.md).

With no option registered, or none enabled, every call returns its neutral value (None / the value given): the engine
behaves as if the dispatcher were not there.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .config import OptionConfig, resolve
from .registry import REGISTRY, Registry
from .spec import HOOKS, Option, OptionSpec


class Dispatcher:
    def __init__(self, svc, overrides: Mapping, environ: Mapping, planner: Optional[Mapping],
                 registry: Registry = REGISTRY) -> None:
        self.svc = svc
        self.configs: Dict[str, OptionConfig] = {}
        self.active: List[Option] = []
        self._by_hook: Dict[str, List[Option]] = {h: [] for h in HOOKS}
        self._by_service: Dict[str, List[Option]] = {}
        for spec in registry.specs():
            cfg = self.configs[spec.name] = resolve(spec, overrides, environ, planner)
            if not cfg.on:
                continue
            opt = registry.factory(spec.name)(cfg, svc)
            opt.spec = spec
            self.active.append(opt)
            for h in spec.hooks:
                self._by_hook[h].append(opt)
            for s in spec.services:
                self._by_service.setdefault(s, []).append(opt)

    # ── config ───────────────────────────────────────────────────────────
    def mirror(self) -> dict:
        """The resolved keys of EVERY registered option (on or off) by legacy name: what ``AssistLoop.cfg`` shows."""
        return {k: v for c in self.configs.values() for k, v in c.mirror().items()}

    def option(self, name: str) -> Optional[Option]:
        return next((o for o in self.active if o.spec.name == name), None)

    def state(self, run, name: str) -> dict:
        """The option's per-run state (``run.opt[name]``), created on first use."""
        return run.__dict__.setdefault("opt", {}).setdefault(name, {})

    # ── plain hooks ──────────────────────────────────────────────────────
    def observe(self, hook: str, *args) -> None:
        for o in self._by_hook[hook]:
            getattr(o, hook)(*args)

    def transform(self, hook: str, value: Any, *args) -> Any:
        for o in self._by_hook[hook]:
            new = getattr(o, hook)(value, *args)
            value = value if new is None else new
        return value

    def decide(self, hook: str, *args) -> Any:
        for o in self._by_hook[hook]:
            res = getattr(o, hook)(*args)
            if res is not None:
                return res
        return None

    # ── hooks that are generators (they may yield run events) ────────────
    def observe_flow(self, hook: str, *args):
        for o in self._by_hook[hook]:
            yield from getattr(o, hook)(*args)

    def decide_flow(self, hook: str, *args):
        for o in self._by_hook[hook]:
            res = yield from getattr(o, hook)(*args)
            if res is not None:
                return res
        return None

    # ── services ─────────────────────────────────────────────────────────
    def query(self, name: str, *args, default=None):
        """What the first active option that answers ``name`` returns (None counts as no answer); ``default`` if nobody does."""
        for o in self._by_service.get(name, ()):
            res = getattr(o, name)(*args)
            if res is not None:
                return res
        return default

    def provides(self, hook: str) -> bool:
        return bool(self._by_hook[hook])


# No option at all: what a loop built without ``__init__`` (a test double) or before its options are resolved uses.
NULL = Dispatcher(None, {}, {}, None, registry=Registry(builtin=None, entry_points=None))
