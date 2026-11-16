"""Per-robot shortcut-button API.

Frontend ButtonPanel stores its list here so the buttons travel with the
robot (across browsers, machines, operators) instead of living in
``localStorage`` keyed by-browser.

Endpoints:
    GET    /buttons              list all buttons in display order
    POST   /buttons              add one (body: {label, plan}) → returns the new entry
    PUT    /buttons/{id}         update label/plan
    DELETE /buttons/{id}         remove
    POST   /buttons/reorder      body: {ids: [...]} — set display order
    POST   /buttons/bulk         body: {items: [{label, plan}, ...]} — append batch
                                 (used by the UI to migrate legacy localStorage)

Groups (2026-11-13): a button has an optional ``group`` (a group id, '' = Other).
    GET    /buttons/groups               [{id, name}] in display order
    POST   /buttons/groups               body: {name}
    PUT    /buttons/groups/{id}          body: {name} — rename
    DELETE /buttons/groups/{id}          its buttons move to Other
    POST   /buttons/groups/reorder       body: {ids: [...]}
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..state import current

router = APIRouter()


class BtnIn(BaseModel):
    label: str
    plan: str = ''
    group: str = ''


class BtnUpdate(BaseModel):
    label: Optional[str] = None
    plan: Optional[str] = None
    group: Optional[str] = None      # None = unchanged, '' = Other


class GroupIn(BaseModel):
    name: str


class ReorderIn(BaseModel):
    ids: list[str]


class BulkIn(BaseModel):
    items: list[BtnIn]


@router.get('/buttons')
def list_buttons():
    return current().bm.all()


@router.get('/buttons/groups')
def list_groups():
    return current().bm.groups()


@router.post('/buttons/groups')
def add_group(body: GroupIn):
    try:
        return current().bm.add_group(body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/buttons/groups/reorder')
def reorder_groups(body: ReorderIn):
    if not current().bm.reorder_groups(body.ids):
        raise HTTPException(status_code=400, detail='ids must match the existing groups exactly')
    return {'ok': True}


@router.put('/buttons/groups/{gid}')
def rename_group(gid: str, body: GroupIn):
    try:
        result = current().bm.rename_group(gid, body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail=f'Group "{gid}" not found')
    return result


@router.delete('/buttons/groups/{gid}')
def delete_group(gid: str):
    if not current().bm.remove_group(gid):
        raise HTTPException(status_code=404, detail=f'Group "{gid}" not found')
    return {'ok': True}


@router.post('/buttons')
def add_button(body: BtnIn):
    try:
        return current().bm.add(label=body.label, plan=body.plan, group=body.group)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put('/buttons/{btn_id}')
def update_button(btn_id: str, body: BtnUpdate):
    try:
        result = current().bm.update(btn_id, label=body.label, plan=body.plan, group=body.group)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail=f'Button "{btn_id}" not found')
    return result


@router.delete('/buttons/{btn_id}')
def delete_button(btn_id: str):
    ok = current().bm.remove(btn_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f'Button "{btn_id}" not found')
    return {'ok': True}


@router.post('/buttons/reorder')
def reorder_buttons(body: ReorderIn):
    ok = current().bm.reorder(body.ids)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail='ids must match the existing set exactly (no add/remove via reorder)',
        )
    return {'ok': True}


@router.post('/buttons/bulk')
def bulk_add(body: BulkIn):
    """Append a batch of buttons. Items missing label are skipped silently."""
    added = current().bm.bulk_import([item.model_dump() for item in body.items])
    return {'added': added, 'count': len(added)}
