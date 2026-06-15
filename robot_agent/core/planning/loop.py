"""ClosedLoop — the planner-agnostic NL -> plan -> exec -> check -> replan driver.

Ties together the planning-layer pieces and streams events (same schema the
frontend already consumes from ``UnifiedAgent.run``, plus ``action/object/
verifies/say``). It is selected from ``UnifiedAgent.run`` behind a config gate;
the legacy open-loop path remains the default fallback.

Per-step it: maps a GRACE step to a kcare skill (``ActionMapper``), executes it
(``SkillRegistry.execute``), verifies the result in layers (``StepVerifier``:
isdone -> symbolic -> VLM), announces milestones (``Announcer`` — robot speaker
+ ``say`` for the dashboard), records everything (``TaskRecord`` + JSONL
``RunLogger``), and on failure asks the planner to ``replan`` a suffix (bounded
by ``max_replans``).

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

from robot_agent.state import current
from .announcer import Announcer
from .base import WorldState
from .mapper import ActionMapper, Unmappable
from .records import RunLogger, StepRecord, VerifyResult, new_task_record
from .registry import get_planner
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


def _read_robot_xy(node):
    """Best-effort (x, y) of the mobile base from the ``mobile_pose`` agent.

    Robust to the payload shapes seen in the wild: a flat ``{'x','y'}`` dict, or
    ``{'pose': <...>}`` where the value is a ROS Pose (``.position.x``), a list
    ``[x, y, ...]``, or a ``{'x','y'}`` dict. The kcare ``decode_func`` returns
    ``{'pose': <geometry_msgs Pose>, 'isdone': True}`` — note the stock
    ``robot_agent.utils.get_closest_loc`` reads ``ret['x']`` and so silently
    fails on this shape; that's why we extract the pose ourselves here.
    Returns ``None`` if unavailable.
    """
    try:
        agents = node.agents
        if 'mobile_pose' not in agents:
            return None
        ret = agents['mobile_pose'].get()
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
    that follows the ``mobile_pose`` agent + ENV ``loc`` convention:

      1. If ``grace_namemap`` defines ``reconcile_world(node, world)`` → it owns
         reconciliation entirely (e.g. can also read a real gripper sensor for
         ``holding``). Core does nothing else.
      2. Else if it defines ``robot_xy(node) -> (x, y) | None`` → core uses that
         pose (robot-specific reader) and does the generic ENV match below.
      3. Else → fully generic: ``_read_robot_xy`` (built-in ``mobile_pose``
         reader) + nearest-ENV-``loc`` match.

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
        self._namemap = importlib.import_module(f"{self.robot_pkg}.configs.grace_namemap")
        self.cfg = self._resolve_cfg(overrides, st)

    # ── config ───────────────────────────────────────────────────────────
    def _resolve_cfg(self, o: dict, st) -> dict:
        env = os.environ.get
        common = Path(getattr(st, "common_dir", "."))
        return {
            "planner": o.get("planner") or env("ROBOT_AGENT_PLANNER", "grace"),
            "vlm_enabled": o.get(
                "vlm_enabled", env("ROBOT_AGENT_VERIFY_VLM", "0") == "1"
            ),
            "speak_backend": o.get(
                "speak_backend", env("ROBOT_AGENT_VOICE_BACKEND", "1") != "0"
            ),
            "mute_skill_tts": o.get("mute_skill_tts", True),
            "max_replans": int(o.get("max_replans", env("ROBOT_AGENT_MAX_REPLANS", "3"))),
            "host": self._llm_cfg.get("url") or self._llm_cfg.get("host"),
            "model": self._llm_cfg.get("model"),
            "log_dir": Path(o.get("log_dir") or (common / "task_runs")),
            "live_path": str(o.get("live_path") or (common / "grace_memory.jsonl")),
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
            )
        if name == "llm_direct":
            return get_planner(
                "llm_direct", llm_cfg=self._llm_cfg, robot_pkg=self.robot_pkg
            )
        return get_planner(name)

    # ── helpers ──────────────────────────────────────────────────────────
    def _arg_name(self, action: str, obj: str) -> str:
        """Localized-ish argument name for announcements."""
        nm = self._namemap
        try:
            return nm.to_loc(obj) if action == "MoveTo" else nm.to_obj(obj)
        except Exception:
            return obj

    def _build_params(self, action: str, obj: str, world, mapped_params: dict) -> dict:
        nm = self._namemap
        params = dict(mapped_params or {})
        if hasattr(nm, "build_params"):
            try:
                extra = nm.build_params(action, obj, world)
                if extra:
                    params.update(extra)
            except Exception:
                pass
        return params

    def _kcare_plan_text(self, steps, meta, mapper, world) -> str:
        """Render the plan in the kcare ``skill::inputs`` format the robot
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
    def run_blocking(self, task: str, lang: str = "en",
                     plan_only: bool = False) -> Iterator[dict]:
        nm = self._namemap
        mapper = ActionMapper(nm)
        verifier = StepVerifier(
            self.cfg["vlm_enabled"],
            set(getattr(nm, "VLM_ACTIONS", set())),
            getattr(nm, "vlm_hook", None),
        )
        announcer = Announcer(lang=lang, speak_backend=self.cfg["speak_backend"])
        world = current().world
        node = getattr(self.dm, "_ros_node", None)
        reconcile_world(node, world)

        # Mute skill-level TTS so the plan-level announcer owns milestone speech.
        muted = False
        if self.cfg["mute_skill_tts"]:
            try:
                from robot_agent.utils import set_skill_tts_muted
                set_skill_tts_muted(True)
                muted = True
            except Exception:
                pass

        now = time.time()
        run_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
        run = new_task_record(
            run_id, task, lang=lang, planner=self.cfg["planner"], started_at=now
        )
        logger = RunLogger(run, self.cfg["log_dir"] / f"{run_id}.jsonl")

        def ev(event: str, say: Optional[str] = None, **fields) -> dict:
            d = {"event": event, **fields}
            if say is not None:
                d["say"] = say
            try:
                logger.event(event, t=time.time(), **{k: v for k, v in d.items() if k != "event"})
            except Exception:
                pass
            return d

        try:
            yield ev("task_start", say=announcer.announce("task_start"),
                     task=task, run_id=run_id, world=world.to_dict())

            # ── Ground ───────────────────────────────────────────────────
            obs, visible = "", []
            try:
                obs, visible = nm.observe(node)
            except Exception:
                obs, visible = "", []
            # Feed the persisted world summary to the planner so the LLM knows
            # current holding/location.
            try:
                obs = (obs + "\n" if obs else "") + "Robot state: " + world.as_text()
            except Exception:
                pass
            run.obs, run.visible = obs, list(visible)

            # ── Plan ─────────────────────────────────────────────────────
            yield ev("status", msg="Generating task plan...")
            planner = self._make_planner()
            # Stream live multi-step progress (decompose/expand/refine) for
            # planners exposing a `progress` hook (GRACE). Best-effort.
            try:
                inner = getattr(planner, "_p", planner)
                if self._emit is not None and hasattr(inner, "progress"):
                    _emit = self._emit
                    def _on_progress(d):
                        try: _emit({"event": "plan_step", **d})
                        except Exception: pass
                    inner.progress = _on_progress
            except Exception:
                pass
            steps, meta = planner.generate_plan(task, obs, visible)
            steps = list(steps)
            run.plan_meta = meta or {}
            warnings = mapper.screen(steps)
            run.warnings = warnings
            run.status = "running"
            plan_text = self._kcare_plan_text(steps, run.plan_meta, mapper, world)
            yield ev("plan", steps=steps, plan_meta=run.plan_meta, warnings=warnings,
                     plan_text=plan_text, world=world.to_dict())
            for w in warnings:
                yield ev("warning", msg=w)

            # Plan-only mode: stop after generating the plan (no execution).
            if plan_only:
                run.status = "planned"
                run.ended_at = time.time()
                yield ev("done", status="planned", run_id=run_id,
                         say=announcer.announce("plan_ready"), world=world.to_dict())
                return

            completed: list[dict] = []
            i = 0
            replans = 0

            while i < len(steps):
                step = steps[i]
                action = step.get("action", "")
                obj = step.get("object", "")
                started = time.time()

                # ── Map ──────────────────────────────────────────────────
                try:
                    mapped = mapper.to_skill(step, world)
                except Unmappable as e:
                    reason = str(e)
                    rec = StepRecord(index=i, action=action, object=obj, skill=None,
                                     status="failed", started_at=started, ended_at=time.time(),
                                     verifies=[VerifyResult("isdone", False, reason)])
                    run.steps.append(rec)
                    yield ev("step_done", index=i, status="failed",
                             say=announcer.announce("step_fail", action=action,
                                                    object=self._arg_name(action, obj), reason=reason),
                             reason=reason, world=world.to_dict())
                    ok_to_replan, steps, i, replans = self._maybe_replan(
                        planner, task, completed, step, reason, obs, visible,
                        run, replans, announcer)
                    if not ok_to_replan:
                        run.status = "failed"
                        break
                    for e2 in self._drain_replan_event(reason, announcer, i, world):
                        yield e2
                    continue

                # ── No-op ────────────────────────────────────────────────
                if mapped is None:
                    rec = StepRecord(index=i, action=action, object=obj, skill=None,
                                     status="success", started_at=started, ended_at=time.time(),
                                     verifies=[VerifyResult("isdone", True, "no-op")])
                    run.steps.append(rec)
                    completed.append(step)
                    yield ev("step_done", index=i, status="success", skill=None,
                             world=world.to_dict())
                    i += 1
                    continue

                skill_name, mapped_params = mapped
                params = self._build_params(action, obj, world, mapped_params)
                rec = StepRecord(index=i, action=action, object=obj, skill=skill_name,
                                 params=params, status="running", started_at=started)

                yield ev("step_start", index=i, action=action, object=obj, skill=skill_name,
                         say=announcer.announce("step_start", action=action,
                                                object=self._arg_name(action, obj)))

                # ── Execute ──────────────────────────────────────────────
                try:
                    result = self.sr.execute(skill_name, params, node)
                except Exception as e:
                    result = {"isdone": False, "msg": str(e)}
                rec.result = result if isinstance(result, dict) else {"result": result}

                # ── Check (layered) ──────────────────────────────────────
                vres = verifier.verify(step, rec.result, world, node)
                rec.verifies = vres
                ok, reason = verdict(vres)
                rec.ended_at = time.time()
                yield ev("step_verify", index=i, verifies=_vr_list(vres))

                if ok:
                    rec.status = "success"
                    mapper.apply_effect(step, world)
                    # stamp/clear the grasp clock (no gripper sensor → belief + timestamp)
                    _act = step.get("action", "")
                    if _act == "Pick":
                        world.holding_since = time.time()
                    elif _act in ("Place", "PutIn"):
                        world.holding_since = None
                    try:
                        current().save_world()   # persist belief after each effect
                    except Exception:
                        pass
                    completed.append(step)
                    run.steps.append(rec)
                    yield ev("step_done", index=i, status="success", result=rec.result,
                             say=announcer.announce("step_success", action=action,
                                                    object=self._arg_name(action, obj)),
                             world=world.to_dict())
                    i += 1
                else:
                    rec.status = "failed"
                    run.steps.append(rec)
                    yield ev("step_done", index=i, status="failed", result=rec.result, reason=reason,
                             say=announcer.announce("step_fail", action=action,
                                                    object=self._arg_name(action, obj), reason=reason),
                             world=world.to_dict())
                    # fresh observation before replanning
                    try:
                        obs, visible = nm.observe(node)
                    except Exception:
                        pass
                    ok_to_replan, steps, i, replans = self._maybe_replan(
                        planner, task, completed, step, reason, obs, visible,
                        run, replans, announcer)
                    if not ok_to_replan:
                        run.status = "failed"
                        break
                    yield ev("replan", at_index=i, reason=reason,
                             say=announcer.announce("replan"), world=world.to_dict())

            if run.status != "failed":
                run.status = "success"
            run.ended_at = time.time()

            # Memory loop: record a fully successful episode (best-effort).
            if run.status == "success":
                try:
                    rec_fn = getattr(planner, "record_episode", None)
                    if callable(rec_fn):
                        rec_fn(task, completed)
                except Exception:
                    pass

            kind = "done_success" if run.status == "success" else "done_fail"
            yield ev("done", status=run.status, run_id=run_id, say=announcer.announce(kind),
                     world=world.to_dict())

        except Exception as e:
            run.status = "aborted"
            run.ended_at = time.time()
            yield {"event": "error", "msg": str(e), "trace": traceback.format_exc()}
        finally:
            if muted:
                try:
                    from robot_agent.utils import set_skill_tts_muted
                    set_skill_tts_muted(False)
                except Exception:
                    pass
            try:
                logger.snapshot()
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
