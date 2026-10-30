"""ClosedLoop — the planner-agnostic NL -> plan -> exec -> check -> replan driver.

Ties together the planning-layer pieces and streams events (same schema the
frontend already consumes from ``UnifiedAgent.run``, plus ``action/object/
verifies/say``). It is selected from ``UnifiedAgent.run`` behind a config gate;
the legacy open-loop path remains the default fallback.

Per-step it: binds a GRACE step to a skill of the robot (``RobotView.bind``, the
robot's ``RobotAdapter`` from ``robot_agent.contract.load_adapter``), executes it
(``SkillRegistry.execute``, normalised by ``run_skill``), verifies the result in
layers (``StepVerifier``: isdone -> symbolic effects -> VLM -> the skill's sensor
VerifySpecs), announces milestones (``Announcer`` — robot speaker + ``say`` for
the dashboard), records everything (``TaskRecord`` + JSONL ``RunLogger``), and on
failure asks the planner to ``replan`` a suffix (bounded by ``max_replans``)
unless the recovery policy says to stop (``contract.recovery``, by FailureCode).

Runtime code (not a workflow/pyplanner library) — ``time.time()`` is allowed
here and is the source of run-ids/timestamps.

See docs/CLOSED_LOOP_ARCHITECTURE.md and docs/TRACKING_VERIFY_VOICE.md.
"""

from __future__ import annotations

import dataclasses
import importlib
import os
import time
import traceback
from pathlib import Path
from typing import Iterator, Optional

from robot_agent.observe.paths import data_dir
from robot_agent.contract import classify_legacy_failure
from robot_agent.state import current, host_id
from .announcer import Announcer
from .base import WorldState
from .mapper import ActionMapper, Unmappable
from .records import RunLogger, StepRecord, VerifyResult, new_task_record
from .registry import get_planner
from .robot import RobotView, current_robot, run_skill
from .verify import StepVerifier, verdict


def _vr_list(results: list[VerifyResult]) -> list[dict]:
    return [dataclasses.asdict(r) for r in results]


def _robot_namemap():
    """Best-effort import of the active robot's ``configs.grace_namemap`` module
    (the duck-typed config that already owns robot-specific knowledge, e.g.
    ``observe``/name maps). Returns ``None`` if unavailable so callers can fall
    back to the generic path. Resolved via ``current().robot_pkg`` so this works
    from both the closed loop and the open-loop / direct paths.
    """
    try:
        import importlib
        from robot_agent.state import current
        return importlib.import_module(f"{current().robot_pkg}.configs.grace_namemap")
    except Exception:
        return None


def _read_robot_xy(node, agent=None):
    """Best-effort (x, y) of the mobile base from the robot's base-pose agent
    (``agent``; default: the running robot's pose sensor, the skill that
    verifies its MoveTo in the manifest).

    Robust to the payload shapes seen in the wild: a flat ``{'x','y'}`` dict, or
    ``{'pose': <...>}`` where the value is a ROS Pose (``.position.x``), a list
    ``[x, y, ...]``, or a ``{'x','y'}`` dict (a ``decode_func`` may return
    ``{'pose': <geometry_msgs Pose>, 'isdone': True}``, so the pose is
    extracted here). Returns ``None`` if unavailable.
    """
    if agent is None:
        robot = current_robot()
        agent = robot.pose_sensor() if robot is not None else None
        if agent is None:
            return None
    try:
        agents = node.agents
        if agent not in agents:
            return None
        ret = agents[agent].get()
    except Exception:
        return None
    if not ret:
        return None
    if isinstance(ret, dict) and 'x' in ret and 'y' in ret:
        return float(ret['x']), float(ret['y'])
    pose = ret.get('pose') if isinstance(ret, dict) else None
    if pose is None:
        return None
    pos = getattr(pose, 'position', None)
    if pos is not None and hasattr(pos, 'x'):
        return float(pos.x), float(pos.y)
    if isinstance(pose, (list, tuple)) and len(pose) >= 2:
        return float(pose[0]), float(pose[1])
    if isinstance(pose, dict) and 'x' in pose and 'y' in pose:
        return float(pose['x']), float(pose['y'])
    return None


def reconcile_world(node, world, threshold=0.8):
    """Sync the persisted WorldState against ground truth before planning.

    **Hybrid (robot-overridable) strategy** — keeps robot-specific knowledge in
    the robot's ``grace_namemap`` config per the project's namemap-hook design
    (``mapper.py`` docstring), while still working out-of-the-box for any robot
    that has a base-pose agent (its manifest's MoveTo sensor) + ENV ``loc``:

      1. If ``grace_namemap`` defines ``reconcile_world(node, world)`` → it owns
         reconciliation entirely (e.g. can also read a real gripper sensor for
         ``holding``). Core does nothing else.
      2. Else if it defines ``robot_xy(node) -> (x, y) | None`` → core uses that
         pose (robot-specific reader) and does the generic ENV match below.
      3. Else → fully generic: ``_read_robot_xy`` (the pose agent named by
         the manifest) + nearest-ENV-``loc`` match.

    Only ``arrived`` is sensor-derived here: the robot's localization is matched
    against the nearest ENV location's ``loc`` (within ``threshold`` metres; a
    bit generous to absorb the approach offset the ``move`` skill leaves the base
    at). ``holding`` has no gripper width/force sensor on this robot, so it stays
    a belief carried over from the previous run (with its ``holding_since``
    timestamp); ``opened``/``on`` likewise stay beliefs. (A robot with a gripper
    sensor can override all of this via the level-1 hook.)
    Best-effort: any failure leaves the prior belief untouched.
    """
    namemap = _robot_namemap()

    # (1) Full robot-owned override.
    if namemap is not None and callable(getattr(namemap, "reconcile_world", None)):
        try:
            namemap.reconcile_world(node, world)
        except Exception:
            pass
        return world

    try:
        from robot_agent.skill_configs import ENV

        # (2) Robot-specific pose reader hook, else (3) built-in generic reader.
        xy = None
        if namemap is not None and callable(getattr(namemap, "robot_xy", None)):
            try:
                xy = namemap.robot_xy(node)
            except Exception:
                xy = None
        if xy is None:
            xy = _read_robot_xy(node)
        if xy is None:
            return world

        x, y = xy
        best, best_d = None, float('inf')
        for name, spec in ENV.items():
            loc = spec.get('loc') if isinstance(spec, dict) else None
            if not (isinstance(loc, dict) and 'x' in loc and 'y' in loc):
                continue
            d = ((float(loc['x']) - x) ** 2 + (float(loc['y']) - y) ** 2) ** 0.5
            if d < best_d:
                best, best_d = name, d
        if best is not None and best_d <= threshold:
            world.arrived = best
    except Exception:
        pass
    return world


def _manager_run_id() -> Optional[str]:
    """The run_id of the RunManager run the calling thread acts for, or None."""
    try:
        from ..run_control import current_run
        run = current_run()
        return run.run_id if run is not None and run.active else None
    except Exception:
        return None


# step outcomes of the phase methods (generators return one of these)
_NEXT, _FAIL, _STOP, _DONE = "next", "fail", "stop", "done"


class RunContext:
    """One run's state, shared by the phase methods of a loop (ClosedLoop / AssistLoop): the
    robot, belief, record + logger, the plan and the cursor in it."""

    def __init__(self, loop, task: str, lang: str, plan_only: bool, planner_name: str):
        self.task, self.lang, self.plan_only = task, lang, plan_only
        self.robot = loop.robot
        self.announcer = loop._announcer(lang)
        self.world = current().world
        self.node = getattr(loop.dm, "_ros_node", None)
        self.muted = False
        now = time.time()
        # one id per run: the RunManager run this executive acts for (events, RunLogger file and RunRecord
        # agree, GET /runs/<id> finds it); a run started outside the RunManager gets a time-based one
        self.run_id = _manager_run_id() or (time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
                                            + f"-{int(now * 1000) % 1000:03d}")
        self.run = new_task_record(self.run_id, task, lang=lang, planner=planner_name, started_at=now)
        self.logger = RunLogger(self.run, loop.cfg["log_dir"] / f"{self.run_id}.jsonl")
        self.planner = None
        self.obs, self.visible = "", []
        self.steps: list = []
        self.completed: list = []
        self.i = 0
        self.replans = 0
        # the failure that decided the run (FailureCode, device, evidence): on the `done` event
        self.failure: dict = {}

    def failed_with(self, code, device: Optional[str] = None, evidence=None, step: Optional[dict] = None) -> None:
        """Remember the deciding failure (the last one wins: it is the one the run ends on)."""
        c = getattr(code, "value", code)
        self.failure = {k: v for k, v in (("code", c), ("device", device), ("evidence", evidence),
                                          ("failed_step", step)) if v not in (None, "", [], {})}
        self.run.failure = dict(self.failure)

    def failure_fields(self) -> dict:
        """The `done` fields of a failed run: code / device / evidence and a msg saying why."""
        f = dict(self.failure)
        st = f.get("failed_step") or {}
        if f.get("code"):
            f.setdefault("msg", f"{st.get('action', '')} {st.get('object', '')} failed ({f['code']}): "
                                f"{f.get('evidence') or ''}".strip())
        return f

    def ev(self, event: str, say: Optional[str] = None, **fields) -> dict:
        """An event to stream, logged to the run's JSONL."""
        d = {"event": event, **fields}
        if say is not None:
            d["say"] = say
        try:
            self.logger.event(event, t=time.time(), **{k: v for k, v in d.items() if k != "event"})
        except Exception:
            pass
        return d


class ClosedLoop:
    """NL -> plan -> exec -> check -> replan driver.

    Parameters
    ----------
    agent:
        The :class:`UnifiedAgent` (provides ``skill_registry``,
        ``device_manager``, ``_llm_cfg``).
    **overrides:
        Optional config overrides (``planner``, ``vlm_enabled``,
        ``speak_backend``, ``mute_skill_tts``, ``max_replans``, ``log_dir``,
        ``live_path``). Anything not overridden is resolved from env vars /
        sensible defaults in :meth:`_resolve_cfg`.
    """

    def __init__(self, agent, emit=None, **overrides) -> None:
        self.agent = agent
        # Optional direct emit channel (the async-queue pusher from
        # UnifiedAgent.run). Used to stream live planner progress that fires
        # *during* the blocking generate_plan call, when the generator cannot
        # yield. None → no live progress (post-hoc plan_meta still ships).
        self._emit = emit
        self.sr = agent.skill_registry
        self.dm = agent.device_manager
        # Resolve the LLM config the planner runs on: explicit /agent/llm-config
        # if set, else the active 'llm' connection (so picking an LLM in the
        # dashboard drives planning and survives a restart).
        self._llm_cfg: dict = (
            agent.effective_llm_cfg() if hasattr(agent, "effective_llm_cfg")
            else (getattr(agent, "_llm_cfg", {}) or {})
        )
        st = current()
        self.robot_pkg: str = st.robot_pkg
        # the robot, through its adapter only (robot_agent.contract.load_adapter)
        self.robot = RobotView.load(self.robot_pkg)
        # the namemap module behind a namemap-shim adapter (kept for callers that read it)
        self._namemap = getattr(self.robot.adapter, "namemap", None)
        self.cfg = self._resolve_cfg(overrides, st)

    @property
    def robot(self) -> RobotView:
        """The robot (its RobotAdapter). A loop made without ``__init__`` that only set
        ``_namemap`` (tests, tools) gets that namemap wrapped in the contract's shim."""
        r = self.__dict__.get("_robot")
        if r is None:
            nm = self.__dict__.get("_namemap")
            if nm is not None:
                from robot_agent.contract import NamemapAdapter
                r = RobotView(NamemapAdapter(nm))
            else:
                r = RobotView.load(self.robot_pkg)
            self._robot = r
        return r

    @robot.setter
    def robot(self, value: RobotView) -> None:
        self._robot = value

    # ── config ───────────────────────────────────────────────────────────
    def _resolve_cfg(self, o: dict, st) -> dict:
        env = os.environ.get
        common = Path(getattr(st, "common_dir", "."))
        return {
            "planner": o.get("planner") or env("ROBOT_AGENT_PLANNER", "grace"),
            "vlm_enabled": o.get(
                "vlm_enabled", env("ROBOT_AGENT_VERIFY_VLM", "0") == "1"
            ),
            # Speech is played by the dashboard (it receives `say` on every
            # event), so the robot's own speaker stays silent by default. Set
            # ROBOT_AGENT_VOICE_BACKEND=1 to also speak on the robot.
            "speak_backend": o.get(
                "speak_backend", env("ROBOT_AGENT_VOICE_BACKEND", "0") != "0"
            ),
            "mute_skill_tts": o.get("mute_skill_tts", True),
            "max_replans": int(o.get("max_replans", env("ROBOT_AGENT_MAX_REPLANS", "3"))),
            "host": self._llm_cfg.get("url") or self._llm_cfg.get("host"),
            "model": self._llm_cfg.get("model"),
            # Per machine (common/ is shared by every robot using this folder).
            # observe.paths: ROBOT_AGENT_DATA_DIR moves them; unset = common/ as before
            "log_dir": Path(o.get("log_dir") or data_dir("task_runs", common, host_id())),
            "live_path": str(o.get("live_path") or data_dir("grace_memory", common, host_id())),
        }

    def _make_planner(self):
        name = self.cfg["planner"]
        if name == "grace":
            return get_planner(
                "grace",
                host=self.cfg.get("host"),
                model=self.cfg.get("model"),
                live_path=self.cfg.get("live_path"),
                safe_refine=True,
                # the robot's body (manifest): SAGE's gate plans e.g. the MoveTo before an Open
                capabilities=self.robot.capabilities(),
            )
        if name == "llm_direct":
            return get_planner(
                "llm_direct", llm_cfg=self._llm_cfg, robot_pkg=self.robot_pkg
            )
        return get_planner(name)

    # ── helpers ──────────────────────────────────────────────────────────
    def _arg_name(self, action: str, obj: str) -> str:
        """Localized-ish argument name for announcements."""
        return self.robot.name_for(action, obj)

    def _say(self, step: dict, action: str, obj: str) -> dict:
        """Announcer kwargs for a step: the spoken names the plan gave
        (``이동->move::식탁 앞->table_top`` → step["say"]) win over the
        name map."""
        from ...spoken import said_of
        say = step.get("say") or {}
        out = {"action": action,
               "object": say.get("object") or self._arg_name(action, said_of(obj))}
        if say.get("action"):
            out["verb"] = say["action"]
        return out

    def _build_params(self, action: str, obj: str, world, mapped_params: dict) -> dict:
        """Legacy: ``mapped_params`` + the namemap's ``build_params`` (``RobotView.bind``
        already includes them)."""
        nm = self._namemap
        params = dict(mapped_params or {})
        if nm is not None and hasattr(nm, "build_params"):
            try:
                extra = nm.build_params(action, obj, world)
                if extra:
                    params.update(extra)
            except Exception:
                pass
        return params

    def _plan_text(self, steps, meta, mapper, world) -> str:
        """Render the plan in the ``skill::inputs`` format the robot
        actually runs (e.g. ``move::living room``), not GRACE CamelCase. The
        direct planner already produced that string (``meta['raw']``); for GRACE
        we map each step through the ActionMapper, threading a world copy so
        ``Pick`` etc. resolve. Best-effort — never raises."""
        raw = meta.get("raw") if isinstance(meta, dict) else None
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        lines = []
        w = world.copy()
        for st in steps:
            action = (st.get("action") or "").strip()
            obj = (st.get("object") or "").strip()
            try:
                mapped = mapper.to_skill(st, w)
                if mapped is None:                       # no-op action
                    lines.append(f"# {action}")
                else:
                    skill, params = mapped
                    inp = params.get("inputs", "") if isinstance(params, dict) else ""
                    lines.append(f"{skill}::{inp}" if inp != "" else skill)
                mapper.apply_effect(st, w)               # advance world for next step
            except Exception:
                lines.append(f"{action}::{obj}" if obj else action)
        return "\n".join(lines)

    # ── main loop ────────────────────────────────────────────────────────
    def _verifier(self, node, env=None) -> StepVerifier:
        """The layered post-step verifier for this robot: its VLM hook, its manifest's
        VerifySpecs (sensors run through ``run_skill``), places located on ``env`` (default:
        the live ENV)."""
        def locate(key):
            if env is None:
                from robot_agent.skill_configs import ENV as e
            else:
                e = env
            l = ((e or {}).get(key) or {}).get("loc") or {}
            return (float(l["x"]), float(l["y"])) if "x" in l and "y" in l else None
        robot = self.robot
        return StepVerifier(self.cfg["vlm_enabled"], robot.vlm_actions, robot.vlm_hook, robot=robot,
                            sensor=lambda skill: run_skill(self.sr, skill, {}, node, raise_errors=True),
                            locate=locate)

    def _announcer(self, lang: str) -> Announcer:
        return Announcer(lang=lang, speak_backend=self.cfg["speak_backend"], i18n=self.robot.i18n,
                         quiet=self.robot.quiet_skills)

    # ── one run ──────────────────────────────────────────────────────────
    def _open_run(self, task: str, lang: str, plan_only: bool, planner_name: str) -> "RunContext":
        return RunContext(self, task, lang, plan_only, planner_name)

    def _mute(self, r: "RunContext") -> None:
        """Mute skill-level TTS so the plan-level announcer owns milestone speech."""
        if self.cfg["mute_skill_tts"]:
            try:
                from robot_agent.utils import set_skill_tts_muted
                set_skill_tts_muted(True)
                r.muted = True
            except Exception:
                pass

    def _close_run(self, r: "RunContext") -> None:
        if r.muted:
            try:
                from robot_agent.utils import set_skill_tts_muted
                set_skill_tts_muted(False)
            except Exception:
                pass
        try:
            r.logger.snapshot()
        except Exception:
            pass

    def _reconcile(self, r: "RunContext", when: str) -> Optional[dict]:
        """The belief brought in line with the robot (``RobotView.reconcile``: the adapter's reconcile,
        then the declared sensors through the skill registry). The ``reconcile`` event when it changed."""
        node = r.node
        changed = r.robot.reconcile(r.world, node,
                                    sense=lambda skill: run_skill(self.sr, skill, {}, node, raise_errors=True))
        if not changed:
            return None
        try:
            current().save_world()
        except Exception:
            pass
        return r.ev("reconcile", when=when, changed=changed, world=r.world.to_dict())

    def _pause_point(self, r: "RunContext"):
        """Hold while the run is paused; after a real pause the belief is reconciled (things may have
        moved meanwhile). Yields the ``reconcile`` event; returns the seconds paused (0: no pause)."""
        from ..run_control import pause_point
        paused: list = []
        pause_point(on_resume=paused.append)
        if not paused:
            return 0.0
        fixed = self._reconcile(r, "resume")
        if fixed is not None:
            yield fixed
        return paused[0]

    @staticmethod
    def _crashed(r: "RunContext", e: Exception) -> dict:
        r.run.status = "aborted"
        r.run.ended_at = time.time()
        return {"event": "error", "msg": str(e), "trace": traceback.format_exc()}

    def _hook_progress(self, planner) -> None:
        """Stream live multi-step progress (decompose/expand/refine) for planners exposing a
        `progress` hook (GRACE). Best-effort."""
        try:
            inner = getattr(planner, "_p", planner)
            if self._emit is not None and hasattr(inner, "progress"):
                _emit = self._emit

                def _on_progress(d):
                    try:
                        _emit({"event": "plan_step", **d})
                    except Exception:
                        pass
                inner.progress = _on_progress
        except Exception:
            pass

    def _log_fn(self, i: int):
        """The per-step emitter of the open-loop path: images, datasets and an HRI skill's speak /
        listen reach the dashboard. `say` stays off: the announcer owns narration here, the same
        reason skill TTS is muted."""
        if self._emit is None:
            return None
        try:
            from ..unified_agent import _make_log_fn
            return _make_log_fn(self._emit, i, allow_say=False)
        except Exception:
            return None

    # ── main loop ────────────────────────────────────────────────────────
    def run_blocking(self, task: str, lang: str = "en",
                     plan_only: bool = False) -> Iterator[dict]:
        r = self._open_run(task, lang, plan_only, self.cfg["planner"])
        r.mapper = ActionMapper(r.robot)
        r.verifier = self._verifier(r.node)
        fixed = self._reconcile(r, "start")
        self._mute(r)
        try:
            yield r.ev("task_start", say=r.announcer.announce("task_start"),
                       task=task, run_id=r.run_id, world=r.world.to_dict())
            if fixed is not None:
                yield fixed
            if (yield from self._cl_plan(r)):
                return
            if (yield from self._cl_execute(r)):
                return
            yield from self._cl_finish(r)
        except Exception as e:
            yield self._crashed(r, e)
        finally:
            self._close_run(r)

    def _cl_plan(self, r: "RunContext"):
        """Ground (observe + belief), plan, screen. True when the run ended (plan only)."""
        obs, visible = r.robot.observe(r.node)
        # Feed the persisted world summary to the planner so the LLM knows
        # current holding/location.
        try:
            obs = (obs + "\n" if obs else "") + "Robot state: " + r.world.as_text()
        except Exception:
            pass
        r.obs, r.visible = obs, list(visible)
        r.run.obs, r.run.visible = obs, list(visible)

        yield r.ev("status", msg="Generating task plan...")
        r.planner = self._make_planner()
        self._hook_progress(r.planner)
        try:
            steps, meta = r.planner.generate_plan(r.task, obs, visible)
        except Exception as e:
            from ..run_control import is_cancel
            if is_cancel(e):
                raise
            yield self._planner_failed(r, e)
            return True
        r.steps = list(steps)
        r.run.plan_meta = meta or {}
        warnings = r.mapper.screen(r.steps)
        r.run.warnings = warnings
        r.run.status = "running"
        plan_text = self._plan_text(r.steps, r.run.plan_meta, r.mapper, r.world)
        yield r.ev("plan", steps=r.steps, plan_meta=r.run.plan_meta, warnings=warnings,
                   plan_text=plan_text, world=r.world.to_dict())
        for w in warnings:
            yield r.ev("warning", msg=w)

        # Plan-only mode: stop after generating the plan (no execution).
        if r.plan_only:
            r.run.status = "planned"
            r.run.ended_at = time.time()
            yield r.ev("done", status="planned", run_id=r.run_id,
                       say=r.announcer.announce("plan_ready"), world=r.world.to_dict())
            return True
        return False

    @staticmethod
    def _planner_failed(r: "RunContext", e: Exception) -> dict:
        """The planner (its LLM) raised: `done failed` with a FailureCode and a spoken reason, not an `error`."""
        from .understand import LLMUnavailable
        u = LLMUnavailable(e, "plan")
        r.failed_with(u.code, "planner", {"call": "generate_plan", "error": str(e)[:300]})
        r.run.status = "failed"
        r.run.ended_at = time.time()
        return r.ev("done", status="failed", run_id=r.run_id, **dict(r.failure_fields(), msg=u.message("planner")),
                    say=r.announcer.announce("llm_down" if u.unreachable else "done_fail"), world=r.world.to_dict())

    def _aborted(self, r: "RunContext", rec: Optional[StepRecord] = None) -> dict:
        """End the run on a cancel — no verify, no replan."""
        if rec is not None:
            rec.status = "aborted"
            rec.ended_at = time.time()
            r.run.steps.append(rec)
        r.run.status = "aborted"
        r.run.ended_at = time.time()
        return r.ev("done", status="aborted", run_id=r.run_id,
                    msg="Cancelled by user", world=r.world.to_dict())

    def _cl_execute(self, r: "RunContext"):
        """Run the plan step by step. True when the run ended (cancelled)."""
        from ..run_control import cancel_requested
        while r.i < len(r.steps):
            yield from self._pause_point(r)     # a paused run holds here, between two steps
            if cancel_requested():
                yield self._aborted(r)
                return True
            yield from self._cl_pending(r)
            out = yield from self._cl_step(r, r.steps[r.i])
            if out is _STOP:
                return True
            if out is _FAIL:
                r.run.status = "failed"
                break
        return False

    def _cl_pending(self, r: "RunContext"):
        """MODIFY / ADD / DROP / NEW said during the run (Run.pending): this loop plans from the task
        text and has no goals to edit (AssistLoop applies them), so it says the change was not applied
        instead of losing it silently."""
        from ..run_control import current_run
        run = current_run()
        for e in (run.take_pending() if run is not None else []):
            text = str(e.get("text") or "")
            yield r.ev("goal_change", applied=False, intent=e.get("intent"), text=text,
                       say=f"I heard '{text}', but I cannot change the running task. I will finish it first; "
                           f"please tell me again afterwards.")

    def _cl_step(self, r: "RunContext", step: dict):
        """Map, execute and check one step. Returns _NEXT, _FAIL or _STOP."""
        from ..run_control import cancel_requested
        action, obj, started = step.get("action", ""), step.get("object", ""), time.time()
        try:
            mapped = r.mapper.to_skill(step, r.world)
        except Unmappable as e:
            return (yield from self._cl_unmappable(r, step, started, str(e)))
        if mapped is None:                                   # no-op
            r.run.steps.append(StepRecord(index=r.i, action=action, object=obj, skill=None,
                                          status="success", started_at=started, ended_at=time.time(),
                                          verifies=[VerifyResult("isdone", True, "no-op")]))
            r.completed.append(step)
            yield r.ev("step_done", index=r.i, status="success", skill=None, world=r.world.to_dict())
            r.i += 1
            return _NEXT

        skill_name, mapped_params = mapped
        params = dict(mapped_params or {})        # bind() = the robot's names + build_params
        rec = StepRecord(index=r.i, action=action, object=obj, skill=skill_name,
                         params=params, status="running", started_at=started)
        yield r.ev("step_start", index=r.i, action=action, object=obj, skill=skill_name,
                   say=r.announcer.announce("step_start", **self._say(step, action, obj)))
        res = run_skill(self.sr, skill_name, params, r.node, log_fn=self._log_fn(r.i),
                        interruptible=r.robot.interruptible(skill_name))
        rec.result = res.to_legacy()
        # A cancelled skill fails like any other, but replanning around
        # it would start the robot moving again — stop instead.
        if cancel_requested():
            yield self._aborted(r, rec)
            return _STOP

        # ── Check (layered) ──
        vres = r.verifier.verify(step, res, r.world, r.node, params=params)
        rec.verifies = vres
        ok, reason = verdict(vres)
        rec.ended_at = time.time()
        yield r.ev("step_verify", index=r.i, verifies=_vr_list(vres))
        if ok:
            yield from self._cl_succeeded(r, step, rec)
            return _NEXT
        return (yield from self._cl_failed(r, step, rec, res, reason))

    def _cl_unmappable(self, r: "RunContext", step: dict, started: float, reason: str):
        action, obj = step.get("action", ""), step.get("object", "")
        r.run.steps.append(StepRecord(index=r.i, action=action, object=obj, skill=None,
                                      status="failed", started_at=started, ended_at=time.time(),
                                      verifies=[VerifyResult("isdone", False, reason)]))
        yield r.ev("step_done", index=r.i, status="failed",
                   say=r.announcer.announce("step_fail", **self._say(step, action, obj), reason=reason),
                   reason=reason, world=r.world.to_dict())
        ok_to_replan, r.steps, r.i, r.replans = self._maybe_replan(
            r.planner, r.task, r.completed, step, reason, r.obs, r.visible, r.run, r.replans, r.announcer)
        if not ok_to_replan:
            return _FAIL
        for e2 in self._drain_replan_event(reason, r.announcer, r.i, r.world):
            yield e2
        return _NEXT

    def _cl_succeeded(self, r: "RunContext", step: dict, rec: StepRecord):
        action, obj = step.get("action", ""), step.get("object", "")
        rec.status = "success"
        r.mapper.apply_effect(step, r.world)
        # stamp/clear the grasp clock (no gripper sensor → belief + timestamp)
        if action == "Pick":
            r.world.holding_since = time.time()
        elif action in ("Place", "PutIn"):
            r.world.holding_since = None
        try:
            current().save_world()   # persist belief after each effect
        except Exception:
            pass
        r.completed.append(step)
        r.run.steps.append(rec)
        yield r.ev("step_done", index=r.i, status="success", result=rec.result,
                   say=r.announcer.announce("step_success", **self._say(step, action, obj)),
                   world=r.world.to_dict())
        r.i += 1

    def _cl_failed(self, r: "RunContext", step: dict, rec: StepRecord, res, reason: str):
        """A failed step: stop when the recovery policy forbids a retry, else replan. _NEXT / _FAIL."""
        action, obj = step.get("action", ""), step.get("object", "")
        rec.status = "failed"
        r.run.steps.append(rec)
        yield r.ev("step_done", index=r.i, status="failed", result=rec.result, reason=reason,
                   say=r.announcer.announce("step_fail", **self._say(step, action, obj), reason=reason),
                   world=r.world.to_dict())
        code = r.robot.failure_code(res, reason)
        r.failed_with(code, res.device or rec.skill, reason, {"action": action, "object": obj})
        if r.robot.stops_now(code):
            # e-stop / cancelled / a skill bug: a replan cannot help (recovery policy)
            yield r.ev("recover", at_index=r.i, kind="stop", code=code.value,
                       msg=f"{rec.skill} failed ({code.value}), not retried: {reason}")
            return _FAIL
        # fresh observation before replanning
        r.obs, r.visible = r.robot.observe(r.node)
        ok_to_replan, r.steps, r.i, r.replans = self._maybe_replan(
            r.planner, r.task, r.completed, step, reason, r.obs, r.visible, r.run, r.replans, r.announcer)
        if not ok_to_replan:
            return _FAIL
        yield r.ev("replan", at_index=r.i, reason=reason,
                   say=r.announcer.announce("replan"), world=r.world.to_dict())
        return _NEXT

    def _cl_finish(self, r: "RunContext"):
        if r.run.status != "failed":
            r.run.status = "success"
        r.run.ended_at = time.time()
        # Memory loop: record a fully successful episode (best-effort).
        if r.run.status == "success":
            self._record_episode(r.planner, r.task, r.completed)
        kind = "done_success" if r.run.status == "success" else "done_fail"
        yield r.ev("done", status=r.run.status, run_id=r.run_id, say=r.announcer.announce(kind),
                   world=r.world.to_dict(), **(r.failure_fields() if r.run.status == "failed" else {}))

    @staticmethod
    def _record_episode(planner, task: str, completed: list) -> None:
        try:
            rec_fn = getattr(planner, "record_episode", None)
            if callable(rec_fn):
                rec_fn(task, completed)
        except Exception:
            pass

    # ── replan bookkeeping ───────────────────────────────────────────────
    def _maybe_replan(self, planner, task, completed, failed_step, reason,
                      obs, visible, run, replans, announcer):
        """Run a bounded replan; splice the suffix after the completed prefix.

        Returns ``(ok, steps, i, replans)`` — ``ok=False`` means the replan
        budget is exhausted and the caller should abort.
        """
        if replans >= self.cfg["max_replans"]:
            return False, [], 0, replans
        replans += 1
        try:
            suffix, _meta = planner.replan(task, completed, failed_step, reason, obs, visible)
            suffix = list(suffix)
        except Exception as e:
            suffix = []
            reason = f"{reason}; replan failed: {e}"
        run.replans.append({
            "at_index": len(completed), "failed": failed_step,
            "reason": reason, "suffix_len": len(suffix),
        })
        steps = list(completed) + suffix
        return True, steps, len(completed), replans

    def _drain_replan_event(self, reason, announcer, i, world=None):
        """Yield the single replan announcement (used by the Unmappable path)."""
        d = {"event": "replan", "at_index": i, "reason": reason,
             "say": announcer.announce("replan")}
        if world is not None:
            d["world"] = world.to_dict()
        yield d
