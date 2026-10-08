"""Spoken names in plans: ``<said>-><real>``.

A plan may give a skill name or any parameter value two names — what the
robot says, and what it runs::

    이동->move::식탁 앞->table_top
    pick_top::inputs='컵->cup', loc='식탁->table@kitchen'
    ask::inputs='어디로 갈까요?', options="식탁 앞->table_top, 옷방->dressroom"

The left side is only spoken / shown (narration, a skill's own announcement,
the dashboard); the right side is what the skill gets, exactly as if the plan
had said ``move::table_top``. Without ``->`` both are the same.

``SkillRegistry.execute`` splits the parameters of every code skill (plan skills
pass them on unsplit, so the step inside still gets both names) and makes the
spoken side readable to the skill through :func:`robot_agent.skills.spoken`.
``options`` is left to the skill (``ask`` needs the pairs).
"""
import ast
import re

ARROW = '->'

# Parameters a skill splits itself.
KEEP_PAIRS = ('options',)


class SpokenError(ValueError):
    """A ``said->real`` value that cannot be split."""


def has_arrow(v) -> bool:
    if isinstance(v, str):
        return ARROW in v
    if isinstance(v, (list, tuple)):
        return any(has_arrow(x) for x in v)
    return False


def split_spoken(s: str):
    """``(said, real)`` of one ``said->real`` string; ``(s, s)`` without an
    arrow, and an empty *said* counts as none (``'->move'`` is ``move``)."""
    s = str(s)
    if ARROW not in s:
        return s, s
    if s.count(ARROW) > 1:
        raise SpokenError(f'"{s}": more than one "{ARROW}"')
    said, _, real = s.partition(ARROW)
    said, real = said.strip(), real.strip()
    if not real:
        raise SpokenError(f'"{s}": nothing after "{ARROW}"')
    if real.startswith('>'):
        raise SpokenError(f'"{s}": "{ARROW}>" is ambiguous')
    return (said or real), real


def _literal(s: str):
    """``'0.5'`` -> 0.5, ``'True'`` -> True, anything else stays a string — so
    ``fixed_angle='정면->0'`` gives the same 0 as ``fixed_angle=0``."""
    try:
        return ast.literal_eval(s)
    except Exception:
        return s


def split_value(v, literal: bool = False):
    """``(said, real)`` of one parameter value. *said* is None when *v* has no
    arrow. A ``>>`` value (``컵->cup>>식탁->table@kitchen``) is split per
    segment: real ``cup>>table@kitchen``, said ``컵>>식탁``. Lists and tuples
    are split per item (said is then a list)."""
    if isinstance(v, (list, tuple)):
        pairs = [split_value(x, literal) for x in v]
        if all(s is None for s, _ in pairs):
            return None, v
        return ([s if s is not None else r for s, r in pairs],
                type(v)(r for _, r in pairs))
    if not isinstance(v, str) or ARROW not in v:
        return None, v
    if '>>' in v.replace(ARROW + '>', ''):          # '->>' is caught by split_spoken
        pairs = [split_spoken(seg) for seg in v.split('>>')]
        return '>>'.join(s for s, _ in pairs), '>>'.join(r for _, r in pairs)
    said, real = split_spoken(v)
    return said, (_literal(real) if literal else real)


def split_params(params: dict):
    """``(real_params, said)``: *params* with every ``said->real`` value
    replaced by its real side, and ``{key: (said, real)}`` for those keys.
    ``inputs`` keeps its real side as text (``lift::0.5`` has always been the
    string ``'0.5'``); other keys read it as a Python literal."""
    real, said = {}, {}
    for k, v in (params or {}).items():
        if k in KEEP_PAIRS or not has_arrow(v):
            real[k] = v
            continue
        s, r = split_value(v, literal=(k != 'inputs'))
        real[k] = r
        if s is not None:
            said[k] = (s, r)
    return real, said


def split_skill(action: str):
    """``(said, real)`` of a plan step's skill name, ``!`` / ``~`` kept on
    *real*: ``'!이동->move'`` -> ``('이동', '!move')``. *said* is None without
    an arrow."""
    action = str(action)
    if ARROW not in action:
        return None, action
    stripped = action.lstrip()
    flags = ''
    while stripped[:1] in ('!', '~'):
        flags += stripped[0]
        stripped = stripped[1:].lstrip()
    said, real = split_spoken(stripped)
    return said, flags + real


def said_of(s) -> str:
    """The spoken side of a plan text fragment, for narration and display —
    ``'식탁 앞->table_top'`` -> ``'식탁 앞'``; the first segment of a ``>>``
    value. Never raises."""
    s = str(s)
    if ARROW not in s:
        return s
    try:
        said, _ = split_value(s)
        return said.split('>>')[0] if said else s
    except SpokenError:
        return s


_SAID = re.compile(r"[^'\",=>]*->\s*")


def real_text(s) -> str:
    """Plan text with every spoken name dropped — ``"inputs='컵->cup'"`` ->
    ``"inputs='cup'"``, ``'컵->cup>>식탁->table'`` -> ``'cup>>table'`` — for
    code that reads the raw argument string (the Robot State update)."""
    return _SAID.sub('', str(s)) if ARROW in str(s) else s


def real_step(skill, params):
    """``(skill, params)`` of a step as the skill ran it: no ``!`` / ``~`` or
    spoken name on *skill*; *params* (a dict or the raw argument string) with
    the real values. Never raises."""
    skill = str(skill or '').replace('!', '').replace('~', '').rpartition(ARROW)[2].strip()
    try:
        params = split_params(params)[0] if isinstance(params, dict) else real_text(params)
    except SpokenError:
        pass
    return skill, params
