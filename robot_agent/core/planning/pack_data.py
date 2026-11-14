"""Facts a pack declares carry their origin: a ``source`` label (a string) or ``assumed: true``. Data with neither is not used (fail open)."""

from __future__ import annotations


def labelled(data) -> bool:
    """Does the declared ``data`` (a dict) carry a ``source`` string or ``assumed: true``?"""
    return isinstance(data, dict) and (isinstance(data.get("source"), str) and bool(data["source"].strip()) or data.get("assumed") is True)


def fresh(spec, cfg, planner, name):
    """The option key ``name``: its resolved value, or, when that is empty, the manifest's value now (``planner``: the manifest's
    ``planner`` section; a host may merge pack data into the robot view after the loop resolved its keys)."""
    from robot_agent.core.options.config import resolve_key
    v = cfg.get(name)
    return v if v else resolve_key(spec, spec.key(name), {}, {}, planner)


def labelled_keys(spec, cfg, planner, names) -> dict:
    """The keys ``names`` of an option (each as ``fresh`` gives it) when the option's own keys ``source`` / ``assumed`` label them (a run
    option ``<option>.source`` / ``<option>.assumed``, or the manifest section ``planner.options.<option>``); else each key's default
    (unlabelled pack data is not used: the option behaves as with no data)."""
    ok = labelled({"source": fresh(spec, cfg, planner, "source"), "assumed": fresh(spec, cfg, planner, "assumed")})
    return {n: fresh(spec, cfg, planner, n) if ok else spec.key(n).default for n in names}


def planner_of(svc):
    """The manifest's ``planner`` section of the robot an option's services drive, or None."""
    return getattr(getattr(getattr(svc, "robot", None), "manifest", None), "planner", None)
