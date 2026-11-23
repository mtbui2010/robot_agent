"""``python -m robot_agent.core.options list`` shows the registered options; ``... check MANIFEST.yaml`` reports the
``planner.options`` of a manifest that name no registered option or key (the contract layer cannot know the registry)."""

from __future__ import annotations

import sys

from .registry import REGISTRY


def unknown_in(planner) -> list:
    """Problems of a manifest's ``planner.options`` against the registry: [str]."""
    out = []
    opts = (planner or {}).get("options") or {}
    for name, keys in opts.items():
        spec = REGISTRY.get(name)
        if spec is None:
            out.append(f"planner.options.{name}: no such option (registered: {', '.join(s.name for s in REGISTRY.specs())})")
            continue
        known = {k.name for k in spec.keys}
        out += [f"planner.options.{name}.{k}: no such key (keys: {', '.join(sorted(known))})" for k in (keys or {}) if k not in known]
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["check"] and len(argv) == 2:
        from robot_agent.contract.manifest import load_manifest
        problems = unknown_in(load_manifest(argv[1]).planner)
        print("\n".join(problems) or "planner.options: ok")
        return 1 if problems else 0
    for s in REGISTRY.specs():
        print(f"{s.order:4d}  {s.name:<18} hooks: {', '.join(s.hooks) or '-'}  services: {', '.join(s.services) or '-'}\n"
              f"      keys: {', '.join(f'{k.name} ({k.type}, default {k.default!r}, legacy {k.aliases or None})' for k in s.keys)}\n      {s.doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
