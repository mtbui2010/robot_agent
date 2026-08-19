"""Compatibility shim for persisted ``pyconnect`` references.

``connections.json`` files written before the connect layer was absorbed embed
code like::

    def decode_func(msg):
        from pyconnect.ros.utils import decode_imgmsg
        return decode_imgmsg(msg)

and dotted ``data_interface`` strings such as
``pyconnect.ros.utils.encode_imgmsg``. Those snippets live on deployed robots'
disks, so they must keep loading after pyconnect is gone.

:func:`install` registers lightweight alias modules under the ``pyconnect``
names in ``sys.modules``, delegating attribute access to the matching
``robot_agent.connect`` modules. It is a no-op when the real pyconnect package
is importable (it then keeps serving the old names itself).

Called from :mod:`robot_agent.core.device_manager` before any persisted
config source is executed or resolved.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types

# Alias name → robot_agent.connect module(s) that provide its attributes.
# Multi-target entries are searched in order.
_ALIASES: dict[str, tuple[str, ...]] = {
    'pyconnect': ('robot_agent.connect',),
    'pyconnect.transport': ('robot_agent.connect.transport',),
    'pyconnect.utils': (
        'robot_agent.connect.helpers',
        'robot_agent.connect.serde',
        'robot_agent.connect.parallel',
        'robot_agent.connect.ros.codecs',   # decode_topic_strmsg & friends
        'robot_agent.connect',              # init_detect_client
        'robot_agent.connect.llm',          # init_llm_client
    ),
    'pyconnect.ros': ('robot_agent.connect.ros',),
    'pyconnect.ros.utils': (
        'robot_agent.connect.ros.codecs',
        'robot_agent.connect.ros.configs',
        'robot_agent.connect.ros.node',     # find_last_session_log_dir, …
    ),
    'pyconnect.ros.custom_node': ('robot_agent.connect.ros.node',),
    'pyconnect.tcp_ip': ('robot_agent.connect.tcp_ip',),
    'pyconnect.tcp_ip.client': ('robot_agent.connect.tcp_ip.client',),
    'pyconnect.tcp_ip.server': ('robot_agent.connect.tcp_ip.server',),
    'pyconnect.tcp_ip.node': ('robot_agent.connect.tcp_ip.node',),
    'pyconnect.zmq': ('robot_agent.connect.zmq',),
    'pyconnect.zmq.client': ('robot_agent.connect.zmq.client',),
    'pyconnect.zmq.server': ('robot_agent.connect.zmq.server',),
    'pyconnect.websocket': ('robot_agent.connect.websocket',),
    'pyconnect.websocket.client': ('robot_agent.connect.websocket.client',),
    'pyconnect.websocket.server': ('robot_agent.connect.websocket.server',),
    'pyconnect.http': ('robot_agent.connect.http',),
    'pyconnect.http.client': ('robot_agent.connect.http.client',),
    'pyconnect.http.server': ('robot_agent.connect.http.server',),
    'pyconnect.webrtc': ('robot_agent.connect.webrtc',),
    'pyconnect.webrtc.client': ('robot_agent.connect.webrtc.client',),
    'pyconnect.llm': ('robot_agent.connect.llm',),
}

_installed = False


class _AliasModule(types.ModuleType):
    """Module that resolves attributes from its target modules on demand.

    Targets are imported lazily so that e.g. registering the
    ``pyconnect.ros.utils`` alias does not pull in rclpy until a snippet
    actually touches it.
    """

    def __init__(self, name: str, targets: tuple[str, ...]):
        super().__init__(name, f'robot_agent compatibility alias for {name}')
        self.__targets = targets
        self.__path__ = []  # mark as package so `import pyconnect.x.y` descends

    def __getattr__(self, attr: str):
        for target in self.__targets:
            try:
                mod = importlib.import_module(target)
            except ImportError:
                continue
            if hasattr(mod, attr):
                return getattr(mod, attr)
        raise AttributeError(
            f'{self.__name__!r} (compat alias) has no attribute {attr!r} — '
            f'not found in {self.__targets}')


def install() -> None:
    """Register the ``pyconnect.*`` aliases in ``sys.modules`` (idempotent).

    Does nothing when a real pyconnect distribution is importable, so
    environments that still have it keep their original behaviour.
    """
    global _installed
    if _installed or 'pyconnect' in sys.modules:
        return
    try:
        if importlib.util.find_spec('pyconnect') is not None:
            _installed = True
            return
    except Exception:
        pass

    for name, targets in _ALIASES.items():
        sys.modules[name] = _AliasModule(name, targets)
    _installed = True
