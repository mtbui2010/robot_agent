import json, os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Any, Optional

from ..state import current
from ..core.unified_agent import _serialize_result
from . import models as M
from .models import OPEN

_PROVIDER_KEY_MAP = {
    'chatgpt': 'OPENAI_API_KEY',
    'openai':  'OPENAI_API_KEY',      # the vlms servers' provider names (connect/pool.py)
    'gemini':  'GOOGLE_API_KEY',
    'anthropic': 'ANTHROPIC_API_KEY',
    'whisper': 'WHISPER_API_KEY',     # a voices server behind a public tunnel (speaches API_KEY)
}


def _env_file() -> Path:
    return current().data_dir / '.env'


router = APIRouter()


# Skills CRUD (GET / POST / PUT / DELETE /skills) lives in skills.py. A second
# POST /skills and DELETE /skills/{name} here were never reached (skills.py's
# router is included first) and were removed.

# ------------------------------------------------------------------
# LLM config
# ------------------------------------------------------------------
class LLMConfigReq(BaseModel):
    config: dict


@router.post('/agent/llm-config', response_model=M.Ok, **OPEN)
def set_llm_config(req: LLMConfigReq):
    current().ua.configure_llm(req.config)
    return {'ok': True}


@router.get('/agent/llm-config', response_model=dict[str, Any])
def get_llm_config():
    """Effective LLM config the planner runs on (explicit /agent/llm-config,
    else the active 'llm' connection). Secrets are stripped."""
    try:
        cfg = current().ua.effective_llm_cfg() or {}
    except Exception:
        cfg = {}
    _SECRET = {'api_key', 'key', 'token', 'authorization', 'apikey'}
    return {k: v for k, v in cfg.items() if k.lower() not in _SECRET}


# ------------------------------------------------------------------
# World state (symbolic belief: holding / arrived / opened / on).
# Persisted on AgentState; the closed-loop driver reads/mutates the same
# object, so an operator edit here is seen live by a running plan.
# ------------------------------------------------------------------
class WorldStateReq(BaseModel):
    arrived: str | None = None
    found: str | None = None
    holding: str | None = None
    opened: list[str] | None = None
    on: list[str] | None = None
    holding_since: float | None = None


@router.get('/agent/world', response_model=M.WorldStateOut, **OPEN)
def get_world():
    return current().world.to_dict()


@router.put('/agent/world', response_model=M.WorldStateOut, **OPEN)
def set_world(req: WorldStateReq):
    # Only apply fields the client actually sent (partial edit) so omitted
    # fields keep their current value.
    st = current()
    edit = req.model_dump(exclude_unset=True)
    st.world.update_from_dict(edit)
    # an operator edit is a belief the robot re-checks (provenance `operator`; docs/guard.md)
    st.world.mark_edited(edit.keys(), 'operator')
    st.save_world()   # operator corrections survive a restart
    return st.world.to_dict()


# ------------------------------------------------------------------
# API keys
# ------------------------------------------------------------------
class ApiKeyReq(BaseModel):
    provider: str


class ApiKeyWithValueReq(BaseModel):
    provider: str
    key: str


@router.post('/agent/api-key', response_model=M.Ok, **OPEN)
def set_api_key(req: ApiKeyWithValueReq):
    env_var = _PROVIDER_KEY_MAP.get(req.provider.lower())
    if not env_var:
        return {'ok': False, 'error': f'Unknown provider: {req.provider}'}
    os.environ[env_var] = req.key
    env_file = _env_file()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    # The site's .env may be shared with another machine: read it fresh (an
    # sshfs read can come back cut short) and replace it in one step.
    from ..core.shared_json import read_fresh
    try:
        lines: list[str] = read_fresh(env_file).splitlines()
    except FileNotFoundError:
        lines = []
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f'{env_var}='):
            lines[i] = f'{env_var}={req.key}'
            updated = True
            break
    if not updated:
        lines.append(f'{env_var}={req.key}')
    tmp = env_file.with_name(f'.env.tmp.{os.getpid()}')
    tmp.write_text('\n'.join(lines) + '\n')
    os.replace(tmp, env_file)
    return {'ok': True}


@router.get('/agent/api-keys', response_model=dict[str, bool])
def get_api_keys():
    env_file = _env_file()
    return {
        provider: bool(os.environ.get(env_var) or (
            env_file.exists() and
            any(l.startswith(f'{env_var}=') and l.split('=', 1)[1].strip()
                for l in env_file.read_text().splitlines())
        ))
        for provider, env_var in _PROVIDER_KEY_MAP.items()
    }


# ------------------------------------------------------------------
# Agent WebSocket
# ------------------------------------------------------------------
class ListenAnswer(BaseModel):
    text: str = ''
    error: Optional[str] = None
    # Why nothing was recognised, when text is empty and it is not an error:
    # the browser's mic events and code, e.g. 'mic on, no speech (no-speech)'.
    note: Optional[str] = None


@router.post('/agent/listen/{req_id}', response_model=M.Ok, **OPEN)
def answer_listen(req_id: str, body: ListenAnswer):
    """The dashboard's transcript for a pending HRI listen request.

    A skill running with ``source='dashboard'`` emits a ``listen`` event over
    the agent WebSocket and blocks in ``robot_agent.utils.listen_dashboard``;
    the browser captures the answer and posts it here. 404 once the request has
    timed out and nothing is waiting any more.
    """
    from ..utils import submit_transcript
    if not submit_transcript(req_id, body.text, body.error, body.note):
        raise HTTPException(status_code=404, detail=f'no pending listen request {req_id!r}')
    return {'ok': True}


@router.post('/agent/listen/{req_id}/audio', response_model=M.OkText, **OPEN)
async def answer_listen_audio(req_id: str, request: Request):
    """The dashboard's recording for a listen with ``capture='whisper'``.

    Raw body (``Content-Type`` audio/webm, audio/ogg, …; empty when nobody
    spoke); ``?note=`` carries the browser's account of an empty recording.
    Transcribed with the 'stt' connection off the event loop.
    """
    from starlette.concurrency import run_in_threadpool
    from ..utils import transcribe_listen_audio
    data = await request.body()
    mime = request.headers.get('content-type', 'audio/webm')
    text = await run_in_threadpool(transcribe_listen_audio, req_id, data, mime,
                                   request.query_params.get('note'))
    if text is None:
        raise HTTPException(status_code=404, detail=f'no pending listen request {req_id!r}')
    return {'ok': True, 'text': text}


@router.post('/agent/qa/end', response_model=M.QaEndResult, **OPEN)
def end_qa():
    """End the running kcare `qa` conversation as a stop word would: it says
    goodbye, returns success and the plan goes on (unlike /agent/cancel).
    409 when no qa conversation is running."""
    from ..core.run_control import request_end_dialog
    out = request_end_dialog()
    if not out.get('ok'):
        raise HTTPException(status_code=409, detail=out.get('detail', 'no qa conversation is running'))
    return out


@router.post('/agent/cancel', response_model=M.CancelResult, **OPEN)
def cancel_run():
    """Stop the robot: cancel every ROS command in flight and end the run.

    This is what the dashboard's Stop button reaches. Closing the WebSocket is
    not enough on its own — the plan runs in a backend thread that would
    otherwise finish the current skill and keep going through the remaining
    steps. Cancelling is process-wide (one robot per process), so it also stops
    a skill started from the CLI or a second client.
    """
    from ..core.run_control import request_cancel
    return request_cancel()


# ------------------------------------------------------------------
# Runs: one active run per robot (robot_agent.interact.run_manager)
# ------------------------------------------------------------------
class RunControlReq(BaseModel):
    user_id: str = ''
    role: Optional[str] = None      # owner | member | guest; missing -> owner


class RunInjectReq(RunControlReq):
    text: str


def _runs():
    from ..core.run_control import manager
    return manager()


_OK_STATUS = ('success', 'aborted', 'planned')


def _done_ok(done: dict) -> bool:
    """Did the run end well? The planning loops say so in ``status`` (success / aborted / planned vs
    failed / refused ...); the open-loop path only in ``success``."""
    st = done.get('status')
    if st:
        return str(st) in _OK_STATUS
    return bool(done.get('success', True))


def _result_or_raise(res) -> dict:
    if not res.ok and res.action == 'refused':
        status = {'not_found': 404, 'not_active': 409}.get(res.code, 403)
        raise HTTPException(status_code=status, detail=res.reason)
    return res.to_dict()


@router.get('/runs', response_model=M.RunList, **OPEN)
def list_runs():
    mgr = _runs()
    act = mgr.active()
    return {'active': act.run_id if act is not None else None,
            'runs': [r.to_dict() for r in mgr.list()]}


@router.get('/runs/{run_id}', response_model=M.RunInfo, **OPEN)
def get_run(run_id: str, events: bool = True):
    run = _runs().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f'no run {run_id!r}')
    return run.to_dict(events=events)


@router.get('/runs/{run_id}/record', response_model=M.RunRecordOut, **OPEN)
def get_run_record(run_id: str, kinds: Optional[str] = None, limit: Optional[int] = None):
    """The run's RunRecord rows (robot_agent.observe.record). ``kinds``: a
    comma-separated filter (``llm,skill``); ``limit``: the last N rows. Works
    for finished runs too, from disk."""
    from ..observe.record import load_record
    try:
        return load_record(run_id, kinds=[k for k in (kinds or '').split(',') if k] or None, limit=limit)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f'no record of run {run_id!r}')


@router.get('/runs/{run_id}/metrics', response_model=M.RunMetrics, **OPEN)
def get_run_metrics(run_id: str):
    """The run's metrics line (LLM calls, latency, replans, failure codes,
    verified success); so far, with ``live: true``, while it runs."""
    from ..observe.record import run_metrics
    try:
        return run_metrics(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f'no record of run {run_id!r}')


def _control(run_id: str, action: str, body: Optional[RunControlReq]) -> dict:
    body = body or RunControlReq()
    fn = getattr(_runs(), action)
    return _result_or_raise(fn(run_id, role=body.role, user_id=body.user_id))


@router.post('/runs/{run_id}/cancel', response_model=M.RunControlResult, **OPEN)
def cancel_one_run(run_id: str, body: Optional[RunControlReq] = None):
    """Cancel one run and stop the robot at once (ROS goals, listens). A lower
    role than the run's starter is refused (403); 409 once it has ended."""
    return _control(run_id, 'cancel', body)


@router.post('/runs/{run_id}/pause', response_model=M.RunControlResult, **OPEN)
def pause_run(run_id: str, body: Optional[RunControlReq] = None):
    """Pause one run; it holds at the next step boundary."""
    return _control(run_id, 'pause', body)


@router.post('/runs/{run_id}/resume', response_model=M.RunControlResult, **OPEN)
def resume_run(run_id: str, body: Optional[RunControlReq] = None):
    return _control(run_id, 'resume', body)


@router.post('/runs/{run_id}/inject', response_model=M.RunControlResult, **OPEN)
def inject_run(run_id: str, body: RunInjectReq):
    """Something said / typed during the run, routed by the intent router."""
    return _result_or_raise(_runs().inject(run_id, body.text, user_id=body.user_id,
                                           role=body.role))


class _Sender:
    """Serialises sends on one socket (the event stream and run-manager
    notices share it)."""

    def __init__(self, websocket: WebSocket):
        import asyncio
        self.ws = websocket
        self.lock = asyncio.Lock()

    async def send(self, event: dict) -> None:
        async with self.lock:
            await self.ws.send_text(
                json.dumps(event, ensure_ascii=False, allow_nan=False, default=_serialize_result))


def _client_text(text: str):
    """A mid-run message: ('cancel', None), ('say', utterance) or (None, None)."""
    try:
        msg = json.loads(text)
    except Exception:
        return None, None
    if not isinstance(msg, dict):
        return None, None
    if msg.get('cancel'):
        return 'cancel', None
    said = msg.get('text') or msg.get('prompt')
    return ('say', str(said)) if said else (None, None)


async def _watch_client(websocket: WebSocket, run=None, user_id: str = '', role=None,
                        sender: '_Sender | None' = None) -> None:
    """Cancel the run when the dashboard goes away mid-plan.

    Runs alongside the event stream: a reload, a closed tab or a dropped
    network connection leaves nobody watching the robot, which must not keep
    executing. The client may also cancel over this socket by sending
    ``{"cancel": true}``. ``{"text": "..."}`` during the run is an event into
    it (stop / pause / answer / goal change, see the intent router); the
    result comes back as a ``run_injected`` event.
    """
    from ..core.run_control import request_cancel
    try:
        while True:
            msg = await websocket.receive()
            if msg.get('type') == 'websocket.disconnect':
                break
            text = msg.get('text')
            if text:
                kind, said = _client_text(text)
                if kind == 'cancel':
                    break
                if kind == 'say' and run is not None:
                    res = _runs().inject(run.run_id, said, user_id=user_id, role=role)
                    if sender is not None:
                        await sender.send({'event': 'run_injected', 'text': said, **res.to_dict()})
    except Exception:
        pass          # socket already torn down — treat as a disconnect
    try:
        request_cancel()
    except Exception:
        pass


async def _observe(websocket: WebSocket, sender: '_Sender', run, prompt: str,
                   user_id: str, role) -> None:
    """A second client while a run is active: it watches that run (every
    event, with its run_id) and what it says goes in as an event, never as a
    parallel run. Its going away does not cancel the run."""
    import asyncio
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    unsub = run.subscribe(lambda ev: loop.call_soon_threadsafe(q.put_nowait, ev))
    try:
        res = _runs().inject(run.run_id, prompt, user_id=user_id, role=role) if prompt else None
        await sender.send({'event': 'run_attached', 'run_id': run.run_id, 'observer': True,
                           'decision': 'busy', 'run': run.to_dict(),
                           **({'injected': res.to_dict()} if res is not None else {})})

        async def _forward():
            while True:
                ev = await q.get()
                await sender.send(ev)
                if ev.get('event') == 'run_state' and ev.get('state') in ('done', 'failed'):
                    return

        async def _receive():
            while True:
                msg = await websocket.receive()
                if msg.get('type') == 'websocket.disconnect':
                    return
                kind, said = _client_text(msg.get('text') or '')
                if kind == 'cancel':
                    r = _runs().cancel(run.run_id, role=role, user_id=user_id)
                    await sender.send({'event': 'run_injected', **r.to_dict()})
                elif kind == 'say':
                    r = _runs().inject(run.run_id, said, user_id=user_id, role=role)
                    await sender.send({'event': 'run_injected', 'text': said, **r.to_dict()})

        if not run.active:
            return
        tasks = [asyncio.create_task(_forward()), asyncio.create_task(_receive())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
    finally:
        unsub()


@router.websocket('/ws/agent')
async def agent_ws(websocket: WebSocket):
    await websocket.accept()
    watcher = None
    run, unsub, notices, outcome = None, None, None, {'ok': True, 'outcome': ''}
    try:
        data = await websocket.receive_json()
        prompt  = data.get('prompt', '')
        lang    = data.get('lang', 'en')
        direct    = data.get('direct', False)
        planner   = data.get('planner')         # 'grace' | 'direct' | 'sage' | 'package.module:Class' | None (legacy)
        plan_only = data.get('plan_only', False) # generate plan, don't execute
        log_data  = data.get('log_data', False)  # save rgb/depth/results of vision
        # 'backend' writes the dataset to disk here; 'frontend' streams it to the
        # dashboard instead. log_all=False keeps only failed cases.
        log_mode  = data.get('log_mode', 'backend')
        log_all   = data.get('log_all', True)
        # planner options (SAGE: confirm_task, confirm_answers, find_min_conf, ...); unknown keys are ignored
        options   = data.get('options') if isinstance(data.get('options'), dict) else None
        # 'sage' only: the planner behind it (a registry name or 'package.module:Class'; default grace)
        if data.get('generator'):
            options = dict(options or {}, generator=data['generator'])
        # who is asking (optional; a client without them acts as the owner, as before)
        user_id   = str(data.get('user_id') or '')
        role      = data.get('role')

        import asyncio
        sender = _Sender(websocket)
        # One active run per robot: while one runs, this client observes it and
        # its prompt becomes an event into that run.
        start = _runs().start(goal=prompt, user_id=user_id, role=role)
        if not start.started:
            await _observe(websocket, sender, start.run, prompt, user_id, role)
            return
        run = start.run

        # Run-manager notices (pause / resume, refusals, others' input) reach
        # this client too, as they happen.
        loop = asyncio.get_running_loop()
        nq: asyncio.Queue = asyncio.Queue()
        unsub = run.subscribe(lambda ev: ev.get('origin') == 'run_manager'
                              and loop.call_soon_threadsafe(nq.put_nowait, ev))

        async def _notices():
            while True:
                await sender.send(await nq.get())
        notices = asyncio.create_task(_notices())

        # Watch for the client going away (or asking to cancel) while the plan
        # runs; the run itself clears the cancel flag as it starts.
        watcher = asyncio.create_task(_watch_client(websocket, run, user_id, role, sender))

        ua = current().ua
        gen = (ua.run_direct(plan=prompt, log_data=log_data, lang=lang,
                             log_mode=log_mode, log_all=log_all) if direct
               else ua.run(prompt=prompt, lang=lang, planner=planner,
                           plan_only=plan_only, log_data=log_data,
                           log_mode=log_mode, log_all=log_all, options=options))
        async for event in gen:
            # Walk the whole event through _serialize_result so numpy scalars
            # and NaN/Inf are sanitized at a single point. `allow_nan=False`
            # crashes-fast on anything that still slipped through.
            safe_event = run.publish(_serialize_result(event))   # + run_id, kept on the run
            if safe_event.get('event') == 'error':
                outcome.update(ok=False, outcome='error')
            elif safe_event.get('event') == 'done':
                outcome.update(ok=_done_ok(safe_event), outcome=str(safe_event.get('status') or ''))
            await sender.send(safe_event)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        outcome.update(ok=False, outcome='error')
        try:
            await websocket.send_text(json.dumps({'event': 'error', 'msg': str(e),
                                                  **({'run_id': run.run_id} if run else {})}))
        except Exception:
            pass
    finally:
        # The run is over: stop watching, so finishing normally never looks
        # like a cancel.
        if watcher is not None:
            watcher.cancel()
        if notices is not None:
            notices.cancel()
        if unsub is not None:
            unsub()
        if run is not None:
            _runs().finish(run.run_id, ok=outcome['ok'], outcome=outcome['outcome'])
