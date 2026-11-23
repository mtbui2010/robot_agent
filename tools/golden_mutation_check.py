#!/usr/bin/env python3
"""Do the golden traces bite? Each mutation below changes ONE behaviour of the engine by monkeypatching a function in a fresh
child process (the tree is never edited); every cassette of tests/golden is then replayed. A mutation is caught when at least
one cassette differs (or its run raises). Exit 0 only when the unmutated replay is clean and EVERY mutation is caught.

    python tools/golden_mutation_check.py            # all mutations, in parallel child processes
    python tools/golden_mutation_check.py --list     # the mutations and what each changes
    python tools/golden_mutation_check.py --one NAME # one mutation in this process (what the children run); prints JSON
    python tools/golden_mutation_check.py --only A,B # only these mutations (and the unmutated replay)

The mutations of later steps live in tools/golden_mutations/<step>_*.py (``MUTATIONS``: name -> (what it changes, patch(mp))),
loaded after the ones in this file, in file-name order.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "golden"))


# ── the mutations: name -> (what it changes, patch(mp)) ──────────────────────────────────────────────────────────────
def _gate_sees_nothing(mp):
    from robot_agent.core.planning.assist_loop import AssistLoop
    mp.setattr(AssistLoop, "_check", lambda self, steps, world, places, goals: [])


def _drop_recover_events(mp):
    from robot_agent.core.planning.assist_loop import AssistLoop
    orig = AssistLoop.run_blocking

    def run_blocking(self, *a, **kw):
        return (e for e in orig(self, *a, **kw) if e.get("event") != "recover")
    mp.setattr(AssistLoop, "run_blocking", run_blocking)


def _one_retry_fewer(mp):
    from robot_agent.contract.recovery import RecoveryRule
    orig = RecoveryRule.next_after
    mp.setattr(RecoveryRule, "next_after", lambda self, attempts: orig(self, attempts + 1))


def _yes_means_no(mp):
    from robot_agent.interact import dialog
    orig = dialog.yes_no

    def yes_no(text):
        v, rest = orig(text)
        return (None if v is None else not v), rest
    mp.setattr(dialog, "yes_no", yes_no)


def _fallback_runs_as_sequence(mp):
    from robot_agent.core.planning import behavior_tree as bt
    orig = bt._tick
    mp.setattr(bt, "_tick", lambda node, host, path: orig(["sequence", *node[1:]] if node[0] == "fallback" else node, host, path))


def _no_replan_budget(mp):
    from robot_agent.core.planning.assist_loop import AssistLoop
    orig = AssistLoop.__init__

    def init(self, *a, **kw):
        orig(self, *a, **kw)
        self.cfg["max_replans"] = 0
    mp.setattr(AssistLoop, "__init__", init)


def _goals_only_reports_none(mp):
    from robot_agent.options import goals_only
    mp.setattr(goals_only, "reported", lambda run_goals, handed: [])


def _plan_only_ignored(mp):
    from robot_agent.core.planning.assist_loop import AssistLoop
    orig = AssistLoop.run_blocking
    mp.setattr(AssistLoop, "run_blocking", lambda self, task, lang="en", plan_only=False, goals=None: orig(self, task, lang, False, goals))


def _stop_not_seen(mp):
    from robot_agent.core.planning.assist_loop import AssistLoop
    mp.setattr(AssistLoop, "_cancelled", lambda self, r: None)


def _menu_stop_word_unheard(mp):
    from robot_agent.options import bt_goal
    orig = bt_goal.read_menu
    mp.setattr(bt_goal, "read_menu", lambda answer, words, place, lang: "none" if orig(answer, words, place, lang) == "stop" else
               orig(answer, words, place, lang))


MUTATIONS = {
    "gate_sees_nothing": ("the plan checker (gate) finds no problem in any plan", _gate_sees_nothing),
    "drop_recover_events": ("the loop no longer emits its `recover` events", _drop_recover_events),
    "one_retry_fewer": ("every recovery rule allows one retry fewer (contract.recovery RecoveryRule.next_after)", _one_retry_fewer),
    "yes_means_no": ("a spoken yes is read as no and a no as yes (interact.dialog.yes_no)", _yes_means_no),
    "bt_fallback_as_sequence": ("a behaviour tree's fallback node runs as a sequence (behavior_tree._tick)", _fallback_runs_as_sequence),
    "no_replan_budget": ("the replan budget is 0 (cfg max_replans)", _no_replan_budget),
    "goals_only_reports_none": ("an understand-only run reports no goal (options.goals_only.reported)", _goals_only_reports_none),
    "plan_only_ignored": ("run_blocking ignores plan_only (the robot moves)", _plan_only_ignored),
    "stop_not_seen": ("a stop between steps is not seen (AssistLoop._cancelled)", _stop_not_seen),
    "menu_stop_word_unheard": ("a stop word answered to the bt_goal menu question is not heard (options.bt_goal.read_menu)",
                               _menu_stop_word_unheard),
}



def _load_sets() -> None:
    """Add the mutations of every set file ``tools/golden_mutations/<step>_*.py`` (its ``MUTATIONS``: name -> (what, patch)),
    in file-name order, after the ones above. One file per step: parallel steps add mutations without a common file."""
    import glob
    import importlib.util
    for path in sorted(glob.glob(os.path.join(ROOT, "tools", "golden_mutations", "s[0-9]*_*.py"))):
        spec = importlib.util.spec_from_file_location("golden_mutations_" + os.path.basename(path)[:-3], path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name, entry in getattr(mod, "MUTATIONS", {}).items():
            if name in MUTATIONS:
                raise ValueError(f"{path}: mutation {name} is defined twice")
            MUTATIONS[name] = entry


_load_sets()

# ── one child: apply a mutation, replay every cassette ───────────────────────────────────────────────────────────────
def run_one(name: str) -> dict:
    import pytest
    import harness_sets
    failed, why = [], {}
    with pytest.MonkeyPatch.context() as mp:
        if name != "none":
            MUTATIONS[name][1](mp)
        for p, h in harness_sets.all_cassettes():               # round A with harness.py, the sets with harness_sets.py
            cname = p.name.split(".")[0]
            try:
                d = h.check(p)
            except Exception as e:                               # a crash under the mutation is a catch too
                d = f"raised {type(e).__name__}: {e}"
            if d:
                failed.append(cname)
                why[cname] = d.splitlines()[0][:160]
        if name != "none":
            _snapshot_tests(MUTATIONS[name], failed, why)
    return {"mutation": name, "caught_by": failed, "first_diff": why}


def _snapshot_tests(entry, failed, why) -> None:
    """A mutation of a table no run reads (only a snapshot test pins it) names its tests as a third element: ``(what, patch,
    [pytest node ids])``; each test that fails under the mutation counts as a catch (``test:<id>``)."""
    import pytest
    for node in (entry[2] if len(entry) > 2 else ()):
        code = pytest.main([os.path.join(ROOT, node), "-q", "-x", "--no-header", "-p", "no:cacheprovider", "-p", "no:warnings"])
        if code != 0:
            failed.append("test:" + node)
            why["test:" + node] = f"pytest exit {int(code)}"


def _child(name: str) -> dict:
    env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--one", name], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=600)
    last = [x for x in p.stdout.splitlines() if x.startswith("{")]
    if p.returncode != 0 or not last:
        return {"mutation": name, "caught_by": None, "error": (p.stderr or p.stdout)[-800:]}
    return json.loads(last[-1])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--one", help="run one mutation (or 'none') in this process and print the result as JSON")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--jobs", type=int, default=min(len(MUTATIONS) + 1, os.cpu_count() or 2))
    ap.add_argument("--only", default="", help="comma-separated mutation names (default: all)")
    a = ap.parse_args(argv)
    if a.list:
        for n, (what, _f) in MUTATIONS.items():
            print(f"{n:26s} {what}")
        return 0
    if a.one:
        print(json.dumps(run_one(a.one)))
        return 0
    names = ["none", *([n for n in a.only.split(",") if n] if a.only else MUTATIONS)]
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        results = dict(zip(names, ex.map(_child, names)))
    return report(results)


def report(results: dict) -> int:
    base, bad = results.pop("none"), 0
    if base.get("caught_by") != []:
        print(f"FAIL  the unmutated replay is not clean: {base.get('caught_by') or base.get('error')}")
        bad += 1
    for n, r in results.items():
        if r.get("caught_by") is None:
            print(f"ERROR {n}: the child failed\n{r.get('error')}")
            bad += 1
        elif not r["caught_by"]:
            print(f"MISS  {n}: no golden caught it ({MUTATIONS[n][0]})")
            bad += 1
        else:
            first = r["caught_by"][0]
            print(f"CAUGHT {n:26s} by {len(r['caught_by'])}: {', '.join(r['caught_by'])}")
            print(f"       e.g. {first}: {r['first_diff'][first]}")
    print(f"== {len(results)} mutations, {bad} not caught or failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
