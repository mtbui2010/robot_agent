"""Teleoperation functions (see ``core/teleop.py``): show their state, switch one on / off, set its speed.

    GET  /teleop                   {functions: [{name, label, available, state?, age_s?}]}   (empty: nothing configured / no ROS)
    POST /teleop/{name}/enable     {on, confirm}   on=true needs confirm=true (a person is next to the robot) and no run in flight
    POST /teleop/{name}/speed      {scale}         0..1, a fraction of the function's own top speed

Reading never switches anything on (GET). Switching OFF never needs a confirmation. A run starting, and Cancel, switch every function off.
"""
from fastapi import APIRouter, HTTPException

from ..core import run_control
from ..core.teleop import bridge
from . import models as M

router = APIRouter()


def _function(name: str):
    row = next((f for f in bridge().listing() if f['name'] == name), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f'no teleop function "{name}"')
    return row


@router.get('/teleop', response_model=M.TeleopList, **M.OPEN)
def list_teleop():
    return {'functions': bridge().listing()}


@router.post('/teleop/{name}/enable', response_model=M.Ok, **M.OPEN)
def enable(name: str, body: M.TeleopEnableIn):
    row = _function(name)
    if body.on:
        if not body.confirm:
            raise HTTPException(status_code=400, detail='confirm: switching teleop on moves the robot; a person must be next to it')
        if not row['available']:
            raise HTTPException(status_code=409, detail=f'"{name}" is not running (no state from it): start robot_teleop on the robot')
        if run_control.manager().active() is not None:
            raise HTTPException(status_code=409, detail='a run is in flight: cancel it first')
    if not bridge().command(name, 'enable' if body.on else 'disable'):
        raise HTTPException(status_code=503, detail='ROS is not up on this agent')
    return {'ok': True}


@router.post('/teleop/{name}/speed', response_model=M.Ok, **M.OPEN)
def speed(name: str, body: M.TeleopSpeedIn):
    _function(name)
    if not bridge().command(name, 'speed', max(0.0, min(1.0, body.scale))):
        raise HTTPException(status_code=503, detail='ROS is not up on this agent')
    return {'ok': True}
