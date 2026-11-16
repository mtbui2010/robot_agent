"""core/episodes.py + /recordings on synthetic raw episodes (the format robot_teleop.recorder writes)."""
import json

import numpy as np
import pytest

from robot_agent.core import episodes as E
from robot_agent.core import recorder as R

T0 = 1_700_000_000.0


def make_episode(raw, day='2026-11-16', ep='ep_0001', dur=20.0, move=(4.0, 15.0), label='success', events=None, prompt='open drawer'):
    """50 Hz state that moves joint 1 between ``move``; head 30 Hz / wrist 12 Hz tiny jpeg files; a gripper step at 10 s."""
    d = raw / day / ep
    (d / 'head').mkdir(parents=True)
    (d / 'wrist').mkdir()
    ts = np.arange(0, dur, 0.02)
    st = np.zeros((len(ts), 8))
    mv = (ts >= move[0]) & (ts <= move[1])
    st[:, 0] = np.where(mv, (ts - move[0]) * 0.2, np.where(ts > move[1], (move[1] - move[0]) * 0.2, 0))
    th, tw = np.arange(0, dur, 1 / 30), np.arange(0, dur, 1 / 12)
    for cam, t in (('head', th), ('wrist', tw)):
        for i in range(len(t)):
            (d / cam / f'{i:06d}.jpg').write_bytes(b'\xff\xd8' + bytes([i % 250]) + b'\xff\xd9')
    tg = np.arange(0, dur, 0.02)
    np.savez(d / 'streams.npz', state_t=T0 + ts, state=st, head_t=T0 + th, head_stamp=th, wrist_t=T0 + tw, wrist_stamp=tw,
             gripper_cmd_t=T0 + tg, gripper_cmd=np.where(tg > 10, 0.1, 0.0).reshape(-1, 1),
             action_servo_t=T0 + ts, action_servo=st[:, :7])
    meta = {'format': 'robot_teleop.raw/1', 'episode': ep, 'prompt': prompt, 'started_at': T0, 'duration_s': dur, 'label': label,
            'cameras': {'head': '/h', 'wrist': '/w'}, 'fields': {'action': 'open'}, 'counts': {'state': len(ts)},
            'events': events if events is not None else [{'t': 0.1, 'kind': 'teleop', 'state': 'active'}]}
    (d / 'meta.json').write_text(json.dumps(meta))
    (d / 'DONE').write_text('1')
    return d


def test_summary_finds_the_idle_ends_and_suggests_a_trim(tmp_path):
    make_episode(tmp_path / 'raw')
    s = E.row(tmp_path / 'raw', '2026-11-16', 'ep_0001')['summary']
    assert s['duration_s'] == 20.0 and abs(s['rates_hz']['head'] - 30) < 1 and abs(s['rates_hz']['wrist'] - 12) < 1
    assert 3.5 < s['idle_head_s'] < 4.5 and 4.0 < s['idle_tail_s'] < 6.0
    a, b = s['trim_suggest']
    assert 3.2 < a < 4.0 and 15.0 < b < 16.0                                        # the movement with 0.5 s of margin
    assert s['gripper_changes'] == 1 and s['inactive_inside_s'] == 0
    assert any('wrist camera only' in w for w in s['warnings']) and not any('teleop was off' in w for w in s['warnings'])


def test_teleop_off_in_the_middle_of_the_movement_is_a_warning(tmp_path):
    ev = [{'t': 0.1, 'kind': 'teleop', 'state': 'active'}, {'t': 8.0, 'kind': 'teleop', 'state': 'inactive'}, {'t': 11.0, 'kind': 'teleop', 'state': 'active'}]
    make_episode(tmp_path / 'raw', events=ev)
    s = E.row(tmp_path / 'raw', '2026-11-16', 'ep_0001')['summary']
    assert abs(s['inactive_inside_s'] - 3.0) < 0.1 and any('teleop was off for' in w for w in s['warnings'])
    assert E._inactive_between([], 0, 5) == 0.0


def test_listing_totals_and_an_unfinished_episode_is_hidden(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw)
    make_episode(raw, ep='ep_0002', label='failure')
    half = make_episode(raw, ep='ep_0003')
    (half / 'DONE').unlink()                                                         # still being copied
    out = E.list_episodes(raw)
    assert [r['episode'] for r in out['episodes']] == ['ep_0002', 'ep_0001']
    assert out['totals'] == {'episodes': 2, 'hours': round(40 / 3600, 2), 'success': 1, 'failure': 1, 'excluded': 0}
    assert E.list_episodes(tmp_path / 'nothing')['episodes'] == []


def test_detail_has_downsampled_curves_and_the_nearest_frame(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw)
    d = E.detail(raw, '2026-11-16', 'ep_0001', points=100)
    assert len(d['series']['t']) == 100 and len(d['series']['joints']) == 7 and len(d['series']['gripper_cmd']) == 100 and d['series']['gripper_cmd'][-1] == 0.1
    assert d['cameras']['head']['frames'] == 600
    f = E.frame_path(raw, '2026-11-16', 'ep_0001', 'head', 1.0)
    assert f.name == '000030.jpg'
    assert E.frame_path(raw, '2026-11-16', 'ep_0001', 'wrist', 99).name == '000239.jpg'      # beyond the end: the last frame
    with pytest.raises(E.EpisodeError):
        E.frame_path(raw, '2026-11-16', 'ep_0001', 'rear', 1.0)


def test_ids_cannot_leave_the_data_folder(tmp_path):
    make_episode(tmp_path / 'raw')
    for day, ep in (('..', 'ep_0001'), ('2026-11-16', '../ep_0001'), ('2026-11-16', 'ep_9999'), ('x', 'y')):
        with pytest.raises(E.EpisodeError):
            E.episode_dir(tmp_path / 'raw', day, ep)


def test_edits_are_validated_saved_beside_the_raw_folder_and_deletable(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw)
    out = E.save_edit(raw, '2026-11-16', 'ep_0001', {'trim_start_s': 3.5, 'trim_end_s': 16, 'label': 'failure', 'exclude': True, 'note': 'x' * 400})
    assert out['trim_start_s'] == 3.5 and out['label'] == 'failure' and out['exclude'] is True and len(out['note']) == 300
    f = tmp_path / 'edits' / '2026-11-16' / 'ep_0001.json'
    assert f.is_file() and not list((raw / '2026-11-16' / 'ep_0001').glob('edit*'))     # next to raw, never into it
    assert E.row(raw, '2026-11-16', 'ep_0001')['edit']['label'] == 'failure'
    for bad in ({'trim_start_s': 5, 'trim_end_s': 4}, {'trim_start_s': -1}, {'trim_end_s': 99}, {'label': 'maybe'}):
        with pytest.raises(E.EpisodeError):
            E.save_edit(raw, '2026-11-16', 'ep_0001', bad)
    E.delete_edit(raw, '2026-11-16', 'ep_0001')
    assert not f.exists() and E.row(raw, '2026-11-16', 'ep_0001')['edit'] is None


def test_export_manifest_includes_only_accepted_labels_and_applies_trims(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw)
    make_episode(raw, ep='ep_0002', label='failure')
    make_episode(raw, ep='ep_0003')
    E.save_edit(raw, '2026-11-16', 'ep_0003', {'exclude': True})
    E.save_edit(raw, '2026-11-16', 'ep_0001', {'trim_start_s': 3.5, 'trim_end_s': 16})
    out = E.export_manifest(raw, 'drawer_v1')
    m = json.loads((tmp_path / 'exports' / 'drawer_v1.json').read_text())
    assert [e['id'] for e in m['episodes']] == ['2026-11-16/ep_0001'] and m['episodes'][0]['trim_start_s'] == 3.5 and m['fps'] == 30
    assert out['episodes'] == 1 and out['hours'] == round(12.5 / 3600, 3) and m['action'] == {'source': 'future_state', 'horizon_s': 0.3}
    assert len(json.loads((tmp_path / 'exports' / 'd2.json').read_text() if E.export_manifest(raw, 'd2', labels=('success', 'failure')) else '{}')['episodes']) == 2
    for bad in ({'name': '../x'}, {'name': 'a', 'fps': 0}, {'name': 'a', 'action': 'magic'}, {'name': 'a', 'horizon_s': 9}):
        with pytest.raises(E.EpisodeError):
            E.export_manifest(raw, **bad)
    with pytest.raises(E.EpisodeError, match='no episode'):
        E.export_manifest(tmp_path / 'empty' / 'raw', 'z')


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import recordings as api
    make_episode(tmp_path / 'raw')
    monkeypatch.setattr(api, 'bridge', lambda: R.RecorderBridge(lambda: {'dir': str(tmp_path / 'raw')}, lambda: None))
    app = FastAPI(); app.include_router(api.router)
    return TestClient(app), tmp_path


def test_endpoints(client):
    c, tmp = client
    r = c.get('/recordings').json()
    assert r['totals']['episodes'] == 1 and r['episodes'][0]['id'] == '2026-11-16/ep_0001'
    assert c.get('/recordings/2026-11-16/ep_0001').json()['series']['joints'][0]
    img = c.get('/recordings/2026-11-16/ep_0001/frame', params={'cam': 'head', 't': 1.0})
    assert img.status_code == 200 and img.headers['content-type'] == 'image/jpeg' and img.content.startswith(b'\xff\xd8')
    assert c.get('/recordings/2026-11-16/ep_0009').status_code == 400
    assert c.put('/recordings/2026-11-16/ep_0001/edit', json={'trim_start_s': 1, 'trim_end_s': 5}).json()['trim_end_s'] == 5
    assert c.put('/recordings/2026-11-16/ep_0001/edit', json={'trim_start_s': 5, 'trim_end_s': 1}).status_code == 400
    e = c.post('/recordings/export', json={'name': 'v1'}).json()
    assert e['episodes'] == 1 and 'export_lerobot.py' in e['command'] and e['manifest'].endswith('exports/v1.json')
    assert c.delete('/recordings/2026-11-16/ep_0001/edit').json() == {'ok': True}


def test_no_config_is_404(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import recordings as api
    monkeypatch.setattr(api, 'bridge', lambda: R.RecorderBridge(lambda: None, lambda: None))
    app = FastAPI(); app.include_router(api.router)
    assert TestClient(app).get('/recordings').status_code == 404
