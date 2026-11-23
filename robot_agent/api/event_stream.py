"""``WS /ws/events``: the events of the active run and of every run that starts later, read-only (docs/fleet_seams.md).

On connect ``{"event": "events_open", "active": <run_id or null>}``, then every event published on a run (each carries its ``run_id``;
the run manager's ``run_state`` too). It never starts, injects into or cancels a run: a message from the client is answered
``{"event": "refused", "reason": "read_only"}`` and changes nothing, and closing the socket stops nothing (robotapp rule 6: a read never
starts a run). Why: ``/ws/agent`` was the only live stream, and opening it is a request. Events published before the connect are in
``GET /runs/{id}?events=true``. Off: nothing happens until a client opens it.
"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


def follow(mgr, push):
    """Call ``push(event)`` for every event of the manager's active run and of every run it starts later (``on_start`` / ``on_finish``
    hooks, ``Run.subscribe``). Returns the function that undoes it all."""
    subs: dict = {}

    def watch(run):
        if run.run_id not in subs:
            subs[run.run_id] = run.subscribe(push)

    def forget(run):
        un = subs.pop(run.run_id, None)
        if un is not None:
            un()

    mgr.on_start.append(watch)
    mgr.on_finish.append(forget)
    act = mgr.active()
    if act is not None:
        watch(act)

    def stop():
        for hooks, fn in ((mgr.on_start, watch), (mgr.on_finish, forget)):
            if fn in hooks:
                hooks.remove(fn)
        for un in list(subs.values()):
            un()
        subs.clear()
    return stop


async def _forward(websocket: WebSocket, q: asyncio.Queue) -> None:
    try:
        while True:
            await websocket.send_text(json.dumps(await q.get(), ensure_ascii=False, default=str))
    except Exception:
        return                     # the socket is gone: the stream ends, nothing else changes


async def _receive(websocket: WebSocket, q: asyncio.Queue) -> None:
    while True:
        msg = await websocket.receive()
        if msg.get('type') == 'websocket.disconnect':
            return
        q.put_nowait({'event': 'refused', 'reason': 'read_only'})


@router.websocket('/ws/events')
async def events_ws(websocket: WebSocket):
    from ..core.run_control import manager
    await websocket.accept()
    loop, q, mgr = asyncio.get_running_loop(), asyncio.Queue(), manager()
    stop = follow(mgr, lambda ev: loop.call_soon_threadsafe(q.put_nowait, ev))
    tasks = []
    try:
        act = mgr.active()
        await websocket.send_text(json.dumps({'event': 'events_open', 'active': act.run_id if act is not None else None}))
        tasks = [asyncio.create_task(_forward(websocket, q)), asyncio.create_task(_receive(websocket, q))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        stop()
