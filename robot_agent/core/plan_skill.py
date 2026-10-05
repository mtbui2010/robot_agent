"""Plan skills: a skill defined as a plan of other skills.

A plan skill is stored in ``skills.json`` like any other skill (``type: 'plan'``)
and runs through ``SkillRegistry.execute`` — so the dashboard, ``POST
/skill/<name>``, the CLI, the planners and other plan skills can all call it::

    pick_top:
        move::$loc=counter@kitchen$
        lift::1
        movej::pre_pick
        forward::0.1
        fine_move::$inputs$
        forward::-0.5
        movej::give
        lift::0.5

    pick_top::cup                      # $inputs$ = 'cup', $loc$ = its default
    pick_top::inputs='cup', loc='table@kitchen'

Lines follow the plan syntax of the direct mode: ``skill::args``, ``&&`` runs
steps in parallel, ``!skill`` ignores its failure, ``~skill`` counts it as
failed, ``#`` starts a comment. ``$name$`` is a parameter, ``$name=default$``
gives it a default; ``$inputs$`` is what follows ``pick_top::``. As in the
direct mode, each step's result is passed on to the steps after it.

On a failure the plan stops where it is. Nothing is run to "clean up": a
fold / lift-home from an unknown pose can hit the furniture the arm is in, so
the robot is left as it stopped and the result says which step failed.
"""
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

# $name$ or $name=default$
_PARAM = re.compile(r'\$([A-Za-z_]\w*)(?:=([^$]*))?\$')
_NAME = re.compile(r'^[A-Za-z_][\w\-]*$')
MAX_DEPTH = 5

_local = threading.local()


def parse_inputs(s: str) -> dict:
    """The ``skill::args`` argument rules of the direct mode: no ``=`` means the
    whole string is ``inputs``; else ``dict(<args>)``, falling back to
    ``inputs`` when that does not evaluate."""
    s = s.strip()
    if not s or s in ('None', ''):
        return {}
    if '=' not in s:
        return {'inputs': s}
    try:
        return eval(f'dict({s})')
    except Exception:
        return {'inputs': s}


def plan_lines(plan: str) -> list:
    """[(line_no, [(action, args), ...])] — one entry per step, several when
    joined by ``&&``. Comments and lines without ``::`` are skipped."""
    out = []
    for no, raw in enumerate(plan.replace('\\n', '\n').split('\n'), 1):
        line = raw.split('#', 1)[0].strip()
        if '::' not in line:
            continue
        group = []
        for part in line.split('&&'):
            bits = part.split('::')
            if len(bits) == 2 and bits[0].strip():
                group.append((bits[0].strip(), bits[1].strip()))
        if group:
            out.append((no, group))
    return out


def plan_params(plan: str) -> dict:
    """{name: default or None} for every ``$name$`` in *plan*. Raises
    ValueError when one parameter is given two different defaults."""
    found: dict = {}
    for m in _PARAM.finditer(plan):
        name, default = m.group(1), m.group(2)       # default None: no "=" given
        if name in found and None not in (found[name], default) and found[name] != default:
            raise ValueError(f'${name}$ has two defaults: {found[name]!r} and {default!r}')
        if found.get(name) is None:
            found[name] = default
    return found


def _bare(action: str) -> str:
    return action.replace('!', '').replace('~', '').strip()


def called_skills(plan: str) -> list:
    return [_bare(a) for _, group in plan_lines(plan) for a, _ in group]


def validate_plan(name: str, plan: str, registry) -> str:
    """'' when *plan* can be saved as plan skill *name*, else why not."""
    if not _NAME.match(name or ''):
        return f'"{name}" is not a valid skill name (letters, digits, _ and -)'
    existing = registry._skills.get(name)
    if existing is not None and existing.type != 'plan':
        article = 'an' if existing.type[:1] in 'aeiou' else 'a'
        return f'"{name}" is already {article} {existing.type} skill'
    steps = plan_lines(plan or '')
    if not steps:
        return 'the plan has no steps (one "skill::args" per line)'
    for no, group in steps:
        for action, _ in group:
            target = _bare(action)
            if target != name and target not in registry._skills:
                return f'line {no}: unknown skill "{target}"'
    try:
        plan_params(plan)
    except ValueError as e:
        return str(e)
    # A plan that ends up calling itself would never finish.
    graph = {s.name: called_skills(s.plan) for s in registry._skills.values() if s.type == 'plan'}
    graph[name] = called_skills(plan)

    def reaches(node, seen):
        for nxt in graph.get(node, []):
            if nxt == name:
                return True
            if nxt in graph and nxt not in seen:
                seen.add(nxt)
                if reaches(nxt, seen):
                    return True
        return False
    if reaches(name, set()):
        return f'"{name}" would call itself (directly or through another plan skill)'
    return ''


def rename_calls(plan: str, old: str, new: str) -> str:
    """*plan* with every step that calls skill *old* calling *new* instead,
    keeping its ``!`` / ``~`` prefix, arguments, parallel ``&&`` and comments."""
    pat = re.compile(r'(^|&&)(\s*[!~]*\s*)' + re.escape(old) + r'(\s*::)')
    out = []
    for line in plan.split('\n'):
        code, sep, comment = line.partition('#')
        out.append(pat.sub(lambda m: m.group(1) + m.group(2) + new + m.group(3), code) + sep + comment)
    return '\n'.join(out)


def plan_rename(registry, old: str, new: str):
    """('', {other plan skill: its updated plan}) when plan skill *old* can be
    renamed *new*, else (why not, {})."""
    skill = registry._skills.get(old)
    if skill is None or skill.type != 'plan':
        return f'"{old}" is not a plan skill — only plan skills can be renamed', {}
    if not _NAME.match(new or ''):
        return f'"{new}" is not a valid skill name (letters, digits, _ and -)', {}
    if new in registry._skills:
        return f'"{new}" is already a skill', {}
    updates = {}
    for other in registry.plan_skills():
        if other.name != old and old in called_skills(other.plan):
            updates[other.name] = rename_calls(other.plan, old, new)
    return '', updates


def _substitute(args: str, values: dict) -> dict:
    """Step params from *args* with every ``$name$`` replaced. Values go in
    after the args are parsed — as Python literals in ``k=v`` form, verbatim
    for a bare argument — so a value with commas or quotes cannot break the
    parsing the way plain text substitution would."""
    if '=' in _PARAM.sub('', args):
        return parse_inputs(_PARAM.sub(lambda m: repr(values[m.group(1)]), args))
    whole = _PARAM.fullmatch(args.strip())
    if whole:                                   # fine_move::$inputs$ keeps the value's type
        v = values[whole.group(1)]
        return {} if v in (None, '') else {'inputs': v}
    text = _PARAM.sub(lambda m: str(values[m.group(1)]), args)
    return parse_inputs(text) if '=' not in text else {'inputs': text}


def _apply_world_effect(action, params, result, node):
    """Mirror one step onto the Robot State, as the direct mode does after each
    of its own steps — the effect of `pick_top` is the effect of its `pick`."""
    try:
        from ..state import current
        from .planning.loop import _robot_namemap
        namemap = _robot_namemap()
        fn = getattr(namemap, 'apply_skill_effect', None) if namemap else None
        if callable(fn):
            fn(current().world, action, params, result, node)
    except Exception:
        pass


def run_plan_skill(registry, skill, params: dict, node=None, log_fn=None) -> dict:
    from .run_control import cancel_requested
    from ..connect.parallel import run_parallel_check

    depth = getattr(_local, 'depth', 0)
    if depth >= MAX_DEPTH:
        return {'isdone': False, 'msg': f'plan skills nested deeper than {MAX_DEPTH}'}

    def log(msg):
        if log_fn is not None:
            try:
                log_fn({'msg': msg})
            except Exception:
                pass

    declared = plan_params(skill.plan)
    params = dict(params or {})
    values = {}
    for pname, default in declared.items():
        if pname in params:
            values[pname] = params.pop(pname)
        elif default is not None:
            values[pname] = default
        else:
            return {'isdone': False, 'msg': f'{skill.name}: missing parameter "{pname}"'}
    params.pop('inputs', None)              # given but not used by this plan
    ctx = params                            # what the caller passed on (previous results)

    steps = plan_lines(skill.plan)
    report, last = [], {'isdone': True}
    _local.depth = depth + 1
    try:
        for i, (no, group) in enumerate(steps, 1):
            text = ' && '.join(f'{a}::{s}' for a, s in group)
            if cancel_requested():
                log(f'{skill.name} {i}/{len(steps)}: cancelled before {text}')
                return {**last, 'isdone': False, 'msg': 'cancelled', 'failed_step': i,
                        'failed_line': text, 'plan_steps': report}

            def run_one(action, args):
                p = _substitute(args, values)
                p.update({k: v for k, v in ctx.items() if k != 'node'})
                name = _bare(action)
                ret = registry.execute(name, p, node=node, log_fn=log_fn)
                if not isinstance(ret, dict):
                    ret = {'isdone': bool(ret)}
                if 'not registered' not in str(ret.get('msg', '')):
                    if '!' in action:
                        ret['isdone'] = True
                    if '~' in action:
                        ret['isdone'] = False
                if ret.get('isdone', True):
                    _apply_world_effect(name, p, ret, node)
                return ret

            t0 = time.time()
            try:
                if len(group) == 1:
                    ret = run_one(*group[0])
                else:
                    ret = run_parallel_check(funcs=[lambda g=g: run_one(*g) for g in group])
            except Exception as e:
                logger.exception(f'plan skill {skill.name}: step {i} raised')
                ret = {'isdone': False, 'msg': str(e)}
            ok = bool(ret.get('isdone', True))
            dt = time.time() - t0
            report.append({'step': i, 'line': text, 'isdone': ok, 'sec': round(dt, 2),
                           **({} if ok else {'msg': str(ret.get('msg', ''))[:300]})})
            log(f'{skill.name} {i}/{len(steps)}: {text} {"✓" if ok else "✗"} ({dt:.1f}s)'
                + ('' if ok else f' — {ret.get("msg", "")}'))
            ctx.update(ret)
            last = ret
            if not ok:
                # Stop here, leaving the robot as it is: no fold / lift-home.
                return {**ret, 'isdone': False, 'failed_step': i, 'failed_line': text,
                        'msg': f'{skill.name} stopped at step {i} ({text}): {ret.get("msg", "")}',
                        'plan_steps': report}
        return {**last, 'isdone': True, 'plan_steps': report}
    finally:
        _local.depth = depth


def describe_plan_skills(registry) -> str:
    """One line per plan skill, for a planner guide (PLAN_SKILLS_HERE)."""
    lines = []
    for s in registry._skills.values():
        if s.type != 'plan':
            continue
        try:
            ps = plan_params(s.plan)
        except ValueError:
            ps = {}
        args = ', '.join(k if v is None else f'{k}={v}' for k, v in ps.items())
        lines.append(f'- {s.name}::{"<inputs>" if "inputs" in ps else ""}'
                     + (f'  ({args})' if args else '')
                     + (f' — {s.description}' if s.description else ''))
    return '\n'.join(lines)
