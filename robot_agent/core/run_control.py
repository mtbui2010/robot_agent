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

import threading

_CANCEL = threading.Event()
_LOCK = threading.Lock()


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
    with _LOCK:
        _CANCEL.clear()
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
