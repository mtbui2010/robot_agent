"""Raw VLA episodes: record while teleoperating (see ``core/recorder.py``, ``core/prompts.py``).

    GET  /recorder          {available, state?, age_s?, dir, teleop, fields}   (404 when the robot has no RECORDER config)
    POST /recorder/prompt   {fields: {action, target_object, ...}}  ->  {prompt}   a random sentence from the robot's templates
    POST /recorder/start    {prompt, meta?}   starts remote control (the configured teleop function) and the recording
    POST /recorder/stop     {label: success | failure | discard}   stops remote control and saves (or throws away) the episode
Reading never starts anything. A run in flight blocks start.
"""
from fastapi import APIRouter, HTTPException

from ..core import prompts, run_control
from ..core import teleop as _teleop
from ..core.recorder import bridge
from . import models as M

router = APIRouter()


def _cfg():
    st = bridge().status()
    if st is None:
        raise HTTPException(status_code=404, detail='this robot has no RECORDER config')
    return st


@router.get('/recorder', response_model=M.RecorderStatus, **M.OPEN)
def get_recorder():
    return _cfg()


@router.post('/recorder/prompt', response_model=M.RecorderPrompt, **M.OPEN)
def make_prompt(body: M.RecorderPromptIn):
    _cfg()
    try:
        return {'prompt': prompts.generate((bridge().config() or {}).get('prompts') or {}, body.fields)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/recorder/start', response_model=M.Ok, **M.OPEN)
def start(body: M.RecorderStartIn):
    st = _cfg()
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail='write or generate a prompt first')
    if not st['available']:
        raise HTTPException(status_code=409, detail='the recorder is not running: start robot_teleop (make run) on the robot')
    if (st.get('state') or {}).get('state') != 'idle':
        raise HTTPException(status_code=409, detail=f'the recorder is {(st.get("state") or {}).get("state")}')
    if run_control.manager().active() is not None:
        raise HTTPException(status_code=409, detail='a run is in flight: cancel it first')
    fn = st.get('teleop')
    row = next((f for f in _teleop.bridge().listing() if f['name'] == fn), None) if fn else None
    if fn and (row is None or not row['available']):
        raise HTTPException(status_code=409, detail=f'teleop "{fn}" is not running: start robot_teleop on the robot')
    if not bridge().send({'cmd': 'start', 'prompt': body.prompt.strip(), 'meta': body.meta or {}}):
        raise HTTPException(status_code=503, detail='ROS is not up on this agent')
    if fn:
        _teleop.bridge().command(fn, 'enable')
    return {'ok': True}


@router.post('/recorder/stop', response_model=M.Ok, **M.OPEN)
def stop(body: M.RecorderStopIn):
    st = _cfg()
    if body.label not in ('success', 'failure', 'discard'):
        raise HTTPException(status_code=400, detail='label: success | failure | discard')
    fn = st.get('teleop')
    if fn:
        _teleop.bridge().command(fn, 'disable')
    cmd = {'cmd': 'discard'} if body.label == 'discard' else {'cmd': 'stop', 'label': body.label}
    if not bridge().send(cmd):
        raise HTTPException(status_code=503, detail='ROS is not up on this agent')
    return {'ok': True}
