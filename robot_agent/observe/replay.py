"""Replay a recorded run offline: no robot, no LLM server, no ROS.

::

    python -m robot_agent.observe.replay plan <run_dir|record.jsonl|run_id> [--planner auto|recorded|live]
    python -m robot_agent.observe.replay exec <run> --fake

``plan`` re-runs understanding, the planner and the checker (the loop with
``plan_only``) and diffs the understanding, the checker's gate verdicts and the
plan against the record. ``exec --fake`` re-runs the whole executive against a
fake robot that returns the recorded skill results, and diffs every event, the
skill calls and the outcome.

In both, every LLM call is answered from the record by its request (prompt,
format / guide, or embodiplan's messages); a request the record does not have
raises :class:`ReplayMismatch` ("the prompt differs"), with a diff against the
closest recorded one. The planner is either served at the robot_agent boundary
(``recorded``: the recorded generate_plan / replan outputs for the same task) or
run for real with its LLM calls served (``live``: SAGE / GRACE's own logic,
embodiplan required). ``auto`` = ``live`` when the record has embodiplan calls
and embodiplan imports, else ``recorded``.

Exit status: 0 identical, 1 differs, 2 mismatch / cannot replay.

Replay patches a few module globals for its duration (``state.current``, the
loops' ``current`` / ``reconcile_world``, ``skill_configs.ENV``) and restores
them; it is meant for a CLI process or a test, not for a live agent process.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import difflib
import json
import shutil
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .llm import ReplayMismatch, request_of, serving
from .record import _MAX_LLM_STR, find_record, metrics_of, read_rows, shrink

# events a loop's run_blocking yields (others come from the API layer, the
# run manager or live progress callbacks the replay does not attach)
LOOP_EVENTS = ('task_start', 'status', 'understand', 'clarify', 'gate', 'plan', 'warning', 'step_start',
               'step_verify', 'step_done', 'replan', 'recover', 'done', 'error')
DIRECT_EVENTS = ('plan', 'step_start', 'step_done', 'stopped', 'done', 'error')
PLAN_EVENTS = ('understand', 'clarify', 'gate', 'plan')
# fields that change from run to run by design (clocks, ids, timings, tracebacks)
VOLATILE = {'t', 'run_id', 'plan_sec', 'holding_since', 'latency_ms', 'trace', 'say_audio', 'origin',
            'log_dir', 'found_pose_stale', 'started_at', 'ended_at'}


def _canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False, default=str)


def normalize(o: Any) -> Any:
    """``o`` without the volatile fields, JSON-normalised (tuples -> lists ...)."""
    o = shrink(o)
    if isinstance(o, dict):
        return {k: normalize(v) for k, v in o.items() if k not in VOLATILE}
    if isinstance(o, list):
        return [normalize(x) for x in o]
    return o


# --------------------------------------------------------------------------- #
# Loading a record
# --------------------------------------------------------------------------- #
@dataclass
class Recorded:
    path: Path
    rows: list[dict]

    @property
    def header(self) -> dict:
        return next((r for r in self.rows if r.get('kind') == 'header'), {})

    @property
    def run_id(self) -> str:
        return self.header.get('run_id', self.path.parent.name)

    @property
    def context(self) -> dict:
        return next((r for r in self.rows if r.get('kind') == 'context'), {})

    def of(self, kind: str) -> list[dict]:
        return [r for r in self.rows if r.get('kind') == kind]

    @property
    def events(self) -> list[dict]:
        return [r['event'] for r in self.of('event') if isinstance(r.get('event'), dict)]

    @property
    def skills(self) -> list[dict]:
        return [r for r in self.of('skill') if not r.get('depth')]


def load(target, data_dir=None) -> Recorded:
    path = find_record(target, data_dir)
    return Recorded(path, read_rows(path))


# --------------------------------------------------------------------------- #
# Fakes that answer from the record
# --------------------------------------------------------------------------- #
class LLMStore:
    """Recorded LLM calls, served by request. Each recorded call answers once."""

    def __init__(self, rows: list[dict], mismatches: Optional[list] = None):
        self.rows = [r for r in rows if r.get('kind') == 'llm']
        self.used: set[int] = set()
        self.served: list[dict] = []
        # what did not match (the loops catch exceptions, so it is kept here too)
        self.mismatches = mismatches if mismatches is not None else []

    def serve(self, source: str, request: dict):
        req = shrink(request, _MAX_LLM_STR)
        key = _canon(req)
        cands = [i for i, r in enumerate(self.rows) if i not in self.used and r.get('source') == source]
        for i in cands:
            if _canon(self.rows[i].get('request')) == key:
                self.used.add(i)
                row = self.rows[i]
                self.served.append(row)
                if row.get('error'):
                    raise RuntimeError(row['error'])
                return copy.deepcopy(row.get('response'))
        near = self.rows[cands[0]].get('request') if cands else None
        raise _mismatch(self.mismatches, f'{source} LLM call not in the record (the prompt differs):\n'
                        + _diff(near, req, 'recorded', 'replayed'))

    @property
    def unused(self) -> list[dict]:
        return [r for i, r in enumerate(self.rows) if i not in self.used]


class ReplayLLM:
    """A robot_agent LLM client answered by an :class:`LLMStore`."""

    def __init__(self, store: LLMStore):
        self.store = store

    def chat(self, **kwargs):
        return self.store.serve('robot_agent', request_of('chat', kwargs))

    def chat_guide(self, prompt, guide, reuse=False, **kwargs):
        return self.store.serve('robot_agent', request_of('chat_guide', dict(kwargs, prompt=prompt, guide=guide)))


class ReplayPlanner:
    """The recorded planner outputs for the same calls (task, completed prefix, failed step)."""

    def __init__(self, rows: list[dict], warnings: list[str], mismatches: Optional[list] = None):
        self.rows = [r for r in rows if r.get('kind') == 'planner']
        self.used: set[int] = set()
        self.warnings = warnings
        self.mismatches = mismatches if mismatches is not None else []

    def _serve(self, method: str, inputs: dict):
        key_fields = ('task',) if method == 'generate_plan' else ('task', 'completed', 'failed_step')
        mine = normalize({k: inputs.get(k) for k in key_fields})
        cands = [i for i, r in enumerate(self.rows) if i not in self.used and r.get('method') == method]
        for i in cands:
            row = self.rows[i]
            if normalize({k: (row.get('inputs') or {}).get(k) for k in key_fields}) == mine:
                self.used.add(i)
                rec_in = normalize(row.get('inputs') or {})
                for k in ('obs', 'reason', 'visible'):
                    if k in rec_in and rec_in.get(k) != normalize(inputs.get(k)):
                        self.warnings.append(f'planner {method}: {k} differs from the record\n'
                                             + _diff(rec_in.get(k), normalize(inputs.get(k))))
                if row.get('error'):
                    raise RuntimeError(row['error'])
                return copy.deepcopy(row.get('steps') or []), copy.deepcopy(row.get('meta') or {})
        near = self.rows[cands[0]].get('inputs') if cands else None
        raise _mismatch(self.mismatches, f'planner {method} call not in the record:\n'
                        + _diff(normalize(near), normalize(inputs), 'recorded', 'replayed'))

    def generate_plan(self, task, obs, visible_objects):
        return self._serve('generate_plan', {'task': task, 'obs': obs, 'visible': visible_objects})

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects):
        return self._serve('replan', {'task': task, 'completed': completed, 'failed_step': failed_step,
                                      'reason': failure_reason, 'obs': obs, 'visible': visible_objects})


class ReplaySkills:
    """A fake robot: each skill call returns the recorded result of the same
    call (same skill and params; the next one in order first)."""

    def __init__(self, rows: list[dict], warnings: list[str], strict: bool = False,
                 mismatches: Optional[list] = None):
        self.rows = [r for r in rows if r.get('kind') == 'skill' and not r.get('depth')]
        self.used: set[int] = set()
        self.calls: list[tuple[str, Any]] = []
        self.warnings = warnings
        self.strict = strict
        self.mismatches = mismatches if mismatches is not None else []

    def execute(self, name, params, node=None, log_fn=None):
        self.calls.append((name, normalize(params)))
        p = _canon(normalize(params))
        free = [i for i in range(len(self.rows)) if i not in self.used]
        same = [i for i in free if self.rows[i].get('skill') == name]
        exact = [i for i in same if _canon(normalize(self.rows[i].get('params'))) == p]
        if exact:
            i = exact[0]
        elif same and not self.strict:
            i = same[0]
            self.warnings.append(f'skill {name}: params differ from the record\n'
                                 + _diff(normalize(self.rows[i].get('params')), normalize(params)))
        else:
            raise _mismatch(self.mismatches,
                            f'skill call {name}({_canon(normalize(params))[:200]}) not in the record')
        self.used.add(i)
        row = self.rows[i]
        if row.get('error'):
            raise RuntimeError(row['error'].split(': ', 1)[-1])
        raw = copy.deepcopy(row.get('raw'))
        return raw if raw is not None else {}

    @property
    def unused(self) -> list[dict]:
        return [r for i, r in enumerate(self.rows) if i not in self.used]


class ReplayState:
    """The bits of ``AgentState`` the loops read."""

    def __init__(self, robot_pkg: str, common_dir: Path, world: Optional[dict], skills):
        from ..core.planning.base import WorldState
        self.robot_pkg = robot_pkg
        self.common_dir = str(common_dir)
        self.world = WorldState()
        if world:
            self.world.update_from_dict(world)
        self.sr = skills
        self.dm = types.SimpleNamespace(_ros_node=None)

    def save_world(self):
        pass


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #
@dataclass
class ReplayResult:
    mode: str
    run_id: str
    loop: str
    identical: bool = False
    diffs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    recorded_plan: Any = None
    replayed_plan: Any = None
    unused_llm: int = 0
    unused_skills: int = 0
    error: Optional[str] = None

    def summary(self) -> str:
        head = (f'replay {self.mode} {self.run_id} ({self.loop}): '
                + ('IDENTICAL' if self.identical else ('ERROR' if self.error else 'DIFFERS')))
        lines = [head]
        if self.error:
            lines.append(self.error)
        if self.recorded_plan is not None or self.replayed_plan is not None:
            lines.append(f'  recorded plan: {self.recorded_plan}')
            lines.append(f'  replayed plan: {self.replayed_plan}')
        lines += [f'  diff: {d}' for d in self.diffs]
        lines += [f'  warning: {w}' for w in self.warnings]
        if self.unused_llm or self.unused_skills:
            lines.append(f'  not replayed: {self.unused_llm} LLM call(s), {self.unused_skills} skill call(s)')
        return '\n'.join(lines)


class _Patch:
    """Set module attributes for the duration of a replay, then restore them."""

    def __init__(self):
        self._undo: list[tuple[Any, str, Any, bool]] = []

    def set(self, obj, name, value):
        had = hasattr(obj, name)
        self._undo.append((obj, name, getattr(obj, name, None), had))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, old, had in reversed(self._undo):
            if had:
                setattr(obj, name, old)
            else:
                try:
                    delattr(obj, name)
                except AttributeError:
                    pass
        self._undo.clear()


def _mismatch(sink: list, msg: str) -> ReplayMismatch:
    sink.append(msg)
    return ReplayMismatch(msg)


def _diff(a, b, la='recorded', lb='replayed', n: int = 40) -> str:
    sa = json.dumps(a, indent=1, sort_keys=True, ensure_ascii=False, default=str).splitlines()
    sb = json.dumps(b, indent=1, sort_keys=True, ensure_ascii=False, default=str).splitlines()
    out = list(difflib.unified_diff(sa, sb, la, lb, lineterm='', n=1))
    return '\n'.join(out[:n]) + ('\n  ...' if len(out) > n else '')


def _plan_of(events: list[dict]):
    p = next((e for e in events if e.get('event') == 'plan'), None)
    if p is None:
        return None
    return p.get('plan_text') or p.get('plan') or p.get('steps')


def _compare(rec: list[dict], rep: list[dict], what: str) -> list[str]:
    a, b = [normalize(e) for e in rec], [normalize(e) for e in rep]
    if a == b:
        return []
    out = []
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            out.append(f'{what} #{i} ({x.get("event")} vs {y.get("event")}):\n{_diff(x, y)}')
            break
    if len(a) != len(b):
        out.append(f'{what}: {len(a)} recorded, {len(b)} replayed '
                   f'(recorded {[e.get("event") for e in a[len(b):]][:8]}, '
                   f'replayed {[e.get("event") for e in b[len(a):]][:8]} extra)')
    return out


def _loop_overrides(ctx: dict, kind: str, work: Path) -> dict:
    cfg = dict(ctx.get('cfg') or {})
    skip = {'planner', 'generator', 'host', 'model', 'log_dir', 'live_path', 'memory_path', 'vlm_enabled',
            'speak_backend', 'mute_skill_tts'}
    ov = {k: v for k, v in cfg.items() if k not in skip and not isinstance(v, dict)}
    if kind == 'assist':
        ov['generator'] = cfg.get('generator') or 'grace'
        if ctx.get('original') is not None:
            ov['original'] = ctx['original']
    else:
        ov['planner'] = cfg.get('planner') or 'grace'
    live = work / 'live.jsonl'
    src = cfg.get('live_path')
    if isinstance(src, str) and Path(src).is_file():
        shutil.copy2(src, live)           # SAGE's live memory as it is now (the record keeps no copy)
    ov.update(log_dir=str(work / 'task_runs'), live_path=str(live), memory_path=str(work / 'object_memory.json'),
              vlm_enabled=False, speak_backend=False, mute_skill_tts=False)
    return ov


def replay(target, mode: str = 'plan', planner: str = 'auto', robot_pkg: Optional[str] = None,
           data_dir=None, strict: bool = False, workdir=None) -> ReplayResult:
    """Replay a recorded run (``mode`` 'plan' or 'exec'). See the module doc."""
    rec = target if isinstance(target, Recorded) else load(target, data_dir)
    ctx = rec.context
    kind = ctx.get('loop') or ''
    res = ReplayResult(mode, rec.run_id, kind)
    if not ctx:
        res.error = 'the record has no context row (recorded before the executive started?)'
        return res
    if mode == 'plan' and kind not in ('assist', 'closed'):
        res.error = f'replay plan needs a planning loop run (this run is {kind!r}); use: replay exec --fake'
        return res
    if kind not in ('assist', 'closed', 'direct'):
        res.error = f'cannot replay a {kind!r} run'
        return res

    own_work = workdir is None
    work = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix='replay-'))
    work.mkdir(parents=True, exist_ok=True)
    mismatches: list[str] = []
    store = LLMStore(rec.rows, mismatches)
    skills = ReplaySkills(rec.rows, res.warnings, strict=strict, mismatches=mismatches)
    events = rec.events
    start_world = next((e.get('world') for e in events if e.get('event') == 'task_start'
                        and isinstance(e.get('world'), dict)), ctx.get('world'))
    pkg = robot_pkg or ctx.get('robot_pkg') or rec.header.get('robot_pkg') or ''
    st = ReplayState(pkg, work, start_world, skills)
    patch = _Patch()
    try:
        import robot_agent.skill_configs as sc
        from .. import state as state_mod
        from ..core.planning import loop as LP
        patch.set(state_mod, 'current', lambda: st)
        patch.set(LP, 'current', lambda: st)
        if ctx.get('env') is not None:
            patch.set(sc, 'ENV', ctx['env'])
        if kind in ('assist', 'closed'):
            from ..core.planning import assist_loop as AL
            patch.set(AL, 'current', lambda: st)
            ident = lambda node, world, threshold=0.8: world        # noqa: E731
            patch.set(LP, 'reconcile_world', ident)
            patch.set(AL, 'reconcile_world', ident)
            if ctx.get('object_memory') is not None:
                (work / 'object_memory.json').write_text(json.dumps(ctx['object_memory'], ensure_ascii=False))
            llm_cfg = dict(ctx.get('llm_cfg') or {})
            agent = types.SimpleNamespace(skill_registry=skills, device_manager=st.dm, _llm_cfg=llm_cfg,
                                          effective_llm_cfg=lambda: llm_cfg)
            Loop = AL.AssistLoop if kind == 'assist' else LP.ClosedLoop
            loop = Loop(agent, emit=None, **_loop_overrides(ctx, kind, work))
            loop._llm_client = ReplayLLM(store)
            use = planner
            if use == 'auto':
                has_ep = any(r.get('source') == 'embodiplan' for r in store.rows)
                from ..core.planning.registry import planner_available
                ep_ok = planner_available('grace')          # embodiplan importable here
                use = 'live' if (has_ep and ep_ok) else 'recorded'
            if use == 'recorded':
                rp = ReplayPlanner(rec.rows, res.warnings, mismatches)
                loop._make_planner = lambda: rp
            from ..core.run_control import begin_run
            begin_run()
            with serving(store):
                out = []
                for ev in loop.run_blocking(ctx.get('task', ''), ctx.get('lang', 'en'),
                                            plan_only=(mode == 'plan')):
                    out.append(ev)
                    if len(out) > 5000:
                        raise ReplayMismatch('replay did not finish (more than 5000 events)')
            res.events = out
        else:
            from ..core.unified_agent import UnifiedAgent
            ua = UnifiedAgent(skill_registry=skills, device_manager=st.dm)

            async def _drain():
                return [ev async for ev in ua.run_direct(plan=ctx.get('task', ''), lang=ctx.get('lang', 'en'))]
            with serving(store):
                res.events = asyncio.run(_drain())
    except ReplayMismatch as e:
        res.error = f'ReplayMismatch: {e}'
        return res
    except Exception as e:
        import traceback
        res.error = f'{type(e).__name__}: {e}\n{traceback.format_exc(limit=6)}'
        return res
    finally:
        patch.restore()
        if own_work:
            shutil.rmtree(work, ignore_errors=True)

    if mismatches:
        # the run left the record (the diff below shows where); the fakes could not answer from there on
        res.error = 'ReplayMismatch: ' + mismatches[0]

    # ---- diff ------------------------------------------------------------
    names = LOOP_EVENTS if kind in ('assist', 'closed') else DIRECT_EVENTS
    rec_evs = [e for e in events if e.get('event') in names and e.get('origin') != 'run_manager']
    rep_evs = [e for e in res.events if e.get('event') in names]
    if kind in ('assist', 'closed'):
        i0 = next((i for i, e in enumerate(rec_evs) if e.get('event') == 'task_start'), 0)
        rec_evs = rec_evs[i0:]
    if mode == 'plan':
        def upto_plan(evs):
            out = []
            for e in evs:
                if e.get('event') in PLAN_EVENTS:
                    out.append(e)
                if e.get('event') == 'plan':
                    break
            return out
        rec_evs, rep_evs = upto_plan(rec_evs), upto_plan(rep_evs)
        res.recorded_plan, res.replayed_plan = _plan_of(rec_evs), _plan_of(rep_evs)
        res.diffs += _compare(rec_evs, rep_evs, 'event')
    else:
        res.diffs += _compare(rec_evs, rep_evs, 'event')
        rec_calls = [(r.get('skill'), normalize(r.get('params'))) for r in rec.skills]
        if rec_calls != skills.calls:
            res.diffs.append('skill calls differ:\n' + _diff(rec_calls, skills.calls))
        m = metrics_of(rec.rows)
        rep_done = next((e for e in reversed(rep_evs) if e.get('event') in ('done', 'error')), {})
        rec_done = next((e for e in reversed(rec_evs) if e.get('event') in ('done', 'error')), {})
        if normalize(rec_done) != normalize(rep_done):
            res.diffs.append(f'outcome: recorded {m.get("status")!r}, replayed {rep_done.get("status")!r}')
        res.unused_skills = len(skills.unused)
    res.unused_llm = len(store.unused) if mode == 'exec' else 0
    res.identical = not res.diffs and res.error is None
    return res


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python -m robot_agent.observe.replay', description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='mode', required=True)
    for name, hlp in (('plan', 're-run understanding / planner / checker on the recorded LLM answers'),
                      ('exec', 're-run the executive against a fake robot returning the recorded results')):
        p = sub.add_parser(name, help=hlp)
        p.add_argument('run', help='run dir, record.jsonl, or run_id')
        p.add_argument('--planner', choices=('auto', 'recorded', 'live'), default='auto')
        p.add_argument('--robot-pkg', default=None, help='robot package (default: the recorded one)')
        p.add_argument('--data-dir', default=None, help='where to look a run_id up (default: the data dir)')
        p.add_argument('--strict', action='store_true', help='a skill call with other params is a mismatch')
        p.add_argument('--json', action='store_true', help='print the result as JSON')
        if name == 'exec':
            p.add_argument('--fake', action='store_true', required=True,
                           help='fake robot from the recorded results (the only exec mode)')
    a = ap.parse_args(argv)
    try:
        res = replay(a.run, a.mode, planner=a.planner, robot_pkg=a.robot_pkg, data_dir=a.data_dir,
                     strict=a.strict)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    if a.json:
        d = {k: v for k, v in vars(res).items() if k != 'events'}
        print(json.dumps(d, ensure_ascii=False, indent=1, default=str))
    else:
        print(res.summary())
    return 0 if res.identical else (2 if res.error else 1)


if __name__ == '__main__':
    sys.exit(main())
