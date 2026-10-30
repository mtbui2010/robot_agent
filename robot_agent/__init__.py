"""robot_agent: the robot-agnostic agent runtime.

``import robot_agent`` stays light (no FastAPI): planning code and tests import
subpackages without pulling in the web stack. ``create_app`` is resolved on
first use.
"""

__all__ = ['create_app']


def __getattr__(name):
    if name == 'create_app':
        from .app_factory import create_app
        return create_app
    raise AttributeError(f"module 'robot_agent' has no attribute {name!r}")
