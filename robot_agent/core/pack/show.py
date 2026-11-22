"""Print the pack a robot resolves to: ``python -m robot_agent.core.pack.show <package | folder | manifest.yaml> [--json] [--import]``.

What: the header, the chain, every table with the pack each entry comes from, the manifest data kept as written, and the
problems of the result check. For a schema-1 manifest this is the ``pack:`` section it is equivalent to.
Why: a pack author sees what the robot will use; ``check_manifest`` (contract layer) checks only the section's schema and
cannot resolve the chain (docs/proposals/generalize_core_design.md, "How the core loads and validates it").
It reads and prints; it writes nothing and starts nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from robot_agent.contract.manifest import load_manifest
from robot_agent.contract.pack import PackError

from .loader import Pack, resolve


def open_robot(target: str, do_import: bool = False) -> tuple:
    """(manifest, adapter or None, note) for an importable robot package, a package folder or a manifest file. A path is read
    as a manifest only: the robot's own code is imported for a package NAME, or for a folder with ``do_import`` (its parent
    then goes LAST on ``sys.path``, so it shadows neither this checkout nor an installed package)."""
    from robot_agent.contract import load_adapter
    p = Path(target)
    name = (p.name if p.is_dir() else target) if do_import or not p.exists() else None
    if name and p.is_dir() and str(p.resolve().parent) not in sys.path:
        sys.path.append(str(p.resolve().parent))
    try:
        adapter = load_adapter(name) if name else None
    except Exception as e:                                      # noqa: BLE001 - a robot's own import may need its devices
        return load_manifest(target), None, f"manifest only (the adapter did not load: {type(e).__name__}: {e})"
    return (adapter.manifest, adapter, "adapter and manifest") if adapter is not None else (load_manifest(target), None, "manifest only")


def as_data(pack: Pack) -> dict:
    """The resolved pack as plain data (JSON-ready): header, chain, tables with origins, kept data, problems."""
    return {"pack": pack.header(), "chain": list(pack.chain), "questions_default_kind": pack.questions_default_kind,
            "tables": {t: {n: {"from": pack.origin[t][n], "entry": e} for n, e in entries.items()}
                       for t, entries in pack.tables.items() if entries},
            "failure_patterns": pack.failure_patterns, "kept": pack.kept,
            "problems": [p._asdict() for p in pack.problems]}


def render(pack: Pack, note: str = "") -> str:
    d = as_data(pack)
    out = [f"pack: schema {pack.schema}" + (f", name {pack.name}, version {pack.version}" if pack.schema >= 2 else
                                            f": the standard pack `{pack.extends}` is used with what the manifest declares"),
           f"chain: {' -> '.join(pack.chain)}" + (f"   ({note})" if note else "")]
    for t, entries in d["tables"].items():
        out.append(f"{t}: {len(entries)}")
        out += [f"  {n}  [{x['from']}]  {json.dumps(x['entry'], ensure_ascii=False, default=str)}" for n, x in entries.items()]
    if not d["tables"]:
        out.append("no table has an entry")
    out += [f"kept from the manifest ({k}): {json.dumps(v, ensure_ascii=False, default=str)}" for k, v in pack.kept.items()]
    out += [f"PROBLEM [{p.pack}] {p.path}: {p.text}" for p in pack.problems]
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    args: List[Any] = list(sys.argv[1:] if argv is None else argv)
    targets = [a for a in args if not a.startswith("--")]
    if len(targets) != 1:
        print(__doc__.strip().splitlines()[0])
        return 2
    try:
        manifest, adapter, note = open_robot(targets[0], "--import" in args)
        pack = resolve(manifest, adapter)
    except (PackError, ValueError, FileNotFoundError) as e:
        print(f"ERROR {e}")
        return 1
    print(json.dumps(as_data(pack), indent=1, ensure_ascii=False, default=str) if "--json" in args else render(pack, note))
    return 1 if pack.problems else 0


if __name__ == "__main__":
    sys.exit(main())
