"""How a ``/ws/agent`` first message becomes a run: the fleet seams ``goals``, ``client_ref`` and ``busy`` (docs/fleet_seams.md).

What: ``with_goals`` puts typed ``goals`` into the run's ``options`` (the loop takes them with ``accept_goals``) and, with no prompt,
writes the prompt from them; ``verdict`` decides a keyed start from the runs this process keeps (pure); ``begin`` answers the client
(``duplicate`` / ``refused``) or starts the run. Why: a machine client (a fleet manager) must not start a goal twice after a resend,
and must not have its text injected into someone else's run. Off: a first message without ``goals``, ``client_ref`` and
``busy`` takes today's path (empty prompt error, start, or observe with injection), byte for byte.
"""

from __future__ import annotations

import contextlib
import json
import threading

_LOCK = threading.Lock()        # a keyed start: the lookup and the start are one step (two resends cannot both start)


def with_goals(data: dict, prompt, options):
    """(prompt, options) with the message's ``goals`` (a list of dicts) in ``options['goals']``; no prompt: the goals' text."""
    goals = data.get('goals')
    if not isinstance(goals, list) or not goals:
        return prompt, options
    goals = [dict(g) for g in goals if isinstance(g, dict)]
    if not str(prompt or '').strip():
        from ..core.planning.goals import goal_text
        try:
            prompt = goal_text(goals)
        except Exception:
            prompt = ''
    return prompt, dict(options or {}, goals=goals)


def ref_key(ref):
    """(plan_id, goal_id) of a ``client_ref`` dict, None when it is not one (both must be non-empty)."""
    if not isinstance(ref, dict) or not str(ref.get('plan_id') or '') or not str(ref.get('goal_id') or ''):
        return None
    try:
        int(ref.get('attempt') or 0)
    except (TypeError, ValueError):
        return None
    return str(ref['plan_id']), str(ref['goal_id'])


def verdict(ref: dict, seen) -> tuple:
    """(``start`` | ``duplicate`` | ``conflict`` | ``stale``, the run it is about or None). ``seen``: (client_ref, run) of the
    runs this process keeps, newest first. Same key, lower attempt than any seen: stale; another hash than the newest: conflict;
    else duplicate. Pure."""
    same = [(r, run) for r, run in seen if ref_key(r) == ref_key(ref)]
    if not same:
        return 'start', None
    if int(ref.get('attempt') or 0) < max(int(r.get('attempt') or 0) for r, _ in same):
        return 'stale', same[0][1]
    newest, run = same[0]
    return ('duplicate' if str(ref.get('hash')) == str(newest.get('hash')) else 'conflict'), run


def _refused(reason: str, run=None) -> dict:
    return {'event': 'refused', 'reason': reason, **({'run_id': run.run_id} if run is not None else {})}


def decide(mgr, data: dict, prompt, direct, user_id: str, role) -> tuple:
    """(kind, message, start) for a first message. kind: ``start`` (start is the new run), ``observe`` (today's observer),
    ``attach`` (a duplicate of the active run: observe it without injecting), ``answer`` (send message, close)."""
    ref, busy = data.get('client_ref'), data.get('busy') == 'refuse'
    if ref is not None and ref_key(ref) is None:
        return 'answer', _refused('bad_ref'), None
    with _LOCK if ref is not None else contextlib.nullcontext():
        if ref is not None:
            kind, run = verdict(ref, [(r.client_ref, r) for r in mgr.list() if r.client_ref])
            if kind == 'duplicate':
                return ('attach' if run.active else 'answer'), {'event': 'duplicate', 'run_id': run.run_id, 'state': run.state.value}, run
            if kind != 'start':
                return 'answer', _refused(kind, run), None
        act = mgr.active()
        if busy and act is not None:
            return 'answer', _refused('busy', act), None
        if not direct and not str(prompt or '').strip() and act is None:
            # an empty prompt with no run to watch is no request: opening the socket to read must not start a run
            return 'empty', None, None
        start = mgr.start(goal=prompt, user_id=user_id, role=role, **({'client_ref': ref} if ref is not None else {}))
    if start.started:
        return 'start', None, start
    return ('answer', _refused('busy', start.run), None) if busy else ('observe', None, start)


async def begin(websocket, sender, data: dict, prompt, direct, user_id: str, role, mgr, observe):
    """The StartDecision of a new run, or None when the client was answered (refused, duplicate, empty prompt) or observed."""
    kind, msg, start = decide(mgr, data, prompt, direct, user_id, role)
    if kind == 'start':
        return start
    if kind == 'empty':
        await websocket.send_text(json.dumps({'event': 'error', 'msg': 'empty prompt: no run was started'}))
    elif kind == 'observe':
        await observe(websocket, sender, start.run, prompt, user_id, role)
    else:
        await sender.send(msg)
        if kind == 'attach':
            await observe(websocket, sender, start, '', user_id, role)      # a resend: the run's events, its text not injected again
        else:
            await websocket.close()                                          # answered (refused / duplicate of an ended run): nothing more comes
    return None
