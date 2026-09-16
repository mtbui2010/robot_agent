"""``CustomNode`` — one rclpy node that owns a dict of named connection agents.

A skill never touches rclpy directly: it calls ``node.agents['<name>'].send(...)``
or ``.get()``, and the agent handles the publisher / subscription / service
client / action client underneath. :class:`~robot_agent.core.device_manager.DeviceManager`
builds one of these per process from ``connections.json``.

Absorbed from ``pyconnect.ros.custom_node``. Behavioural differences:
  * logs go to :func:`robot_agent.connect.paths.log_dir` instead of a directory
    inside the installed package;
  * the module-level ``test_*`` demos were dropped.
"""

import array
import copy
import glob
import os
import threading
import time
import traceback

import cv2
import numpy as np
import rclpy
from rclpy.action import ActionClient, ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from ..helpers import Timer, data_info, printif, strftime, update_dict
from ..serde import write_json
from ..paths import log_dir as _log_dir
from ..tcp_ip.node import make_agent as make_tcpip_agent

try:
    from rosinterfaces.action import SendStringData as SendData
    from rosinterfaces.srv import SendStringData as SendSeviceData
except Exception:
    printif('rosinterfaces not found — ROS service/action interfaces unavailable.')
    SendData = None
    SendSeviceData = None

# Initialise rclpy once per process; guard against double-init.
if not rclpy.ok():
    rclpy.init()

# action_msgs/GoalStatus values, used to tell a cancelled goal from a real
# result. Imported rather than hard-coded — guessing them once already turned
# every SUCCEEDED (4) goal into a reported "aborted".
try:
    from action_msgs.msg import GoalStatus as _GoalStatus
    _STATUS_EXECUTING = _GoalStatus.STATUS_EXECUTING
    _STATUS_SUCCEEDED = _GoalStatus.STATUS_SUCCEEDED
    _STATUS_CANCELED = _GoalStatus.STATUS_CANCELED
    _STATUS_ABORTED = _GoalStatus.STATUS_ABORTED
except Exception:
    _STATUS_EXECUTING, _STATUS_SUCCEEDED, _STATUS_CANCELED, _STATUS_ABORTED = 2, 4, 5, 6


# ---------------------------------------------------------------------------
# Logging locations
# ---------------------------------------------------------------------------

def log_node_dir() -> str:
    return str(_log_dir('ros', 'node'))


def log_sub_dir() -> str:
    return str(_log_dir('ros', 'sub'))


def find_last_session_log_dir(log_dir=None):
    """Most recent per-session log directory, or None when there is none yet."""
    log_dir = log_node_dir() if log_dir is None else log_dir
    dirs = [el for el in glob.glob(os.path.join(log_dir, '*')) if os.path.isdir(el)]
    if not dirs:
        return None
    return sorted(dirs)[-1]


def exception_handler(func):
    """Turn an exception into the ``{'isdone': False, 'msg': ...}`` skill contract.

    Deliberately minimal — the richer, logging-aware variant that skills use is
    :func:`robot_agent.utils.exception_handler`; importing it here would make
    ``robot_agent.utils`` and the connect layer circular.
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            return {'isdone': False, 'msg': f"'Exception in {func.__name__}': {e}"}
    return wrapper


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class NodeAgent:
    def __init__(self, agent_name=None, conn_name='unnamed_conn', conn_type='sub', executor=None, is_init=False,
                 data_interface=String, encode_func=None, decode_func=None, response_func=None, do_log_msg=False, **kwargs):

        self.conn_name, self.conn_type, self.executor = conn_name, conn_type, executor
        self.agent_name = conn_name if agent_name is None else agent_name
        self.rev_data, self.ret_data, self.msg_id = None, None, None
        self.id = f'{self.agent_name}.{self.conn_name}.{self.conn_type}'
        self.is_init, self.do_log_msg = is_init, do_log_msg
        self.data_interface = data_interface
        self.encode_func, self.decode_func, self.response_func = encode_func, decode_func, response_func
        self.connected = False
        self.wait_until_done = kwargs.get('wait_until_done', True)
        # Set by make_agent — lets an agent see the node-wide cancel flag.
        self.node = None
        # Optional per-connection cancel written by the user (DevicePanel's
        # `cancel_func` box, stored in connections.json like encode/decode):
        #   def cancel_func(node, agent) -> bool
        # When given it replaces the default cancel for this connection.
        self.cancel_func = kwargs.get('cancel_func')

    def make_log_dirs(self, make_new_dir=None):
        """Create directories for logging messages and images.

        Args:
            make_new_dir (bool, optional): Force creation of a new log directory.
                Defaults to self.is_init if None.
        """
        session_dir = find_last_session_log_dir()
        make_new_dir = self.is_init if make_new_dir is None else make_new_dir
        if make_new_dir or session_dir is None:
            session_dir = os.path.join(log_node_dir(), strftime())
        self.log_msg_dir = os.path.join(session_dir, 'msg')
        self.log_img_dir = os.path.join(session_dir, 'img')
        os.makedirs(self.log_msg_dir, exist_ok=True)
        os.makedirs(self.log_img_dir, exist_ok=True)

    def log_msg(self, data, msg_type='returned', make_new_dir=None):

        self.make_log_dirs(make_new_dir=make_new_dir)
        msg_id = f'{strftime()}@{self.id}@{msg_type}'.replace('/', '.')
        try:
            write_json(os.path.join(self.log_msg_dir, f'{msg_id}.json'), skip_non_serializable=True,
                       adict={k: v for k, v in data.items()
                              if not isinstance(v, np.ndarray) and k != 'ins'})
            for k, v in data.items():
                if isinstance(v, np.ndarray):
                    try:
                        threading.Thread(
                            target=lambda k=k, v=v: cv2.imwrite(
                                os.path.join(self.log_img_dir, f'{msg_id}_{k}.png'),
                                v if len(v.shape) <= 2 else v[..., ::-1]
                            ), daemon=True
                        ).start()
                    except Exception as e:
                        print(f"Image save failed for '{k}': {e}")
                        np.save(os.path.join(self.log_img_dir, f'{msg_id}_{k}.npy'), v)
                elif k == 'ins':
                    np.save(os.path.join(self.log_img_dir, f'{msg_id}_{k}.npy'), v)
        except Exception as e:
            print(f'log_msg JSON write failed: {e}')
            np.save(os.path.join(self.log_msg_dir, f'{msg_id}.npy'), data)
        printif(f'{msg_id} saved', do_print=self.do_log_msg)

    def reform_response(self, ret_data):
        """Format response data to ensure it includes an 'isdone' key."""
        if not isinstance(ret_data, dict):
            return {'isdone': True, 'ret': ret_data}
        if 'isdone' in ret_data:
            return ret_data
        ret_data['isdone'] = True
        return ret_data

    def get(self, data={}, **kwargs):

        if self.conn_type == 'sub':
            if 'show_info' in kwargs and kwargs['show_info']:
                print(data_info(self.rev_data))
            return self.rev_data
        elif 'client' in self.conn_type:
            return self.send(data=data, **kwargs)
        else:
            raise NotImplementedError

    def send(self, **kwargs):
        pass

    def cancel(self) -> bool:
        """Abort whatever this agent has in flight, best effort.

        False means this connection kind has nothing to cancel (a subscription,
        a publisher, a timer) — not that cancelling failed. Overridden by the
        action and service clients, the only agents that wait on the robot.
        """
        return False

    def _node_cancelled(self) -> bool:
        """True while the owning node is under a :meth:`CustomNode.cancel_all`.

        Checked before sending so a skill that keeps issuing commands after one
        of them was cancelled cannot start a fresh motion.
        """
        node = getattr(self, 'node', None)
        return bool(node is not None and getattr(node, 'cancel_requested', False))

    @property
    def raw(self):
        """Underlying rclpy primitive (Client / Publisher / Subscription / ActionClient).

        Public escape hatch for skills that want pure-rclpy control (custom QoS,
        callback groups, timeouts, action feedback handling, ...) while still
        reusing the connection registered via DeviceManager.

        Returns None for conn_types that do not have an rclpy executor object
        attached (e.g. action_server, service_server, sub).
        """
        return getattr(self, 'executor', None)


class ActionServerAgent(NodeAgent):
    """ROS 2 action server agent.

    Extra keyword arg:
        feedback_interval (float): Seconds between feedback publications.
                                   Default ``0.1`` s (10 Hz).  Reduce for
                                   latency-sensitive actions; increase to lower
                                   overhead on long-running ones.

    Example::

        node.add_agent(**get_action_server_configs(
            agent_name='pick',
            response_func=pick_handler,
            feedback_interval=0.05,   # 20 Hz feedback
        ))
    """

    def __init__(self, *args, feedback_interval: float = 0.1, **kwargs):
        super().__init__(*args, **kwargs)
        self.feedback_interval = feedback_interval

    def callback(self, goal_handle):

        printif(f'{"=" * 5}{self.id}: Received goal', do_print=self.do_log_msg)
        assert self.encode_func is not None and self.decode_func is not None

        feedback = self.data_interface.Feedback()
        feedback.status = 'processing'

        stop_event = threading.Event()

        # Publish feedback at the configured rate until the goal finishes.
        def feedback_loop():
            while not stop_event.is_set():
                goal_handle.publish_feedback(feedback)
                time.sleep(self.feedback_interval)

        feedback_thread = threading.Thread(target=feedback_loop, daemon=True)
        feedback_thread.start()

        try:
            request = goal_handle.request
            rev_data = self.decode_func(request)

            ret_data = rev_data if self.response_func is None else self.response_func(rev_data)
            ret_data = self.reform_response(ret_data)

            stop_event.set()
            feedback_thread.join()

            feedback.status = 'completed'
            goal_handle.publish_feedback(feedback)
            goal_handle.succeed()

            result = self.data_interface.Result()
            result = self.encode_func(ret_data, result)
            printif(f'{"=" * 5}{self.id}: Goal completed and data sent back', do_print=self.do_log_msg)
            return result

        except Exception as e:
            # Ensure feedback thread stops even on error
            stop_event.set()
            feedback_thread.join()

            printif(f'{"=" * 5}{self.id}: Error occurred: {e}', do_print=self.do_log_msg)
            traceback.print_exc()

            feedback.status = f'error: {str(e)}'
            goal_handle.publish_feedback(feedback)
            goal_handle.abort()

            result = self.data_interface.Result()
            try:
                result = self.encode_func({'error': str(e)}, result)
            except Exception:
                # Fallback if encode_func also fails
                result = self.data_interface.Result()
                if hasattr(result, 'error'):
                    result.error = str(e)
            return result


class ActionClientAgent(NodeAgent):
    def __init__(self, agent_name=None, conn_name='unnamed_conn', conn_type='sub', executor=None, is_init=False,
                 data_interface=String, encode_func=None, decode_func=None, response_func=None, do_log_msg=False, **kwargs):
        super().__init__(agent_name, conn_name, conn_type, executor, is_init, data_interface,
                         encode_func, decode_func, response_func, do_log_msg, **kwargs)
        self.feedback_status = None
        self.timeout = kwargs.pop('timeout', None)
        # Goal handles accepted by the server and not finished yet — what
        # cancel() sends cancel_goal_async() to.
        self._goal_handles = []
        self._goal_lock = threading.Lock()
        self._cancelled = False
        self._cancel_cli = None

    @exception_handler
    def send(self, data, **kwargs):
        """Send an action goal to the server and log data if enabled."""
        assert self.encode_func is not None and self.decode_func is not None
        if self._node_cancelled():
            return {'isdone': False, 'msg': f'{self.id}: cancelled'}
        self._cancelled = False
        if self.do_log_msg:
            printif(data_info(data), do_print=self.do_log_msg)
            self.log_msg(data, msg_type='sent')

        goal_msg = self.data_interface.Goal()
        goal_msg = self.encode_func(data, goal_msg)

        printif(f'{"=" * 5}{self.id}: Sending goal to server...', do_print=self.do_log_msg)
        if not self.executor.wait_for_server(timeout_sec=3.0):
            printif(f'{"=" * 5}{self.id} Action Server not available', do_print=self.do_log_msg)
            return {'isdone': False, 'msg': f'{self.id}: action server not available'}

        self.rev_data = None
        goal_handle = self.executor.send_goal_async(
            goal_msg, feedback_callback=self.feedback_callback
        )
        goal_handle.add_done_callback(self.goal_response_callback)

        tic = time.perf_counter()
        while self.wait_until_done and rclpy.ok() and self.rev_data is None:
            printif(f'{"=" * 5}{self.id}: Feedback {self.feedback_status}', do_print=self.do_log_msg)
            printif(f'{"=" * 5}{self.id}: Received {self.rev_data}', do_print=self.do_log_msg)
            time.sleep(0.5)
            toc = time.perf_counter() - tic
            printif(f'{"=" * 5}{self.id}: Duration {toc:.3f} s', do_print=self.do_log_msg)
            if self.timeout is not None and toc > self.timeout:
                printif(f'{"=" * 5}{self.id}: Timed out after {toc:.3f} s', do_print=self.do_log_msg)
                self.rev_data = {'isdone': False, 'msg': f'{self.id}: timed out waiting for result'}
                break

        rev_data = self.rev_data
        self.rev_data = None

        return rev_data

    def feedback_callback(self, feedback_msg):
        """Process feedback from the action server."""
        try:
            self.feedback_status = feedback_msg.feedback.status
        except AttributeError:
            self.feedback_status = 'undefined'

    def goal_response_callback(self, future):
        """Handle the server's response to the goal request."""
        try:
            goal_handle = future.result()
        except Exception as e:
            printif(f'{"=" * 5}{self.id}: Error getting goal handle: {e}', do_print=self.do_log_msg)
            self.rev_data = {'isdone': False, 'msg': f'{self.id}: error getting goal handle: {e}'}
            return

        if not goal_handle.accepted:
            printif(f'{"=" * 5}{self.id}: Goal was rejected', do_print=self.do_log_msg)
            self.rev_data = {'isdone': False, 'msg': f'{self.id}: goal rejected by server'}
            return

        printif(f'{"=" * 5}{self.id}: Goal accepted, waiting for result...', do_print=self.do_log_msg)
        with self._goal_lock:
            self._goal_handles.append(goal_handle)
        # cancel() may have run between send() and the server accepting: cancel
        # this goal right away instead of letting the motion start.
        if self._cancelled:
            try:
                goal_handle.cancel_goal_async()
            except Exception:
                pass
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self.result_callback)

    def result_callback(self, future):
        """Process the final result from the action server."""
        try:
            outcome = future.result()
            # A cancelled goal still carries a Result message, and decode_func
            # would read its default fields as a success — kaair's action
            # results are plain structs. Only CANCELED is special-cased: an
            # ABORTED goal keeps decoding as it always did, since servers like
            # GripperActionController abort on a perfectly normal stall.
            status = getattr(outcome, 'status', None)
            if status is not None and status == _STATUS_CANCELED:
                printif(f'{"=" * 5}{self.id}: Goal cancelled', do_print=self.do_log_msg)
                self.rev_data = {'isdone': False, 'msg': f'{self.id}: goal cancelled'}
                self._forget_goal(getattr(future, '_goal_handle', None))
                return
            result = outcome.result
            self.rev_data = self.decode_func(result)
            if self.rev_data is None:
                self.rev_data = {'ret': None}
        except Exception as e:
            printif(f'{"=" * 5}{self.id}: Error processing result: {e}', do_print=self.do_log_msg)
            self.rev_data = {'isdone': False, 'msg': f'{self.id}: error processing result: {e}'}
            return

        printif(f'{"=" * 5}{self.id}: Return received...', do_print=self.do_log_msg)
        self._forget_goal(getattr(future, '_goal_handle', None))
        if self.do_log_msg:
            printif(data_info(self.rev_data), do_print=self.do_log_msg)
            self.log_msg(self.rev_data, msg_type='retunred')

    def _forget_goal(self, goal_handle=None) -> None:
        """Drop finished goals so cancel() only touches live ones."""
        with self._goal_lock:
            if goal_handle is not None and goal_handle in self._goal_handles:
                self._goal_handles.remove(goal_handle)
            else:
                # The result future does not carry its handle on every rclpy
                # version; a finished goal is harmless to cancel, but keeping
                # the list bounded matters more than precision here.
                self._goal_handles = [
                    h for h in self._goal_handles
                    if getattr(h, 'status', None) in (None, _STATUS_EXECUTING)
                ]

    def _cancel_goal_client(self):
        """Lazy client for this action's built-in ``<conn_name>/_action/cancel_goal``.

        Derived from ``conn_name``, so every action client can cancel without
        an extra entry in connections.json.
        """
        if self._cancel_cli is None:
            if self.node is None:
                return None
            from action_msgs.srv import CancelGoal
            self._cancel_cli = self.node.create_client(
                CancelGoal, f'{self.conn_name}/_action/cancel_goal')
        return self._cancel_cli

    def cancel_all_goals(self, timeout_sec: float = 0.3) -> bool:
        """Ask the server to drop every goal — the Python form of::

            ros2 service call /navigate_to_pose/_action/cancel_goal \\
              action_msgs/srv/CancelGoal \\
              "{goal_info: {goal_id: {uuid: [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]}, \\
                stamp: {sec: 0, nanosec: 0}}}"

        A zero uuid with a zero stamp is CANCEL_ALL, so this also stops goals
        this process never held — sent by another client, or left running
        across a backend restart. ``move::home`` with the base already driving
        is the case it exists for.
        """
        try:
            from action_msgs.srv import CancelGoal
            cli = self._cancel_goal_client()
            if cli is None:
                return False
            if not cli.wait_for_service(timeout_sec=timeout_sec):
                printif(f'{"=" * 5}{self.id}: cancel_goal service not available',
                        do_print=self.do_log_msg)
                return False
            cli.call_async(CancelGoal.Request())   # defaults: uuid all-zero, stamp 0
            printif(f'{"=" * 5}{self.id}: cancel_goal sent (all goals)', do_print=self.do_log_msg)
            return True
        except Exception as e:
            printif(f'{"=" * 5}{self.id}: cancel_goal failed: {e}', do_print=self.do_log_msg)
            return False

    def cancel(self) -> bool:
        """Cancel this action's unfinished requests.

        Two steps, because they cover different goals: ``cancel_goal_async()``
        on the handles this process is waiting on, then a CANCEL_ALL on the
        action's ``/_action/cancel_goal`` service for anything else the server
        still has. The kcare servers (arm / lift / head moves,
        navigate_to_pose) accept both and stop the motion.

        A connection whose config defines ``cancel_func(node, agent)`` uses
        that instead — for a robot whose action cannot be stopped this way.

        ``rev_data`` is filled in either way, so a ``send()`` blocked in its
        wait loop returns immediately even if the server never answers.
        """
        self._cancelled = True
        with self._goal_lock:
            handles, self._goal_handles = list(self._goal_handles), []

        if self.cancel_func is not None:
            try:
                done = bool(self.cancel_func(self.node, self))
            except Exception as e:
                printif(f'{"=" * 5}{self.id}: cancel_func failed: {e}', do_print=self.do_log_msg)
                done = False
        else:
            done = False
            for h in handles:
                try:
                    h.cancel_goal_async()
                    done = True
                except Exception as e:
                    printif(f'{"=" * 5}{self.id}: cancel failed: {e}', do_print=self.do_log_msg)
            done = self.cancel_all_goals() or done

        if done or self.rev_data is None:
            self.rev_data = {'isdone': False, 'msg': f'{self.id}: cancelled'}
        return done


class TopicAgent(NodeAgent):
    def callback(self, msg):
        """Process incoming topic messages and apply response function if provided."""
        if not hasattr(self, 'is_firstmsg'):
            self.is_firstmsg = True
            self.connected = True

        assert self.decode_func is not None
        data = self.decode_func(msg)
        if self.is_firstmsg:
            print(f'{"=" * 5}{self.id}: msg received')
            print(data_info(data))
            self.is_firstmsg = False
            self.connected = True
        self.rev_data = data

        if self.response_func is not None:
            self.response_func(data)
            if self.do_log_msg:
                print(f'{"=" * 5}{self.id}: Process finished')

    def send(self, data, do_log_msg=False):
        """Publish data to a topic."""
        assert self.encode_func is not None
        if self.do_log_msg:
            print(f'{"=" * 5}{self.id}: Data published')
            self.log_msg(data=data, msg_type='sent')

        msg = self.data_interface()
        msg = self.encode_func(data, msg)
        self.executor.publish(msg)

    def publish(self):
        """Publish data to a topic (used for periodic publishing)."""
        assert self.encode_func is not None
        msg = self.data_interface()
        msg = self.encode_func(None, msg)

        if not hasattr(self, 'is_firstmsg'):
            self.is_firstmsg = True
        if self.is_firstmsg:
            printif(f'{"=" * 5}{self.id}: Data published', do_print=self.do_log_msg)
            self.is_firstmsg = False

        self.executor.publish(msg)


class ServiceServerAgent(NodeAgent):
    def callback(self, request, response):
        """Handle incoming service requests and return a response."""
        print(f'{"=" * 5}{self.id}: Data received')
        assert self.decode_func is not None and self.encode_func is not None

        try:
            rev_data = self.decode_func(request)
            print(f'{"=" * 5}{self.id}: Processing')
            ret_data = rev_data if self.response_func is None else self.response_func(rev_data)
            ret_data = self.reform_response(ret_data)

            response = self.encode_func(ret_data, response)
            print(f'{"=" * 5}{self.id}: Response sent...')
            return response
        except Exception as e:
            # Always return a response — an uncaught exception here leaves the
            # request unanswered and hangs the ServiceClientAgent forever.
            print(f'{"=" * 5}{self.id}: Error occurred: {e}')
            traceback.print_exc()
            try:
                return self.encode_func({'isdone': False, 'error': str(e)}, response)
            except Exception:
                return response


class ServiceClientAgent(NodeAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Service calls being waited on — cancel() drops these futures.
        self._futures = []
        self._future_lock = threading.Lock()
        self._cancelled = False

    def send(self, data={}, wait_until_done=True, show_info=False, timeout=None, **kwargs):
        """Send a service request and wait for the response if specified.

        Args:
            data (dict): Data to send in the service request. Defaults to {}.
            wait_until_done (bool): Flag to wait for the response. Defaults to True.
            show_info (bool): Flag to display timing and data information. Defaults to False.
            timeout (float, optional): Max seconds to wait for the response before
                giving up. Falls back to ``self.timeout`` (if set), otherwise waits
                indefinitely (until ``rclpy.ok()`` becomes False). Defaults to None.

        Returns:
            dict: Decoded response data.
        """
        assert self.encode_func is not None and self.decode_func is not None
        if self._node_cancelled():
            return {'isdone': False, 'msg': f'{self.id}: cancelled'}
        self._cancelled = False
        if show_info:
            timer = Timer()
        if self.do_log_msg:
            printif(data_info(data), do_print=self.do_log_msg)
            self.log_msg(data, msg_type='sent')

        printif(f'{"=" * 5}{self.id}[{"conneted" if self.connected else "disconnected"}]: Data sent',
                do_print=self.do_log_msg)
        try:
            self.request = self.encode_func(data, self.request)
        except Exception as e:
            print(f'{"=" * 5}{self.id}: Error: {e}. Stopped ...')
            return {'isdone': False}

        if show_info:
            timer.pin_time('encode')

        future = self.executor.call_async(self.request)
        if not wait_until_done:
            print('No waiting until done')
            return {'isdone': True}

        with self._future_lock:
            self._futures.append(future)
        try:
            timeout = timeout if timeout is not None else getattr(self, 'timeout', None)
            tic = time.perf_counter()
            while rclpy.ok() and not future.done() and wait_until_done:
                if self._cancelled:
                    printif(f'{"=" * 5}{self.id}: Cancelled while waiting', do_print=self.do_log_msg)
                    return {'isdone': False, 'msg': f'{self.id}: cancelled'}
                time.sleep(0.1)
                if timeout is not None and time.perf_counter() - tic > timeout:
                    printif(f'{"=" * 5}{self.id}: Timed out waiting for response', do_print=self.do_log_msg)
                    future.cancel()
                    return {'isdone': False, 'msg': f'{self.id}: timed out waiting for response'}
        finally:
            with self._future_lock:
                if future in self._futures:
                    self._futures.remove(future)

        try:
            result = future.result()
            if result is None:
                raise RuntimeError('service call returned no result (cancelled or server unavailable)')
        except Exception as e:
            printif(f'{"=" * 5}{self.id}: Error: {e}', do_print=self.do_log_msg)
            return {'isdone': False, 'msg': f'{self.id}: {e}'}

        if show_info:
            timer.pin_time('get_return')

        ret_data = self.decode_func(result)
        printif(f'{"=" * 5}{self.id}: Return received', do_print=self.do_log_msg)

        if show_info:
            print(data_info(ret_data))
        if self.do_log_msg:
            self.log_msg(ret_data, msg_type='returned', make_new_dir=False)

        if show_info:
            timer.pin_time('decode')
            print(timer.pin_times_str)
        return ret_data

    def cancel(self) -> bool:
        """Stop waiting on the service calls in flight.

        A ROS service has no cancel protocol, so this is client-side only: the
        caller stops blocking and the skill unwinds, but a server already
        moving the robot (kcare's ``mobile/shift_pose`` publishes /cmd_vel for
        the whole duration) keeps going until it finishes on its own. Stopping
        that needs a stop service on the robot — see robotapp/CLAUDE.md.
        """
        self._cancelled = True
        with self._future_lock:
            futures, self._futures = list(self._futures), []
        for f in futures:
            try:
                f.cancel()
            except Exception as e:
                printif(f'{"=" * 5}{self.id}: cancel failed: {e}', do_print=self.do_log_msg)
        return bool(futures)


class TimerAgent(NodeAgent):
    def callback(self):
        """Execute the response function for timer-based callbacks."""
        if self.response_func is None:
            print(f'{"=" * 5}{self.id}: response func is None. Returned')
            return
        self.response_func()


AGENT_CLASS_DICT = {
    'action_server': ActionServerAgent,
    'action_client': ActionClientAgent,
    'sub': TopicAgent,
    'pub': TopicAgent,
    'service_server': ServiceServerAgent,
    'service_client': ServiceClientAgent,
    'timer': TimerAgent,
}


def make_agent(node, **config):
    """Create an agent instance and attach it to a node based on the configuration.

    Args:
        node (rclpy.node.Node): The ROS2 node to attach the agent to.
        **config: Configuration dictionary for the agent.

    Returns:
        NodeAgent: The created agent instance.

    Raises:
        AssertionError: If neither agent_name nor conn_name is provided in config.
        NotImplementedError: If conn_type is not supported.
    """
    default_cfg = {'qos': 10}
    assert 'agent_name' in config or 'conn_name' in config
    config['agent_name'] = config['conn_name'] if 'agent_name' not in config else config['agent_name']
    config['conn_name'] = config['agent_name'] if 'conn_name' not in config else config['conn_name']

    config = update_dict(default_cfg, config)
    data_interface, conn_name, conn_type, qos = (config['data_interface'], config['conn_name'],
                                                 config['conn_type'], config['qos'])

    agent = AGENT_CLASS_DICT[conn_type](**config, is_init=node.is_init)
    agent.node = node          # so the agent can see the node-wide cancel flag
    node.is_init = False

    is_image_sensor = 'image' in data_interface.__name__.lower()
    callback_group = ReentrantCallbackGroup() if is_image_sensor else node.shared_callbackgroup
    node.num_callbackgroup += is_image_sensor

    if conn_type == 'action_server':
        ActionServer(node, data_interface, conn_name, agent.callback, callback_group=callback_group)
    elif conn_type == 'action_client':
        agent.executor = ActionClient(node, data_interface, conn_name)
    elif conn_type == 'sub':
        node.create_subscription(data_interface, conn_name, agent.callback, qos, callback_group=callback_group)
    elif conn_type == 'pub':
        agent.executor = node.create_publisher(data_interface, conn_name, qos)
        if 'time_period' in config:
            node.create_timer(config['time_period'], agent.publish)
    elif conn_type == 'timer':
        node.create_timer(config['time_period'], agent.callback)
    elif conn_type == 'service_server':
        node.create_service(data_interface, conn_name, agent.callback, callback_group=callback_group)
    elif conn_type == 'service_client':
        executor = node.create_client(data_interface, conn_name)
        agent.executor = executor

        def wait_service():
            print(f'Waiting for {conn_name} service...')
            while not executor.wait_for_service(timeout_sec=1.0):
                pass
            print(f'Service {conn_name} connected ...')
            agent.connected = True
            agent.request = data_interface.Request()
        threading.Thread(target=wait_service, daemon=True).start()
    else:
        raise NotImplementedError

    print(f'===== {conn_name} {conn_type} made ...')
    return agent


class CustomNode(Node):
    def __init__(self, name='unnamed_node', is_init_node=False, num_callbackgroup=0, stop_func=None, **kwargs):
        """Initialize a CustomNode instance for managing multiple agents.

        Args:
            name (str): Name of the node. Defaults to 'unnamed_node'.
            is_init_node (bool): Flag to initialize new log directories. Defaults to False.
            num_callbackgroup (int): Number of callback groups. Defaults to 0.
            stop_func (callable, optional): Function to call on node shutdown. Defaults to None.
        """
        super().__init__(name)
        self.name, self.is_init, self.stop_func = name, is_init_node, stop_func
        self.agents = dict()
        self.get_logger().info(f'{"=" * 10} Waiting for data')
        self.num_callbackgroup = max(2, num_callbackgroup)
        self.shared_callbackgroup = ReentrantCallbackGroup()

        self._spin_thread = None
        self._spinning = False
        self._executor = None

        # ── cancellation ──
        # Set by cancel_all() (the dashboard's Stop button, via
        # robot_agent.core.run_control) and cleared by clear_cancel() when the
        # next run starts. While set, action / service client agents refuse to
        # send, so a skill cannot start a new motion after being cancelled.
        self._cancel_event = threading.Event()
        self._cancel_hooks = []

    def add_agent(self, agent_name=None, conn_name=None, conn_type='sub', data_interface=SendData, callback_group=None,
                  encode_func=None, decode_func=None, response_func=None, do_log_msg=False, qos=10, **kwargs):
        """Add a single agent to the node.

        Args:
            agent_name (str, optional): Name of the agent. Defaults to conn_name if None.
            conn_name (str, optional): Connection name. Defaults to agent_name if None.
            conn_type (str): Type of connection. Defaults to 'sub'.
            data_interface (type): ROS2 message interface type. Defaults to SendData.
            callback_group: Callback group for the agent. Defaults to None.
            encode_func (callable, optional): Function to encode data.
            decode_func (callable, optional): Function to decode data.
            response_func (callable, optional): Function to process received data.
            do_log_msg (bool): Flag to enable message logging. Defaults to False.
            qos (int): Quality of service profile. Defaults to 10.

        Raises:
            AssertionError: If neither agent_name nor conn_name is provided.
        """
        assert agent_name is not None or conn_name is not None
        agent_name = conn_name if agent_name is None else agent_name
        conn_name = agent_name if conn_name is None else conn_name

        if 'tcp' in conn_type:
            self.agents[agent_name] = make_tcpip_agent(agent_name=agent_name, conn_type=conn_type, **kwargs)
            return

        self.agents[agent_name] = make_agent(node=self, agent_name=agent_name, conn_name=conn_name, conn_type=conn_type,
                                             data_interface=data_interface, encode_func=encode_func, decode_func=decode_func,
                                             response_func=response_func, do_log_msg=do_log_msg, qos=qos, **kwargs)

    def add_agents(self, configs, **kwargs):
        """Add multiple agents to the node from a list of configurations."""
        for cfg in configs:
            self.add_agent(**cfg, **kwargs)

    def listen(self, run_thread=False):
        if self._spinning:
            return

        self._spinning = True

        def _spin():
            try:
                if self.num_callbackgroup <= 1:
                    rclpy.spin(self)
                else:
                    self._executor = MultiThreadedExecutor(
                        num_threads=self.num_callbackgroup
                    )
                    self._executor.add_node(self)
                    self._executor.spin()

            except KeyboardInterrupt:
                self.get_logger().info('KeyboardInterrupt received')

            finally:
                if self.stop_func:
                    self.stop_func()
                self.stop()

        if run_thread:
            self._spin_thread = threading.Thread(target=_spin, daemon=True)
            self._spin_thread.start()
        else:
            _spin()

    def spin(self, run_thread=False):
        """Alias for the listen method."""
        self.listen(run_thread=run_thread)

    def stop(self):
        if not self._spinning:
            return

        self._spinning = False

        # Stop executor first
        if self._executor is not None:
            self._executor.shutdown()
            self._executor.remove_node(self)
            self._executor = None

        # Destroy node AFTER executor stops
        if self.context.ok():
            self.destroy_node()

        # Join thread safely — skip when stop() is called from within the spin
        # thread itself (the thread is about to exit on its own).
        if (
            self._spin_thread is not None
            and self._spin_thread is not threading.current_thread()
        ):
            self._spin_thread.join(timeout=1.0)
            self._spin_thread = None

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------
    @property
    def cancel_requested(self) -> bool:
        """True from :meth:`cancel_all` until :meth:`clear_cancel`."""
        return self._cancel_event.is_set()

    def add_cancel_hook(self, fn) -> None:
        """Register extra work to do on cancel, e.g. publishing a stop command.

        A robot whose motion cannot be stopped by cancelling the goal (a
        service that drives the base for a fixed duration, say) can add a hook
        that publishes to whatever stop topic it does have::

            node.add_cancel_hook(lambda: node.agents['mobile_stop'].send({}))
        """
        self._cancel_hooks.append(fn)

    def clear_cancel(self) -> None:
        """Re-arm the node for a new run."""
        self._cancel_event.clear()

    def cancel_all(self) -> dict:
        """Stop every command this node has in flight.

        Cancels each action goal (the servers stop the motion), drops the
        service calls being waited on, and runs any cancel hooks. Returns
        ``{'cancelled': [agent names], 'errors': {agent: msg}}``; it never
        raises, so one broken agent cannot keep the rest from being stopped.
        """
        self._cancel_event.set()
        cancelled, errors = [], {}

        # In parallel: each agent may wait on its cancel_goal service, and a
        # Stop button must not take one timeout per action connection.
        def _cancel_one(name, agent):
            try:
                if agent.cancel():
                    cancelled.append(name)
            except Exception as e:
                errors[name] = str(e)

        threads = [threading.Thread(target=_cancel_one, args=(n, a), daemon=True)
                   for n, a in list(self.agents.items())]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)
        for hook in list(self._cancel_hooks):
            try:
                hook()
            except Exception as e:
                errors[getattr(hook, '__name__', 'cancel_hook')] = str(e)
        print(f'===== cancel_all: {cancelled or "nothing in flight"}')
        return {'cancelled': cancelled, 'errors': errors}

    def wait_until_connected(self):
        """Wait until all subscriber and service client agents are connected."""
        for agent_name, agent in self.agents.items():
            if agent.conn_type not in ['sub', 'service_client']:
                continue
            print(f'Waiting for {agent_name} server ...')
            while not agent.connected:
                time.sleep(0.1)
            print('connected ...')

    def start_log_subs(self, fps=10, save_dir=None):
        """Start logging subscriber data periodically.

        Args:
            fps (float): Frequency of logging in frames per second. Defaults to 10.
            save_dir (str, optional): Directory to save logs. Defaults to a
                timestamped directory.
        """
        self.do_log_subs = True
        save_dir = os.path.join(log_sub_dir(), strftime() if save_dir is None else f'{strftime()}@{save_dir}')
        os.makedirs(save_dir, exist_ok=True)
        self.current_log_subs_dir = save_dir

        print('started to log all subs ....')

        def log_data(agent, sub_im_dir, sub_dir):
            data = copy.deepcopy(agent.rev_data)
            if data is None:
                return
            for k, v in data.items():
                if isinstance(v, (np.ndarray, array.array)):
                    im_name = f'{agent.agent_name}_{k}.png' if 'im' in k else f'{agent.agent_name}_{k}.npy'
                    data[k] = im_name
                    im_path = os.path.join(sub_im_dir, im_name)
                    threading.Thread(
                        target=lambda k=k, v=v: cv2.imwrite(im_path, v if len(v.shape) < 3 else v[..., ::-1]) if 'im' in k else np.save(im_path, v),
                        daemon=True).start()

            write_json(os.path.join(sub_dir, f'{agent.agent_name}.json'), data)

        def run():
            while self.do_log_subs:
                sub_dir = os.path.join(save_dir, strftime())
                os.makedirs(sub_dir, exist_ok=True)
                sub_im_dir = os.path.join(sub_dir, 'array')
                os.makedirs(sub_im_dir, exist_ok=True)

                for agent_name, agent in self.agents.items():
                    if agent.conn_type != 'sub':
                        continue
                    log_data(agent=agent, sub_im_dir=sub_im_dir, sub_dir=sub_dir)
                time.sleep(1. / fps)

        self.log_subs_thread = threading.Thread(target=run, daemon=True)
        self.log_subs_thread.start()

    def stop_log_subs(self):
        """Stop logging subscriber data."""
        if not hasattr(self, 'do_log_subs'):
            return
        self.do_log_subs = False
        self.log_subs_thread.join()
        print('logging subs stopped...')

    @property
    def is_logging_subs(self):
        """True while :meth:`start_log_subs` is running."""
        if not hasattr(self, 'do_log_subs'):
            return False
        return self.do_log_subs


def node_add_agents_from_configs(node, configs):
    """Add multiple agents from a name→config dict to *node* and return it."""
    for name, cfg in configs.items():
        node.add_agent(**cfg)
    return node


def get_ros2_node_names_and_types(kill_ros=False):
    """Snapshot the ROS 2 graph as ``{'topic': [...], 'service': [...], 'action': [...]}``.

    Each entry is ``[name, type]``. Actions are recovered from their
    ``/_action/send_goal`` services, and the action-internal topics/services are
    filtered out of the other two lists.
    """
    if not rclpy.ok():
        rclpy.init()
    node = Node('introspect_node')

    topics_and_types = node.get_topic_names_and_types()
    services_and_types = node.get_service_names_and_types()

    actions = [
        [name.replace('/_action/send_goal', ''), types[0].replace('_SendGoal', '')]
        for name, types in services_and_types
        if name.endswith('/_action/send_goal')
    ]

    topics = [
        [name, types[0]]
        for name, types in topics_and_types
        if '/_action/' not in name
    ]
    services = [
        [name, types[0]]
        for name, types in services_and_types
        if '/_action/' not in name
    ]

    node.destroy_node()
    if kill_ros:
        rclpy.shutdown()
    return {'topic': topics, 'service': services, 'action': actions}
