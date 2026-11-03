"""RunRecord: everything one run did, as one JSONL file keyed by the RunManager's run_id.

Every run started through the RunManager (closed loop, SAGE assist loop, the
open-loop LLM plan and ``direct`` command runs alike) gets a record at
``<data_dir('run_records')>/<run_id>/record.jsonl`` (see :mod:`.paths`). One
row per line, ``{"seq", "t", "kind", ...}``:

==========  ===================================================================
kind        what
==========  ===================================================================
header      run_id, robot, user, role, goal, host, schema version (first row)
context     what the executive was started with: loop kind, task, lang, loop
            config, ENV, world at start, object memory (for replay)
event       every event published on the run (agent events and run-manager
            notices), big strings shortened
user        a user's input during the run (``run_input`` / ``run_refused``),
            with ``user_id`` and role
llm         one LLM call: source, request (prompt / messages), response,
            model, backend, seed, temperature, latency_ms, error
planner     one planner call at the robot_agent boundary (generate_plan /
            replan): inputs, steps, meta
skill       one skill call: skill, params, the raw return, its SkillResult,
            latency, nesting depth, the world belief before it
step        a step that ended: index, status, world before and after
health      the robot adapter's ``health()`` at start and at the end
end         outcome, then a ``snapshot`` row with the header and the metrics
==========  ===================================================================

The capture points are the single paths every run goes through:
``LLMobj.chat`` / ``chat_guide`` (every robot_agent LLM client),
``embodiplan.LLMBackend.chat`` (SAGE / GRACE, hooked in
``core/planning/backends/pyplanner_grace.py``), ``get_planner`` (planner
boundary), ``SkillRegistry.execute`` and ``Run.publish``. Test doubles that do
not go through them are wrapped with :func:`instrument`.

Recording is best-effort and never raises into the run. ``ROBOT_AGENT_RECORD=0``
turns it off. Without a data dir (no ``AgentState``) a run is not recorded.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.planning.records import RunLogger, _to_jsonable

SCHEMA_VERSION = 1
RECORD_FILE = 'record.jsonl'

# Strings longer than this are replaced by a stub (base64 images / audio in
# events and skill results). LLM requests and responses keep up to _MAX_LLM_STR
# (prompts must stay whole for replay; an image in a message does not).
_MAX_STR = 4096
_MAX_LLM_STR = 65536
_DROP_KEYS = {'say_audio'}


def enabled() -> bool:
    return os.environ.get('ROBOT_AGENT_RECORD', '1') != '0'


def shrink(obj: Any, limit: int = _MAX_STR) -> Any:
    """JSON-safe copy of ``obj`` with long strings replaced by
    ``{"_omitted": "str", "len", "sha1"}`` and bulky keys dropped."""
    obj = _to_jsonable(obj)

    def walk(o):
        if isinstance(o, str):
            if len(o) <= limit:
                return o
            return {'_omitted': 'str', 'len': len(o),
                    'sha1': hashlib.sha1(o.encode('utf-8', 'replace')).hexdigest()}
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items() if k not in _DROP_KEYS}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o
    return walk(obj)


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #
@dataclass
class RunHeader:
    run_id: str
    robot_id: str = ''
    user_id: str = ''
    role: str = 'owner'
    goal: str = ''
    created: float = 0.0
    host: str = ''
    robot_pkg: str = ''
    schema: int = SCHEMA_VERSION
    metrics: dict = field(default_factory=dict)


class RunRecord(RunLogger):
    """The JSONL record of one run (a :class:`RunLogger` with typed rows).

    Thread-safe: the executive thread (LLM / skill rows) and the API's event
    loop (event rows) append to it at the same time.
    """

    def __init__(self, header: RunHeader, path) -> None:
        super().__init__(header, path)
        self.header = header
        self._lock = threading.Lock()
        self._seq = 0
        self._world: Optional[dict] = None          # last world seen on an event
        self._step_world: dict[Any, Optional[dict]] = {}
        self.rows_mem: list[dict] = []              # kept for metrics (small rows only)
        self.row('header', **{k: v for k, v in vars(header).items() if k != 'metrics'})

    @property
    def dir(self) -> Path:
        return self.path.parent

    # ---- writing ------------------------------------------------------
    def _append(self, payload: dict) -> None:
        with self._lock:
            super()._append(payload)

    def row(self, kind: str, **fields: Any) -> dict:
        with self._lock:
            self._seq += 1
            seq = self._seq
        payload = {'seq': seq, 't': time.time(), 'kind': kind, **fields}
        try:
            self._append(payload)
        except Exception:
            pass
        # what metrics need, without the bulky parts
        keep = {k: payload.get(k) for k in ('seq', 'kind', 'source', 'latency_ms', 'error', 'skill',
                                            'depth', 'result', 'method', 'status', 'user_id')
                if k in payload}
        if kind in ('header', 'end'):
            keep = dict(payload)
        elif kind == 'event':
            ev = payload.get('event') or {}
            keep['event'] = {k: ev.get(k) for k in ('event', 'status', 'success', 'verifies', 'index',
                                                    'run_id', 'outcome', 'state', 'code', 'device') if k in ev}
            if ev.get('event') == 'step_done' and isinstance(ev.get('result'), dict):
                r = ev['result']      # the step's verdict and failure code (metrics' failure_codes)
                keep['event']['result'] = {k: r.get(k) for k in ('isdone', 'ok', 'code') if k in r}
        elif kind == 'context':
            keep['loop'] = payload.get('loop')
        with self._lock:
            self.rows_mem.append(keep)
        return payload

    # ---- typed rows ---------------------------------------------------
    def on_event(self, ev: dict) -> None:
        """Run.publish subscriber: one ``event`` row, plus ``user`` / ``step`` rows."""
        try:
            name = ev.get('event')
            self.row('event', event=shrink(ev))
            if name in ('run_input', 'run_refused'):
                self.row('user', event=name, user_id=ev.get('user_id', ''), role=ev.get('role'),
                         text=ev.get('text'), action=ev.get('action'), intent=ev.get('intent'))
            if name == 'step_start':
                self._step_world[ev.get('index', ev.get('step'))] = self._world
            if isinstance(ev.get('world'), dict):
                if name == 'step_done':
                    idx = ev.get('index', ev.get('step'))
                    self.row('step', index=idx, status=ev.get('status'),
                             world_before=self._step_world.pop(idx, self._world),
                             world_after=shrink(ev['world']))
                self._world = shrink(ev['world'])
            elif name == 'step_done':
                idx = ev.get('index', ev.get('step'))
                self.row('step', index=idx, status=ev.get('status'),
                         world_before=self._step_world.pop(idx, self._world), world_after=None)
        except Exception:
            pass

    def llm(self, **fields: Any) -> None:
        for k in ('request', 'response', 'messages'):
            if k in fields:
                fields[k] = shrink(fields[k], _MAX_LLM_STR)
        self.row('llm', **fields)

    def skill(self, **fields: Any) -> None:
        for k in ('params', 'raw', 'world_before'):
            if k in fields:
                fields[k] = shrink(fields[k])
        self.row('skill', **fields)

    def planner(self, **fields: Any) -> None:
        self.row('planner', **{k: shrink(v, _MAX_LLM_STR) for k, v in fields.items()})

    def snapshot(self) -> None:
        self._append({'seq': self._seq + 1, 't': time.time(), 'kind': 'snapshot',
                      'record': _to_jsonable(vars(self.header))})


def read_rows(path) -> list[dict]:
    """All rows of a record file (a torn last line is skipped)."""
    out = []
    with Path(path).open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def find_record(target, data_dir=None) -> Path:
    """A record file from a run dir, a record file, or a run_id (an open run's
    record, else looked up under ``data_dir`` / the configured data dir, new
    location then old)."""
    p = Path(str(target)).expanduser()
    if p.is_file():
        return p
    if p.is_dir() and (p / RECORD_FILE).is_file():
        return p / RECORD_FILE
    rec = get_open(str(target))
    if rec is not None and rec.path.is_file():
        return rec.path
    roots: list[Path] = []
    if data_dir is not None:
        d = Path(data_dir)
        roots += [d, d / 'run_records']
        if (d / 'run_records').is_dir():
            roots += sorted(x for x in (d / 'run_records').iterdir() if x.is_dir())
    else:
        try:
            from .paths import search_dirs
            roots += search_dirs('run_records')
        except Exception:
            # no AgentState (a CLI): the configured root, every host
            from .paths import root
            r = root()
            if r is not None and (r / 'run_records').is_dir():
                roots += sorted(x for x in (r / 'run_records').iterdir() if x.is_dir())
    for r in roots:
        f = r / str(target) / RECORD_FILE
        if f.is_file():
            return f
    raise FileNotFoundError(f'no run record for {target!r} (looked in: {", ".join(map(str, roots)) or "-"})')


def load_record(run_id: str, kinds=None, limit: Optional[int] = None) -> dict:
    """``{run_id, path, live, n_rows, rows}`` of a run (``kinds``: keep only
    these row kinds; ``limit``: the last N rows). FileNotFoundError when there is none."""
    path = find_record(run_id)
    rows = read_rows(path)
    n = len(rows)
    if kinds:
        rows = [r for r in rows if r.get('kind') in set(kinds)]
    if limit:
        rows = rows[-int(limit):]
    return {'run_id': run_id, 'path': str(path), 'live': get_open(run_id) is not None, 'n_rows': n,
            'rows': rows}


def run_metrics(run_id: str) -> dict:
    """The metrics of a run: so far for an active run (``live``: True), the
    stored line for a finished one. FileNotFoundError when there is no record."""
    rec = get_open(run_id)
    if rec is not None:
        with rec._lock:
            rows = list(rec.rows_mem)
        return dict(metrics_of(rows), live=True)
    rows = read_rows(find_record(run_id))
    snap = next((r for r in reversed(rows) if r.get('kind') == 'snapshot'), None)
    m = ((snap or {}).get('record') or {}).get('metrics')
    return dict(m if m else metrics_of(rows), live=False)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def metrics_of(rows: list[dict]) -> dict:
    """One metrics line from a record's rows (full rows or ``rows_mem``)."""
    head = next((r for r in rows if r.get('kind') == 'header'), {})
    ctx = next((r for r in rows if r.get('kind') == 'context'), {})
    llm = [r for r in rows if r.get('kind') == 'llm']
    skills = [r for r in rows if r.get('kind') == 'skill' and not r.get('depth')]
    events = [r.get('event') or {} for r in rows if r.get('kind') == 'event']
    codes: collections.Counter = collections.Counter()
    # failure codes of the plan's steps: a sensor read the loop makes to verify a step (e.g. "gripper empty"
    # after Place) is a skill call too, but not a failure. Runs without step events (direct) count skill calls.
    steps = [e for e in events if e.get('event') == 'step_done']
    for e in steps:
        res = e.get('result') if isinstance(e.get('result'), dict) else {}
        if e.get('status') not in ('success', 'skipped') and not res.get('isdone', res.get('ok', False)):
            codes[res.get('code') or 'unknown'] += 1
    for r in ([] if steps else skills):
        res = r.get('result') or {}
        if not res.get('ok', True):
            codes[res.get('code') or 'unknown'] += 1
        elif r.get('error'):
            codes['software'] += 1
    done = next((e for e in reversed(events) if e.get('event') in ('done', 'error')), {})
    status = done.get('status') or ('error' if done.get('event') == 'error' else
                                     ('success' if done.get('success') in (True, 'True', 1) else
                                      ('failed' if done else '')))
    end = next((r for r in reversed(rows) if r.get('kind') == 'end'), {})
    last_verify: dict = {}
    for e in events:
        if e.get('event') == 'step_verify':
            last_verify[e.get('index')] = all(v.get('ok') for v in (e.get('verifies') or []))
    success = status == 'success'
    task_run = next((e.get('run_id') for e in events if e.get('event') == 'task_start'
                     and e.get('run_id') and e.get('run_id') != head.get('run_id')), None)
    t0 = head.get('created') or (rows[0].get('t') if rows else None)
    t1 = end.get('t') or (rows[-1].get('t') if rows else None)
    return {
        'run_id': head.get('run_id'),
        'task_run_id': task_run,
        'robot_id': head.get('robot_id'),
        'user_id': head.get('user_id'),
        'goal': head.get('goal'),
        'loop': ctx.get('loop'),
        'started': t0,
        'duration_s': round(t1 - t0, 3) if t0 and t1 else None,
        'outcome': end.get('outcome'),
        'status': status,
        'llm_calls': len(llm),
        'llm_errors': sum(1 for r in llm if r.get('error')),
        'llm_latency_ms': round(sum(float(r.get('latency_ms') or 0) for r in llm), 1),
        'planner_calls': sum(1 for r in rows if r.get('kind') == 'planner'),
        'skill_calls': len(skills),
        'replans': sum(1 for e in events if e.get('event') == 'replan'),
        'failure_codes': dict(codes),
        # the deciding failure of a failed run (the loop's done event)
        'failure_code': done.get('code') if status not in ('success', '') else None,
        'failure_device': done.get('device') if status not in ('success', '') else None,
        'success': success,
        # success that a check confirmed: every verified step's last check passed
        'verified_success': bool(success and last_verify and all(last_verify.values())),
    }


# --------------------------------------------------------------------------- #
# Open records (one per active run) and the RunManager hooks
# --------------------------------------------------------------------------- #
_OPEN: dict[str, RunRecord] = {}
_UNSUB: dict[str, Callable[[], None]] = {}
_LOCK = threading.Lock()
_TLS = threading.local()


def records_dir(common_dir=None, host=None) -> Path:
    from .paths import data_dir
    return data_dir('run_records', common_dir, host)


def _robot_pkg() -> str:
    try:
        from .. import state
        return getattr(state.current(), 'robot_pkg', '') or ''
    except Exception:
        return ''


def open_record(run_id: str, robot_id: str = '', user_id: str = '', role: str = 'owner',
                goal: str = '', created: Optional[float] = None, directory=None) -> Optional[RunRecord]:
    """Create (and register) the record of ``run_id``; None when recording is
    off or there is no data dir."""
    if not enabled():
        return None
    try:
        base = Path(directory) if directory is not None else records_dir()
        from ..state import host_id
        hdr = RunHeader(run_id=run_id, robot_id=robot_id, user_id=user_id, role=role, goal=goal,
                        created=created or time.time(), host=host_id(), robot_pkg=_robot_pkg())
        rec = RunRecord(hdr, base / run_id / RECORD_FILE)
    except Exception:
        return None
    with _LOCK:
        _OPEN[run_id] = rec
    return rec


def on_run_start(run) -> None:
    """RunManager ``on_start`` hook: open the record and follow the run's events."""
    rec = open_record(run.run_id, run.robot_id, run.user_id, getattr(run.role, 'value', str(run.role)),
                      run.goal, run.created)
    if rec is None:
        return
    with _LOCK:
        _UNSUB[run.run_id] = run.subscribe(rec.on_event)
    _health(rec, 'start')


def on_run_finish(run) -> None:
    """RunManager ``on_finish`` hook: end row, metrics line, close."""
    close_record(run.run_id, outcome=run.outcome, state=run.state.value)


def close_record(run_id: str, outcome: str = '', state: str = '') -> Optional[dict]:
    with _LOCK:
        rec = _OPEN.pop(run_id, None)
        unsub = _UNSUB.pop(run_id, None)
    if unsub is not None:
        try:
            unsub()
        except Exception:
            pass
    if rec is None:
        return None
    _health(rec, 'end')
    rec.row('end', outcome=outcome, state=state)
    try:
        with rec._lock:
            rows = list(rec.rows_mem)
        m = metrics_of(rows)
        rec.header.metrics = m
        rec.snapshot()
        write_metrics(m, rec.dir.parent)
        return m
    except Exception:
        return None


def write_metrics(m: dict, directory) -> None:
    """Append one line to ``<directory>/metrics.jsonl``."""
    try:
        p = Path(directory) / 'metrics.jsonl'
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(_to_jsonable(m), ensure_ascii=False) + '\n')
    except Exception:
        pass


def get_open(run_id: str) -> Optional[RunRecord]:
    with _LOCK:
        return _OPEN.get(run_id)


def current_record() -> Optional[RunRecord]:
    """The record of the run the calling thread acts for, or None."""
    forced = getattr(_TLS, 'record', None)
    if forced is not None:
        return forced
    with _LOCK:
        if not _OPEN:
            return None
    try:
        from ..core.run_control import current_run
        run = current_run()
    except Exception:
        return None
    return get_open(run.run_id) if run is not None else None


class bound:
    """``with bound(rec):`` — calls in this thread go to ``rec`` (tests,
    threads the run manager does not know)."""

    def __init__(self, rec: Optional[RunRecord]):
        self.rec, self.prev = rec, None

    def __enter__(self):
        self.prev = getattr(_TLS, 'record', None)
        _TLS.record = self.rec
        return self.rec

    def __exit__(self, *exc):
        _TLS.record = self.prev
        return False


def _health(rec: RunRecord, when: str) -> None:
    """The robot adapter's health() (best-effort; a robot without one says so)."""
    try:
        from .. import state
        st = state.current()
        pkg = getattr(st, 'robot_pkg', '')
        node = getattr(getattr(st, 'dm', None), '_ros_node', None)
        from ..contract import load_adapter
        h = load_adapter(pkg).health(node) if pkg else {'ok': None, 'note': 'no robot package'}
    except Exception as e:
        h = {'ok': None, 'error': str(e)}
    rec.row('health', when=when, health=shrink(h))


# --------------------------------------------------------------------------- #
# Capture helpers used by the hooks
# --------------------------------------------------------------------------- #
def _world_now() -> Optional[dict]:
    try:
        from .. import state
        return state.current().world.to_dict()
    except Exception:
        return None


def observed_skill(name: str, params: Any, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` (one skill call) and record it on the current run."""
    rec = current_record()
    if rec is None:
        return fn()
    depth = getattr(_TLS, 'skill_depth', 0)
    world = _world_now() if depth == 0 else None
    t0 = time.time()
    _TLS.skill_depth = depth + 1
    try:
        out = fn()
    except BaseException as e:
        _TLS.skill_depth = depth
        rec.skill(skill=name, params=params, depth=depth, error=f'{type(e).__name__}: {e}',
                  latency_ms=round((time.time() - t0) * 1000, 1), world_before=world,
                  result={'ok': False, 'code': 'software', 'msg': str(e)})
        raise
    _TLS.skill_depth = depth
    try:
        from ..contract import SkillResult
        sr = SkillResult.from_legacy(out)
        res = {'ok': sr.ok, 'code': sr.code.value if sr.code else None, 'msg': sr.msg}
    except Exception:
        res = {'ok': None}
    rec.skill(skill=name, params=params, depth=depth, raw=out, result=res,
              latency_ms=round((time.time() - t0) * 1000, 1), world_before=world)
    return out


def note_loop(loop: Any, task: str, lang: str = 'en', plan_only: bool = False,
              kind: Optional[str] = None, kwargs: Optional[dict] = None) -> None:
    """The ``context`` row: what the executive was started with (replay needs it)."""
    rec = current_record()
    if rec is None:
        return
    try:
        kind = kind or {'AssistLoop': 'assist', 'ClosedLoop': 'closed'}.get(type(loop).__name__,
                                                                          type(loop).__name__)
        cfg = dict(getattr(loop, 'cfg', {}) or {})
        llm_cfg = dict(getattr(loop, '_llm_cfg', {}) or {})
        for k in list(llm_cfg):
            if k.lower() in ('api_key', 'key', 'token', 'authorization', 'apikey'):
                llm_cfg.pop(k)
        try:
            from robot_agent.skill_configs import ENV
            env = dict(ENV)
        except Exception:
            env = None
        memory = None
        mp = cfg.get('memory_path')
        if mp and Path(mp).is_file():
            try:
                memory = json.loads(Path(mp).read_text())
            except Exception:
                memory = None
        rec.row('context', loop=kind, task=task, lang=lang, plan_only=bool(plan_only),
                kwargs=shrink(kwargs or {}), original=getattr(loop, 'original', None),
                cfg=shrink(cfg), llm_cfg=shrink(llm_cfg), env=_to_jsonable(env), world=_world_now(),
                object_memory=_to_jsonable(memory), robot_pkg=getattr(loop, 'robot_pkg', _robot_pkg()))
    except Exception:
        pass


def note_direct(plan: str, lang: str = 'en', kind: str = 'direct') -> None:
    """The ``context`` row of an open-loop run (``direct`` command list or ``open`` LLM plan)."""
    rec = current_record()
    if rec is None:
        return
    rec.row('context', loop=kind, task=plan, lang=lang, plan_only=False, world=_world_now(),
            robot_pkg=_robot_pkg())


# --------------------------------------------------------------------------- #
# Test doubles / foreign clients: transparent recording proxies
# --------------------------------------------------------------------------- #
class _Proxy:
    __slots__ = ('_inner',)

    def __init__(self, inner):
        object.__setattr__(self, '_inner', inner)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_inner'), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, '_inner'), name, value)


class RecordingSkills(_Proxy):
    """A skill registry whose ``execute`` is recorded (for registries that are
    not :class:`SkillRegistry`, e.g. a fake robot)."""
    __slots__ = ()

    def execute(self, name, params, node=None, log_fn=None):
        inner = object.__getattribute__(self, '_inner')
        return observed_skill(name, params, lambda: inner.execute(name, params, node, log_fn=log_fn))


class RecordingPlanner(_Proxy):
    """The planner boundary: ``generate_plan`` / ``replan`` inputs and outputs."""
    __slots__ = ()

    def _call(self, method, inputs, fn):
        rec = current_record()
        if rec is None:
            return fn()
        t0 = time.time()
        try:
            steps, meta = fn()
        except BaseException as e:
            rec.planner(method=method, inputs=inputs, error=f'{type(e).__name__}: {e}',
                        latency_ms=round((time.time() - t0) * 1000, 1))
            raise
        rec.planner(method=method, inputs=inputs, steps=steps, meta=meta,
                    latency_ms=round((time.time() - t0) * 1000, 1))
        return steps, meta

    def generate_plan(self, task, obs, visible_objects, **kw):
        # optional keywords (subgoals=, budget=) go through only when the caller passes them
        # (base.accepts looks through this proxy at the planner's own signature)
        inner = object.__getattribute__(self, '_inner')
        inputs = {'task': task, 'obs': obs, 'visible': visible_objects}
        if kw.get('subgoals'):
            inputs['subgoals'] = list(kw['subgoals'])
        return self._call('generate_plan', inputs,
                          lambda: inner.generate_plan(task, obs, visible_objects, **kw))

    def replan(self, task, completed, failed_step, failure_reason, obs, visible_objects, **kw):
        inner = object.__getattribute__(self, '_inner')
        return self._call('replan', {'task': task, 'completed': completed, 'failed_step': failed_step,
                                     'reason': failure_reason, 'obs': obs, 'visible': visible_objects},
                          lambda: inner.replan(task, completed, failed_step, failure_reason, obs,
                                               visible_objects, **kw))


def recording_planner(planner):
    """Wrap a planner backend once (``get_planner`` does this)."""
    return planner if isinstance(planner, RecordingPlanner) else RecordingPlanner(planner)


def instrument(loop) -> Any:
    """Record a loop built around test doubles: its skill registry, its LLM
    client (when it is not an ``LLMobj``, which records itself) and its planner.
    Returns ``loop``."""
    from .llm import RecordingLLM, records_itself
    if not isinstance(loop.sr, RecordingSkills):
        loop.sr = RecordingSkills(loop.sr)
    cli = getattr(loop, '_llm_client', None)
    if cli is not None and not records_itself(cli) and not isinstance(cli, RecordingLLM):
        loop._llm_client = RecordingLLM(cli)
    make = loop._make_planner
    if not getattr(make, '_observed', False):
        def _make():
            return recording_planner(make())
        _make._observed = True
        loop._make_planner = _make
    return loop
