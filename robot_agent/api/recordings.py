"""Look at the raw VLA episodes, clean them (a non-destructive edit list) and prepare an export (see ``core/episodes.py``).

    GET    /recordings                        {dir, episodes: [row], totals}      (404 without a RECORDER config)
    GET    /recordings/{day}/{ep}             one episode: row + curves + camera info
    GET    /recordings/{day}/{ep}/frame?cam=head&t=3.2     image/jpeg: the camera frame nearest to t seconds
    PUT    /recordings/{day}/{ep}/edit        {trim_start_s?, trim_end_s?, label?, exclude?, note?}   saved next to the raw folder, never into it
    DELETE /recordings/{day}/{ep}/edit
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


@router.get('/recordings', response_model=M.RecordingList, **M.OPEN)
def list_recordings():
    return _guard(E.list_episodes, _dir())


@router.post('/recordings/export', response_model=M.RecordingExport, **M.OPEN)
def export(body: M.RecordingExportIn):
    d = _dir()
    out = _guard(E.export_manifest, d, body.name, body.fps, body.action, body.horizon_s, tuple(body.labels))
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
