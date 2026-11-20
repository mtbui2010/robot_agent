"""Run one of openpi's own scripts with the project's configs registered (openpi itself is not edited).

    python -m vla_train.openpi_runner CODE_MODULE|- SCRIPT.py [args ...]

CODE_MODULE is a python file with `register()` that adds TrainConfigs to openpi's registry (and may patch the data loader); `-` = none.
SCRIPT is one of $OPENPI_ROOT/scripts (train.py, compute_norm_stats.py, serve_policy.py).
"""
from __future__ import annotations

import importlib
import os
import runpy
import sys
from pathlib import Path


def load_module(path: str):
    """Import the project's file by its own name (`kcare_config.py` -> `kcare_config`) with its folder on sys.path. By name, not from a file spec: the data loader
    pickles the project's classes to its workers, and pickle finds a class by an importable module name (a spec-only module made it fail with a PicklingError)."""
    p = Path(path).resolve()
    if str(p.parent) not in sys.path:
        sys.path.insert(0, str(p.parent))
    return importlib.import_module(p.stem)


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise SystemExit(__doc__)
    code, script, rest = argv[0], argv[1], argv[2:]
    if code != "-":
        mod = load_module(code)
        if hasattr(mod, "register"):
            mod.register()
    target = Path(os.environ.get("OPENPI_ROOT", "/opt/openpi")) / "scripts" / script
    sys.argv = [str(target), *rest]
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
