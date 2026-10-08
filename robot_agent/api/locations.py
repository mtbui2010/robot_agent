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
    GET    /config/locations/{name}/files/{path}                  → a static file of the site
                                                                     (images / json, e.g. map layers);
                                                                     name ``_active`` = the active site

If a robot has no site configured yet, the UI falls back to the ``default``
site (always present).
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..state import _safe_location_name, current

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


# Files a site may serve to the UI: map layers (images + their metadata). Nothing else —
# connections.json / .env / overrides stay behind their own endpoints.
_SERVABLE = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
             '.svg': 'image/svg+xml', '.json': 'application/json'}
_PRIVATE = {'connections.json', 'skill_configs_override.json'}


@router.get('/config/locations/{name}/files/{path:path}')
def location_file(name: str, path: str):
    """A static file under the site's folder, e.g. ``map/heightmap.png`` for the Map tab."""
    state = current()
    try:
        loc = state.location if name == '_active' else _safe_location_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    base = (state.locations_dir / loc if loc else state.locations_dir).resolve()
    target = (base / path).resolve()
    parts = Path(path).parts
    if (base not in target.parents or any(p.startswith('.') for p in parts)
            or target.name in _PRIVATE or target.suffix.lower() not in _SERVABLE):
        raise HTTPException(status_code=403, detail=f'not a servable site file: {path}')
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f'no such file in site {loc!r}: {path}')
    return FileResponse(target, media_type=_SERVABLE[target.suffix.lower()],
                        headers={'Cache-Control': 'no-cache'})
