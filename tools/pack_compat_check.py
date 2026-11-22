#!/usr/bin/env python3
"""Does a robot package resolve to the same vocabulary as before the pack schema? (design, "Standard pack", proof layer 4)

    python tools/pack_compat_check.py <package name | package folder | manifest.yaml> [more ...] [--import] [--json]

What: for each robot it compares (1) the vocabulary the OLD code computed, rebuilt here from a frozen copy of the old tables
(``tools/pack_compat_ref.json``, recorded on the code before any table moved) and the robot's manifest, with what the LIVE
code gives now (goal kinds, the actions each kind lacks, the prompt lines, the plan actions); (2) every table the standard
pack holds by now with the frozen table it replaces (a table that has not moved yet is reported as such, not as an error);
(3) what the loader lifts from a schema-1 manifest with the manifest itself, and the recovery table with the adapter's.
Why: the six real packs live in other repositories and are not copied into this one; the integrator runs this on each of
them after every step, and the test suite runs it on the made-up packs. Exit 0 = no difference.
A folder or a file is read as a manifest only; ``--import`` also imports the robot's adapter (its own code) from the
folder's parent. ``--record`` writes the frozen copy once and refuses to overwrite it. It starts nothing and writes
nothing else.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REF_FILE = Path(__file__).with_name("pack_compat_ref.json")
BY_ACTION = re.compile(r"recover = None if no is not None else (\{.*?\})\.get\(a\)", re.S)   # the loop's inline dict


def record() -> dict:
    """The old tables, read from the code as it is NOW: run once, before any table moves (the commit is kept in the file)."""
    from robot_agent.core.planning import goals, robot, speech
    from robot_agent.options import bt_goal
    kind = lambda k: {"needs": [list(n) if isinstance(n, tuple) else n for n in k.needs], "prompt": k.prompt}   # noqa: E731
    loop = (ROOT / "robot_agent/core/planning/assist_loop.py").read_text(encoding="utf-8")
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return {"recorded_on": commit, "source": "robot_agent/core/planning/goals.py, speech.py, robot.py, assist_loop.py (the dict of "
            "_step_failed), options/bt_goal.py",
            "goal_kinds": {n: kind(k) for n, k in goals.GOAL_KINDS.items()}, "manipulation": list(goals.MANIPULATION),
            "template_kinds": list(goals.TEMPLATE_KINDS), "optional_kinds": {n: kind(k) for n, k in goals.OPTIONAL_KINDS.items()},
            "conditions": list(bt_goal.CONDITIONS), "core_phrases": dict(speech.CORE_PHRASES),
            "by_action": re.findall(r'"(\w+)":', BY_ACTION.search(loop).group(1)), "not_plan_steps": sorted(robot.NOT_PLAN_STEPS)}


def reference() -> dict:
    return json.loads(REF_FILE.read_text(encoding="utf-8"))


def _missing(ref: dict, kind: str, actions: set, to: str = "") -> list:
    """goals.missing_actions as it was (goals.py:57-71 at the recorded commit), over the frozen table."""
    k = ref["goal_kinds"].get(kind)
    if k is None:
        return [kind]
    out = [(n if isinstance(n, list) else [n])[0] for n in k["needs"] if not any(a in actions for a in (n if isinstance(n, list) else [n]))]
    return out + (["MoveTo"] if kind == "say" and to and "MoveTo" not in actions else [])


def old_vocabulary(ref: dict, view: Any) -> dict:
    """What the old code computed for this robot, from the frozen tables and the robot view's pass-through lookups."""
    plan = ({a for a in view.skill_map if a not in set(ref["not_plan_steps"])} & view.supported_actions) | view.noop_actions
    kinds = list(ref["manipulation"]) + [k for k in ref["template_kinds"] if not _missing(ref, k, plan)]
    table = {**ref["goal_kinds"], **ref["optional_kinds"]}
    return {"plan_actions": sorted(plan), "kinds": kinds,
            "missing": {k: [_missing(ref, k, plan), _missing(ref, k, plan, "x")] for k in list(ref["goal_kinds"]) + ["no_such_kind"]},
            "prompt_lines": "".join(f"\n  {table[k]['prompt']}" for k in kinds if table[k]["prompt"])}


def new_vocabulary(ref: dict, view: Any) -> dict:
    """The same four values from the live code (which reads the pack for every table that has moved)."""
    from robot_agent.core.planning import goals
    plan = set(view.plan_actions)
    kinds = goals.kinds_for(plan)
    miss = lambda k, to: goals.missing_actions({"type": k, **({"to": to} if to else {})}, plan)   # noqa: E731
    return {"plan_actions": sorted(plan), "kinds": kinds, "prompt_lines": goals.prompt_lines(kinds),
            "missing": {k: [miss(k, ""), miss(k, "x")] for k in list(ref["goal_kinds"]) + ["no_such_kind"]}}


def moved_tables(ref: dict, pack: Any) -> tuple:
    """(differences, notes): each table the SHIPPED packs of the chain hold by now, against the frozen table it replaces.
    The robot's own entries (schema 2) are its own vocabulary and are not compared."""
    diffs, notes = [], []
    shipped, base = set(pack.chain[:-1]), set(pack.chain[1:-1])

    def table(name: str, packs=shipped) -> dict:
        return {n: e for n, e in pack.table(name).items() if pack.origin[name][n] in packs}

    def compare(what: str, entries: dict, got, want) -> None:
        if not entries:
            notes.append(f"{what}: not moved yet (the code is the source)")
        elif got != want:
            diffs.append(f"{what}: the pack gives {got!r}, the old code had {want!r}")

    kinds = table("goal_kinds")
    compare("goal kinds (names, in order)", kinds, [k for k in kinds if k in ref["goal_kinds"]], list(ref["goal_kinds"]))
    if any(isinstance(e, dict) and "needs" in e for e in kinds.values()):
        compare("goal kinds (needs)", kinds, {k: kinds[k].get("needs") for k in kinds if k in ref["goal_kinds"]},
                {k: v["needs"] for k, v in ref["goal_kinds"].items()})
    preds = table("predicates", base)                           # the standard pack's own: the core pack may add generic ones
    compare("tree conditions", preds, sorted(n for n, e in preds.items() if "tree" in (e.get("scopes") or ())), sorted(ref["conditions"]))
    words = table("phrases")
    compare("core phrases (English)", words, {k: (words.get(k) or {}).get("en") for k in ref["core_phrases"]}, ref["core_phrases"])
    by_action = table("recovery.by_action")
    compare("recovery by action", by_action, sorted(by_action), sorted(ref["by_action"]))
    return diffs, notes


def lifted(pack: Any, manifest: Any, adapter: Any) -> list:
    """Differences between what the loader lifted from a schema-1 manifest and the manifest / adapter themselves."""
    from robot_agent.contract.recovery import RecoveryPolicy, policy_for
    from robot_agent.core.pack import loader
    from robot_agent.core.pack.lift import dig, plain
    idx, diffs = loader.index(), []
    for table, path in (idx.get("schema_1_lift") or {}).items():
        want = dig(manifest, adapter, path)
        if pack.table(table) != (plain(want) if isinstance(want, dict) and want else {}):
            diffs.append(f"lift {path} -> {table}: {pack.table(table)!r} is not what the robot declares ({want!r})")
    for path in idx.get("schema_1_keep") or ():
        want = dig(manifest, adapter, path)
        if pack.kept.get(path) != (plain(want) if want not in (None, {}, []) else None):
            diffs.append(f"keep {path}: {pack.kept.get(path)!r} is not what the manifest declares ({want!r})")
    if RecoveryPolicy(pack.table("recovery.codes") or None).table != policy_for(adapter).table:
        diffs.append("recovery codes: the pack's table differs from the adapter's policy")
    return diffs


def check(target: str, do_import: bool = False, ref: Optional[dict] = None) -> dict:
    """The report of one robot: {target, loaded, pack, chain, notes, differences}; ``differences`` empty = compatible."""
    from robot_agent.core.pack import PackError, resolve
    from robot_agent.core.pack.show import open_robot
    from robot_agent.core.planning.robot import RobotView
    ref = ref or reference()
    manifest, adapter, how = open_robot(target, do_import)
    view = RobotView(adapter if adapter is not None else SimpleNamespace(manifest=manifest))
    before = new_vocabulary(ref, view)
    try:
        pack = resolve(manifest, adapter)
    except PackError as e:
        return {"target": target, "loaded": how, "pack": None, "chain": [], "notes": [], "differences": [f"the pack does not load: {e}"]}
    old, new = old_vocabulary(ref, view), new_vocabulary(ref, view)
    diffs, notes = moved_tables(ref, pack)
    diffs += [f"result check: [{p.pack}] {p.path}: {p.text}" for p in pack.problems]
    diffs += [f"{k}: resolving the pack changed it ({before[k]!r} -> {new[k]!r})" for k in new if before[k] != new[k]]
    if pack.schema < 2:
        diffs += [f"{k}: the live code gives {new[k]!r}, the old code gave {old[k]!r}" for k in old if old[k] != new[k]]
        diffs += lifted(pack, manifest, adapter)
        notes.append(f"schema 1: the standard pack is used ({' -> '.join(pack.chain[:-1])}) with what the manifest declares")
    else:
        notes.append("schema 2: the robot's own vocabulary is not compared with the old code, only the shipped tables are")
    return {"target": target, "loaded": how, "pack": pack.header(), "chain": list(pack.chain), "notes": notes, "differences": diffs}


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--record" in args:
        if REF_FILE.exists():
            print(f"refused: {REF_FILE} exists (the frozen copy of the old tables is recorded once)")
            return 2
        REF_FILE.write_text(json.dumps(record(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"recorded {REF_FILE}")
        return 0
    targets = [a for a in args if not a.startswith("--")]
    if not targets:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    reports = [check(t, "--import" in args) for t in targets]
    if "--json" in args:
        print(json.dumps(reports, indent=1, ensure_ascii=False, default=str))
    for r in reports if "--json" not in args else ():
        print(f"{'DIFF' if r['differences'] else 'SAME'}  {r['target']}  [{r['loaded']}]  pack {r['pack']}  chain {' -> '.join(r['chain'])}")
        print("".join(f"      note: {n}\n" for n in r["notes"]) + "".join(f"      DIFFERENCE {d}\n" for d in r["differences"]), end="")
    return 1 if any(r["differences"] for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
