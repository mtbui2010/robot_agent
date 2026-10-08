from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Optional

from ..state import current

router = APIRouter()


class SkillIn(BaseModel):
    name: str
    type: Literal['internal', 'external', 'plan']
    description: str = ''
    module_path: str = ''
    func_name: str = ''
    url: str = ''
    timeout: float = 30.0
    method: str = 'POST'
    headers: dict = Field(default_factory=dict)
    plan: str = ''
    aliases: list[str] = Field(default_factory=list)


class SkillUpdate(BaseModel):
    name: Optional[str] = None          # rename (plan skills only)
    description: Optional[str] = None
    module_path: Optional[str] = None
    func_name: Optional[str] = None
    url: Optional[str] = None
    timeout: Optional[float] = None
    method: Optional[str] = None
    headers: Optional[dict] = None
    plan: Optional[str] = None
    aliases: Optional[list[str]] = None


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

    # Plan skills are data the operator wrote, not code: keep them — and the
    # aliases given to code skills, which skills_config does not carry.
    plans = sr.plan_skills()
    aliases = {s.name: list(s.aliases) for s in sr._skills.values() if s.aliases}
    sr._skills.clear()
    sr.load_from_skill_configs(SKILL_CONFIGS)
    for p in plans:
        sr._skills.setdefault(p.name, p)
    for name, al in aliases.items():
        if name in sr._skills:
            sr._skills[name].aliases = al
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
    error = sr.check_aliases(skill.name, skill.aliases)
    if not error and sr.resolve(skill.name) not in (None, skill.name):
        error = f'"{skill.name}" is an alias of skill "{sr.resolve(skill.name)}"'
    if error:
        raise HTTPException(status_code=400, detail=error)
    if skill.type == 'plan':
        from ..core.plan_skill import validate_plan
        error = validate_plan(skill.name, skill.plan, sr)
        if error:
            raise HTTPException(status_code=400, detail=error)
        sr.register_plan(name=skill.name, plan=skill.plan, description=skill.description)
    elif skill.type == 'internal':
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
    sr._skills[skill.name].aliases = [a.strip() for a in skill.aliases]
    sr._save()
    return {'ok': True}


@router.put('/skills/{name}')
def update_skill(name: str, body: SkillUpdate):
    sr = current().sr
    existing = sr._skills.get(name)
    new_name = (body.name or '').strip()
    if new_name and new_name != name:
        # Rename a plan skill; plan skills that call it are updated to the new
        # name. Shortcut buttons and guides are free text and keep the old one.
        from ..core.plan_skill import plan_rename
        error, updates = plan_rename(sr, name, new_name)
        if error:
            raise HTTPException(status_code=400, detail=error)
        if body.plan is not None:
            from ..core.plan_skill import validate_plan
            error = validate_plan(name, body.plan, sr)
            if error:
                raise HTTPException(status_code=400, detail=error)
        sr.rename(name, new_name)
        for other, plan in updates.items():
            sr._skills[other].plan = plan
        name, existing = new_name, sr._skills[new_name]
    body.name = None
    if body.aliases is not None:
        error = sr.check_aliases(name, body.aliases)
        if error:
            raise HTTPException(status_code=400, detail=error)
        body.aliases = [a.strip() for a in body.aliases]
    if existing is not None and existing.type == 'plan' and body.plan is not None:
        from ..core.plan_skill import validate_plan
        error = validate_plan(name, body.plan, sr)
        if error:
            raise HTTPException(status_code=400, detail=error)
    ok = sr.update(name, **body.model_dump(exclude_none=True))
    if not ok:
        raise HTTPException(status_code=404, detail=f'Skill "{name}" not found')
    sr._save()
    return {'ok': True, 'name': name}


@router.delete('/skills/{name}')
def delete_skill(name: str):
    sr = current().sr
    from ..core.plan_skill import called_skills
    users = [p.name for p in sr.plan_skills()
             if p.name != name and any(sr.resolve(c) == name for c in called_skills(p.plan))]
    if users:
        raise HTTPException(status_code=400,
                            detail=f'"{name}" is used by plan skill {", ".join(users)}')
    sr.remove(name)
    return {'ok': True}


@router.post('/skill/{name}')
def execute_skill(name: str, params: dict = {}):
    from ..core.run_control import begin_run
    state = current()
    node = state.dm._ros_node
    begin_run(node)          # a cancel must not leak into the next command
    return state.sr.execute(name, params, node=node)


def _ros_to_plain(obj):
    """ROS messages (anywhere in a dict / list) -> plain dicts, for JSON."""
    if hasattr(obj, 'get_fields_and_field_types'):
        from rosidl_runtime_py.convert import message_to_ordereddict
        return dict(message_to_ordereddict(obj))
    if isinstance(obj, dict):
        return {k: _ros_to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ros_to_plain(v) for v in obj]
    return obj


@router.get('/agent/{agent_name}/get')
def get_from_agent(agent_name: str):
    """Latest value of a device agent (e.g. ``mobile_pose``), read-only.

    Unlike ``POST /skill/<name>`` and ``POST /agent/<name>/send`` this does NOT call
    ``begin_run``: a UI polling a pose must never clear a pending cancel.
    """
    from ..core.unified_agent import _serialize_result
    node = current().dm._ros_node
    if node is None:
        raise HTTPException(status_code=503, detail='No ROS node available')
    agent = node.agents.get(agent_name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f'No device agent "{agent_name}"')
    try:
        value = agent.get()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {'value': _serialize_result(_ros_to_plain(value))}


@router.post('/agent/{agent_name}/send')
def send_to_agent(agent_name: str, params: dict = {}):
    from ..core.run_control import begin_run
    node = current().dm._ros_node
    if node is None:
        return {'isdone': False, 'msg': 'No ROS node available'}
    begin_run(node)          # a cancel must not leak into the next command
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
