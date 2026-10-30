"""Versioned planner-guide management.

CRUD + activate over the `GuideManager` store (``common_dir/guides.json``). The
active version is what the planner uses (see ``core/guide_manager.resolve_guide``);
when the store is empty the planner falls back to the robot's guide modules.

Only the direct path and the llm_direct backend plan with the guide; SAGE builds its
own prompts from the manifest. ``GET /guides?planner=<choice>`` says whether that
planner uses it (``active_planner_uses_guide``), ``POST /guides/preview`` renders a
guide's placeholders for a command (docs/planner_guide.md).
"""

from fastapi import APIRouter, HTTPException, Query
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


class GuidePreviewReq(BaseModel):
    # the text to render (unsaved edits); else the stored version `name`, else the active one
    guide: Optional[str] = None
    name: Optional[str] = None
    command: str = ''
    k: Optional[int] = None
    leave_one_out: Optional[bool] = None


def _usage(planner: Optional[str]) -> dict:
    from ..core.guide_manager import GUIDE_USERS, default_planner, planner_uses_guide
    p = (planner or '').strip() or default_planner()
    return {'used_by': list(GUIDE_USERS), 'active_planner': p,
            'active_planner_uses_guide': planner_uses_guide(p)}


@router.get('/guides')
def list_guides(planner: Optional[str] = Query(None, description="the dashboard's planner choice "
                                               "(grace | sage | direct | a plug-in); default: the server's")):
    from ..core.guide_manager import placeholders_in
    out = current().guides.list()
    for v in out.get('versions') or []:
        v['placeholders'] = placeholders_in(v.get('guide') or '')
    return {**out, **_usage(planner)}


@router.post('/guides/preview')
def preview_guide(req: GuidePreviewReq, planner: Optional[str] = Query(None)):
    from ..core.guide_manager import preview_guide as render
    st = current()
    text = req.guide
    if text is None:
        name = req.name or st.guides.list().get('active')
        v = st.guides.get(name) if name else None
        if v is None:
            raise HTTPException(status_code=404, detail=f'no guide version {name!r}')
        text = v.get('guide') or ''
    try:
        out = render(text, st.robot_pkg, req.command, k=req.k, leave_one_out=req.leave_one_out)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'cannot render the guide: {e}')
    return {**out, **_usage(planner)}


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
