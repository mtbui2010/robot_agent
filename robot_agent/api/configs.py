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
