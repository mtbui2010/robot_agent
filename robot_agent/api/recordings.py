"""Look at the raw VLA episodes, clean them (a non-destructive edit list) and prepare an export (see ``core/episodes.py``).

    GET    /recordings                        {dir, episodes: [row], totals}      (404 without a RECORDER config)
    GET    /recordings/{day}/{ep}             one episode: row + curves + camera info
    GET    /recordings/{day}/{ep}/frame?cam=head&t=3.2     image/jpeg: the camera frame nearest to t seconds
    PUT    /recordings/{day}/{ep}/edit        {trim_start_s?, trim_end_s?, label?, exclude?, note?}   saved next to the raw folder, never into it
    DELETE /recordings/{day}/{ep}/edit
    DELETE /recordings/{day}/{ep}             move the episode (and its edit list) to <data>/trash/: nothing is deleted for good
    POST   /recordings/auto-clean             {ids?, overwrite?}   save the suggested trim as the edit of each episode (those without an edit; all with overwrite)
    POST   /recordings/export                 {name, fps, action, horizon_s, labels}   writes the manifest the export tool reads; answers with the command to run
Reading changes nothing. The raw files are never modified.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..core import episodes as E
from ..core.recorder import bridge
from . import models as M

router = APIRouter()


def _dir() -> str:
    cfg = bridge().config()
    if not cfg or not cfg.get('dir'):
        raise HTTPException(status_code=404, detail='this robot has no RECORDER config')
    return str(cfg['dir'])


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except E.EpisodeError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _crop_spec():
    spec = (bridge().config() or {}).get('crop')
    return spec if isinstance(spec, dict) and spec else None


@router.get('/recordings', response_model=M.RecordingList, **M.OPEN)
def list_recordings():
    out = _guard(E.list_episodes, _dir())
    spec = _crop_spec()
    if spec:
        out['crop'] = spec
        out['modes'] = sorted(set(out['modes']) | {m for sp in spec.values() for m in (sp.get('anchor') or {})})
    return out


@router.post('/recordings/auto-clean', response_model=M.RecordingAutoClean, **M.OPEN)
def auto_clean(body: M.RecordingAutoCleanIn):
    return _guard(E.auto_clean, _dir(), body.ids, body.overwrite)


@router.post('/recordings/export', response_model=M.RecordingExport, **M.OPEN)
def export(body: M.RecordingExportIn):
    d = _dir()
    if body.crop and _crop_spec() is None:
        raise HTTPException(status_code=400, detail='this robot defines no crop (RECORDER config "crop")')
    out = _guard(E.export_manifest, d, body.name, body.fps, body.action, body.horizon_s, tuple(body.labels), _crop_spec() if body.crop else None, body.auto_trim)
    out['command'] = (f'python robot_teleop/tools/export_lerobot.py --data {out["data_dir"]} --manifest {out["manifest"]}'
                      '   # run it where LeRobot is installed (the training server)')
    return out


@router.get('/recordings/{day}/{ep}', response_model=M.RecordingDetail, **M.OPEN)
def get_recording(day: str, ep: str):
    return _guard(E.detail, _dir(), day, ep)


@router.get('/recordings/{day}/{ep}/frame', **M.OPEN)
def get_frame(day: str, ep: str, cam: str = 'head', t: float = 0.0):
    return FileResponse(_guard(E.frame_path, _dir(), day, ep, cam, t), media_type='image/jpeg', headers={'Cache-Control': 'max-age=3600'})


@router.put('/recordings/{day}/{ep}/edit', response_model=M.RecordingEdit, **M.OPEN)
def put_edit(day: str, ep: str, body: M.RecordingEditIn):
    return _guard(E.save_edit, _dir(), day, ep, body.model_dump(exclude_unset=True))


@router.delete('/recordings/{day}/{ep}/edit', response_model=M.Ok, **M.OPEN)
def delete_edit(day: str, ep: str):
    _guard(E.delete_edit, _dir(), day, ep)
    return {'ok': True}


@router.delete('/recordings/{day}/{ep}', response_model=M.RecordingTrashed, **M.OPEN)
def trash_recording(day: str, ep: str):
    return _guard(E.delete_episode, _dir(), day, ep)
