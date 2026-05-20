import json, os
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Literal

from ..state import current
from ..core.unified_agent import _serialize_result

_PROVIDER_KEY_MAP = {
    'chatgpt': 'OPENAI_API_KEY',
    'gemini':  'GOOGLE_API_KEY',
}


def _env_file() -> Path:
    return current().data_dir / '.env'


router = APIRouter()


# ------------------------------------------------------------------
# Skills CRUD (GET is in skills.py; add/delete managed here)
# ------------------------------------------------------------------
class AddSkillReq(BaseModel):
    name: str
    type: Literal['internal', 'external']
    description: str = ''
    module_path: str = ''
    func_name: str = ''
    url: str = ''


@router.post('/skills')
def add_skill(req: AddSkillReq):
    sr = current().sr
    if req.type == 'internal':
        sr.register_internal(
            name=req.name, module_path=req.module_path,
            func_name=req.func_name or req.name, description=req.description,
        )
    else:
        sr.register_external(name=req.name, url=req.url, description=req.description)
    return {'ok': True}


@router.delete('/skills/{name}')
def remove_skill(name: str):
    current().sr.remove(name)
    return {'ok': True}


# ------------------------------------------------------------------
# LLM config
# ------------------------------------------------------------------
class LLMConfigReq(BaseModel):
    config: dict


@router.post('/agent/llm-config')
def set_llm_config(req: LLMConfigReq):
    current().ua.configure_llm(req.config)
    return {'ok': True}


# ------------------------------------------------------------------
# API keys
# ------------------------------------------------------------------
class ApiKeyReq(BaseModel):
    provider: str


class ApiKeyWithValueReq(BaseModel):
    provider: str
    key: str


@router.post('/agent/api-key')
def set_api_key(req: ApiKeyWithValueReq):
    env_var = _PROVIDER_KEY_MAP.get(req.provider.lower())
    if not env_var:
        return {'ok': False, 'error': f'Unknown provider: {req.provider}'}
    os.environ[env_var] = req.key
    env_file = _env_file()
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = env_file.read_text().splitlines() if env_file.exists() else []
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f'{env_var}='):
            lines[i] = f'{env_var}={req.key}'
            updated = True
            break
    if not updated:
        lines.append(f'{env_var}={req.key}')
    env_file.write_text('\n'.join(lines) + '\n')
    return {'ok': True}


@router.get('/agent/api-keys')
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
@router.websocket('/ws/agent')
async def agent_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        prompt = data.get('prompt', '')
        lang   = data.get('lang', 'en')
        direct = data.get('direct', False)

        ua = current().ua
        gen = ua.run_direct(plan=prompt) if direct else ua.run(prompt=prompt, lang=lang)
        async for event in gen:
            # Walk the whole event through _serialize_result so numpy scalars
            # and NaN/Inf are sanitized at a single point. `allow_nan=False`
            # crashes-fast on anything that still slipped through.
            safe_event = _serialize_result(event)
            await websocket.send_text(
                json.dumps(safe_event, ensure_ascii=False, allow_nan=False, default=_serialize_result)
            )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({'event': 'error', 'msg': str(e)}))
        except Exception:
            pass
