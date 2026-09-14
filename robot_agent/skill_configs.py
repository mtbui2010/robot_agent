"""
Lazy proxy access to skill configs via ConfigManager.

Resolution order on read:
  1. ConfigManager overrides    (persisted in skill_configs_override.json)
  2. The robot package's ``<robot_pkg>.configs.tasks`` module, if importable
  3. Hardcoded `_default` below (the proxies in this file)

Skills import these proxies; the value resolved is what the running skill
actually sees, and is also what the /skill-configs API returns.
"""


PROXY_REGISTRY: dict = {}

# Robot package to read configs/tasks.py from when there is no AgentState.
# ConfigManager owns this lookup normally; this is the no-agent path only.
# `robot_agent.runtime.bootstrap` and `robot_agent.diagnose` set the env var.
_ROBOT_PKG_ENV = 'ROBOT_SKILLS_PKG'
_tasks_cache: dict = {}


def _tasks_value(name: str):
    """`name` from ``<robot_pkg>.configs.tasks``, or None if unavailable."""
    import os
    pkg = os.environ.get(_ROBOT_PKG_ENV)
    if not pkg:
        return None
    if pkg not in _tasks_cache:
        try:
            import importlib
            _tasks_cache[pkg] = importlib.import_module(f'{pkg}.configs.tasks')
        except Exception:
            _tasks_cache[pkg] = None
    mod = _tasks_cache[pkg]
    return getattr(mod, name, None) if mod is not None else None


class _ConfigProxy:
    """Dict/list proxy that always fetches live values from ConfigManager."""
    def __init__(self, name, default=None):
        self._name = name
        self._default = default if default is not None else {}
        PROXY_REGISTRY[name] = self

    def _data(self):
        try:
            from robot_agent.state import current
            val = current().cm.get(self._name)
            if val is not None:
                return val
        except Exception:
            pass
        # No live agent. That happens whenever a skill module is imported
        # outside a running robot — `robot_agent.diagnose`, unit tests, tooling —
        # and some skills read a config at import time (kcare's place.py builds
        # a Head2BaseCalibration at module level). Resolve the robot package's
        # own configs/tasks.py before giving up, so those imports still see real
        # numbers instead of the neutral shapes in skill_config_defaults.
        val = _tasks_value(self._name)
        return val if val is not None else self._default

    def __getitem__(self, key):       return self._data()[key]
    def __setitem__(self, key, val):  self._data()[key] = val
    def __contains__(self, item):     return item in self._data()
    def __iter__(self):               return iter(self._data())
    def __len__(self):                return len(self._data())
    def __repr__(self):               return repr(self._data())
    def get(self, key, default=None):
        d = self._data()
        return d.get(key, default) if isinstance(d, dict) else default
    def items(self):   return self._data().items()
    def values(self):  return self._data().values()
    def keys(self):    return self._data().keys()
    def update(self, other):
        d = self._data()
        if isinstance(d, dict):
            d.update(other)


from .skill_config_defaults import (
    GRIP_CONFIGS_DEFAULT, LIFT_CONFIGS_DEFAULT, HEAD_CONFIGS_DEFAULT,
    ARM_CONFIGS_DEFAULT, MOBILE_CONFIGS_DEFAULT, FIND_CONFIGS_DEFAULT,
    CALIB_PARAMS_DEFAULT, HOME_LOC_DEFAULT,
    ENV_DEFAULT, KR2EN_DEFAULT, EN2KR_DEFAULT,
)

# ── Live-proxied configs ──────────────────────────────────────────────────────
GRIP_CONFIGS    = _ConfigProxy('GRIP_CONFIGS',    GRIP_CONFIGS_DEFAULT)
LIFT_CONFIGS    = _ConfigProxy('LIFT_CONFIGS',    LIFT_CONFIGS_DEFAULT)
HEAD_CONFIGS    = _ConfigProxy('HEAD_CONFIGS',    HEAD_CONFIGS_DEFAULT)
ARM_CONFIGS     = _ConfigProxy('ARM_CONFIGS',     ARM_CONFIGS_DEFAULT)
MOBILE_CONFIGS  = _ConfigProxy('MOBILE_CONFIGS',  MOBILE_CONFIGS_DEFAULT)
FIND_CONFIGS    = _ConfigProxy('FIND_CONFIGS',    FIND_CONFIGS_DEFAULT)
CALIB_PARAMS    = _ConfigProxy('CALIB_PARAMS',    CALIB_PARAMS_DEFAULT)
ENV             = _ConfigProxy('ENV',             ENV_DEFAULT)
HOME_LOC        = _ConfigProxy('HOME_LOC',        HOME_LOC_DEFAULT)
LLM_SERVERS     = _ConfigProxy('LLM_SERVERS',    {})
KR2EN           = _ConfigProxy('KR2EN',           KR2EN_DEFAULT)
EN2KR           = _ConfigProxy('EN2KR',           EN2KR_DEFAULT)

# ── Simple constants (not proxied — scalar arithmetic doesn't work with proxy) ─
NO_ACTION            = False
MOBILE_HEIGHT        = 435
ME                   = 'living room'
DO_TEXT2VOICE        = True
STANDING_OBJ_NAMES   = ['cup', 'can', 'bottle']
LYING_OBJ_NAMES      = ['phone', 'fork', 'spoon', 'bread', 'banana', 'control']
HAVING_HANDLE_OBJ_NAMES = []
UTIL_AGENT_CONFIGS   = {'observe': {'conn_name': 'observe'}}
VLA_CLIENTS          = {}
DEVICE_CLIENT_CONFIGS = []
PROMPT_CONFIGS       = {}
OBSERVATION_NODE_CONFIGS = {}
GUIDE                = ''
