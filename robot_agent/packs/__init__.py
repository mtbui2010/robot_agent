"""The packs that ship with the engine: declared data, plus the named callables that data refers to.

What: one folder per pack, listed by name in ``index.yaml``: ``core`` (what a run needs at all) and ``household`` (the standard
pack: today's household behaviour as data). A folder holds ``pack.yaml`` (the header only), one ``<section>.yaml`` per section
(a missing file is an empty section) and ``callables/`` (every module in it is imported and registers its names).
Why: the engine names no kind of robot. It loads this layer by name (``robot_agent/core/pack/loader.py``) and never imports
it; this layer may import the contract and the engine's public pack API (``robot_agent.core.pack``).
Off: a robot whose manifest says ``pack: {extends: null}`` gets the core pack only; a section with no file declares nothing.
"""

import importlib
import pkgutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.yaml"


def import_siblings(package: str, path) -> list:
    """Import every module of the callables package ``package`` (``path``: its ``__path__``) in name order, so that a step
    adds its own module and edits no shared file. Returns the module names."""
    names = sorted(m.name for m in pkgutil.iter_modules(path))
    for n in names:
        importlib.import_module(f"{package}.{n}")
    return names


__all__ = ["ROOT", "INDEX", "import_siblings"]
