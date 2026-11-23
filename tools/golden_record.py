#!/usr/bin/env python3
"""Record the golden traces of tests/golden (cassettes of the real AssistLoop with the model and the robot stubbed).

    python tools/golden_record.py                 # list the scenarios: recorded / missing (writes nothing)
    python tools/golden_record.py --check         # replay every cassette, print the diff of any that differs
    python tools/golden_record.py --record        # write the cassettes that do not exist yet
    python tools/golden_record.py --record NAME   # write the cassette of scenario NAME (refused if it exists)
    python tools/golden_record.py --snapshots     # write the missing manifest / config / prompt snapshots and the replay run
    python tools/golden_record.py --snapshots --prefix s27a   # a later step's own snapshot set (<step>_<pack>.json)

The scenarios are those of tests/golden/scenarios.py (cassettes in tests/golden/cassettes, harness.py) and of the scenario sets
(tests/golden/scenario_sets/<step>_*.py: cassettes in tests/golden/set_cassettes, harness_sets.py). A set cassette carries the
commit it was recorded on (refused when robot_agent/ has uncommitted changes); the step lists it in its own
tests/golden/commits/<step>.json (kind equivalence or feature).

A cassette is never overwritten: a golden is only added. A change of behaviour that is MEANT deletes the cassette in a reviewed
commit and records it again (the owner's rule: the default-path goldens are never regenerated to make a test pass).
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "golden"))

import harness  # noqa: E402
import harness_sets  # noqa: E402
import scenario_sets  # noqa: E402

# scenarios.py first (its order), then every scenario set (tests/golden/scenario_sets/<step>_*.py) in file-name order
SCENARIOS = scenario_sets.all_scenarios()


def _harness(name: str):
    """harness.py for a scenario of scenarios.py (round A), harness_sets.py for a scenario of a set."""
    import scenarios
    return harness if name in {sc["name"] for sc, _t, _a in scenarios.SCENARIOS} else harness_sets


def _record(names) -> int:
    refused = 0
    for sc, tags, about in SCENARIOS:
        if names and sc["name"] not in names:
            continue
        h = _harness(sc["name"])
        if h.cassette_path(sc["name"]).exists():
            print(f"refused   {sc['name']}: the cassette exists (never overwritten)")
            refused += 1 if names else 0
            continue
        print(f"recorded  {h.record(sc, tags, about).relative_to(ROOT)}")
    unknown = set(names) - {sc["name"] for sc, _t, _a in SCENARIOS}
    for n in sorted(unknown):
        print(f"unknown   {n}")
    return 1 if refused or unknown else 0


def _check() -> int:
    bad = 0
    for p, h in harness_sets.all_cassettes():
        d = h.check(p)
        print(f"{'same' if not d else 'DIFFERS'}  {p.name}")
        if d:
            bad += 1
            print(d)
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", action="store_true", help="write the missing cassettes (refuses to overwrite one)")
    ap.add_argument("--check", action="store_true", help="replay every cassette and print the diffs")
    ap.add_argument("--snapshots", action="store_true", help="write the missing non-cassette goldens (tests/golden/snapshots.py)")
    ap.add_argument("--prefix", default="s01", help="with --snapshots: the step whose snapshot set is written")
    ap.add_argument("names", nargs="*", help="scenario names (with --record)")
    a = ap.parse_args(argv)
    if a.record:
        return _record(a.names)
    if a.check:
        return _check()
    if a.snapshots:
        import snapshots
        for path in snapshots.write_missing(a.prefix):
            print(f"recorded  {path.relative_to(ROOT)}")
        return 0
    for sc, tags, _about in SCENARIOS:
        state = "recorded" if _harness(sc["name"]).cassette_path(sc["name"]).exists() else "missing "
        print(f"{state}  {sc['name']:30s} {','.join(tags)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
