"""Location (deployment-site) config management.

A KCare robot deployed at different sites needs different *connections* (device
endpoints) and *global configs* (HOME_LOC, LLM_SERVERS, ENV, …). Each site is a
folder under ``configs/locations/<name>`` holding its own ``connections.json``,
``skill_configs_override.json`` and ``.env``. Shared state (skills, buttons)
lives in ``configs/common`` and is NOT per-site.

The active site can be switched at runtime — DeviceManager tears down the
current connections (keeping the shared ROS node) and reconnects from the new
site's files; ConfigManager swaps its overrides. No restart needed.

Endpoints
    GET    /config/locations              {locations: [...], active: name}
    POST   /config/locations              {name, copy_from?}      → create
    POST   /config/locations/{name}/activate                      → hot-switch
    PUT    /config/locations/{name}       {new_name}              → rename
    DELETE /config/locations/{name}                               → delete

If a robot has no site configured yet, the UI falls back to the ``default``
site (always present).
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..state import current

router = APIRouter()


class CreateLocationReq(BaseModel):
    name: str
    copy_from: Optional[str] = None


class RenameLocationReq(BaseModel):
    new_name: str


def _payload(state) -> dict:
    return {'locations': state.list_locations(), 'active': state.location}


@router.get('/config/locations')
def list_locations():
    return _payload(current())


@router.post('/config/locations')
def create_location(req: CreateLocationReq):
    state = current()
    try:
        state.create_location(req.name, copy_from=req.copy_from)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _payload(state)


@router.post('/config/locations/{name}/activate')
def activate_location(name: str):
    state = current()
    try:
        state.switch_location(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _payload(state)


@router.put('/config/locations/{name}')
def rename_location(name: str, req: RenameLocationReq):
    state = current()
    try:
        state.rename_location(name, req.new_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _payload(state)


@router.delete('/config/locations/{name}')
def delete_location(name: str):
    state = current()
    try:
        state.delete_location(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _payload(state)
