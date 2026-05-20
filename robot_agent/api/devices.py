from fastapi import APIRouter
from pydantic import BaseModel
from typing import Literal

from ..state import current

router = APIRouter()


class AddConnectReq(BaseModel):
    type: Literal['ros_service', 'ros_topic', 'ros_action', 'webrtc', 'llm', 'tcp']
    name: str
    config: dict


class UpdateConnectReq(BaseModel):
    name: str
    config: dict


@router.get('/ros/scan')
def scan_ros():
    return current().dm.scan_ros()


@router.get('/connects')
def list_connects():
    return current().dm.get_all()


@router.get('/connects/status')
def connects_status():
    return current().dm.get_status()


@router.post('/connects')
def add_connect(req: AddConnectReq):
    cid, error = current().dm.add_connect(conn_type=req.type, name=req.name, config=req.config)
    return {'id': cid, 'error': error}


@router.delete('/connects/{cid:path}')
def remove_connect(cid: str):
    return {'ok': current().dm.remove_connect(cid)}


@router.put('/connects/{cid:path}')
def update_connect(cid: str, req: UpdateConnectReq):
    new_id, error = current().dm.update_connect(cid=cid, name=req.name, config=req.config)
    return {'id': new_id, 'error': error}


@router.post('/connects/{cid:path}/set_active')
def set_active(cid: str):
    """Mark a type='llm' connect as active; peers get cleared. Used by the
    'Active' radio in the DevicePanel UI."""
    ok = current().dm.set_active(cid)
    return {'ok': ok}
