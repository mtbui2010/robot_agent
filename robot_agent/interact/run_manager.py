"""One active run per robot, and every utterance or command as an event into it.

Before this module the run was implicit: a process-global cancel flag
(``core.run_control``) that every execution entry cleared, no public run id,
and a second ``/ws/agent`` client simply started a second plan in parallel.

A :class:`Run` is the unit the dashboard, the API and the executive agree on:

  * ``run_id`` is stamped on every event the agent emits and is the key of
    ``GET /runs/{id}`` and ``POST /runs/{id}/cancel|pause|resume``;
  * cancel / pause / resume are per run (``threading.Event``), so starting or
    finishing one run can never clear the cancel of another;
  * the executive cooperates at its step boundaries: :meth:`Run.wait_if_paused`
    blocks while paused, :meth:`Run.check_cancel` raises :class:`RunCancelled`;
  * :meth:`RunManager.inject` takes anything said or sent while the run is
    active, classifies it with :mod:`.intent_router` and routes it (stop, pause,
    resume, answer to a pending question, status, or a queued goal change).

Roles: owner > member > guest. Someone with a lower role than the run's starter
cannot cancel, pause, resume, answer for or change that run; they get a
refusal with a reason, and the refusal is recorded on the run so the owner's
dashboard sees it. STATUS is open to everyone.

Not here yet (see docs/run_manager.md): applying MODIFY / ADD / DROP / NEW to
the running plan. They are queued on ``Run.pending`` for the executive to take
with :meth:`Run.take_pending`; :class:`TaskStack` is the interface a persistent
suspended-task stack will implement.
"""

from __future__ import annotations

import collections
import enum
import itertools
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from .intent_router import Intent, IntentDecision, IntentRouter

DEFAULT_ROBOT = 'default'
MAX_EVENTS = 1000          # events kept per run (oldest dropped)
MAX_FINISHED = 50          # finished runs kept for GET /runs


class RunState(str, enum.Enum):
    RUNNING = 'running'
    PAUSED = 'paused'
    CANCELLING = 'cancelling'
    DONE = 'done'
    FAILED = 'failed'


ACTIVE_STATES = (RunState.RUNNING, RunState.PAUSED, RunState.CANCELLING)


class Role(str, enum.Enum):
    OWNER = 'owner'
    MEMBER = 'member'
    GUEST = 'guest'

    @property
    def priority(self) -> int:
        return {'owner': 3, 'member': 2, 'guest': 1}[self.value]

    @classmethod
    def parse(cls, value, default: 'Role | None' = None) -> 'Role':
        """A role from a request field; unknown or missing -> ``default``
        (owner when not given: a client that predates roles keeps the rights it
        always had)."""
        if isinstance(value, Role):
            return value
        try:
            return cls(str(value).strip().lower())
        except Exception:
            return default or cls.OWNER


class RunCancelled(Exception):
    """Raised by :meth:`Run.check_cancel` once the run was asked to stop."""


@dataclass
class Refusal:
    """Why a request on a run was not carried out."""
    reason: str
    code: str = 'refused'          # refused | not_active | not_found | not_awaiting


@dataclass
class StartDecision:
    """What :meth:`RunManager.start` did. ``started`` False means a run was
    already active on that robot: ``run`` is that run and the caller becomes an
    observer of it (its text goes in via :meth:`RunManager.inject`)."""
    run: 'Run'
    started: bool
    decision: str                  # 'started' | 'busy'
    reason: str = ''


@dataclass
class InjectResult:
    """What :meth:`RunManager.inject` did with one event."""
    run_id: Optional[str]
    action: str                    # cancelled paused resumed answered status queued refused ignored
    decision: Optional[IntentDecision] = None
    ok: bool = True
    reason: str = ''
    status: Optional[dict] = None
    code: str = ''                 # on a refusal: refused | not_active | not_found

    def to_dict(self) -> dict:
        d = {'run_id': self.run_id, 'action': self.action, 'ok': self.ok}
        if self.reason:
            d['reason'] = self.reason
        if self.code:
            d['code'] = self.code
        if self.decision is not None:
            d['intent'] = self.decision.intent.value
            d['intent_source'] = self.decision.source
        if self.status is not None:
            d['status'] = self.status
        return d


class TaskStack(Protocol):
    """TODO(step 6b): a persistent stack of suspended runs (goals, achieved
    goals, belief) so a NEW command can suspend the current task and resume it
    later after ``reconcile()``. Not implemented; RunManager accepts one."""

    def push(self, run: 'Run') -> None: ...
    def pop(self, robot_id: str) -> Optional['Run']: ...
    def peek(self, robot_id: str) -> Optional['Run']: ...


_ids = itertools.count(1)


def _new_run_id() -> str:
    # Sortable and short: a time prefix, then random.
    return time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]


class Run:
    """One execution on one robot. Thread-safe; the executive thread polls it,
    API handlers and observers act on it."""

    def __init__(self, robot_id: str, goal: str = '', user_id: str = '',
                 role: Role = Role.OWNER, run_id: str | None = None):
        self.run_id = run_id or _new_run_id()
        self.robot_id = robot_id
        self.goal = goal
        self.user_id = user_id
        self.role = role
        self.created = time.time()
        self.updated = self.created
        self.outcome: str = ''             # set by finish(): success / failed / aborted / error text
        self.events: collections.deque = collections.deque(maxlen=MAX_EVENTS)
        self.pending: list[dict] = []      # MODIFY / ADD / DROP / NEW waiting for the executive
        self.begun = False                 # run_control.begin_run() bound the executive to it
        self._state = RunState.RUNNING
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._subs: dict[int, Callable[[dict], None]] = {}
        # A question the run is waiting on (ask / confirm): its text and the
        # answer slot that an ANSWER event fills.
        self._question: Optional[str] = None
        self._answer: Optional[str] = None
        self._answered = threading.Event()
        # A skill that must not be cut (manifest interruptible: false) is running:
        # (token, skill); a stop meanwhile is deferred until it ends.
        self._critical: Optional[tuple] = None
        self._critical_seq = 0
        self._stop_deferred = False

    # ---- state -------------------------------------------------------
    @property
    def state(self) -> RunState:
        return self._state

    @property
    def active(self) -> bool:
        return self._state in ACTIVE_STATES

    def _set_state(self, st: RunState) -> None:
        self._state = st
        self.updated = time.time()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    @property
    def paused(self) -> bool:
        return self._state == RunState.PAUSED

    # ---- cooperative points (called by the executive) -----------------
    def check_cancel(self) -> None:
        """Raise :class:`RunCancelled` once a stop was requested."""
        if self._cancel.is_set():
            raise RunCancelled(self.run_id)

    def wait_if_paused(self, timeout: float | None = None) -> bool:
        """Block while the run is paused. Returns True to carry on, False when
        the run was cancelled (a cancel always wakes a paused run) or the wait
        timed out still paused."""
        if not self._resume.wait(timeout):
            return False
        return not self._cancel.is_set()

    # ---- controls (called through RunManager) ------------------------
    def _cancel_now(self) -> bool:
        with self._lock:
            if not self.active:
                return False
            self._cancel.set()
            self._resume.set()             # wake a paused executive so it sees the cancel
            self._answered.set()           # and one blocked on a question
            if self._state != RunState.CANCELLING:
                self._set_state(RunState.CANCELLING)
            return True

    def _pause_now(self) -> bool:
        with self._lock:
            if self._state != RunState.RUNNING:
                return self._state == RunState.PAUSED   # idempotent
            self._resume.clear()
            self._set_state(RunState.PAUSED)
            return True

    def _resume_now(self) -> bool:
        with self._lock:
            if self._state != RunState.PAUSED:
                return self._state == RunState.RUNNING
            self._set_state(RunState.RUNNING)
            self._resume.set()
            return True

    # ---- questions ---------------------------------------------------
    @property
    def awaiting(self) -> Optional[str]:
        """The question the run waits on, or None."""
        return self._question

    def set_awaiting(self, question: Optional[str]) -> None:
        """Mark the run as waiting for an answer (None clears it). Used by a
        dialog that collects its answer elsewhere but wants interjections
        routed as ANSWER while it waits."""
        with self._lock:
            self._question = question
            self._answer = None
            self._answered.clear()
            self.updated = time.time()

    def ask(self, question: str, timeout: float | None = None) -> Optional[str]:
        """Wait for an ANSWER event to ``question``. None on timeout or cancel."""
        self.set_awaiting(question)
        try:
            self._answered.wait(timeout)
            return None if self._cancel.is_set() else self._answer
        finally:
            with self._lock:
                self._question = None

    def _deliver_answer(self, text: str) -> bool:
        with self._lock:
            if self._question is None:
                return False
            self._answer = text
            self._question = None
            self._answered.set()
            return True

    # ---- non-interruptible skills ------------------------------------
    @property
    def critical(self) -> Optional[str]:
        """The non-interruptible skill running now, or None."""
        c = self._critical
        return c[1] if c else None

    def begin_critical(self, what: str) -> int:
        """A skill that must not be cut starts. Returns its token."""
        with self._lock:
            self._critical_seq += 1
            self._critical = (self._critical_seq, what)
            self._stop_deferred = False
            return self._critical_seq

    def defer_stop(self) -> Optional[tuple]:
        """Called by the cancel hook: inside a non-interruptible skill the robot
        stop waits; returns (token, skill) then, else None (stop now)."""
        with self._lock:
            if self._critical is None:
                return None
            self._stop_deferred = True
            return self._critical

    def end_critical(self, token: int) -> bool:
        """The skill ended. True when a stop was deferred meanwhile (the caller
        stops the robot now)."""
        with self._lock:
            if self._critical is None or self._critical[0] != token:
                return False
            due, self._critical, self._stop_deferred = self._stop_deferred, None, False
            return due

    def take_deferred_stop(self, token: int) -> bool:
        """The wait for the skill ran out: True when its deferred stop is still
        due (the caller stops the robot; :meth:`end_critical` then returns False)."""
        with self._lock:
            if self._critical is None or self._critical[0] != token or not self._stop_deferred:
                return False
            self._stop_deferred = False
            return True

    # ---- pending goal changes -----------------------------------------
    def take_pending(self) -> list[dict]:
        """Hand the queued MODIFY / ADD / DROP / NEW events to the executive."""
        with self._lock:
            out, self.pending = self.pending, []
            return out

    # ---- events ------------------------------------------------------
    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        """Call ``cb(event)`` for every event published on this run from now
        on. Returns the unsubscribe function. ``cb`` must not block."""
        key = next(_ids)
        with self._lock:
            self._subs[key] = cb
        return lambda: self._subs.pop(key, None)

    def publish(self, event: dict) -> dict:
        """Stamp ``run_id``, keep the event, and fan it out to subscribers."""
        ev = dict(event)
        ev.setdefault('run_id', self.run_id)
        with self._lock:
            self.events.append(ev)
            self.updated = time.time()
            subs = list(self._subs.values())
        for cb in subs:
            try:
                cb(ev)
            except Exception:
                pass
        return ev

    def to_dict(self, events: bool = False) -> dict:
        d = {'run_id': self.run_id, 'robot_id': self.robot_id, 'user_id': self.user_id,
             'role': self.role.value, 'state': self._state.value, 'goal': self.goal,
             'created': self.created, 'updated': self.updated, 'outcome': self.outcome,
             'cancel_requested': self.cancel_requested, 'awaiting': self._question,
             'pending': list(self.pending), 'n_events': len(self.events)}
        if events:
            d['events'] = list(self.events)
        return d


def _outranks(role: Role, run: Run) -> Optional[str]:
    """None when ``role`` may act on ``run``, else the refusal reason."""
    if role.priority >= run.role.priority:
        return None
    who = f"{run.user_id or 'someone'} ({run.role.value})"
    return f'run {run.run_id} was started by {who}; a {role.value} cannot override it'


class RunManager:
    """The runs of this process, at most one active per robot."""

    def __init__(self, router: IntentRouter | None = None,
                 task_stack: TaskStack | None = None):
        self._lock = threading.RLock()
        self._active: dict[str, Run] = {}
        self._runs: collections.OrderedDict[str, Run] = collections.OrderedDict()
        self._local = threading.local()
        self.router = router or IntentRouter()
        self.task_stack = task_stack        # TODO(step 6b): suspend on NEW
        # Called with the run after a successful cancel, outside the locks:
        # run_control hooks the robot stop in (cancel ROS goals, release listens).
        self.on_cancel: list[Callable[[Run], None]] = []
        # Called with the run right after it is created (before its first
        # event) and after it has finished (after its last one), outside the
        # locks; robot_agent.observe opens / closes the run's RunRecord here.
        self.on_start: list[Callable[[Run], None]] = []
        self.on_finish: list[Callable[[Run], None]] = []

    # ---- lifecycle ---------------------------------------------------
    def start(self, goal: str = '', robot_id: str = DEFAULT_ROBOT, user_id: str = '',
              role: Role | str = Role.OWNER) -> StartDecision:
        """Start a run, or return the robot's active one with ``started=False``."""
        role = Role.parse(role)
        with self._lock:
            cur = self._active.get(robot_id)
            if cur is not None and cur.active:
                return StartDecision(cur, False, 'busy',
                                     f'run {cur.run_id} is {cur.state.value} on {robot_id}')
            run = Run(robot_id, goal=goal, user_id=user_id, role=role)
            self._active[robot_id] = run
            self._runs[run.run_id] = run
            self._trim()
        self._call_hooks(self.on_start, run)
        run.publish({'event': 'run_state', 'state': run.state.value, 'origin': 'run_manager'})
        return StartDecision(run, True, 'started')

    @staticmethod
    def _call_hooks(hooks, run: Run) -> None:
        for fn in list(hooks):
            try:
                fn(run)
            except Exception:
                pass

    def finish(self, run_id: str, ok: bool = True, outcome: str = '') -> Optional[Run]:
        """End a run: ``done`` (also after a cancel; outcome 'aborted') or
        ``failed``. Idempotent."""
        run = self.get(run_id)
        if run is None:
            return None
        with run._lock:
            if not run.active:
                return run
            cancelled = run.cancel_requested
            run.outcome = outcome or ('aborted' if cancelled else ('success' if ok else 'failed'))
            run._set_state(RunState.DONE if (ok or cancelled) else RunState.FAILED)
            run._resume.set()
            run._answered.set()
        with self._lock:
            if self._active.get(run.robot_id) is run:
                del self._active[run.robot_id]
        run.publish({'event': 'run_state', 'state': run.state.value, 'outcome': run.outcome,
                     'origin': 'run_manager'})
        self._call_hooks(self.on_finish, run)
        return run

    def _trim(self) -> None:
        done = [k for k, r in self._runs.items() if not r.active]
        for k in done[:max(0, len(done) - MAX_FINISHED)]:
            del self._runs[k]

    # ---- lookup ------------------------------------------------------
    def get(self, run_id: str) -> Optional[Run]:
        with self._lock:
            return self._runs.get(run_id)

    def active(self, robot_id: str | None = None) -> Optional[Run]:
        """The robot's active run; with no robot given, the only active run of
        the process (one robot per process), else None."""
        with self._lock:
            if robot_id is not None:
                r = self._active.get(robot_id)
                return r if r is not None and r.active else None
            live = [r for r in self._active.values() if r.active]
            return live[0] if len(live) == 1 else None

    def list(self) -> list[Run]:
        with self._lock:
            return list(reversed(self._runs.values()))

    # ---- thread binding (run_control shims) --------------------------
    def bind_thread(self, run: Optional[Run]) -> None:
        self._local.run = run

    def thread_run(self) -> Optional[Run]:
        return getattr(self._local, 'run', None)

    def current(self) -> Optional[Run]:
        """The run the calling thread belongs to, else the active run."""
        r = self.thread_run()
        if r is not None and r.active:
            return r
        return self.active()

    # ---- controls ----------------------------------------------------
    def _resolve(self, run_id: str) -> Run | Refusal:
        run = self.get(run_id)
        if run is None:
            return Refusal(f'no run {run_id!r}', 'not_found')
        if not run.active:
            return Refusal(f'run {run_id} is {run.state.value}', 'not_active')
        return run

    def _control(self, run_id: str, role, user_id: str, verb: str, fn) -> InjectResult:
        run = self._resolve(run_id)
        if isinstance(run, Refusal):
            return InjectResult(run_id, 'refused', ok=False, reason=run.reason, code=run.code)
        why = _outranks(Role.parse(role), run)
        if why:
            run.publish({'event': 'run_refused', 'action': verb, 'user_id': user_id,
                         'role': Role.parse(role).value, 'reason': why, 'origin': 'run_manager'})
            return InjectResult(run_id, 'refused', ok=False, reason=why, code='refused')
        was = run.state
        changed = fn(run)
        past = {'cancel': 'cancelled', 'pause': 'paused', 'resume': 'resumed'}[verb]
        if not changed:
            return InjectResult(run_id, 'ignored', ok=False,
                                reason=f'cannot {verb} a run that is {run.state.value}')
        if run.state != was:              # a repeated pause / cancel is not news
            run.publish({'event': 'run_state', 'state': run.state.value, 'action': verb,
                         'user_id': user_id, 'origin': 'run_manager'})
        return InjectResult(run_id, past)

    def cancel(self, run_id: str, role: Role | str = Role.OWNER, user_id: str = '',
               hooks: bool = True) -> InjectResult:
        """Stop the run. ``hooks=False`` skips :attr:`on_cancel` (the caller
        stops the robot itself, as ``run_control.request_cancel`` does)."""
        res = self._control(run_id, role, user_id, 'cancel', Run._cancel_now)
        if res.action == 'cancelled' and hooks:
            run = self.get(run_id)
            for cb in list(self.on_cancel):
                try:
                    cb(run)
                except Exception:
                    pass
        return res

    def pause(self, run_id: str, role: Role | str = Role.OWNER, user_id: str = '') -> InjectResult:
        return self._control(run_id, role, user_id, 'pause', Run._pause_now)

    def resume(self, run_id: str, role: Role | str = Role.OWNER, user_id: str = '') -> InjectResult:
        return self._control(run_id, role, user_id, 'resume', Run._resume_now)

    def inject(self, run_id: str, text: str = '', user_id: str = '',
               role: Role | str = Role.OWNER,
               decision: IntentDecision | None = None) -> InjectResult:
        """Route one utterance / command into a run (the router classifies,
        this executes). ``decision`` skips classification."""
        role = Role.parse(role)
        run = self._resolve(run_id)
        if isinstance(run, Refusal):
            return InjectResult(run_id, 'refused', decision, ok=False, reason=run.reason,
                                code=run.code)
        if decision is None:
            decision = self.router.classify(text, awaiting=run.awaiting, goal=run.goal)
        run.publish({'event': 'run_input', 'text': text, 'user_id': user_id, 'role': role.value,
                     'intent': decision.intent.value, 'intent_source': decision.source,
                     'origin': 'run_manager'})
        it = decision.intent
        if it == Intent.STATUS:
            return InjectResult(run_id, 'status', decision, status=run.to_dict())
        if it in (Intent.STOP, Intent.PAUSE, Intent.RESUME):
            fn = {Intent.STOP: self.cancel, Intent.PAUSE: self.pause, Intent.RESUME: self.resume}[it]
            res = fn(run_id, role, user_id)
            res.decision = decision
            return res
        why = _outranks(role, run)
        if why:
            run.publish({'event': 'run_refused', 'action': it.value, 'user_id': user_id,
                         'role': role.value, 'reason': why, 'origin': 'run_manager'})
            return InjectResult(run_id, 'refused', decision, ok=False, reason=why, code='refused')
        if it == Intent.ANSWER:
            if run._deliver_answer(text):
                return InjectResult(run_id, 'answered', decision)
            # The question was answered or withdrawn meanwhile: keep it as a change.
            decision = IntentDecision(Intent.NEW, text, 'fallback', 0.3, 'answer without a question')
        with run._lock:
            run.pending.append({'intent': decision.intent.value, 'text': text, 'user_id': user_id,
                                'role': role.value, 'at': time.time()})
        return InjectResult(run_id, 'queued', decision)


_MANAGER: Optional[RunManager] = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> RunManager:
    """The process's RunManager (one robot per process, see robot_agent.state)."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = RunManager()
        return _MANAGER


def reset_manager(manager: RunManager | None = None) -> RunManager:
    """Replace the process manager (tests)."""
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = manager or RunManager()
        return _MANAGER
