"""Skill-side public API.

Two responsibilities:

1. ``log_data``: per-step data emitter used by skills to push intermediate
   data (e.g. ``log_image``) up to the execution panel. The unified_agent
   installs an emitter via ``_set_emitter`` / ``_clear_emitter``; outside
   that window ``log_data`` is a no-op so skills can be unit-tested.

2. ``skill_entry`` / ``auto_wrap_skills``: lazy-bootstrap wrapper for the
   Python-API mode. When a user does ``from kcare_robot.skills.recognition
   import find; find(inputs='apple')``, the wrapper transparently runs
   ``bootstrap(robot_pkg)`` on first call and injects ``node``.

   Robot packages opt in by calling ``auto_wrap_skills(SKILL_CONFIGS,
   pkg='<robot_pkg>')`` from their ``<robot_pkg>/skills/__init__.py``.
   Already-wrapped functions are detected via the ``_skill_entry_wrapped``
   attribute so re-import is a no-op.
"""
import functools
import importlib
import threading

_local = threading.local()


def log_data(data: dict) -> None:
    fn = getattr(_local, 'emit', None)
    if fn is not None:
        fn(data)


def _set_emitter(fn) -> None:
    _local.emit = fn


def _clear_emitter() -> None:
    _local.emit = None


def skill_entry(fn, pkg: str):
    """Wrap a skill function so it can be called from user code without
    explicit ``node`` or prior ``bootstrap()``.

    Semantics:
      - ``node`` keyword: if absent or None, the wrapper boots robot_agent for
        ``pkg`` (idempotent) and injects ``state.dm._ros_node``.
      - First positional argument (if any) is treated as ``inputs`` to match
        the CLI convention (``find::apple`` → ``find(inputs='apple')``).
      - Remaining args/kwargs are forwarded.

    Designed to be the outermost decorator — sits above ``@exception_handler``
    so exceptions inside the skill are still wrapped into ``{'isdone': False,
    'msg': ...}``.
    """
    if getattr(fn, '_skill_entry_wrapped', False):
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        node = kwargs.pop('node', None)

        if args and 'inputs' not in kwargs:
            kwargs['inputs'] = args[0]
            args = args[1:]
        if args:
            raise TypeError(
                f"{fn.__name__}: unexpected positional args {args!r} "
                f"(use keyword arguments after the first positional 'inputs')"
            )

        if node is None:
            from ..runtime import bootstrap
            from ..state import current
            bootstrap(pkg)
            node = current().dm._ros_node

        return fn(node=node, **kwargs)

    wrapper._skill_entry_wrapped = True
    wrapper._skill_inner = fn
    return wrapper


def auto_wrap_skills(skill_configs: dict, pkg: str) -> None:
    """Wrap every function listed in ``skill_configs`` with ``skill_entry``.

    ``skill_configs`` mirrors ``robot_agent.SkillRegistry`` input:
        {skill_name: (module_path, func_name)}  or  {skill_name: module_path}

    Modules are imported lazily; failures are swallowed and printed so a
    broken submodule doesn't prevent the rest from being importable.
    """
    seen = set()
    for entry in skill_configs.values():
        if isinstance(entry, tuple):
            module_path, func_name = entry
        else:
            module_path, func_name = entry, None
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:
            print(f'[auto_wrap_skills] skip {module_path}: {e}')
            continue

        names = [func_name] if func_name else [n for n in dir(mod) if not n.startswith('_')]
        for name in names:
            key = (module_path, name)
            if key in seen:
                continue
            seen.add(key)
            fn = getattr(mod, name, None)
            if fn is None or not callable(fn):
                continue
            if isinstance(fn, type):
                continue
            setattr(mod, name, skill_entry(fn, pkg=pkg))
