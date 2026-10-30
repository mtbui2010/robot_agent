"""Contract test: does a robot's manifest.yaml match its skills?

    python -m robot_agent.contract.check_manifest <pkg_dir> [--manifest PATH] [--strict]

``<pkg_dir>`` is the robot package directory (``my_robot/my_robot``),
its repo root (``my_robot``) or an importable package name. Nothing of the
robot is imported and no ROS is needed: the skill registry is read the way
``SkillRegistry`` builds it (``configs/skills_config.py`` ``SKILL_CONFIGS`` +
the saved ``skills.json``), and skill signatures are read from source.

Errors (exit 1):
  * the manifest does not load (bad field, unknown FailureCode in ``errors`` ...)
  * a manifest skill is not in the registry, or its module / function is missing
  * a manifest param the function cannot take (no such kwarg and no ``**kwargs``)
  * a function parameter without default that the manifest does not declare
  * a ``sensor`` verify naming a skill the robot does not have
  * ``planner.seed_memory`` naming a file that does not exist or is not JSONL
Warnings (exit 1 only with --strict): unknown manifest keys, an action the
namemap's ``SKILL_MAP`` binds to a different skill, skills that cannot be
inspected (external / defined indirectly).

From a robot package's own tests::

    from robot_agent.contract.check_manifest import assert_manifest_matches
    def test_manifest(): assert_manifest_matches(Path(__file__).parents[1] / "my_robot")
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Optional

from .manifest import ALIAS_ACTIONS_KEY, PLANNER_KEYS, Manifest, ManifestError


@dataclass
class Report:
    pkg_dir: Path
    manifest: Optional[Manifest] = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    registry: dict = field(default_factory=dict)     # skill name -> SkillDef-like dict

    @property
    def ok(self) -> bool:
        return not self.errors

    def format(self) -> str:
        head = (f"manifest {self.manifest.source} ({self.manifest.robot}, {len(self.manifest.skills)} skills)"
                if self.manifest else f"manifest of {self.pkg_dir}")
        lines = [head + f": {len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        lines += [f"  ERROR   {e}" for e in self.errors]
        lines += [f"  warning {w}" for w in self.warnings]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# locating the package
# ─────────────────────────────────────────────────────────────────────────────
def resolve_pkg_dir(target) -> Path:
    p = Path(str(target))
    if p.is_dir():
        p = p.resolve()
        if (p / "configs").is_dir():
            return p
        if (p / p.name / "configs").is_dir():
            return p / p.name
        # a repo root under another name (a worktree): its one package with configs/
        inner = [d for d in p.iterdir() if d.is_dir() and (d / "configs").is_dir() and (d / "__init__.py").is_file()]
        if len(inner) == 1:
            return inner[0]
        raise FileNotFoundError(f"{p}: no configs/ here nor in one package below it")
    spec = find_spec(str(target))
    if spec is None or not spec.submodule_search_locations:
        raise FileNotFoundError(f"{target!r} is neither a directory nor an importable package")
    return Path(list(spec.submodule_search_locations)[0]).resolve()


# ─────────────────────────────────────────────────────────────────────────────
# the skill registry, without importing the robot
# ─────────────────────────────────────────────────────────────────────────────
class _Eval(ast.NodeVisitor):
    """Literal evaluation that also follows module-level string constants
    and f-strings over them (``f'{_PKG}.pick'``)."""

    def __init__(self, names: dict):
        self.names = names

    def ev(self, node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name) and node.id in self.names:
            return self.names[node.id]
        if isinstance(node, ast.JoinedStr):
            out = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    out.append(str(v.value))
                elif isinstance(v, ast.FormattedValue):
                    out.append(str(self.ev(v.value)))
                else:
                    raise ValueError("unsupported f-string part")
            return "".join(out)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.ev(node.left) + self.ev(node.right)
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(self.ev(e) for e in node.elts)
        if isinstance(node, ast.Dict):
            return {self.ev(k): self.ev(v) for k, v in zip(node.keys, node.values)}
        raise ValueError(f"not a literal: {ast.dump(node)[:60]}")


def read_skill_configs(path: Path) -> dict:
    """``SKILL_CONFIGS`` of a skills_config.py, read from its AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: dict = {}
    ev = _Eval(names)
    for node in tree.body:
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for t in targets:
            if isinstance(t, ast.Name):
                try:
                    names[t.id] = ev.ev(value)
                except (ValueError, TypeError, KeyError):
                    pass
    cfg = names.get("SKILL_CONFIGS")
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: no literal SKILL_CONFIGS dict")
    return cfg


def read_registry(pkg_dir: Path) -> tuple:
    """(registry, notes): name -> {type, module_path, func_name, aliases},
    SKILL_CONFIGS first, then skills.json on top (what SkillRegistry does)."""
    reg: dict = {}
    notes: list = []
    sc = pkg_dir / "configs" / "skills_config.py"
    if sc.is_file():
        try:
            for name, entry in read_skill_configs(sc).items():
                mod, func = entry if isinstance(entry, tuple) else (entry, name)
                reg[name] = {"type": "internal", "module_path": mod, "func_name": func or name, "aliases": []}
        except (SyntaxError, ValueError) as e:
            notes.append(f"configs/skills_config.py not readable: {e}")
    else:
        notes.append("no configs/skills_config.py")
    for sj in (pkg_dir / "configs" / "common" / "skills.json", pkg_dir / "data" / "skills.json"):
        if not sj.is_file():
            continue
        try:
            data = json.loads(sj.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            notes.append(f"{sj.relative_to(pkg_dir)} not readable: {e}")
            continue
        for it in data if isinstance(data, list) else []:
            if isinstance(it, dict) and it.get("name"):
                reg[it["name"]] = {"type": it.get("type", "internal"), "module_path": it.get("module_path", ""),
                                   "func_name": it.get("func_name") or it["name"],
                                   "aliases": list(it.get("aliases") or [])}
        break
    return reg, notes


def read_skill_map(pkg_dir: Path) -> Optional[dict]:
    """The namemap's literal ``SKILL_MAP`` (None when there is none)."""
    f = pkg_dir / "configs" / "grace_namemap.py"
    if not f.is_file():
        return None
    try:
        tree = ast.parse(f.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for node in tree.body:
        t = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if isinstance(t, ast.Name) and t.id == "SKILL_MAP" and getattr(node, "value", None) is not None:
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# signatures
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _Sig:
    named: set                 # every keyword-able parameter
    required: set              # parameters without a default
    var_kw: bool               # takes **kwargs


def _module_file(pkg_dir: Path, module_path: str) -> Optional[Path]:
    root = pkg_dir.name
    if not module_path or not (module_path == root or module_path.startswith(root + ".")):
        return None
    rel = module_path.split(".")[1:]
    base = pkg_dir.joinpath(*rel) if rel else pkg_dir
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        if cand.is_file():
            return cand
    return base.with_suffix(".py")      # reported as missing


def read_signature(path: Path, func: str) -> Optional[_Sig]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func),
              None)
    if fn is None:
        return None
    a = fn.args
    pos = a.posonlyargs + a.args
    pos_req = pos[:len(pos) - len(a.defaults)] if a.defaults else pos
    kw_req = [k for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
    skip = {"node", "self"}
    return _Sig(named={x.arg for x in a.args + a.kwonlyargs} - skip,
                required={x.arg for x in pos_req + kw_req} - skip,
                var_kw=a.kwarg is not None)


# ─────────────────────────────────────────────────────────────────────────────
# the check
# ─────────────────────────────────────────────────────────────────────────────
def check(target, manifest_path=None) -> Report:
    pkg_dir = resolve_pkg_dir(target)
    rep = Report(pkg_dir=pkg_dir)
    mpath = Path(manifest_path) if manifest_path else pkg_dir / "configs" / "manifest.yaml"
    if not mpath.is_file():
        rep.errors.append(f"no manifest at {mpath}")
        return rep
    try:
        rep.manifest = m = Manifest.from_yaml(mpath)
    except ManifestError as e:
        rep.errors += [f"manifest: {x}" for x in e.errors]
        return rep

    _check_planner(m, rep)
    reg, notes = read_registry(pkg_dir)
    rep.registry = reg
    rep.warnings += notes
    aliases = {a: n for n, d in reg.items() for a in d.get("aliases") or []}

    def known(name: str) -> Optional[str]:
        return name if name in reg else aliases.get(name)

    for s in m.skills:
        where = f"skill {s.name!r} ({s.action})"
        for k in s.extra:
            if k == ALIAS_ACTIONS_KEY:
                continue
            rep.warnings.append(f"{where}: key {k!r} is not part of the contract (kept, ignored by the core)")
        for v in s.verify:
            if v.kind == "sensor" and not known(v.skill):
                rep.errors.append(f"{where}: verify skill {v.skill!r} is not a registered skill")
        real = known(s.name)
        if real is None:
            rep.errors.append(f"{where}: not in the skill registry (configs/skills_config.py / skills.json)")
            continue
        d = reg[real]
        if d["type"] != "internal":
            if d["type"] == "external":
                rep.warnings.append(f"{where}: external skill, params not checked")
            continue
        f = _module_file(pkg_dir, d["module_path"])
        if f is None:
            rep.warnings.append(f"{where}: module {d['module_path']!r} is outside {pkg_dir.name}, not inspected")
            continue
        if not f.is_file():
            rep.errors.append(f"{where}: module {d['module_path']!r} not found ({f})")
            continue
        try:
            sig = read_signature(f, d["func_name"])
        except SyntaxError as e:
            rep.errors.append(f"{where}: {f.name} does not parse: {e}")
            continue
        if sig is None:
            rep.warnings.append(f"{where}: no top-level def {d['func_name']}() in {f.name}, signature not checked")
            continue
        declared = set(s.param_names)
        for p in s.param_names:
            if p not in sig.named and not sig.var_kw:
                rep.errors.append(f"{where}: param {p!r} is not accepted by {d['func_name']}() "
                                  f"(takes {', '.join(sorted(sig.named)) or 'nothing'})")
        for p in sorted(sig.required - declared):
            rep.errors.append(f"{where}: {d['func_name']}() requires {p!r}, which the manifest params do not declare")

    sm = read_skill_map(pkg_dir)
    if sm:
        for action, skill in sm.items():
            spec = m.skill_for(action)
            if spec is not None and spec.name != skill:
                rep.warnings.append(f"action {action}: grace_namemap.SKILL_MAP binds {skill!r}, "
                                    f"the manifest {spec.name!r} (the manifest wins in NamemapAdapter)")
            elif spec is None and m.skill(skill) is None:
                rep.warnings.append(f"action {action}: grace_namemap.SKILL_MAP binds {skill!r}, "
                                    f"which the manifest does not declare")
    return rep


def _check_planner(m: Manifest, rep: Report) -> None:
    """``planner:``: unknown keys warn; the seed pool must exist and hold JSON lines."""
    for k in sorted(set(m.planner) - PLANNER_KEYS):
        rep.warnings.append(f"planner.{k}: not part of the contract (kept, ignored by the core)")
    p = m.seed_memory
    if p is None:
        return
    if not p.is_file():
        rep.errors.append(f"planner.seed_memory: no file {p}")
        return
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            rep.errors.append(f"planner.seed_memory: {p.name} line {i} is not JSON ({e.msg})")
            continue
        if not isinstance(rec, dict) or not rec.get("task") or not (rec.get("plan_text") or rec.get("steps")):
            rep.errors.append(f"planner.seed_memory: {p.name} line {i} needs `task` and `plan_text` / `steps`")


def assert_manifest_matches(target, manifest_path=None, strict: bool = False) -> Report:
    rep = check(target, manifest_path)
    if rep.errors or (strict and rep.warnings):
        raise AssertionError(rep.format())
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m robot_agent.contract.check_manifest",
                                 description="Check a robot's manifest.yaml against its skills.")
    ap.add_argument("pkg_dir", help="robot package dir, its repo root, or an importable package name")
    ap.add_argument("--manifest", default=None, help="manifest file (default: <pkg>/configs/manifest.yaml)")
    ap.add_argument("--strict", action="store_true", help="warnings fail too")
    a = ap.parse_args(argv)
    try:
        rep = check(a.pkg_dir, a.manifest)
    except FileNotFoundError as e:
        print(f"[check_manifest] {e}", file=sys.stderr)
        return 2
    print(rep.format())
    return 1 if rep.errors or (a.strict and rep.warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
