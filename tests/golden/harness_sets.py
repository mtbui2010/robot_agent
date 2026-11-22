"""The harness of the scenario SETS (tests/golden/scenario_sets, Step S01 onwards): ``harness.py`` (round A, frozen at 65124d0)
plus the opt-in scenario keys of the later steps. Their cassettes live in ``tests/golden/set_cassettes`` and replay with this
module (``test_golden_set_cassettes.py``); the 21 cassettes of ``tests/golden/cassettes`` keep replaying with ``harness.py``
alone, unchanged.

Why a second module: an existing test file is never edited (owner's rule), so the additions wrap ``harness.py`` instead of
changing it. What it adds, each opt-in per scenario:

* ``capture`` (a list) adds sections to the cassette, ``FORMAT`` stays 1:
    planner_io  the hash and head of the observation and of the visible list of every planner call
    llm_kw      the options (keys, hash) and the role of every model call; role clients are served by the same stub
    prompts     hash and head of robot_prompt.sage_prompt_kwargs(robot), of direct_prompt.build_prompt and of _planner_cfg()
    reconcile   the run-start reconcile is NOT stubbed (loop.reconcile_world runs against the FakeRobot)
    trace       the rows of option ``trace``'s file (the order of the hooks it watches), section ``trace_rows``
* ``environ``: ROBOT_AGENT_* variables for the run (every ROBOT_AGENT_* variable is cleared before every run of a set)
* ``pack`` (``RobotView.use_pack``, Step S02), ``pause_after`` ({skill, nth}: paused and resumed at once)
* robot keys ``skills`` (results of skills the FakeRobot lacks), ``answers_in_order``, ``drops`` ([{nth, to}])
* planner key ``takes_subgoals``: the planner takes ``subgoals`` (as SAGE does), so the loop passes them; each planner call
  records the list it got (``planner_calls[i].subgoals``)

A new cassette carries the ``commit`` it was recorded on (tests/golden/commits/<step>.json lists the allowed ones).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import harness as H
from harness import FORMAT, GOLDEN, ROOT, TESTS, load, load_env, normal, sha  # noqa: F401  (re-exported for the set tests)

SET_CASSETTES = GOLDEN / "set_cassettes"
CAPTURE_SECTIONS = ("planner_io", "llm_kw", "prompts", "trace_rows")
CAPTURES = ("planner_io", "llm_kw", "prompts", "reconcile", "trace")


# ── the stubs: harness.py's, with the set keys ──────────────────────────────────────────────────────────────────────
class CassetteLLM(H.CassetteLLM):
    kw_calls = None                                              # capture ``llm_kw``: [{role, keys, kw}] per call

    def chat(self, prompt="", format=None, **kw):
        role = kw.pop("_golden_role", None)
        if self.kw_calls is not None:
            self.kw_calls.append({"role": role, "keys": sorted(kw), "kw": sha(kw) if kw else None})
        return super().chat(prompt=prompt, format=format, **kw)


class RecordingPlanner(H.RecordingPlanner):
    io = None                                                    # capture ``planner_io``: the inputs of every call

    def generate_plan(self, task, obs, visible_objects):
        out = super().generate_plan(task, obs, visible_objects)
        if self.io is not None:
            self.io.append(_planner_inputs("generate_plan", obs, visible_objects))
        return out

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        out = super().replan(task, completed, failed_step, failure_reason, obs, visible_objects)
        if self.io is not None:
            self.io.append(_planner_inputs("replan", obs, visible_objects))
        return out


class SubgoalPlanner(RecordingPlanner):
    """``planner.takes_subgoals``: ``generate_plan`` / ``replan`` take the keyword ``subgoals`` (base.accepts looks for it)."""

    def generate_plan(self, task, obs, visible_objects, subgoals=None):
        out = super().generate_plan(task, obs, visible_objects)
        self.calls[-1] = dict(self.calls[-1], subgoals=None if subgoals is None else list(subgoals))
        return out

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects, subgoals=None):
        out = super().replan(task, completed, failed_step, failure_reason, obs, visible_objects)
        self.calls[-1] = dict(self.calls[-1], subgoals=None if subgoals is None else list(subgoals))
        return out


class ScenarioRobot(H.ScenarioRobot):
    skills: dict = {}                                            # ``robot.skills``: skill -> result(s) the FakeRobot lacks
    in_order = False                                             # ``robot.answers_in_order``: no automatic yes to a read-back
    drops = ()                                                   # ``robot.drops``: [{nth, to}] the held object falls to ``to``
    pause_after = None

    def execute(self, skill, params, node=None, log_fn=None):
        if skill in self.skills:
            return self._scripted(skill, params)
        return super().execute(skill, params, node, log_fn)

    def _after(self, skill):
        from robot_agent.core import run_control
        super()._after(skill)
        p = self.pause_after                                     # ``pause_after``: paused and resumed at once
        if p and p["skill"] == skill and self.n[skill] == p.get("nth", 1):
            rid = run_control.current_run().run_id
            run_control.manager().pause(rid)
            run_control.manager().resume(rid)

    def _exec(self, skill, o, params):
        if skill == "grasp_succeed" and self.holding:            # ``drops``: at the nth hand check the object is on ``to``
            k = self.n.get("_hand", 0) + 1
            self.n["_hand"] = k
            hit = next((d for d in self.drops if d.get("nth") == k), None)
            if hit:
                self.obj[self.holding], self.holding = hit["to"], None
        if skill == "ask" and self.in_order:                     # every question takes the next answer ("" when none is left)
            self.asked.append(o)
            a = self.answers.pop(0) if self.answers else ""
            return {"isdone": bool(a), "text": a}
        return super()._exec(skill, o, params)

    def _scripted(self, skill, params):
        """A skill the FakeRobot does not have (``robot.skills``): one result, or a list used in turn (the last one repeats)."""
        f = self._fault(skill, (params or {}).get("inputs", ""))   # the scenario's faults apply to these skills too
        if f and f["kind"] == "fail":
            out = {"isdone": False, "msg": f.get("msg", f"{skill} failed")}
            self.calls.append({"skill": skill, "params": params, "result": out})
            self.n[skill] = self.n.get(skill, 0) + 1
            return out
        res = self.skills[skill]
        if isinstance(res, list):
            k = self.n.get(skill, 0)
            res = res[min(k, len(res) - 1)] if res else {"isdone": True}
        out = dict(res)
        dest = out.pop("release_to", None)                       # a hand-over: the held object goes to ``dest``
        if dest and self.holding:
            self.obj[self.holding], self.holding = dest, None
        self.calls.append({"skill": skill, "params": params, "result": out})
        self.n[skill] = self.n.get(skill, 0) + 1
        self._after(skill)
        return out


# ── one run ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def capture_of(sc: dict) -> tuple:
    bad = [c for c in sc.get("capture") or () if c not in CAPTURES]
    if bad:
        raise ValueError(f"{sc.get('name')}: unknown capture {bad} (known: {CAPTURES})")
    return tuple(sc.get("capture") or ())


def _environ(mp, sc):
    """No ROBOT_AGENT_* variable of the calling shell reaches a run; the scenario's ``environ`` is set."""
    import os
    for k in [k for k in os.environ if k.startswith("ROBOT_AGENT_")]:
        mp.delenv(k)
    for k, v in (sc.get("environ") or {}).items():
        if not str(k).startswith("ROBOT_AGENT_"):
            raise ValueError(f"{sc.get('name')}: environ {k!r} is not a ROBOT_AGENT_* variable")
        mp.setenv(k, str(v))


def _install(mp, sc, env, tmp):
    st = H._install(mp, sc, env, tmp)
    if "reconcile" in capture_of(sc):
        import robot_agent.core.planning.assist_loop as AL
        import robot_agent.core.planning.loop as LP
        mp.setattr(AL, "reconcile_world", LP.reconcile_world)    # capture ``reconcile``: the run-start reconcile runs
    return st


def _stubs(sc, env, recorded):
    """harness.py's stubs as the set classes (they add only defaults and methods), with the set robot keys."""
    robot, llm, planner = H._stubs(sc, env, recorded)
    takes = bool((sc.get("planner") or {}).get("takes_subgoals"))
    robot.__class__, llm.__class__ = ScenarioRobot, CassetteLLM
    planner.__class__ = SubgoalPlanner if takes else RecordingPlanner
    rb = sc.get("robot") or {}
    if rb.get("skills"):
        robot.skills = json.loads(json.dumps(rb["skills"]))
    if rb.get("answers_in_order"):
        robot.in_order = True
    if rb.get("drops"):
        robot.drops = [dict(d) for d in rb["drops"]]
    if sc.get("pause_after"):
        robot.pause_after = dict(sc["pause_after"])
    return robot, llm, planner


_loop, _no_network, _fresh_run_state = H._loop, H._no_network, H._fresh_run_state


def run_scenario(sc: dict, recorded: dict = None, max_events: int = 3000) -> dict:
    """harness.run_scenario with the set keys: the five sections plus the captured ones (normalised)."""
    import tempfile

    import pytest
    env = load_env() if sc.get("env") == "sim_house" else dict(sc["env"])
    with tempfile.TemporaryDirectory(prefix="golden_") as tmp, pytest.MonkeyPatch.context() as mp:
        H._no_network(mp)
        _environ(mp, sc)
        st = _install(mp, sc, env, tmp)
        H._fresh_run_state(sc["task"] if sc.get("managed") else None)
        try:
            robot, llm, planner = _stubs(sc, env, recorded)
            _arm_capture(mp, sc, llm, planner)
            loop = H._loop(sc, robot, llm, planner, tmp)
            if sc.get("pack") is not None:
                loop.robot.use_pack(json.loads(json.dumps(sc["pack"])))   # scenario key ``pack`` (RobotView.use_pack: S02)
            evs = H._drain(loop, sc, max_events)
            got = {"llm_calls": llm.calls, "planner_calls": planner.calls, "robot_calls": robot.calls, "events": evs,
                   "final": H._final(robot, st, loop, tmp)}
            got.update(_captured(sc, llm, planner, loop, tmp))
            got = _run_dirs(sc, got, tmp)
            return normal(got, tmp)
        finally:
            H._fresh_run_state()


class _RoleClient:
    """A client the loop builds for a role (understand model, translator, role models): the scenario's stub, the role noted."""

    def __init__(self, llm, cfg):
        self.llm, self.role = llm, str((cfg or {}).get("role") or (cfg or {}).get("model") or "")

    def chat(self, prompt="", format=None, **kw):
        return self.llm.chat(prompt=prompt, format=format, _golden_role=self.role, **kw)

    def chat_guide(self, prompt, guide=None, reuse=False, **kw):
        return self.chat(prompt=prompt, **kw)


def _arm_capture(mp, sc, llm, planner):
    cap = capture_of(sc)
    if "planner_io" in cap:
        planner.io = []
    if "llm_kw" in cap:
        llm.kw_calls = []
        import robot_agent.connect.llm as connect_llm
        mp.setattr(connect_llm, "init_llm_client", lambda cfg=None, **k: _RoleClient(llm, cfg))


def _planner_inputs(call, obs, visible) -> dict:
    return {"call": call, "obs": sha(str(obs)), "obs_head": " ".join(str(obs).split())[:96], "visible": sha(visible),
            "visible_n": len(visible or ())}


def _text_digest(x) -> dict:
    s = x if isinstance(x, str) else json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)
    return {"sha": sha(s), "head": " ".join(s.split())[:96], "chars": len(s)}


def prompts_of(loop, task: str, tmp: str = "") -> dict:
    """The planner-side prompts of the loop's robot (what the harness's planner stub never sees)."""
    from robot_agent.core.planning import direct_prompt, robot_prompt
    out = {"sage_prompt_kwargs": {k: _text_digest(v) for k, v in sorted(robot_prompt.sage_prompt_kwargs(loop.robot).items())}}
    try:
        out["direct_prompt"] = _text_digest(direct_prompt.build_prompt(loop.robot, task))
    except Exception as e:                                       # noqa: BLE001 - recorded as data: a change shows
        out["direct_prompt"] = {"raised": type(e).__name__}
    out["planner_cfg"] = _text_digest(normal(loop._planner_cfg(), tmp))
    return out


def _trace_rows(tmp) -> list:
    rows = []
    for f in sorted(Path(tmp).rglob("trace.jsonl")):
        rows += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return rows


def _captured(sc, llm, planner, loop, tmp) -> dict:
    cap, out = capture_of(sc), {}
    if "planner_io" in cap:
        out["planner_io"] = planner.io
    if "llm_kw" in cap:
        out["llm_kw"] = llm.kw_calls
    if "prompts" in cap:
        out["prompts"] = prompts_of(loop, sc["task"], tmp)
    if "trace" in cap:
        out["trace_rows"] = _trace_rows(tmp)                  # not "trace": a VOLATILE key
    return out


def _run_dirs(sc, got, tmp):
    """Capture ``trace``: the trace file's folder is named by the run id (a clock), replaced by ``<run>``."""
    if "trace" not in capture_of(sc):
        return got
    return json.loads(re.sub(r"\d{8}-\d{6}-\d{3}", "<run>", json.dumps(normal(got, tmp), ensure_ascii=False)))


# ── cassettes of the sets ───────────────────────────────────────────────────────────────────────────────────────────
def cassette_path(name: str) -> Path:
    return SET_CASSETTES / f"{name}.cassette.json"


def cassettes() -> list:
    return sorted(SET_CASSETTES.glob("*.cassette.json"))


def all_cassettes() -> list:
    """(path, the module that replays it): the round-A cassettes with harness.py, then the set cassettes with this module."""
    import sys
    me = sys.modules[__name__]
    return [(p, H) for p in H.cassettes()] + [(p, me) for p in cassettes()]


def record_commit() -> str:
    """The commit a new cassette is recorded on; refuses when robot_agent/ has changes not committed (the cassette would
    name a commit whose code it was not recorded on)."""
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "robot_agent"], cwd=ROOT, capture_output=True, text=True).stdout
    if dirty.strip():
        raise RuntimeError("refuse to record: robot_agent/ has uncommitted changes\n" + dirty)
    return subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def record(sc: dict, tags, about: str) -> Path:
    """Write a NEW set cassette for ``sc`` (two runs must agree first); refuses to overwrite one, here or in cassettes/."""
    path = cassette_path(sc["name"])
    if path.exists() or H.cassette_path(sc["name"]).exists():
        raise FileExistsError(f"refuse to overwrite {path.name}: goldens are never regenerated")
    sc = json.loads(json.dumps(sc))                              # what a replay reads back from the file
    first, second = run_scenario(sc), run_scenario(sc)
    if first != second:
        raise RuntimeError(f"{sc['name']}: two runs differ, the scenario is not deterministic:\n" + diff(first, second))
    cas = {"format": FORMAT, "name": sc["name"], "tags": list(tags), "about": about, "scenario": sc, "recorded": first,
           "commit": record_commit()}
    SET_CASSETTES.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:                 # "x": fails if the file appeared meanwhile
        f.write(H.dump(cas))
    return path


def replay(cas: dict) -> dict:
    return run_scenario(cas["scenario"], recorded=cas["recorded"])


def diff(want: dict, got: dict, limit: int = 120) -> str:
    """harness.diff plus the capture sections (empty: identical)."""
    import difflib
    base = H.diff(want, got, limit)
    out = [base] if base else []
    for s in CAPTURE_SECTIONS:
        a, b = want.get(s), got.get(s)
        if a != b:
            out.append(f"== {s}")
            out += list(difflib.unified_diff(H._lines(a), H._lines(b), "cassette", "replay", n=2, lineterm=""))[2:limit]
    return "\n".join(out)


def check(path) -> str:
    """'' when the set cassette at ``path`` replays identically, else the diff."""
    cas = load(path)
    return diff(cas["recorded"], replay(cas))
