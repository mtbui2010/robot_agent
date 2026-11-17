"""Read the raw VLA episodes the recorder wrote (``robot_teleop`` format ``robot_teleop.raw/1``): list them, summarise one, serve a frame, keep an edit list.

Layout under the data folder ``D`` (the ``RECORDER`` config's ``dir`` is ``D/raw``):  ``raw/<YYYY-MM-DD>/ep_NNNN/{meta.json, streams.npz, DONE, head/, wrist/}`` is
READ ONLY here; what the operator decides is written next to it, never into it:  ``edits/<YYYY-MM-DD>/ep_NNNN.json``
    {version: 1, trim_start_s, trim_end_s, label?, exclude?, note?}      (times in seconds from the start of the episode)
The export tool (``robot_teleop/tools/export_lerobot.py``) reads the same files. Only episodes with a ``DONE`` file are listed (the rest are being copied).
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

import numpy as np

DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
EP = re.compile(r'^ep_\d{4}$')
MOVING_RAD_S = 0.02          # summed joint speed above this = the arm is moving
PAD_S = 0.5                  # margin kept around the movement in the suggested trim
LABELS = ('success', 'failure')
_CACHE: dict = {}


class EpisodeError(ValueError):
    """A request the operator can read (bad id, nothing there, bad edit)."""


def _root(raw_dir) -> Path:
    if not raw_dir:
        raise EpisodeError('no data folder is configured')
    return Path(raw_dir)


def episode_dir(raw_dir, day: str, ep: str) -> Path:
    if not DAY.match(day or '') or not EP.match(ep or ''):
        raise EpisodeError('bad episode id')
    p = _root(raw_dir) / day / ep
    if not (p / 'DONE').is_file():
        raise EpisodeError(f'{day}/{ep} is not there (or still being copied)')
    return p


def edits_path(raw_dir, day: str, ep: str) -> Path:
    return _root(raw_dir).parent / 'edits' / day / f'{ep}.json'


# ── numbers from one episode ──────────────────────────────────────────────────────────
def _streams(p: Path) -> dict:
    with np.load(p / 'streams.npz') as z:
        return {k: z[k] for k in z.files}


def _rate(t) -> float:
    return round(float((len(t) - 1) / (t[-1] - t[0])), 1) if len(t) > 1 and t[-1] > t[0] else 0.0


def summarise(meta: dict, z: dict) -> dict:
    """Numbers that tell a good episode from a bad one, and a suggested trim (the idle start and end)."""
    t0, dur = float(meta['started_at']), float(meta.get('duration_s') or 0)
    out: dict = {'duration_s': round(dur, 2), 'rates_hz': {}, 'max_gap_s': {}, 'warnings': []}
    for k in ('state', 'action_servo', 'action_ref', 'gripper_cmd', *(meta.get('cameras') or {})):
        t = z.get(f'{k}_t')
        if t is not None and len(t) > 1:
            out['rates_hz'][k] = _rate(t)
            out['max_gap_s'][k] = round(float(np.diff(t).max()), 2)
    st, tt = z.get('state'), z.get('state_t')
    if st is None or len(st) < 3:
        out['warnings'].append('no joint state recorded')
        return out
    tt = tt - t0
    dt = np.maximum(np.diff(tt), 1e-3)
    speed = np.abs(np.diff(st[:, :7], axis=0)).sum(1) / dt
    speed = np.convolve(speed, np.ones(10) / 10, mode='same')          # ~0.2 s at 50 Hz: a twitch is not movement
    moving = speed > MOVING_RAD_S
    out.update(max_speed_rad_s=round(float(speed.max()), 3), path_rad=round(float(np.abs(np.diff(st[:, :7], axis=0)).sum()), 2),
               moving_fraction=round(float(moving.mean()), 2))
    t1 = tt[1:]
    init = _init_moves(meta.get('events') or [], t1, moving, dur)             # Go to pose moves (teleop off): KEPT as the "go to the start pose" part of the episode
    out['init_moves'] = init
    if moving.any():
        first, last = float(t1[moving][0]), float(t1[moving][-1])
        out.update(idle_head_s=round(first, 2), idle_tail_s=round(max(0.0, dur - last), 2),
                   trim_suggest=[round(max(0.0, first - PAD_S), 2), round(min(dur, last + PAD_S), 2)])
        out['inactive_inside_s'] = round(_inactive_between(meta.get('events') or [], first, last), 2)
        out['notes'] = [f"the arm went to a pose{' (' + m['name'] + ')' if m.get('name') else ''} from {m['start']:.1f} to {m['end']:.1f} s with teleop off: kept as the "
                        'init part of the episode' for m in init]
        quiet = sum(max(0.0, min(m['end'], last) - max(m['start'], first)) for m in init)
        if out['inactive_inside_s'] - quiet > 0.5:
            out['warnings'].append(f"teleop was off for {round(out['inactive_inside_s'] - quiet, 2)} s in the middle of the movement")
    else:
        out['warnings'].append('the arm never moved')
    g = z.get('gripper_cmd')
    out['gripper_changes'] = int((np.abs(np.diff(g[:, 0])) > 0.01).sum()) if g is not None and len(g) > 1 else 0
    if out['gripper_changes'] == 0:
        out['warnings'].append('the gripper command never changed')
    for cam in (meta.get('cameras') or {}):
        r = out['rates_hz'].get(cam, 0)
        if r and r < 15:
            out['warnings'].append(f'{cam} camera only {r} Hz')
        if out['max_gap_s'].get(cam, 0) > 0.5:
            out['warnings'].append(f"{cam} camera stopped for {out['max_gap_s'][cam]} s")
    ta = z.get('action_servo_t')
    if ta is not None and len(ta) > 1:
        for i in np.where(np.diff(ta) > 1.0)[0]:
            g0, g1 = float(ta[i] - t0), float(ta[i + 1] - t0)
            if not any(g0 < m['end'] + 0.5 and g1 > m['start'] - 0.5 for m in init):          # a gap during a Go to pose is explained by it
                out['warnings'].append(f'the servo command stopped for {g1 - g0:.1f} s (at {g0:.1f} s)')
    return out


def _states(events: list, dur: float) -> list:
    """[(a, b, teleop state)] covering [0, dur] from the recorded state changes (before the first change: that first state)."""
    ev = sorted((e for e in events if e.get('kind') == 'teleop'), key=lambda e: e['t'])
    if not ev:
        return [(0.0, dur, 'active')]
    segs, t, cur = [], 0.0, ev[0].get('state')
    for e in ev:
        if e['t'] > t:
            segs.append((t, min(e['t'], dur), cur))
            t = e['t']
        cur = e.get('state')
    if t < dur:
        segs.append((t, dur, cur))
    return segs


def _init_moves(events: list, t1, moving, dur: float) -> list:
    """The moves the arm made WITHOUT the operator: [{start, end, name}]. A ``goto`` event pair (the dashboard's Go to pose, with the pose name) is one; any other stretch where the
    teleop function was not active and the arm moved for more than 0.5 s (a move made some other way) is one with name None."""
    out, open_ = [], None
    for e in sorted((e for e in events if e.get('kind') == 'goto'), key=lambda e: e['t']):
        if e.get('phase') == 'start':
            open_ = e
        elif e.get('phase') == 'end' and open_ is not None:
            out.append({'start': round(open_['t'], 2), 'end': round(e['t'], 2), 'name': open_.get('name') or None})
            open_ = None
    if open_ is not None:
        out.append({'start': round(open_['t'], 2), 'end': round(dur, 2), 'name': open_.get('name') or None})
    merged: list = []
    for a, b, st in _states(events, dur):
        if st == 'active':
            continue
        if merged and abs(merged[-1][1] - a) < 1e-6:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    for a, b in merged:
        if (moving & (t1 >= a) & (t1 <= b)).sum() * 0.02 > 0.5 and not any(m['start'] < b and m['end'] > a for m in out):
            out.append({'start': round(a, 2), 'end': round(b, 2), 'name': None})
    return sorted(out, key=lambda m: m['start'])


def _inactive_between(events: list, a: float, b: float) -> float:
    """Seconds in [a, b] during which the teleop function was not `active` (from the recorded state changes)."""
    ev = sorted((e for e in events if e.get('kind') == 'teleop'), key=lambda e: e['t'])
    if not ev:
        return 0.0
    total, cur, t = 0.0, ev[0].get('state'), a
    for e in ev:
        if e['t'] > a:
            if e['t'] >= b:
                break
            if cur != 'active':
                total += e['t'] - t
            t = e['t']
        cur = e.get('state')
    if cur != 'active':
        total += b - t
    return total


def _load(p: Path) -> tuple:
    key = str(p)
    mt = (p / 'meta.json').stat().st_mtime
    hit = _CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1], hit[2]
    meta = json.loads((p / 'meta.json').read_text())
    z = _streams(p)
    summ = summarise(meta, z)
    _CACHE[key] = (mt, meta, summ)
    return meta, summ


def load_edit(raw_dir, day: str, ep: str) -> dict | None:
    f = edits_path(raw_dir, day, ep)
    try:
        return json.loads(f.read_text()) if f.is_file() else None
    except (OSError, ValueError):
        return None


def row(raw_dir, day: str, ep: str) -> dict:
    p = episode_dir(raw_dir, day, ep)
    meta, summ = _load(p)
    edit = load_edit(raw_dir, day, ep)
    return {'id': f'{day}/{ep}', 'day': day, 'episode': ep, 'prompt': meta.get('prompt', ''), 'label': meta.get('label'), 'kind_fields': meta.get('fields'),
            'robot_mode': (edit or {}).get('robot_mode') or meta.get('robot_mode'),           # the operator's setting wins over the one recorded
            'recorded_mode': meta.get('robot_mode'),
            'started_at': meta.get('started_at'), 'counts': meta.get('counts'), 'summary': summ, 'edit': edit}


def list_episodes(raw_dir) -> dict:
    """{dir, episodes: [row], totals}: newest first; an episode that cannot be read is listed with an `error`."""
    root = _root(raw_dir)
    rows = []
    if root.is_dir():
        for d in sorted((x for x in root.iterdir() if x.is_dir() and DAY.match(x.name)), reverse=True):
            for e in sorted((x for x in d.iterdir() if x.is_dir() and EP.match(x.name) and (x / 'DONE').is_file()), reverse=True):
                try:
                    rows.append(row(raw_dir, d.name, e.name))
                except Exception as ex:                                   # a damaged episode must not hide the others
                    rows.append({'id': f'{d.name}/{e.name}', 'day': d.name, 'episode': e.name, 'error': str(ex)})
    ok = [r for r in rows if 'error' not in r]
    eff = lambda r: (r.get('edit') or {}).get('label') or r.get('label')
    secs = sum(r['summary']['duration_s'] for r in ok)
    modes = sorted({r['robot_mode'] for r in ok if r.get('robot_mode')})
    return {'dir': str(root), 'episodes': rows, 'modes': modes,
            'totals': {'episodes': len(ok), 'hours': round(secs / 3600, 2), 'success': sum(eff(r) == 'success' for r in ok),
                       'failure': sum(eff(r) == 'failure' for r in ok), 'excluded': sum(bool((r.get('edit') or {}).get('exclude')) for r in ok)}}


def detail(raw_dir, day: str, ep: str, points: int = 400) -> dict:
    """One episode: the row plus curves (down-sampled to ``points``) for the chart and the cameras' frame times."""
    p = episode_dir(raw_dir, day, ep)
    r = row(raw_dir, day, ep)
    meta = json.loads((p / 'meta.json').read_text())
    z = _streams(p)
    t0 = float(meta['started_at'])
    tt = z['state_t'] - t0
    idx = np.unique(np.linspace(0, len(tt) - 1, min(points, len(tt))).astype(int))
    grid = tt[idx]
    series = {'t': np.round(grid, 3).tolist(), 'joints': np.round(z['state'][idx, :7].T, 4).tolist(), 'gripper': np.round(z['state'][idx, 7], 4).tolist()}
    if 'gripper_cmd' in z and len(z['gripper_cmd']):
        gi = np.clip(np.searchsorted(z['gripper_cmd_t'] - t0, grid, side='right') - 1, 0, len(z['gripper_cmd']) - 1)
        series['gripper_cmd'] = np.round(z['gripper_cmd'][gi, 0], 4).tolist()
    cams = {c: {'frames': int(len(z[f'{c}_t'])), 'first_t': round(float(z[f'{c}_t'][0] - t0), 3)} for c in (meta.get('cameras') or {}) if f'{c}_t' in z}
    return {**r, 'meta': {k: meta.get(k) for k in ('prompt', 'label', 'fields', 'robot', 'joint_names', 'cameras', 'events', 'started_at', 'ended_at')},
            'series': series, 'cameras': cams}


def frame_path(raw_dir, day: str, ep: str, cam: str, t: float) -> Path:
    """The camera frame nearest to ``t`` seconds after the start of the episode."""
    p = episode_dir(raw_dir, day, ep)
    meta = json.loads((p / 'meta.json').read_text())
    if cam not in (meta.get('cameras') or {}):
        raise EpisodeError(f'no camera "{cam}"')
    ts = _streams(p)[f'{cam}_t'] - float(meta['started_at'])
    i = int(np.abs(ts - t).argmin())
    f = p / cam / f'{i:06d}.jpg'
    if not f.is_file():
        raise EpisodeError('frame missing')
    return f


# ── the edit list ───────────────────────────────────────────────────────────────────
def save_edit(raw_dir, day: str, ep: str, edit: dict) -> dict:
    p = episode_dir(raw_dir, day, ep)
    meta, summ = _load(p)
    dur = float(summ['duration_s'])
    out: dict = {'version': 1, 'saved_at': round(time.time(), 1)}
    a, b = edit.get('trim_start_s'), edit.get('trim_end_s')
    if a is not None or b is not None:
        a = 0.0 if a is None else float(a)
        b = dur if b is None else float(b)
        if not (0 <= a < b <= dur + 0.05):
            raise EpisodeError(f'trim must satisfy 0 <= start < end <= {dur} s')
        out.update(trim_start_s=round(a, 3), trim_end_s=round(min(b, dur), 3))
    if edit.get('label') is not None:
        if edit['label'] not in LABELS:
            raise EpisodeError(f'label: one of {LABELS}')
        out['label'] = edit['label']
    if edit.get('robot_mode'):
        if not re.match(r'^[a-z][a-z_\-]{0,19}$', str(edit['robot_mode'])):
            raise EpisodeError('robot_mode: a short lower-case name (left, right, front ...)')
        out['robot_mode'] = str(edit['robot_mode'])
    if edit.get('exclude'):
        out['exclude'] = True
    if edit.get('note'):
        out['note'] = str(edit['note'])[:300]
    f = edits_path(raw_dir, day, ep)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix('.tmp')
    tmp.write_text(json.dumps(out, indent=1))
    tmp.replace(f)
    return out


def delete_edit(raw_dir, day: str, ep: str) -> None:
    episode_dir(raw_dir, day, ep)
    edits_path(raw_dir, day, ep).unlink(missing_ok=True)


# ── the export manifest: what the export tool (on a machine with LeRobot) turns into a dataset ──────────────────────
ACTIONS = ('future_state', 'servo', 'ref')


def export_manifest(raw_dir, name: str, fps: int = 30, action: str = 'future_state', horizon_s: float = 0.3, labels=('success',), crop: dict = None, auto_trim: bool = True) -> dict:
    """Write ``<data>/exports/<name>.json``: the episodes to include (not excluded, with an accepted label), each with its prompt and trim."""
    if not re.match(r'^[A-Za-z0-9_\-]{1,60}$', name or ''):
        raise EpisodeError('name: letters, digits, _ and - only')
    if not 1 <= int(fps) <= 120:
        raise EpisodeError('fps must be 1..120')
    if action not in ACTIONS:
        raise EpisodeError(f'action: one of {ACTIONS}')
    if not 0 <= float(horizon_s) <= 3:
        raise EpisodeError('horizon_s must be 0..3')
    eps, secs = [], 0.0
    for r in list_episodes(raw_dir)['episodes']:
        if 'error' in r:
            continue
        e = r.get('edit') or {}
        label = e.get('label') or r.get('label')
        if e.get('exclude') or label not in labels:
            continue
        dur = r['summary']['duration_s']
        sug = r['summary'].get('trim_suggest') if auto_trim else None            # no trim saved: the idle start and end (nothing changes there) are cut anyway
        a = e.get('trim_start_s', sug[0] if sug else 0.0)
        b = e.get('trim_end_s', sug[1] if sug else dur)
        secs += b - a
        eps.append({'id': r['id'], 'prompt': r['prompt'], 'label': label, 'robot_mode': r.get('robot_mode'), 'trim_start_s': a, 'trim_end_s': b})
    if not eps:
        raise EpisodeError('no episode matches (check the labels and the excluded ones)')
    if crop and any(len(set((sp.get('anchor') or {}).values())) > 1 for sp in crop.values()):       # a crop placed by the mode: every episode needs one
        missing = [e['id'] for e in eps if not e['robot_mode']]
        if missing:
            raise EpisodeError(f'the crop depends on the robot mode and {len(missing)} episode(s) have none: {", ".join(missing[:6])}{" ..." if len(missing) > 6 else ""}. Set it in the episode\'s detail panel.')
    manifest = {'version': 1, 'name': name, 'created_at': round(time.time(), 1), 'fps': int(fps), 'action': {'source': action, 'horizon_s': float(horizon_s)},
                'labels': list(labels), **({'crop': crop} if crop else {}), 'episodes': eps}
    f = _root(raw_dir).parent / 'exports' / f'{name}.json'
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(manifest, indent=1))
    return {'manifest': str(f), 'episodes': len(eps), 'hours': round(secs / 3600, 3), 'data_dir': str(_root(raw_dir).parent), 'name': name}


def auto_clean(raw_dir, ids=None, overwrite: bool = False) -> dict:
    """Save the suggested trim (the idle ends, and everything outside the teleop part when the arm was moved by something else) as the edit of each episode.
    An episode that already has an edit is left alone unless ``overwrite``; an existing label / exclude / note is kept. Returns {cleaned, skipped, rows}."""
    done, skipped = [], []
    for r in list_episodes(raw_dir)['episodes']:
        if 'error' in r or (ids is not None and r['id'] not in ids):
            continue
        sug = r['summary'].get('trim_suggest')
        if not sug or (r.get('edit') and not overwrite):
            skipped.append(r['id'])
            continue
        e = r.get('edit') or {}
        edit = {'trim_start_s': sug[0], 'trim_end_s': sug[1], **{k: e[k] for k in ('label', 'exclude', 'note') if k in e}}
        if edit['trim_end_s'] - edit['trim_start_s'] < 0.5:
            skipped.append(r['id'])
            continue
        save_edit(raw_dir, r['day'], r['episode'], edit)
        done.append({'id': r['id'], 'trim_start_s': sug[0], 'trim_end_s': sug[1], 'kept_s': round(sug[1] - sug[0], 1), 'was_s': r['summary']['duration_s']})
    return {'cleaned': len(done), 'skipped': len(skipped), 'rows': done}


# ── delete: into the trash folder, never for good ─────────────────────────────────────────────────────────────────
def trash_dir(raw_dir) -> Path:
    return _root(raw_dir).parent / 'trash'


def delete_episode(raw_dir, day: str, ep: str) -> dict:
    """Move an episode (and its edit list) to ``<data>/trash/<day>/<ep>``: the raw files are kept there, so a deleted episode can be put back by moving
    the folder back. A folder with that name already in the trash gets a time suffix. Only finished episodes (DONE) can be moved."""
    p = episode_dir(raw_dir, day, ep)
    dest = trash_dir(raw_dir) / day / ep
    if dest.exists():
        dest = dest.with_name(f'{ep}_{int(time.time())}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.rename(dest)                                   # one filesystem (the data folder): atomic
    except OSError:
        shutil.move(str(p), str(dest))
    e = edits_path(raw_dir, day, ep)
    if e.is_file():
        e.rename(dest / 'edit.json')                     # the operator's decision travels with the episode
    _CACHE.pop(str(p), None)
    return {'ok': True, 'trash': str(dest)}
