"""Map tab: save the robot's SLAM map as the active site's map files.

    POST /map/snapshot  {connection, inflate_m?, occupied?, timeout?, keep?, speck_cells?, preview?}

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

Tidying (2026-10-22, .101: lidar dots outside the house, and the Slamtec map marks
everything it never saw as free):

- ``keep`` — a polygon ``[[x, y], …]`` in the map frame drawn on the Map tab. Outside it
  is unknown (blocked for clicks) and the images are cropped to it (+ 0.3 m). Saved in
  ``MAP.source.keep`` and applied again by every later snapshot; ``[]`` removes it.
- ``speck_cells`` — obstacle blobs smaller than this many cells with no other obstacle
  within 0.5 m are dropped (lidar noise; chair legs come in groups and stay). Default 4,
  0 = off; saved like ``keep``.
- ``preview`` — return the full, untidied map as a PNG data URL without saving (the
  Map tab draws the keep area on it).

Only the dashboard's picture changes: the robot keeps navigating on its own map.
"""
import math
import shutil
import base64
import threading
import time
from pathlib import Path
from typing import List, Optional

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
    keep: Optional[List[List[float]]] = None    # area to keep (map frame); None = the saved one, [] = all
    speck_cells: Optional[int] = None           # drop lone obstacle blobs below this; None = saved / 4
    preview: bool = False                       # full map as a data URL, nothing saved


SPECK_CELLS = 4
SPECK_GAP_M = 0.5
CROP_MARGIN_M = 0.3


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


def _despeck(occ, max_cells, gap):
    """occ without the blobs of < max_cells cells that have no other obstacle within gap cells."""
    import cv2
    out = occ.copy()
    if max_cells <= 1:
        return out, 0
    n, lab, st, _ = cv2.connectedComponentsWithStats(occ.astype(np.uint8), connectivity=8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap + 1, 2 * gap + 1))
    H, W = occ.shape
    removed = 0
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area >= max_cells:
            continue
        x0, y0, x1, y1 = max(0, x - gap), max(0, y - gap), min(W, x + w + gap), min(H, y + h + gap)
        win = lab[y0:y1, x0:x1]
        mine = win == i
        if (cv2.dilate(mine.astype(np.uint8), k).astype(bool) & (win > 0) & ~mine).any():
            continue
        out[y0:y1, x0:x1][mine] = False
        removed += 1
    return out, removed


def _keep_mask(shape, keep, res, ox, oy):
    """Pixels (row 0 = top) inside the keep polygon given in the map frame."""
    import cv2
    H = shape[0]
    pts = np.array([[(x - ox) / res, H - (y - oy) / res] for x, y in keep], np.float64)
    mask = np.zeros(shape, np.uint8)
    cv2.fillPoly(mask, [np.round(pts * 16).astype(np.int32)], 1, shift=4)
    return mask.astype(bool)


def _images(grid, res, inflate_m, occupied, keep=None, speck_cells=0, origin=(0.0, 0.0)):
    """(display uint8, occupancy uint8, crop (r0, r1, c0, c1) or None, specks removed),
    row 0 = TOP of the map."""
    import cv2
    g = np.flipud(grid).copy()
    crop = None
    if keep:
        inside = _keep_mask(g.shape, keep, res, *origin)
        if not inside.any():
            raise ValueError('the keep area is outside the map')
        g[~inside] = -1
        rows, cols = np.where(inside)
        m = int(round(CROP_MARGIN_M / res))
        crop = (max(0, rows.min() - m), min(g.shape[0], rows.max() + 1 + m),
                max(0, cols.min() - m), min(g.shape[1], cols.max() + 1 + m))
    occ = g >= occupied
    occ2, specks = _despeck(occ, speck_cells, max(1, int(round(SPECK_GAP_M / res))))
    g[occ & ~occ2] = 0
    unknown, occ = g < 0, occ2
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
    if crop:
        r0, r1, c0, c1 = crop
        show, occupancy = show[r0:r1, c0:c1], occupancy[r0:r1, c0:c1]
    return show, occupancy, crop, specks


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
    if req.preview:
        show = _images(grid, res, req.inflate_m, req.occupied)[0]
        h, w = grid.shape
        return {'ok': True, 'image': 'data:image/png;base64,' + base64.b64encode(cv2.imencode('.png', show)[1]).decode(),
                'frame': {'origin': [ox, oy], 'resolution': res, 'width': int(w), 'height': int(h)}}
    saved = (state.cm.get('MAP') or {}).get('source') or {}
    keep = saved.get('keep') if req.keep is None else req.keep
    if keep and (len(keep) < 3 or any(len(p) != 2 for p in keep)):
        raise HTTPException(status_code=400, detail='keep: a polygon of at least 3 [x, y] points')
    speck_cells = int(saved.get('speck_cells', SPECK_CELLS) if req.speck_cells is None else req.speck_cells)
    try:
        show, occupancy, crop, specks = _images(grid, res, req.inflate_m, req.occupied, keep, speck_cells, (ox, oy))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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

        H, W = grid.shape
        h, w = show.shape
        r0, c0 = (crop[0], crop[2]) if crop else (0, 0)
        frame = {'origin': [ox + c0 * res, oy + (H - r0 - h) * res], 'resolution': res, 'width': int(w), 'height': int(h)}
        cfg = state.cm.get('MAP') or {}
        cfg['image'] = {'file': 'map/map.png', 'frame': frame}
        layers = dict(cfg.get('layers') or {})
        layers['occupancy'] = {'file': 'map/occupancy.png', 'frame': frame,
                               'values': {'free': 0, 'inflated': 128, 'occupied': 255}}
        cfg['layers'] = layers
        cfg['source'] = {'connection': req.connection, 'saved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                         'inflate_m': req.inflate_m, 'occupied': req.occupied, 'speck_cells': speck_cells}
        if keep:
            cfg['source']['keep'] = [[round(float(x), 3), round(float(y), 3)] for x, y in keep]
        error = state.cm.update('MAP', cfg)
        if error:
            raise HTTPException(status_code=400, detail=error)

    return {'ok': True, 'frame': frame,
            'size_m': [round(w * res, 2), round(h * res, 2)],
            'cells': {'known': int((occupancy < 255).sum() + (show == 0).sum()), 'occupied': int((show == 0).sum()),
                      'unknown': int(((occupancy == 255) & (show != 0)).sum())},
            'kept': bool(keep), 'specks': specks,
            'missing': [k for k in ('pose', 'goal') if not cfg.get(k)]}
