from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Optional

from ..state import current

router = APIRouter()


class SkillIn(BaseModel):
    name: str
    type: Literal['internal', 'external']
    description: str = ''
    module_path: str = ''
    func_name: str = ''
    url: str = ''
    timeout: float = 30.0
    method: str = 'POST'
    headers: dict = Field(default_factory=dict)


class SkillUpdate(BaseModel):
    description: Optional[str] = None
    module_path: Optional[str] = None
    func_name: Optional[str] = None
    url: Optional[str] = None
    timeout: Optional[float] = None
    method: Optional[str] = None
    headers: Optional[dict] = None


@router.get('/skills')
def list_skills():
    return current().sr.all()


@router.post('/skills/reload')
def reload_skills():
    import importlib
    state = current()
    sr = state.sr
    try:
        skills_mod = importlib.import_module(f'{state.robot_pkg}.configs.skills_config')
        # invalidate module cache so file changes on disk are picked up
        importlib.reload(skills_mod)
        SKILL_CONFIGS = skills_mod.SKILL_CONFIGS
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Could not load skills_config: {e}')

    sr._skills.clear()
    sr.load_from_skill_configs(SKILL_CONFIGS)
    sr._save()
    return {'ok': True, 'count': len(sr.all())}


@router.get('/skills/status')
def skills_status():
    import importlib
    result = {}
    for s in current().sr.all():
        if s.get('type') != 'internal':
            continue
        name = s['name']
        module_path = s.get('module_path', '')
        func_name = s.get('func_name') or name
        if not module_path:
            result[name] = {'ok': False, 'error': 'no module_path'}
            continue
        try:
            mod = importlib.import_module(module_path)
            if not hasattr(mod, func_name):
                result[name] = {'ok': False, 'error': f'no function "{func_name}" in module'}
            else:
                result[name] = {'ok': True, 'error': ''}
        except Exception as e:
            result[name] = {'ok': False, 'error': str(e)}
    return result


@router.post('/skills')
def add_skill(skill: SkillIn):
    sr = current().sr
    if skill.type == 'internal':
        sr.register_internal(
            name=skill.name,
            module_path=skill.module_path,
            func_name=skill.func_name or skill.name,
            description=skill.description,
        )
    else:
        sr.register_external(
            name=skill.name,
            url=skill.url,
            description=skill.description,
            timeout=skill.timeout,
            method=skill.method,
            headers=skill.headers,
        )
    sr._save()
    return {'ok': True}


@router.put('/skills/{name}')
def update_skill(name: str, body: SkillUpdate):
    ok = current().sr.update(name, **body.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(status_code=404, detail=f'Skill "{name}" not found')
    return {'ok': True}


@router.delete('/skills/{name}')
def delete_skill(name: str):
    current().sr.remove(name)
    return {'ok': True}


@router.post('/skill/{name}')
def execute_skill(name: str, params: dict = {}):
    state = current()
    node = state.dm._ros_node
    return state.sr.execute(name, params, node=node)


@router.post('/agent/{agent_name}/send')
def send_to_agent(agent_name: str, params: dict = {}):
    node = current().dm._ros_node
    if node is None:
        return {'isdone': False, 'msg': 'No ROS node available'}
    agent = node.agents.get(agent_name)
    if agent is None:
        return {'isdone': False, 'msg': f'No skill or device agent "{agent_name}"'}
    try:
        ret = agent.send(params if params else {})
        if isinstance(ret, dict):
            return ret
        return {'isdone': True, 'msg': str(ret)}
    except Exception as e:
        return {'isdone': False, 'msg': str(e)}
