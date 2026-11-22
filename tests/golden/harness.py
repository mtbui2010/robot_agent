"""Golden traces of the REAL AssistLoop: record a scenario once into a cassette, replay it and fail on any difference.

A scenario is plain data (``scenarios.py``; a cassette carries its own copy): a made-up flat (ENV), objects, the robot package
(``fakebot`` / ``fakehome``), faults and answers of the symbolic FakeRobot, the scripted model and planner, the loop's options
and the pack data set on the manifest (``bt_goal`` trees). A run is captured as

* ``llm_calls``      every model call: sha256 of the prompt (and of the JSON format), the prompt's first words, the answer
* ``planner_calls``  every planner call (generate_plan / replan with the failed step and the reason)
* ``robot_calls``    every skill call of the FakeRobot: skill, params, result
* ``events``         every event of ``run_blocking`` without wall-clock values, run ids, the bulky world and audio
* ``final``          the robot's end state, the belief (WorldState), the lab log and the run record's last snapshot

On replay the model answers from the cassette: the answer recorded for the same prompt hash (a prompt never seen falls back to
the scenario's script, so the diff shows its effects too). The robot and the planner are the same deterministic stubs.
A cassette is only written by ``tools/golden_record.py --record``, which refuses to overwrite one that exists.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import socket
import sys
import tempfile
import types
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent
TESTS = GOLDEN.parent
ROOT = TESTS.parent
CASSETTES = GOLDEN / "cassettes"
for _p in (str(ROOT), str(TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fakes import FakeLLM, FakeRobot, FakeSkillRegistry, FakeState, ScriptedPlanner, load_env  # noqa: E402

FORMAT = 1
# robot_agent/observe/replay.py's VOLATILE (clocks, ids, timings, tracebacks) plus a few clocks of its own; the belief (``world``)
# of every event is kept (step by step), its clocks dropped
VOLATILE = {"t", "run_id", "plan_sec", "holding_since", "latency_ms", "trace", "say_audio", "origin", "log_dir", "found_pose_stale",
            "started_at", "ended_at", "ts", "age_s", "elapsed_s", "time", "created_at"}
EPOCH = (1.0e9, 4.0e9)          # a number in this range is a wall-clock time under another key: replaced by "<epoch>"
SECTIONS = ("llm_calls", "planner_calls", "robot_calls", "events", "final")
# make_assist's base (tests/conftest.py) without the paths: a cassette states its options in full on top of this
BASE_OPTIONS = dict(mute_skill_tts=False, speak_backend=False, vlm_enabled=False)


# ── normalising ─────────────────────────────────────────────────────────────────────────────────────────────────────
def sha(x) -> str:
    text = x if isinstance(x, str) else json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def normal(x, tmp: str = ""):
    """``x`` as plain JSON data with no wall-clock value, run id, temp path or memory address (two runs compare equal)."""
    def walk(v):
        if isinstance(v, dict):
            return {str(k): walk(w) for k, w in v.items() if k not in VOLATILE}
        if isinstance(v, (list, tuple, set, frozenset)):
            items = [walk(w) for w in v]
            return sorted(items, key=lambda i: json.dumps(i, sort_keys=True)) if isinstance(v, (set, frozenset)) else items
        if isinstance(v, str):
            s = v.replace(tmp, "<tmp>") if tmp else v
            return re.sub(r" at 0x[0-9a-f]+", " at 0x", s)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and EPOCH[0] < v < EPOCH[1]:
            return "<epoch>"
        if v is None or isinstance(v, (bool, int, float)):
            return v
        if hasattr(v, "value") and isinstance(getattr(v, "value"), (str, int)):
            return v.value                                        # an Enum (FailureCode, Recovery)
        return walk(str(v))
    return json.loads(json.dumps(walk(x), ensure_ascii=False, default=str))


# ── the stubs ───────────────────────────────────────────────────────────────────────────────────────────────────────
class CassetteLLM:
    """The model: answers a prompt whose hash the cassette recorded with the recorded answer; else the scenario's script."""

    def __init__(self, script, default, recorded=None):
        self.script = FakeLLM(*[tuple(s) if isinstance(s, list) else s for s in script], default=default)
        self.recorded = [dict(c) for c in (recorded or [])]
        self.calls: list = []
        self.kw_calls = None                                      # capture ``llm_kw``: [{role, kw}] per call (S01)

    def _recorded_answer(self, h):
        i = len(self.calls)
        if i < len(self.recorded) and self.recorded[i]["prompt"] == h:
            return self.recorded[i]["answer"]
        return next((c["answer"] for c in self.recorded if c["prompt"] == h), None)

    def chat(self, prompt="", format=None, **kw):
        role = kw.pop("_golden_role", None)
        if self.kw_calls is not None:
            self.kw_calls.append({"role": role, "keys": sorted(kw), "kw": sha(kw) if kw else None})
        h = sha(prompt)
        ans = self._recorded_answer(h)
        if ans is None:
            ans = self.script.chat(prompt=prompt, format=format)
        self.calls.append({"prompt": h, "format": sha(format) if format else None, "head": " ".join(str(prompt).split())[:72],
                           "answer": ans})
        return ans

    def chat_guide(self, prompt, guide=None, reuse=False, **kw):
        return self.chat(prompt=prompt, **kw)


class RecordingPlanner(ScriptedPlanner):
    """ScriptedPlanner whose calls are kept as data."""
    io = None                                                    # capture ``planner_io``: the inputs of every call (S01)

    def generate_plan(self, task, obs, visible_objects):
        out = super().generate_plan(task, obs, visible_objects)
        self.calls[-1] = {"call": "generate_plan", "task": task}
        if self.io is not None:
            self.io.append(_planner_inputs("generate_plan", obs, visible_objects))
        return out

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        out = super().replan(task, completed, failed_step, failure_reason, obs, visible_objects)
        self.calls[-1] = {"call": "replan", "failed": dict(failed_step), "reason": failure_reason,
                          "completed": [f"{s.get('action')} {s.get('object')}" for s in completed or ()]}
        if self.io is not None:
            self.io.append(_planner_inputs("replan", obs, visible_objects))
        return out


class ScenarioRobot(FakeRobot):
    """FakeRobot that keeps every call with its result, and can stop the run or say something into it after the n-th call
    of a skill (``cancel_after`` / ``say_after``: ``{"skill", "nth", "text"}``)."""
    skills: dict = {}                                            # scenario ``robot.skills``: skill -> result(s) the FakeRobot lacks (S01)
    in_order = False                                             # scenario ``robot.answers_in_order``: no automatic yes to a read-back
    drops = ()                                                   # scenario ``robot.drops``: [{nth, to}] the held object falls to ``to``
    pause_after = None

    def __init__(self, env, *a, cancel_after=None, say_after=None, **kw):
        super().__init__(env, *a, **kw)
        self.calls, self.cancel_after, self.say_after, self.n = [], cancel_after, say_after, {}

    def execute(self, skill, params, node=None, log_fn=None):
        if skill in self.skills:
            return self._scripted(skill, params)
        try:
            out = super().execute(skill, params, node, log_fn)
        except Exception as e:
            self.calls.append({"skill": skill, "params": params, "raised": str(e)})
            raise
        self.calls.append({"skill": skill, "params": params, "result": out})
        self.n[skill] = self.n.get(skill, 0) + 1
        self._after(skill)
        return out

    def _after(self, skill):
        from robot_agent.core import run_control
        c, s = self.cancel_after, self.say_after
        if c and c["skill"] == skill and self.n[skill] == c.get("nth", 1):
            run_control.request_cancel()
        if s and s["skill"] == skill and self.n[skill] == s.get("nth", 1):
            run_control.manager().inject(run_control.current_run().run_id, s["text"], role="member")
        p = self.pause_after                                     # scenario ``pause_after``: paused and resumed at once (S01)
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
def _no_network(mp):
    """Any TCP connection is a bug here (a robot, a model server): the guard of tests/conftest.py, also outside pytest."""
    def refuse(*a, **k):
        raise AssertionError(f"network access in a golden run: {a[:2]}")
    real_connect = socket.socket.connect

    def connect(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            refuse(addr)
        return real_connect(self, addr)
    mp.setattr(socket, "create_connection", refuse)
    mp.setattr(socket.socket, "connect", connect)


def _fresh_run_state(managed_task=None):
    """No cancel, no run manager, no run left over from another scenario; a managed run when the scenario says something."""
    from robot_agent.core import run_control
    from robot_agent.interact.run_manager import reset_manager
    reset_manager()
    run_control._CANCEL.clear()
    if managed_task is not None:
        run_control.manager().start(managed_task, user_id="golden", role="member")
    run_control.begin_run()


def _install(mp, sc, env, tmp):
    """make_assist's wiring (tests/conftest.py) without pytest: the fake agent state, the ENV, no run-start reconcile."""
    import robot_agent.core.planning.assist_loop as AL
    import robot_agent.core.planning.loop as LP
    import robot_agent.skill_configs as skill_configs
    import robot_agent.state as state_mod
    st = FakeState(tmp)
    st.robot_pkg = sc.get("robot_pkg", "fakebot")
    if sc.get("world"):
        st.world.update_from_dict(sc["world"])
    for m in (AL, LP, state_mod):
        mp.setattr(m, "current", lambda: st)
    for m in (AL, LP):
        mp.setattr(m, "host_id", lambda: "test")
    mp.setattr(skill_configs, "ENV", env)
    mp.setattr(AL, "reconcile_world", lambda node, world, threshold=0.8: world)
    if "reconcile" in capture_of(sc):
        mp.setattr(AL, "reconcile_world", LP.reconcile_world)     # capture ``reconcile``: the run-start reconcile runs (S01)
    if sc.get("health") is not None:
        from robot_agent.core.planning.robot import RobotView
        mp.setattr(RobotView, "health", lambda self, node=None: {"ok": sc["health"]})
    return st


def _loop(sc, robot, llm, planner, tmp):
    import robot_agent.core.planning.assist_loop as AL
    agent = types.SimpleNamespace(skill_registry=FakeSkillRegistry(robot), device_manager=types.SimpleNamespace(_ros_node=None),
                                  _llm_cfg={"name": "fake", "url": None, "model": "scripted"})
    ov = dict(BASE_OPTIONS, log_dir=str(Path(tmp) / "runs"), live_path=str(Path(tmp) / "live.jsonl"),
              memory_path=str(Path(tmp) / "object_memory.json"))
    ov.update(sc.get("options") or {})
    loop = AL.AssistLoop(agent, emit=None, **ov)
    loop._llm_client = llm
    loop._make_planner = lambda: planner
    if sc.get("pack_options"):
        loop.robot.manifest.planner = {**(loop.robot.manifest.planner or {}), "options": json.loads(json.dumps(sc["pack_options"]))}
    return loop


def _stubs(sc, env, recorded):
    rb = sc.get("robot") or {}
    robot = ScenarioRobot(env, rb.get("objects") or {}, answers=rb.get("answers") or (), faults=rb.get("faults") or (),
                          opened=rb.get("opened") or (), start=rb.get("start", "entrance"), reply_rules=rb.get("reply_rules"),
                          cancel_after=sc.get("cancel_after"), say_after=sc.get("say_after"))
    if rb.get("skills"):
        robot.skills = json.loads(json.dumps(rb["skills"]))
    if rb.get("answers_in_order"):
        robot.in_order = True
    if rb.get("drops"):
        robot.drops = [dict(d) for d in rb["drops"]]
    if sc.get("pause_after"):
        robot.pause_after = dict(sc["pause_after"])
    m = sc.get("llm") or {}
    llm = CassetteLLM(m.get("script") or [], m.get("default"), (recorded or {}).get("llm_calls"))
    p = sc.get("planner") or {}
    planner = RecordingPlanner(_steps(p.get("plan") or []), replans=[_steps(r) for r in p.get("replans") or []])
    return robot, llm, planner


def _steps(items):
    """``"MoveTo desk@study room"`` or a step dict -> a PlanStep dict."""
    out = []
    for it in items:
        if isinstance(it, dict):
            out.append(dict(it))
        else:
            a, _, o = str(it).partition(" ")
            out.append({"action": a, "object": o})
    return out


def _drain(loop, sc, max_events):
    evs = []
    kw = {"goals": sc["goals"]} if sc.get("goals") is not None else {}
    for ev in loop.run_blocking(sc["task"], lang=sc.get("lang", "en"), plan_only=bool(sc.get("plan_only")), **kw):
        evs.append(ev)
        if len(evs) > max_events:
            raise AssertionError("run_blocking did not finish (too many events)")
    return evs


def _final(robot, st, loop, tmp):
    rows = []
    for f in sorted((Path(tmp) / "runs").glob("*.jsonl")):
        rows += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    snap = [r_ for r_ in rows if r_.get("kind") == "snapshot"]
    return {"robot": {"at": robot.at, "holding": robot.holding, "objects": robot.obj, "opened": sorted(robot.opened),
                      "asked": robot.asked},
            "belief": st.world.to_dict(), "lab_log": getattr(loop, "lab_log", []),
            "record": snap[-1].get("record") if snap else None, "log_kinds": [r_.get("kind") for r_ in rows]}


def run_scenario(sc: dict, recorded: dict = None, max_events: int = 3000) -> dict:
    """Run ``sc`` through the real AssistLoop and return the five sections (normalised). ``recorded``: the cassette's sections
    (the model answers from it)."""
    import pytest
    env = load_env() if sc.get("env") == "sim_house" else dict(sc["env"])
    with tempfile.TemporaryDirectory(prefix="golden_") as tmp, pytest.MonkeyPatch.context() as mp:
        _no_network(mp)
        _environ(mp, sc)
        st = _install(mp, sc, env, tmp)
        _fresh_run_state(sc["task"] if sc.get("managed") else None)
        try:
            robot, llm, planner = _stubs(sc, env, recorded)
            _arm_capture(mp, sc, llm, planner)
            loop = _loop(sc, robot, llm, planner, tmp)
            if sc.get("pack") is not None:
                loop.robot.use_pack(json.loads(json.dumps(sc["pack"])))   # scenario key ``pack`` (RobotView.use_pack: Step S02)
            evs = _drain(loop, sc, max_events)
            got = {"llm_calls": llm.calls, "planner_calls": planner.calls, "robot_calls": robot.calls, "events": evs,
                   "final": _final(robot, st, loop, tmp)}
            got.update(_captured(sc, llm, planner, loop, tmp))
            got = _run_dirs(sc, got, tmp)
            return normal(got, tmp)
        finally:
            _fresh_run_state()


# ── cassettes ───────────────────────────────────────────────────────────────────────────────────────────────────────
def cassette_path(name: str) -> Path:
    return CASSETTES / f"{name}.cassette.json"


def cassettes() -> list:
    return sorted(CASSETTES.glob("*.cassette.json"))


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(cas: dict) -> str:
    """Keys in their order: the order of a scenario's ENV and objects is part of the behaviour (the places listed in a prompt)."""
    return json.dumps(cas, indent=1, ensure_ascii=False) + "\n"


def record(sc: dict, tags, about: str) -> Path:
    """Write a NEW cassette for ``sc`` (two runs must agree first). Refuses to overwrite a cassette that exists: a golden is only
    added, never regenerated (delete it by hand in a reviewed commit if a change of behaviour is meant)."""
    path = cassette_path(sc["name"])
    if path.exists():
        raise FileExistsError(f"refuse to overwrite {path.name}: goldens are never regenerated")
    sc = json.loads(json.dumps(sc))                              # what a replay reads back from the file
    first, second = run_scenario(sc), run_scenario(sc)
    if first != second:
        raise RuntimeError(f"{sc['name']}: two runs differ, the scenario is not deterministic:\n" + diff(first, second))
    cas = {"format": FORMAT, "name": sc["name"], "tags": list(tags), "about": about, "scenario": sc, "recorded": first}
    cas["commit"] = record_commit()                              # the code it was recorded on (tests/golden/base_commits.json)
    CASSETTES.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:                 # "x": fails if the file appeared meanwhile
        f.write(dump(cas))
    return path


def replay(cas: dict) -> dict:
    """The cassette's scenario run again, the model answering from the cassette."""
    return run_scenario(cas["scenario"], recorded=cas["recorded"])


def diff(want: dict, got: dict, limit: int = 120) -> str:
    """A readable diff per section (empty: identical)."""
    out = []
    for s in SECTIONS:
        a, b = want.get(s), got.get(s)
        if a == b:
            continue
        if s in ("events", "llm_calls", "planner_calls", "robot_calls") and isinstance(a, list) and isinstance(b, list):
            out.append(f"== {s}: {len(a)} recorded, {len(b)} now; first difference at item {_first_diff(a, b)}")
        else:
            out.append(f"== {s}")
        lines = difflib.unified_diff(_lines(a), _lines(b), "cassette", "replay", n=2, lineterm="")
        out += list(lines)[2:limit]
    for s in CAPTURE_SECTIONS:
        a, b = want.get(s), got.get(s)
        if a != b:
            out.append(f"== {s}")
            out += list(difflib.unified_diff(_lines(a), _lines(b), "cassette", "replay", n=2, lineterm=""))[2:limit]
    return "\n".join(out)


def _first_diff(a, b) -> int:
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def _lines(x) -> list:
    if isinstance(x, list):          # one item per line: the diff names the event / call that changed
        return [json.dumps(i, sort_keys=True, ensure_ascii=False) for i in x]
    return json.dumps(x, indent=1, sort_keys=True, ensure_ascii=False).splitlines()


def check(path) -> str:
    """'' when the cassette at ``path`` replays identically, else the diff."""
    cas = load(path)
    return diff(cas["recorded"], replay(cas))


# ── S01: capture sections, environment, commit (opt-in per scenario: the old cassettes replay as they are) ────────────────
# A scenario key ``capture`` (a list) adds sections to a NEW cassette; ``FORMAT`` stays 1.
#   planner_io  the hash and head of the observation and of the visible list of every planner call
#   llm_kw      the options (keys, hash) and the role of every model call; role clients are served by the same stub
#   prompts     hash and head of robot_prompt.sage_prompt_kwargs(robot), of direct_prompt.build_prompt and of _planner_cfg()
#   reconcile   the run-start reconcile is NOT stubbed (loop.reconcile_world runs against the FakeRobot)
#   trace       the rows of option ``trace``'s file (the order of the hooks it watches), section ``trace_rows``
# ``environ``: ROBOT_AGENT_* variables for the run (every ROBOT_AGENT_* variable is cleared before every run).
CAPTURE_SECTIONS = ("planner_io", "llm_kw", "prompts", "trace_rows")
CAPTURES = ("planner_io", "llm_kw", "prompts", "reconcile", "trace")


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


def record_commit() -> str:
    """The commit a new cassette is recorded on; refuses when robot_agent/ has changes not committed (the cassette would
    name a commit whose code it was not recorded on)."""
    import subprocess
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "robot_agent"], cwd=ROOT, capture_output=True, text=True).stdout
    if dirty.strip():
        raise RuntimeError("refuse to record: robot_agent/ has uncommitted changes\n" + dirty)
    return subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
