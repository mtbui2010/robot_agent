"""core/episodes.py + /recordings on synthetic raw episodes (the format robot_teleop.recorder writes)."""
import json

import numpy as np
import pytest

from robot_agent.core import episodes as E
from robot_agent.core import recorder as R

T0 = 1_700_000_000.0


def make_episode(raw, day='2026-11-16', ep='ep_0001', dur=20.0, move=(4.0, 15.0), label='success', events=None, prompt='open drawer', mode=None):
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
            'events': events if events is not None else [{'t': 0.1, 'kind': 'teleop', 'state': 'active'}], **({'robot_mode': mode} if mode else {})}
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


def test_inactive_time_between_two_moments():
    ev = [{'t': 0.1, 'kind': 'teleop', 'state': 'active'}, {'t': 8.0, 'kind': 'teleop', 'state': 'inactive'}, {'t': 11.0, 'kind': 'teleop', 'state': 'active'}]
    assert abs(E._inactive_between(ev, 4.0, 15.0) - 3.0) < 1e-9 and abs(E._inactive_between(ev, 9.0, 10.0) - 1.0) < 1e-9
    assert E._inactive_between(ev, 0.5, 7.0) == 0.0 and E._inactive_between([], 0, 5) == 0.0


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
    ac = c.post('/recordings/auto-clean', json={'overwrite': True}).json()
    assert ac['cleaned'] == 1 and ac['rows'][0]['id'] == '2026-11-16/ep_0001' and ac['rows'][0]['was_s'] == 20.0
    assert c.post('/recordings/auto-clean', json={}).json()['cleaned'] == 0                  # already has an edit now
    e = c.post('/recordings/export', json={'name': 'v1'}).json()
    assert e['episodes'] == 1 and 'export_lerobot.py' in e['command'] and e['manifest'].endswith('exports/v1.json')
    assert c.delete('/recordings/2026-11-16/ep_0001/edit').json() == {'ok': True}
    gone = c.delete('/recordings/2026-11-16/ep_0001').json()
    assert gone['ok'] is True and gone['trash'].endswith('trash/2026-11-16/ep_0001') and c.get('/recordings').json()['totals']['episodes'] == 0
    assert c.delete('/recordings/2026-11-16/ep_0001').status_code == 400


def test_no_config_is_404(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import recordings as api
    monkeypatch.setattr(api, 'bridge', lambda: R.RecorderBridge(lambda: None, lambda: None))
    app = FastAPI(); app.include_router(api.router)
    assert TestClient(app).get('/recordings').status_code == 404


def goto_events(named=False):
    ev = [{'t': 0.1, 'kind': 'teleop', 'state': 'active'}, {'t': 6.0, 'kind': 'teleop', 'state': 'inactive'},
          {'t': 9.0, 'kind': 'teleop', 'state': 'hold'}, {'t': 10.0, 'kind': 'teleop', 'state': 'active'}]
    if named:
        ev += [{'t': 6.0, 'kind': 'goto', 'name': 'approach drawer', 'phase': 'start'}, {'t': 9.5, 'kind': 'goto', 'name': 'approach drawer', 'phase': 'end', 'ok': True}]
    return ev


def test_a_go_to_pose_move_is_kept_as_the_init_part(tmp_path):
    make_episode(tmp_path / 'raw', move=(4.0, 18.0), events=goto_events(), dur=20.0)       # the arm moves all the time; teleop is off from 6 to 10 s
    s = E.row(tmp_path / 'raw', '2026-11-16', 'ep_0001')['summary']
    assert s['init_moves'] == [{'start': 6.0, 'end': 10.0, 'name': None}]                  # inactive + hold are one range
    a, b = s['trim_suggest']
    assert a < 4.0 and b > 17.5                                                            # the trim does NOT cut it: only the idle ends go
    assert any('with teleop off' in n and 'init part' in n for n in s['notes']) and not any('teleop was off for' in w for w in s['warnings'])


def test_a_named_go_to_event_gives_the_pose_name(tmp_path):
    make_episode(tmp_path / 'raw', move=(4.0, 18.0), events=goto_events(named=True), dur=20.0)
    s = E.row(tmp_path / 'raw', '2026-11-16', 'ep_0001')['summary']
    assert s['init_moves'] == [{'start': 6.0, 'end': 9.5, 'name': 'approach drawer'}] and 'approach drawer' in s['notes'][0]


def test_teleop_off_while_the_arm_rests_is_no_automatic_motion(tmp_path):
    ev = [{'t': 0.1, 'kind': 'teleop', 'state': 'inactive'}, {'t': 1.0, 'kind': 'teleop', 'state': 'active'}]
    make_episode(tmp_path / 'raw', events=ev)
    s = E.row(tmp_path / 'raw', '2026-11-16', 'ep_0001')['summary']
    assert s['init_moves'] == [] and s['trim_suggest'][0] < 4.0


def test_auto_clean_saves_the_suggested_trim_and_keeps_other_edits(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw, move=(4.0, 18.0), events=goto_events())
    make_episode(raw, ep='ep_0002')
    make_episode(raw, ep='ep_0003')
    E.save_edit(raw, '2026-11-16', 'ep_0003', {'trim_start_s': 1, 'trim_end_s': 2, 'label': 'failure'})        # the operator already edited this one
    out = E.auto_clean(raw)
    assert out['cleaned'] == 2 and out['skipped'] == 1 and {r['id'] for r in out['rows']} == {'2026-11-16/ep_0001', '2026-11-16/ep_0002'}
    assert E.row(raw, '2026-11-16', 'ep_0001')['edit']['trim_start_s'] < 4.0               # the init move is not cut
    assert E.row(raw, '2026-11-16', 'ep_0003')['edit'] == {**E.row(raw, '2026-11-16', 'ep_0003')['edit']} and E.row(raw, '2026-11-16', 'ep_0003')['edit']['trim_end_s'] == 2.0
    E.save_edit(raw, '2026-11-16', 'ep_0002', {'label': 'failure', 'note': 'x'})
    out = E.auto_clean(raw, overwrite=True)
    e = E.row(raw, '2026-11-16', 'ep_0002')['edit']
    assert e['label'] == 'failure' and e['note'] == 'x' and 'trim_start_s' in e                  # the label and the note survive an overwrite of the trim
    assert E.auto_clean(raw, ids=['2026-11-16/ep_0001'])['cleaned'] == 0                         # already edited, no overwrite


def test_delete_moves_the_episode_and_its_edit_to_the_trash_and_nothing_is_lost(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw)
    make_episode(raw, ep='ep_0002')
    E.save_edit(raw, '2026-11-16', 'ep_0001', {'trim_start_s': 1, 'trim_end_s': 5, 'label': 'failure'})
    E.row(raw, '2026-11-16', 'ep_0001')                                              # warm the cache
    out = E.delete_episode(raw, '2026-11-16', 'ep_0001')
    t = tmp_path / 'trash' / '2026-11-16' / 'ep_0001'
    assert out['trash'] == str(t) and (t / 'DONE').is_file() and (t / 'streams.npz').is_file() and (t / 'head' / '000000.jpg').is_file()      # the raw files are all there
    assert json.loads((t / 'edit.json').read_text())['label'] == 'failure'                                                                   # with the decision
    assert not (raw / '2026-11-16' / 'ep_0001').exists() and not (tmp_path / 'edits' / '2026-11-16' / 'ep_0001.json').exists()
    assert [r['episode'] for r in E.list_episodes(raw)['episodes']] == ['ep_0002']                                                          # gone from the list
    with pytest.raises(E.EpisodeError):
        E.delete_episode(raw, '2026-11-16', 'ep_0001')                               # already gone
    with pytest.raises(E.EpisodeError):
        E.delete_episode(raw, '..', 'ep_0002')
    half = make_episode(raw, ep='ep_0003')
    (half / 'DONE').unlink()
    with pytest.raises(E.EpisodeError):
        E.delete_episode(raw, '2026-11-16', 'ep_0003')                               # still being copied: not touched


def test_a_second_episode_with_the_same_name_in_the_trash_gets_a_suffix(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw)
    E.delete_episode(raw, '2026-11-16', 'ep_0001')
    make_episode(raw)                                                                 # the same name again (e.g. restored and recorded over)
    out = E.delete_episode(raw, '2026-11-16', 'ep_0001')
    assert out['trash'] != str(tmp_path / 'trash' / '2026-11-16' / 'ep_0001') and (tmp_path / 'trash' / '2026-11-16' / 'ep_0001').is_dir()


def test_the_robot_mode_is_in_the_row_and_in_the_export_manifest(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw, mode='left')
    make_episode(raw, ep='ep_0002')                                                   # recorded before the mode was saved
    rows = {r['episode']: r for r in E.list_episodes(raw)['episodes']}
    assert rows['ep_0001']['robot_mode'] == 'left' and rows['ep_0002']['robot_mode'] is None
    E.export_manifest(raw, 'm1')
    m = json.loads((tmp_path / 'exports' / 'm1.json').read_text())
    assert {e['id']: e['robot_mode'] for e in m['episodes']} == {'2026-11-16/ep_0001': 'left', '2026-11-16/ep_0002': None}


CROP = {'head': {'square': True, 'offset': 120, 'size': 480, 'anchor': {'left': 'left', 'right': 'right', 'front': 'centre'}}}


def test_the_operator_can_set_the_mode_of_an_episode_and_it_wins(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw)                                                                  # recorded without a mode
    make_episode(raw, ep='ep_0002', mode='right')
    assert E.row(raw, '2026-11-16', 'ep_0001')['robot_mode'] is None
    E.save_edit(raw, '2026-11-16', 'ep_0001', {'robot_mode': 'left'})
    E.save_edit(raw, '2026-11-16', 'ep_0002', {'robot_mode': 'left'})
    r1, r2 = E.row(raw, '2026-11-16', 'ep_0001'), E.row(raw, '2026-11-16', 'ep_0002')
    assert (r1['robot_mode'], r1['recorded_mode']) == ('left', None) and (r2['robot_mode'], r2['recorded_mode']) == ('left', 'right')       # the setting wins, the recorded one is kept
    for bad in ('Left', '1x', 'a b', 'x' * 30):
        with pytest.raises(E.EpisodeError):
            E.save_edit(raw, '2026-11-16', 'ep_0001', {'robot_mode': bad})
    assert E.list_episodes(raw)['modes'] == ['left']


def test_a_crop_placed_by_the_mode_needs_a_mode_on_every_episode_and_travels_in_the_manifest(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw, mode='left')
    make_episode(raw, ep='ep_0002')                                                    # no mode
    with pytest.raises(E.EpisodeError, match='ep_0002'):
        E.export_manifest(raw, 'c1', crop=CROP)
    E.export_manifest(raw, 'c0')                                                       # no crop: no mode needed
    E.save_edit(raw, '2026-11-16', 'ep_0002', {'robot_mode': 'right'})
    E.export_manifest(raw, 'c2', crop=CROP)
    m = json.loads((tmp_path / 'exports' / 'c2.json').read_text())
    assert m['crop'] == CROP and {e['id']: e['robot_mode'] for e in m['episodes']} == {'2026-11-16/ep_0001': 'left', '2026-11-16/ep_0002': 'right'}
    assert 'crop' not in json.loads((tmp_path / 'exports' / 'c0.json').read_text())
    E.export_manifest(raw, 'c3', crop={'head': {'square': True, 'anchor': {'left': 'centre', 'right': 'centre'}}})     # a crop that does not depend on the mode needs none


def test_the_list_and_the_export_know_the_robots_crop(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import recordings as api
    make_episode(tmp_path / 'raw', mode='left')
    make_episode(tmp_path / 'raw', ep='ep_0002')
    monkeypatch.setattr(api, 'bridge', lambda: R.RecorderBridge(lambda: {'dir': str(tmp_path / 'raw'), 'crop': CROP}, lambda: None))
    app = FastAPI(); app.include_router(api.router)
    c = TestClient(app)
    lst = c.get('/recordings').json()
    assert lst['crop'] == CROP and lst['modes'] == ['front', 'left', 'right']          # the modes the crop knows, so the operator can set one
    r = c.post('/recordings/export', json={'name': 'x1', 'crop': True})
    assert r.status_code == 400 and 'ep_0002' in r.json()['detail']                    # one episode has no mode
    assert c.put('/recordings/2026-11-16/ep_0002/edit', json={'robot_mode': 'left'}).json()['robot_mode'] == 'left'
    ok = c.post('/recordings/export', json={'name': 'x2', 'crop': True}).json()
    assert ok['episodes'] == 2 and json.loads((tmp_path / 'exports' / 'x2.json').read_text())['crop'] == CROP
    monkeypatch.setattr(api, 'bridge', lambda: R.RecorderBridge(lambda: {'dir': str(tmp_path / 'raw')}, lambda: None))
    assert c.post('/recordings/export', json={'name': 'x3', 'crop': True}).status_code == 400          # this robot defines no crop


def test_the_export_cuts_the_idle_start_and_end_of_an_episode_with_no_saved_trim(tmp_path):
    raw = tmp_path / 'raw'
    make_episode(raw)                                                                   # moves from 4 to 15 s of 20 s
    make_episode(raw, ep='ep_0002')
    E.save_edit(raw, '2026-11-16', 'ep_0002', {'trim_start_s': 1.0, 'trim_end_s': 19.0})     # the operator's own trim wins
    out = E.export_manifest(raw, 'a1')
    m = {e['id']: e for e in json.loads((tmp_path / 'exports' / 'a1.json').read_text())['episodes']}
    sug = E.row(raw, '2026-11-16', 'ep_0001')['summary']['trim_suggest']
    assert (m['2026-11-16/ep_0001']['trim_start_s'], m['2026-11-16/ep_0001']['trim_end_s']) == tuple(sug) and sug[0] > 3 and sug[1] < 17          # the idle ends are out
    assert (m['2026-11-16/ep_0002']['trim_start_s'], m['2026-11-16/ep_0002']['trim_end_s']) == (1.0, 19.0)
    assert out['hours'] == round(((sug[1] - sug[0]) + 18.0) / 3600, 3)
    E.export_manifest(raw, 'a2', auto_trim=False)                                       # switched off: the whole episode, as before
    m2 = {e['id']: e for e in json.loads((tmp_path / 'exports' / 'a2.json').read_text())['episodes']}
    assert (m2['2026-11-16/ep_0001']['trim_start_s'], m2['2026-11-16/ep_0001']['trim_end_s']) == (0.0, 20.0)
