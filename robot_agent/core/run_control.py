"""Run-level cancellation — the backend half of the dashboard's Stop button.

Closing the agent WebSocket never reached the robot: the plan runs in a plain
thread (``UnifiedAgent.run``), so the skill in flight finished and every
remaining step still executed. Cancelling has to happen in three places, and
this module is what ties them together:

  1. the ROS goals already sent — :meth:`CustomNode.cancel_all` cancels every
     in-flight action goal and drops the service calls being waited on;
  2. the commands a skill would send next — while the flag is set, an action /
     service agent refuses to send instead of starting a fresh motion;
  3. the plan loop — it checks :func:`cancel_requested` between steps and ends
     the run with status ``aborted``.

Since the run manager (``robot_agent.interact.run_manager``) the flag is per
run. These functions keep their names and meaning for every caller (plan loops,
skills, the dashboard), and act on the *current run*: the run the calling
thread was bound to by :func:`begin_run`, else the robot's active run. With no
run at all (a skill started from the CLI) the old process-global flag is used,
so Stop still means "stop the robot".

:func:`begin_run` used to clear the flag at every entry, which also wiped a
Stop pressed for a run still finishing. It now never clears the cancel of an
active run: a new run starts with its own, clear flag.
"""

import contextlib
import os
import threading
import time

_CANCEL = threading.Event()
_LOCK = threading.Lock()

# "End Q&A" (dashboard button, POST /agent/qa/end): ends a running `qa` skill
# conversation the way a stop word does — qa says goodbye and returns success,
# and the plan carries on. Unlike Cancel, nothing else is touched. Only a skill
# that called dialog_begin() can be ended (ask / reply need their answer; the
# emergency exchange must not be dismissed by a click).
_END_DIALOG = threading.Event()
_DIALOG = {'active': 0}


def _manager():
    from ..interact.run_manager import get_manager
    mgr = get_manager()
    if not getattr(mgr, '_run_control_hooked', False):
        mgr.on_cancel.append(_on_run_cancel)
        # every run gets a RunRecord (robot_agent.observe.record)
        from ..observe.record import on_run_start, on_run_finish
        mgr.on_start.append(on_run_start)
        mgr.on_finish.append(on_run_finish)
        # a resume (pause ended / suspended task taken back): the executive reconciles at its next
        # pause_point, in its own thread
        mgr.on_resume.append(_on_run_resume)
        mgr._run_control_hooked = True
    return mgr


def manager():
    """The process RunManager, with the robot stop hooked into its cancel."""
    return _manager()


def _on_run_cancel(run) -> None:
    """A run was cancelled through the manager (a spoken "stop", POST
    /runs/{id}/cancel): stop the robot now, not only at the next step; inside a
    non-interruptible skill (Pick mid-grasp, a drawer pull) after it ends, see
    :func:`uninterruptible`. The run itself is cancelled at once either way."""
    held = run.defer_stop() if run is not None else None
    if held is not None:
        token, skill = held
        max_s = uninterruptible_max_s()
        run.publish({'event': 'run_stop_deferred', 'skill': skill, 'max_s': max_s, 'origin': 'run_control',
                     'msg': f'stopping when {skill} has finished (it must not be cut)'})
        t = threading.Timer(max_s, _deferred_stop_due, (run, token))
        t.daemon = True
        t.start()
        return
    with _LOCK:
        _stop_robot()


def uninterruptible_max_s() -> float:
    """How long a stop waits for a non-interruptible skill before it cuts it anyway."""
    try:
        return float(os.environ.get('ROBOT_AGENT_UNINTERRUPTIBLE_MAX_S', '30'))
    except ValueError:
        return 30.0


def _deferred_stop_due(run, token) -> None:
    if run.take_deferred_stop(token):
        with _LOCK:
            _stop_robot()


@contextlib.contextmanager
def uninterruptible(skill: str, interruptible: bool = False):
    """Run a skill the manifest marks ``interruptible: false`` inside this: a
    stop of the run that arrives meanwhile does not cancel the robot's goals
    until the skill has finished (at most :func:`uninterruptible_max_s`), so a
    grasp or a drawer pull is not cut half-way. The run is cancelled at once and
    the executive stops at the step boundary. ``interruptible`` True (or no run)
    is a no-op. The hardware e-stop never goes through here."""
    run = current_run() if not interruptible else None
    if run is None:
        yield
        return
    token = run.begin_critical(skill)
    try:
        yield
    finally:
        if run.end_critical(token):
            with _LOCK:
                _stop_robot()


def is_cancel(exc: BaseException) -> bool:
    """Is ``exc`` the run's stop (``RunCancelled``)?"""
    from ..interact.run_manager import RunCancelled
    return isinstance(exc, RunCancelled)


def call_cancellable(fn, *args, poll: float = 0.01, **kwargs):
    """``fn(*args, **kwargs)`` (an LLM / planner call) that a stop does not wait
    for: it runs in a worker thread and ``RunCancelled`` is raised within
    ``poll`` s of the run's stop (the call's result is then dropped). With no
    run, a plain call."""
    run = current_run()
    if run is None:
        return fn(*args, **kwargs)
    from ..interact.run_manager import RunCancelled
    run.check_cancel()
    box: dict = {}
    done = threading.Event()

    def work():
        try:
            box['value'] = fn(*args, **kwargs)
        except BaseException as e:      # noqa: BLE001 - re-raised in the caller
            box['error'] = e
        finally:
            done.set()
    threading.Thread(target=work, name='cancellable-call', daemon=True).start()
    while not done.wait(poll):
        if run.cancel_requested:
            raise RunCancelled(run.run_id)
    if 'error' in box:
        raise box['error']
    return box['value']


# ── the robot's questions (state in robot_agent.interact.dialog) ─────────────
def ask_person(question: str, io, silence_retries: int = 0, again: str | None = None):
    """Ask the person ``question`` through ``io`` (the robot's Ask skill) with
    the current run waiting on it, so a typed / injected answer from any client
    answers it. Returns ``(Answer, times asked)``; ``Answer.source`` is
    'robot' (heard), 'typed' (injected) or '' (no answer / stopped)."""
    from ..interact import dialog
    kw = {'again': again} if again else {}
    return dialog.ask_heard(current_run(), question, io, silence_retries=silence_retries, **kw)


def yes_no(text):
    """(True | False | None, correction text) of a spoken confirmation."""
    from ..interact.dialog import yes_no as _yn
    return _yn(text)


# ── suspended tasks (a NEW command that goes first) ──────────────────────────
def suspend_task(task: str, goals: list, achieved: list, belief: dict, reason: str = ''):
    """Push the current run's task on the task stack. None with no run."""
    run = current_run()
    if run is None:
        return None
    from ..interact.run_manager import TaskFrame
    return _manager().suspend(run, TaskFrame(run_id=run.run_id, task=task, goals=list(goals),
                                             achieved=list(achieved), belief=dict(belief), reason=reason))


def resume_task():
    """Pop the current run's last suspended task (runs the manager's
    ``on_resume`` hooks); None when there is none."""
    run = current_run()
    return _manager().resume_task(run) if run is not None else None


def current_run():
    """The run the calling thread acts for (bound by :func:`begin_run`, else
    the active run), or None."""
    try:
        return _manager().current()
    except Exception:
        return None


def cancel_requested() -> bool:
    """True once the current run was asked to stop; with no run, between a
    :func:`request_cancel` and the next :func:`begin_run`."""
    run = current_run()
    if run is not None:
        return run.cancel_requested
    return _CANCEL.is_set()


def wait_if_paused(timeout: float | None = None) -> bool:
    """Cooperative pause point between two steps: blocks while the current run
    is paused. True to carry on, False when the run was cancelled meanwhile.
    No run -> returns True at once."""
    run = current_run()
    if run is None:
        return not _CANCEL.is_set()
    return run.wait_if_paused(timeout)


def _on_run_resume(run) -> None:
    """RunManager ``on_resume`` hook: count the resume; :func:`pause_point` acts on it."""
    run.resumes = getattr(run, 'resumes', 0) + 1


def pause_point(on_resume=None) -> bool:
    """:func:`wait_if_paused` plus the resume hook: after the run carried on from a hold (a pause ended,
    the RunManager's ``on_resume`` fired), ``on_resume(seconds_held)`` runs here, in the executive
    thread, before the next step (the loops reconcile their belief there: the world may have changed
    meanwhile). Each resume triggers it once, whether the executive or the hook sees it first; never
    after a cancel."""
    run = current_run()
    was_paused = run is not None and run.paused
    t0 = time.time()
    ok = wait_if_paused()
    if not ok or run is None:
        return ok
    seen, resumes = getattr(run, '_resumes_seen', 0), getattr(run, 'resumes', 0)
    if was_paused or resumes > seen:
        # the hook may not have run yet when this thread woke up: count that resume as seen already
        run._resumes_seen = max(resumes, seen + 1)
        if on_resume is not None:
            try:
                on_resume(time.time() - t0)
            except Exception:
                pass
    return ok


def mark_resumed() -> None:
    """The executive reconciled after a resume itself (a suspended task taken back): the pending
    ``on_resume`` count needs no :func:`pause_point` reconcile."""
    run = current_run()
    if run is not None:
        run._resumes_seen = getattr(run, 'resumes', 0)


def check_cancel() -> None:
    """Raise ``RunCancelled`` when :func:`cancel_requested`."""
    if cancel_requested():
        from ..interact.run_manager import RunCancelled
        run = current_run()
        raise RunCancelled(run.run_id if run is not None else '')


def _node(node=None):
    """The process's CustomNode, or None when ROS never came up."""
    if node is not None:
        return node
    try:
        from ..state import current
        return getattr(current().dm, '_ros_node', None)
    except Exception:
        return None


def begin_run(node=None) -> None:
    """A new execution starts (plan, skill, direct agent send).

    The calling thread is bound to the robot's active run when that run has
    not begun yet (the API started it and this is its executive). The stale
    process flag and the node's cancel are cleared, except while the active run
    is being cancelled: a skill command or a second entry must not undo
    someone's Stop."""
    with _LOCK:
        run = None
        try:
            mgr = _manager()
            run = mgr.active()
            if run is not None and not run.begun:
                run.begun = True
                mgr.bind_thread(run)
            elif mgr.thread_run() is not None and not mgr.thread_run().active:
                mgr.bind_thread(None)      # this thread's old run is over
        except Exception:
            run = None
        if run is not None and run.cancel_requested:
            return
        _CANCEL.clear()
        _END_DIALOG.clear()
        n = _node(node)
        if n is not None:
            try:
                n.clear_cancel()
            except Exception:
                pass


def request_cancel(node=None) -> dict:
    """Stop the robot: cancel in-flight ROS work and block further commands.

    Returns ``{'ok', 'cancelled', 'errors'}`` — which agents had something to
    cancel, and which raised while doing so. Never raises: a Stop button that
    fails with a 500 is worse than a partial stop.
    """
    with _LOCK:
        _CANCEL.set()
        result = {'ok': True, 'cancelled': [], 'errors': {}}
        # The active run ends too (no role check: this is the Stop button and
        # the "client went away" path).
        try:
            run = _manager().active()
            if run is not None:
                _manager().cancel(run.run_id, role='owner', hooks=False)
                result['run_id'] = run.run_id
        except Exception as e:
            result['errors']['run'] = str(e)
        out = _stop_robot(node)
        result['errors'].update(out.pop('errors'))
        result.update(out)
        return result


def _stop_robot(node=None) -> dict:
    """Cancel the ROS work in flight and release blocked listens."""
    result = {'errors': {}}
    n = _node(node)
    if n is not None:
        try:
            result.update(n.cancel_all())
        except Exception as e:
            result['errors']['node'] = str(e)
    # Release any HRI skill blocked on the dashboard microphone, which would
    # otherwise sit in listen_dashboard() until its timeout.
    try:
        from ..utils import cancel_pending_listens
        result['listens_released'] = cancel_pending_listens()
    except Exception as e:
        result['errors']['listen'] = str(e)
    return result


def dialog_begin() -> None:
    """A conversation that "End Q&A" may end has started (a qa skill)."""
    with _LOCK:
        _END_DIALOG.clear()
        _DIALOG['active'] += 1


def dialog_end() -> None:
    """That conversation is over (always called, also on errors)."""
    with _LOCK:
        _DIALOG['active'] = max(0, _DIALOG['active'] - 1)
        _END_DIALOG.clear()


def dialog_active() -> bool:
    return _DIALOG['active'] > 0


def end_dialog_requested() -> bool:
    """True once "End Q&A" was pressed during the running conversation."""
    return _END_DIALOG.is_set()


def request_end_dialog() -> dict:
    """End the running conversation: set the flag and release the listen it
    waits on (an empty answer, so it returns at once instead of after its
    listen window). Returns ``{'ok', 'listens_released'}``, or ``{'ok': False,
    'detail'}`` when no conversation is running."""
    with _LOCK:
        if _DIALOG['active'] <= 0:
            return {'ok': False, 'detail': 'no qa conversation is running'}
        _END_DIALOG.set()
    from ..utils import end_pending_listens
    return {'ok': True, 'listens_released': end_pending_listens()}
