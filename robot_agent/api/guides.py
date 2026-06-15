"""Versioned planner-guide management.

CRUD + activate over the `GuideManager` store (``common_dir/guides.json``). The
active version is what the planner uses (see ``core/guide_manager.resolve_guide``);
when the store is empty the planner falls back to the robot's guide modules.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..state import current

router = APIRouter()


class GuideUpsertReq(BaseModel):
    name: str
    guide: str = ''
    # None → freeform (llm.chat_guide); a JSON schema dict → structured (llm.chat).
    format: Optional[dict] = None


class GuideBodyReq(BaseModel):
    guide: str = ''
    format: Optional[dict] = None


class GuideRenameReq(BaseModel):
    new: str


@router.get('/guides')
def list_guides():
    return current().guides.list()


@router.get('/guides/{name}')
def get_guide(name: str):
    g = current().guides.get(name)
    if g is None:
        raise HTTPException(status_code=404, detail=f'no guide version {name!r}')
    return g


@router.post('/guides')
def create_guide(req: GuideUpsertReq):
    try:
        return current().guides.upsert(req.name, req.guide, req.format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put('/guides/{name}')
def update_guide(name: str, req: GuideBodyReq):
    try:
        return current().guides.upsert(name, req.guide, req.format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/guides/{name}/rename')
def rename_guide(name: str, req: GuideRenameReq):
    try:
        return current().guides.rename(name, req.new)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete('/guides/{name}')
def delete_guide(name: str):
    current().guides.delete(name)
    return {'ok': True}


@router.post('/guides/{name}/activate')
def activate_guide(name: str):
    try:
        return {'active': current().guides.activate(name)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
