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

The flag is process-global, not per-run: one robot per process (see
``robot_agent.state``), and Stop means "stop the robot", including skills
started from the CLI or a second client. It is cleared by :func:`begin_run` at
every execution entry point, so a cancel never leaks into the next run.
"""

import logging
import threading
import traceback

_dbg = logging.getLogger('robot_agent.spoken_debug')     # [spoken-debug] temporary

_CANCEL = threading.Event()
_LOCK = threading.Lock()

# "End Q&A" (dashboard button, POST /agent/qa/end): ends a running kcare `qa`
# conversation the way a stop word does — qa says goodbye and returns success,
# and the plan carries on. Unlike Cancel, nothing else is touched. Only a skill
# that called dialog_begin() can be ended (ask / reply need their answer; the
# emergency exchange must not be dismissed by a click).
_END_DIALOG = threading.Event()
_DIALOG = {'active': 0}


def cancel_requested() -> bool:
    """True between a :func:`request_cancel` and the next :func:`begin_run`."""
    return _CANCEL.is_set()


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
    """Clear the flag before a new execution (plan, skill, direct agent send)."""
    _dbg.warning('[spoken-debug] begin_run (cancel flag cleared)')
    with _LOCK:
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
    _dbg.warning('[spoken-debug] request_cancel from:\n%s',
                 ''.join(traceback.format_stack(limit=8)[:-1]))
    with _LOCK:
        _CANCEL.set()
        result = {'ok': True, 'cancelled': [], 'errors': {}}
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
    """A conversation that "End Q&A" may end has started (kcare qa)."""
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
