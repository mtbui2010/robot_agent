import copy
from fastapi import APIRouter, HTTPException
from ..state import current
from ..skill_configs import PROXY_REGISTRY

router = APIRouter()


def _resolve(name: str):
    """Return the same value the proxy would expose to running skills:
    ConfigManager value if present, else the proxy's hardcoded `_default`."""
    val = current().cm.get(name)
    if val is not None:
        return val
    proxy = PROXY_REGISTRY.get(name)
    if proxy is not None:
        return copy.deepcopy(proxy._default)
    return None


@router.get('/skill-configs')
def list_configs():
    result = current().cm.get_all()
    for name, proxy in PROXY_REGISTRY.items():
        if name not in result:
            result[name] = copy.deepcopy(proxy._default)
    return result


@router.get('/skill-configs/{name}')
def get_config(name: str):
    val = _resolve(name)
    if val is None:
        raise HTTPException(status_code=404, detail=f'Config "{name}" not found')
    return val


@router.put('/skill-configs/{name}')
async def update_config(name: str, body: dict):
    error = current().cm.update(name, body)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {'ok': True}


@router.patch('/skill-configs/{name}')
async def merge_config(name: str, body: dict):
    """Body ``{base, value}``: the group as the client loaded it and as it is now.
    Only the entries the client changed are written (3-way, per top-level key —
    per location for ENV); returns the saved group, other machines' edits included."""
    base, value = body.get('base'), body.get('value')
    if not isinstance(base, dict) or not isinstance(value, dict):
        raise HTTPException(status_code=400, detail='body must be {"base": {...}, "value": {...}}')
    error, saved = current().cm.merge_update(name, base, value)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {'ok': True, 'value': saved}
