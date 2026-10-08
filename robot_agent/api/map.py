"""Map tab: save the robot's SLAM map as the active site's map files.

    POST /map/snapshot  {connection, inflate_m?, occupied?, timeout?}

`connection` is a ROS topic connection subscribed to a ``nav_msgs/OccupancyGrid``
(on the real kcare robot: ``/map`` from the Slamtec bridge). Its decode_func may
return the message itself or ``{'grid', 'info'}`` (see ``_grid_of``). The latest
map is written into the site folder

    map/map.png        what the panel shows: free white, unknown grey, occupied black
    map/occupancy.png  0 free / 128 too close for the base (inflate_m) / 255 occupied or unknown

(the previous pair goes to ``map/history/``), and the ``MAP`` skill-config group
gets ``image`` / ``layers.occupancy`` / ``source`` — every other key (pose, goal,
surfaces …) is kept. Frames follow ROS map.yaml: origin = world (x, y) of the
image's bottom-left corner; row 0 of the PNG is the TOP of the map.
"""
import math
import shutil
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..state import current

router = APIRouter()
_lock = threading.Lock()


class SnapshotReq(BaseModel):
    connection: str
    inflate_m: float = 0.3      # base radius: cells closer than this to an obstacle are 'too close'
    occupied: int = 65          # OccupancyGrid probability (0–100) counted as an obstacle
    timeout: float = 5.0        # wait this long for a first message


def _get(o, k, default=None):
    return o.get(k, default) if isinstance(o, dict) else getattr(o, k, default)


def _grid_of(data):
    """(grid int16 HxW, row 0 = map BOTTOM as in the message, resolution, (ox, oy, yaw))."""
    if data is None:
        raise ValueError('no map received yet')
    grid, info = _get(data, 'grid'), _get(data, 'info')
    if grid is None and _get(data, 'data') is not None:          # the raw nav_msgs/OccupancyGrid
        info = _get(data, 'info')
        grid = np.asarray(_get(data, 'data'), dtype=np.int16).reshape(int(_get(info, 'height')), int(_get(info, 'width')))
    if grid is None or info is None:
        raise ValueError("the connection's data has neither 'grid'+'info' nor an OccupancyGrid")
    grid = np.asarray(grid, dtype=np.int16)
    res = float(_get(info, 'resolution'))
    o = _get(info, 'origin')
    pos, ori = _get(o, 'position', o), _get(o, 'orientation')
    ox, oy = float(_get(pos, 'x')), float(_get(pos, 'y'))
    yaw = 0.0
    if ori is not None:
        qz, qw = float(_get(ori, 'z', 0.0)), float(_get(ori, 'w', 1.0))
        yaw = 2 * math.atan2(qz, qw)
    return grid, res, (ox, oy, yaw)


def _images(grid, res, inflate_m, occupied):
    """(display uint8, occupancy uint8), both with row 0 = TOP of the map."""
    import cv2
    g = np.flipud(grid)
    unknown, occ = g < 0, g >= occupied
    show = np.full(g.shape, 205, np.uint8)                     # unknown: grey
    show[~unknown] = (254 - (np.clip(g[~unknown], 0, 100) * 254 // 100)).astype(np.uint8)
    show[occ] = 0
    blocked = occ | unknown
    r = max(0, int(round(inflate_m / res)))
    near = blocked
    if r > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        near = cv2.dilate(occ.astype(np.uint8), k).astype(bool)
    occupancy = np.zeros(g.shape, np.uint8)
    occupancy[near] = 128
    occupancy[blocked] = 255
    return show, occupancy


@router.post('/map/snapshot')
def snapshot(req: SnapshotReq):
    import cv2
    state = current()
    node = state.dm._ros_node
    agent = node.agents.get(req.connection) if node is not None else None
    if agent is None:
        raise HTTPException(status_code=404, detail=f'no connection "{req.connection}" (add it in the Connections panel)')
    end = time.monotonic() + max(0.0, req.timeout)
    while getattr(agent, 'rev_data', None) is None and time.monotonic() < end:
        time.sleep(0.1)
    try:
        grid, res, (ox, oy, yaw) = _grid_of(getattr(agent, 'rev_data', None))
    except Exception as e:
        raise HTTPException(status_code=409, detail=f'{req.connection}: {e}')
    if abs(yaw) > 1e-3:
        raise HTTPException(status_code=409, detail=f'map origin is rotated ({math.degrees(yaw):.1f}°): not supported')
    show, occupancy = _images(grid, res, req.inflate_m, req.occupied)

    with _lock:
        site = Path(state.location_dir)
        mdir = site / 'map'
        mdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        for name in ('map.png', 'occupancy.png'):               # keep the previous pair
            if (mdir / name).exists():
                (mdir / 'history').mkdir(exist_ok=True)
                shutil.copy2(mdir / name, mdir / 'history' / f'{stamp}_{name}')
        cv2.imwrite(str(mdir / 'map.png'), show)
        cv2.imwrite(str(mdir / 'occupancy.png'), occupancy)

        h, w = grid.shape
        frame = {'origin': [ox, oy], 'resolution': res, 'width': int(w), 'height': int(h)}
        cfg = state.cm.get('MAP') or {}
        cfg['image'] = {'file': 'map/map.png', 'frame': frame}
        layers = dict(cfg.get('layers') or {})
        layers['occupancy'] = {'file': 'map/occupancy.png', 'frame': frame,
                               'values': {'free': 0, 'inflated': 128, 'occupied': 255}}
        cfg['layers'] = layers
        cfg['source'] = {'connection': req.connection, 'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                         'inflate_m': req.inflate_m, 'occupied': req.occupied}
        error = state.cm.update('MAP', cfg)
        if error:
            raise HTTPException(status_code=400, detail=error)

    known = (grid >= 0).sum()
    return {'ok': True, 'frame': frame,
            'size_m': [round(w * res, 2), round(h * res, 2)],
            'cells': {'known': int(known), 'occupied': int((grid >= req.occupied).sum()), 'unknown': int((grid < 0).sum())},
            'missing': [k for k in ('pose', 'goal') if not cfg.get(k)]}
