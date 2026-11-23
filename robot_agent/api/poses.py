"""The dashboard's "Go to" poses: a label + a plan of steps like a shortcut button's, edited by the operator (see ``core/poses.py``).

    GET    /poses                  {speed, poses: [{id, label, plan}], defaults_missing}
    POST   /poses                  {label, plan}          add
    PUT    /poses/{id}             {label?, plan?}        edit
    DELETE /poses/{id}
    PUT    /poses/speed            {speed}                the one speed every pose runs at (0.05..1)
    POST   /poses/defaults         add the robot package's seed poses that are missing
    POST   /poses/{id}/run         run the pose now (switches teleop off first; answers when it is done); during a recording the move is marked in the episode
Reading changes nothing.
"""
from fastapi import APIRouter, HTTPException

from ..core import poses as P
from ..core import run_control
from ..core.recorder import bridge as recorder_bridge
from ..state import current
from . import models as M

router = APIRouter()


def _pm():
    return current().pm


def _bad(e: Exception):
    return HTTPException(status_code=400, detail=str(e))


@router.get('/poses', response_model=M.PoseList, **M.OPEN)
def list_poses():
    return _pm().all()


@router.post('/poses', response_model=M.Pose, **M.OPEN)
def add_pose(body: M.PoseIn):
    try:
        return _pm().add(body.label, body.plan)
    except ValueError as e:
        raise _bad(e)


@router.put('/poses/speed', response_model=M.PoseSpeed, **M.OPEN)
def set_speed(body: M.PoseSpeed):
    try:
        return {'speed': _pm().set_speed(body.speed)}
    except ValueError as e:
        raise _bad(e)


@router.post('/poses/defaults', response_model=M.PoseDefaults, **M.OPEN)
def restore_defaults():
    return {'added': _pm().restore_defaults()}


@router.put('/poses/{pid}', response_model=M.Pose, **M.OPEN)
def update_pose(pid: str, body: M.PoseUpdate):
    try:
        out = _pm().update(pid, body.label, body.plan)
    except ValueError as e:
        raise _bad(e)
    if out is None:
        raise HTTPException(status_code=404, detail='no such pose')
    return out


@router.delete('/poses/{pid}', response_model=M.Ok, **M.OPEN)
def delete_pose(pid: str):
    if not _pm().remove(pid):
        raise HTTPException(status_code=404, detail='no such pose')
    return {'ok': True}


def _mark(label: str, phase: str, ok=None) -> None:
    """While an episode is being recorded, tell the recorder that a Go to starts / ends (it becomes an event of the episode)."""
    try:
        rb = recorder_bridge()
        st = rb.status()
        if st and st.get('available') and (st.get('state') or {}).get('state') == 'recording':
            rb.send({'cmd': 'mark', 'kind': 'goto', 'name': label, 'phase': phase, **({} if ok is None else {'ok': bool(ok)})})
    except Exception:
        pass


@router.post('/poses/{pid}/run', response_model=M.PoseRun, **M.OPEN)
def run_pose(pid: str):
    got = _pm().plan_to_run(pid)
    if got is None:
        raise HTTPException(status_code=404, detail='no such pose')
    if run_control.manager().active() is not None:
        raise HTTPException(status_code=409, detail='a run is in flight: cancel it first')
    label, plan = got
    state = current()
    node = state.dm._ros_node
    run_control.begin_run(node)               # a cancel must not leak into this move; switches teleop off
    _mark(label, 'start')
    try:
        out = P.run_plan(state.sr, plan, node=node, name=f'pose {label}')
    except Exception as e:
        out = {'isdone': False, 'msg': str(e)}
    ok = bool(out.get('isdone', True))
    _mark(label, 'end', ok)
    return {'isdone': ok, 'msg': str(out.get('msg', '')), 'plan': plan}
