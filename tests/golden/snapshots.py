"""Goldens that are not cassettes (Step S01 of docs/proposals/generalize_core_design.md), recorded on the old code:

* ``manifest_snapshots/s01_<pack>.json``  ``GET /robot/manifest``'s answer (``api.robot_info.manifest_of``) and the whole parsed
  manifest of the made-up packs fakebot, fakehome, fakeline: "additive only" is proven against them (Steps S02, S27a)
* ``config_snapshots/s01_<pack>.json``    the resolved config of the loop (every key of ``AssistLoop.cfg``, engine defaults,
  no ROBOT_AGENT_* variable) for the three packs (Step S24)
* ``prompt_snapshots/s01_<pack>.json``    the planner-side prompts the harness's planner stub never sees: SAGE's robot prompt
  (``robot_prompt.sage_prompt_kwargs``), the direct planner's prompt, ``_planner_cfg()`` (Step S10)
* ``prompt_snapshots/s01_core_phrases.json``  the core English phrase table (``speech.CORE_PHRASES``, the help questions)
* ``replay_runs/s01_fetch/record.jsonl``  one recorded assist run of fakebot for ``robot_agent/observe/replay.py`` (Step S24)

The tests compare the files with what the code gives now; they never write. ``python tools/golden_record.py --snapshots`` writes
the files that do not exist yet and refuses to overwrite one (as for cassettes); ``--prefix <step>`` writes a later step's own
set (``<step>_<pack>.json``, e.g. S27a's manifest with its added keys) next to the s01 files, which stay. Switch off: delete a
file and its test.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import harness
import harness_sets as HS

GOLDEN = harness.GOLDEN
PACKS = ("fakebot", "fakehome", "fakeline")
SITE = "golden"


def _env(pkg: str) -> dict:
    if pkg == "fakeline":
        from scenario_sets.s01_kinds import LINE
        return json.loads(json.dumps(LINE))
    return harness.load_env()


def manifest_snapshot(pkg: str) -> dict:
    from robot_agent.api.robot_info import manifest_of
    from robot_agent.core.planning.robot import RobotView
    view = RobotView.load(pkg)
    return harness.normal({"manifest_of": manifest_of(view, _env(pkg), SITE), "manifest": view.manifest.to_dict()})


def _with_loop(pkg: str, fn):
    """``fn(loop, tmp)`` on an AssistLoop of ``pkg`` built as the harness builds it (engine defaults, no environment)."""
    import pytest
    sc = {"name": f"snapshot_{pkg}", "robot_pkg": pkg, "env": _env(pkg), "task": "", "options": {}}
    with tempfile.TemporaryDirectory(prefix="golden_") as tmp, pytest.MonkeyPatch.context() as mp:
        HS._no_network(mp)
        HS._environ(mp, sc)
        HS._install(mp, sc, sc["env"], tmp)
        robot, llm, planner = HS._stubs(sc, sc["env"], None)
        return fn(HS._loop(sc, robot, llm, planner, tmp), tmp)


def config_snapshot(pkg: str) -> dict:
    return _with_loop(pkg, lambda loop, tmp: harness.normal(dict(loop.cfg), tmp))


def prompt_snapshot(pkg: str) -> dict:
    from robot_agent.core.planning import direct_prompt, robot_prompt

    def fn(loop, tmp):
        return harness.normal({"sage_prompt_kwargs": robot_prompt.sage_prompt_kwargs(loop.robot),
                               "direct_prompt": direct_prompt.build_prompt(loop.robot, "Bring the part to the station."),
                               "planner_cfg": loop._planner_cfg()}, tmp)
    return _with_loop(pkg, fn)


def core_phrases() -> dict:
    from robot_agent.core.planning import speech
    return {"CORE_PHRASES": dict(speech.CORE_PHRASES), "HELP_MOVE": speech.HELP_MOVE, "HELP_PICK": speech.HELP_PICK}


def snapshot_path(kind: str, name: str, prefix: str = "s01") -> Path:
    """``<kind>_snapshots/<prefix>_<name>.json`` (kind: manifest, config, prompt; name: a pack or ``core_phrases``)."""
    return GOLDEN / f"{kind}_snapshots" / f"{prefix}_{name}.json"


def snapshot_files(prefix: str = "s01") -> dict:
    """path -> the function that gives its content now, for the snapshot set of step ``prefix``."""
    out = {}
    for pkg in PACKS:
        out[snapshot_path("manifest", pkg, prefix)] = (lambda p=pkg: manifest_snapshot(p))
        out[snapshot_path("config", pkg, prefix)] = (lambda p=pkg: config_snapshot(p))
        out[snapshot_path("prompt", pkg, prefix)] = (lambda p=pkg: prompt_snapshot(p))
    out[snapshot_path("prompt", "core_phrases", prefix)] = core_phrases
    return out


def recorded_keys(got: dict, want: dict) -> dict:
    """The part of ``got`` under the top-level keys ``want`` recorded (a missing key stays missing): an additive change of the
    answer compares equal, a dropped or changed key does not (owner's decision 5)."""
    return {k: got[k] for k in want if k in got}


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_missing(prefix: str = "s01") -> list:
    """Write every snapshot file of step ``prefix`` that does not exist yet (never one that exists); the paths written. The
    recorded run for observe/replay.py belongs to the s01 set only."""
    done = []
    for path, fn in snapshot_files(prefix).items():
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8") as f:
            f.write(json.dumps(fn(), indent=1, ensure_ascii=False, sort_keys=True) + "\n")
        done.append(path)
    rec = REPLAY_RUN / "record.jsonl"
    if prefix == "s01" and not rec.exists():
        record_replay_run(rec)
        done.append(rec)
    return done


# ── one recorded run for observe/replay.py ────────────────────────────────────────────────────────────────────────────────
REPLAY_RUN = GOLDEN / "replay_runs" / "s01_fetch"


def record_replay_run(dest: Path) -> None:
    """Record the cassette scenario ``fetch_success`` (fakebot) through the RunManager with the run record on (what
    /ws/agent does) and copy its ``record.jsonl`` to ``dest`` (refused when it exists)."""
    import pytest
    from robot_agent.core import run_control
    from robot_agent.core.unified_agent import _serialize_result
    from robot_agent.interact.run_manager import reset_manager
    from robot_agent.observe import record as R
    sc = json.loads(json.dumps(harness.load(harness.cassette_path("fetch_success"))["scenario"]))
    env = harness.load_env()
    with tempfile.TemporaryDirectory(prefix="golden_") as tmp, pytest.MonkeyPatch.context() as mp:
        HS._no_network(mp)
        HS._environ(mp, sc)
        mp.setenv("ROBOT_AGENT_DATA_DIR", str(Path(tmp) / "data"))
        mp.setenv("ROBOT_AGENT_HOST", "test")
        HS._install(mp, sc, env, tmp)
        reset_manager()
        try:
            robot, llm, planner = HS._stubs(sc, env, None)
            loop = HS._loop(sc, robot, llm, planner, tmp)
            mgr = run_control.manager()
            run = mgr.start(goal=sc["task"], user_id="golden", role="owner").run
            run_control.begin_run()
            R.note_loop(R.instrument(loop), sc["task"], "en", kwargs={})
            status = None
            for ev in loop.run_blocking(sc["task"], "en"):
                ev = run.publish(_serialize_result(ev))
                status = ev.get("status") if ev.get("event") == "done" else status
            mgr.finish(run.run_id, ok=status == "success", outcome=status)
            src = Path(tmp) / "data" / "run_records" / "test" / run.run_id / "record.jsonl"
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "x", encoding="utf-8") as f:
                f.write(src.read_text(encoding="utf-8"))
        finally:
            reset_manager()
            harness._fresh_run_state()
