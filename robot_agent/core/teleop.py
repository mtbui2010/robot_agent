"""Teleoperation functions the dashboard may show and command (``/teleop`` API).

A teleop *function* is a node outside this package (the ``robot_teleop`` repo: an arm servo driven by a SpaceMouse today) that
speaks two JSON-in-String ROS topics, by convention ``/teleop/<name>/state`` (latched) and ``/teleop/<name>/command``:

    state    {name, label, state: inactive|hold|active, ready, gripper_open, speed, input_age_s, stalled, t}
    command  {"cmd": "enable" | "disable" | "speed", "value": 0..1}

This module names no device and no robot: the functions come from the ``TELEOP`` config group (the robot package's
``configs/tasks.py`` or a site override)::

    TELEOP = {'functions': [{'name': 'arm', 'label': 'Arm + gripper', 'state_topic': '/teleop/arm/state',
                             'command_topic': '/teleop/arm/command'}]}      # the two topics default to the convention above

No group, or no ROS node: no functions, and the dashboard shows nothing. The bridge subscribes lazily, on the first request.
"""
from __future__ import annotations

import json
import logging
import threading
import time

log = logging.getLogger('robot_agent.teleop')

STALE_S = 3.0          # a state older than this means the function is not running (it publishes ~2 Hz)


class RosTransport:
    """Subscribe / publish String topics on the agent's rclpy node (rclpy imported only here)."""

    def __init__(self, node):
        self._node = node
        self._pubs: dict = {}

    def subscribe(self, topic: str, callback) -> None:
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from std_msgs.msg import String
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
        self._node.create_subscription(String, topic, lambda m: callback(m.data), qos)

    def publish(self, topic: str, text: str) -> None:
        from std_msgs.msg import String
        if topic not in self._pubs:
            self._pubs[topic] = self._node.create_publisher(String, topic, 10)
        self._pubs[topic].publish(String(data=text))


def _functions(cfg) -> list:
    """The valid entries of the TELEOP group: [{name, label, state_topic, command_topic}]."""
    out, seen = [], set()
    for f in (cfg or {}).get('functions') or []:
        name = str(f.get('name') or '').strip() if isinstance(f, dict) else ''
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({'name': name, 'label': str(f.get('label') or name),
                    'state_topic': str(f.get('state_topic') or f'/teleop/{name}/state'),
                    'command_topic': str(f.get('command_topic') or f'/teleop/{name}/command')})
    return out


class TeleopBridge:
    def __init__(self, get_cfg, get_transport, get_group=None):
        """``get_cfg()`` -> the TELEOP group (or None); ``get_transport()`` -> a transport (or None when ROS is not up);
        ``get_group`` is unused (kept so older callers still work)."""
        self._get_cfg, self._get_transport = get_cfg, get_transport
        self._lock = threading.Lock()
        self._state: dict = {}          # name -> (decoded state, monotonic receive time)
        self._subscribed: set = set()
        self._transport = None

    def functions(self) -> list:
        return _functions(self._get_cfg())

    def _ensure(self, fn: dict):
        tr = self._get_transport()
        if tr is None:
            return None
        if tr is not self._transport:           # a new ROS node (the agent restarted its ROS side): subscribe again
            self._transport, self._subscribed = tr, set()
        if fn['name'] not in self._subscribed:
            self._subscribed.add(fn['name'])
            tr.subscribe(fn['state_topic'], lambda text, n=fn['name']: self._on_state(n, text))
        return tr

    def _on_state(self, name: str, text: str) -> None:
        try:
            d = json.loads(text)
        except (TypeError, ValueError):
            return
        if isinstance(d, dict):
            with self._lock:
                self._state[name] = (d, time.monotonic())

    def listing(self) -> list:
        """[{name, label, available, state?}]: ``available`` False until a fresh state has arrived (function not running)."""
        out = []
        for fn in self.functions():
            self._ensure(fn)
            with self._lock:
                got = self._state.get(fn['name'])
            age = None if got is None else time.monotonic() - got[1]
            ok = got is not None and age <= STALE_S
            out.append({'name': fn['name'], 'label': fn['label'], 'available': ok,
                        **({'state': got[0], 'age_s': round(age, 2)} if got is not None else {})})
        return out

    def command(self, name: str, cmd: str, value=None) -> bool:
        """Publish a command to function ``name``; False when there is no such function or no ROS."""
        fn = next((f for f in self.functions() if f['name'] == name), None)
        if fn is None:
            return False
        tr = self._ensure(fn)
        if tr is None:
            return False
        tr.publish(fn['command_topic'], json.dumps({'cmd': cmd, **({} if value is None else {'value': value})}))
        return True

    def stop_all(self) -> None:
        """Switch every function off (a run starts, or Cancel): best effort, never raises."""
        try:
            for fn in self.functions():
                self.command(fn['name'], 'disable')
        except Exception as e:
            log.warning('teleop stop_all: %s', e)

    def is_active(self) -> bool:
        """Some function is hold / active (and its state is fresh)."""
        return any(f['available'] and f['state'].get('state') in ('hold', 'active') for f in self.listing())


_BRIDGE: TeleopBridge | None = None


def bridge() -> TeleopBridge:
    """The process-wide bridge on the running robot's config and ROS node."""
    global _BRIDGE
    if _BRIDGE is None:
        def cfg():
            from ..state import current
            return current().cm.get('TELEOP')

        cache: dict = {}

        def transport():
            from ..state import current
            node = getattr(current().dm, '_ros_node', None)
            if node is None:
                return None
            if cache.get('node') is not node:       # one transport per ROS node (it holds the publishers)
                cache['node'], cache['tr'] = node, RosTransport(node)
            return cache['tr']
        _BRIDGE = TeleopBridge(cfg, transport)
    return _BRIDGE
