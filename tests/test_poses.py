"""core/poses.py (the editable Go to list) and /poses, with no ROS and no robot."""
import json

import pytest

from robot_agent.core import poses as P

ARM = {'range': {'x': [0, 1]}, 'base_x': 0.19, 'home': {'front': [0, 15, 0, 15, -180, 0, 0]},
       'approach_lying': {'right': [1.0, 2.0], 'left': [3.0, 4.0]}, 'approach_drawer': {'front': [[1, 2], [3, 4]]}, 'approach_bad': {'x': 'text'}}
CFG = {'seed': {'from': 'ARM_CONFIGS', 'skill': 'movej', 'prefix': 'approach_', 'keys': ['front', 'left', 'right']}, 'speed_skills': ['movej', 'movel'], 'default_speed': 0.25}


def manager(tmp_path, cfg=CFG, group=ARM):
    return P.PoseManager(tmp_path, get_cfg=lambda: cfg, get_group=lambda n: group if n == 'ARM_CONFIGS' else None)


def test_seed_poses_are_read_from_the_robots_config_group():
    assert [s['label'] for s in P.seed_poses(CFG['seed'], ARM)] == ['approach lying', 'approach drawer']          # only poses of the right keys and prefix
    assert P.seed_poses(CFG['seed'], ARM)[0] == {'label': 'approach lying', 'plan': 'movej::approach_lying'}
    assert 'range' in [s['label'] for s in P.seed_poses({'skill': 'movej'}, ARM)]                              # without keys a table of numbers looks like a pose
    assert P.seed_poses(None, ARM) == [] and P.seed_poses({'from': 'x'}, ARM) == [] and P.seed_poses(CFG['seed'], 'junk') == []


def test_the_first_list_is_seeded_once_and_everything_is_editable_and_deletable(tmp_path):
    m = manager(tmp_path)
    got = m.all()
    assert [p['label'] for p in got['poses']] == ['approach lying', 'approach drawer'] and got['speed'] == 0.25 and got['defaults_missing'] == 0
    lying = got['poses'][0]['id']
    assert m.update(lying, label='lying (mine)', plan='lift::0.5&&movej::approach_lying')['label'] == 'lying (mine)'
    assert m.remove(got['poses'][1]['id']) is True                                  # a seeded pose can be deleted too
    m2 = manager(tmp_path)                                                           # another process / machine: the file is the truth, not the seed
    assert [p['label'] for p in m2.all()['poses']] == ['lying (mine)']
    assert m2.all()['defaults_missing'] == 2                                         # both default labels are not in the list any more
    assert m2.restore_defaults() == 2 and [p['label'] for p in m2.all()['poses']] == ['lying (mine)', 'approach lying', 'approach drawer']
    assert m2.restore_defaults() == 0


def test_add_validates_and_labels_are_unique(tmp_path):
    m = manager(tmp_path, cfg={}, group=None)
    assert m.all() == {'speed': 1.0, 'poses': [], 'defaults_missing': 0}            # no POSES config: empty list, full speed
    p = m.add(' my pose ', 'movej::fold\nlift::0.5')
    assert p['label'] == 'my pose' and m.get(p['id'])['plan'] == 'movej::fold\nlift::0.5'
    for label, plan in (('', 'movej::x'), ('a', ''), ('a', 'no steps here'), ('MY POSE', 'movej::x')):
        with pytest.raises(ValueError):
            m.add(label, plan)
    assert m.update('nope', label='x') is None and m.remove('nope') is False
    q = m.add('other', 'movej::x')
    with pytest.raises(ValueError):
        m.update(q['id'], label='my pose')                                           # taken by another pose


def test_one_speed_for_all_poses(tmp_path):
    m = manager(tmp_path)
    m.all()
    assert m.set_speed(0.4) == 0.4 and manager(tmp_path).speed() == 0.4
    for bad in (0, 0.01, 1.5, '0.5', True, None):
        with pytest.raises(ValueError):
            m.set_speed(bad)


def test_the_speed_is_added_to_the_steps_of_speed_skills_only():
    sk = ['movej', 'movel']
    assert P.apply_speed('movej::fold', 0.25, sk) == 'movej::inputs="fold", speed=0.25'
    assert P.apply_speed('movej::dr6=-90, dr5=10', 0.25, sk) == 'movej::dr6=-90, dr5=10, speed=0.25'
    assert P.apply_speed('lift::0.5&&movej::give', 0.3, sk) == 'lift::0.5&&movej::inputs="give", speed=0.3'      # lift is not a speed skill
    assert P.apply_speed('movej::fold, speed=0.9', 0.25, sk) == 'movej::fold, speed=0.9'                         # a step that sets its own speed keeps it
    assert P.apply_speed('movej::fold  # tuck\n\nmovel::dz=0.1', 0.2, sk) == 'movej::inputs="fold", speed=0.2  # tuck\n\nmovel::dz=0.1, speed=0.2'
    assert P.apply_speed('movej::fold', 1.0, sk) == 'movej::fold' and P.apply_speed('movej::fold', 0.25, None) == 'movej::fold'
    assert P.apply_speed('~movej::fold', 0.25, sk) == '~movej::inputs="fold", speed=0.25'                       # the ~ / ! markers of a step stay


def test_plan_to_run_applies_the_current_speed(tmp_path):
    m = manager(tmp_path)
    pid = m.all()['poses'][0]['id']
    assert m.plan_to_run(pid) == ('approach lying', 'movej::inputs="approach_lying", speed=0.25')
    m.set_speed(1.0)
    assert m.plan_to_run(pid)[1] == 'movej::approach_lying' and m.plan_to_run('nope') is None


def test_two_machines_saving_do_not_lose_each_others_poses(tmp_path):
    a, b = manager(tmp_path), manager(tmp_path)
    a.all(), b.all()
    a.add('from a', 'movej::x')
    b.add('from b', 'movej::y')
    assert {p['label'] for p in manager(tmp_path).all()['poses']} == {'approach lying', 'approach drawer', 'from a', 'from b'}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import poses as api
    m = manager(tmp_path)
    ran = []

    class _Mgr:
        active = staticmethod(lambda: None)
    monkeypatch.setattr(api, 'current', lambda: SimpleNamespace(pm=m, sr=object(), dm=SimpleNamespace(_ros_node=None)))
    monkeypatch.setattr(api.run_control, 'manager', lambda: _Mgr)
    monkeypatch.setattr(api.run_control, 'begin_run', lambda node=None: ran.append('begin'))
    monkeypatch.setattr(api.P, 'run_plan', lambda reg, plan, node=None, name='': ran.append(plan) or {'isdone': True})
    sent = []
    rb = SimpleNamespace(status=lambda: {'available': True, 'state': {'state': 'recording'}}, send=lambda c: sent.append(c) or True)
    monkeypatch.setattr(api, 'recorder_bridge', lambda: rb)
    app = FastAPI(); app.include_router(api.router)
    return TestClient(app), m, ran, sent, _Mgr


def test_endpoints(client):
    c, m, ran, sent, mgr = client
    r = c.get('/poses').json()
    assert r['speed'] == 0.25 and [p['label'] for p in r['poses']] == ['approach lying', 'approach drawer']
    pid = c.post('/poses', json={'label': 'mine', 'plan': 'movej::fold'}).json()['id']
    assert c.post('/poses', json={'label': 'mine', 'plan': 'movej::fold'}).status_code == 400
    assert c.put(f'/poses/{pid}', json={'plan': 'movej::give'}).json()['plan'] == 'movej::give'
    assert c.put('/poses/nope', json={'plan': 'movej::give'}).status_code == 404
    assert c.put('/poses/speed', json={'speed': 0.5}).json() == {'speed': 0.5} and c.put('/poses/speed', json={'speed': 9}).status_code == 400
    assert c.post('/poses/defaults').json() == {'added': 0}
    assert c.delete(f'/poses/{pid}').json() == {'ok': True} and c.delete(f'/poses/{pid}').status_code == 404


def test_run_applies_the_speed_marks_a_recording_and_refuses_while_a_run_is_in_flight(client):
    c, m, ran, sent, mgr = client
    pid = c.get('/poses').json()['poses'][0]['id']
    out = c.post(f'/poses/{pid}/run').json()
    assert out['isdone'] is True and out['plan'] == 'movej::inputs="approach_lying", speed=0.25' and ran == ['begin', 'movej::inputs="approach_lying", speed=0.25']
    assert sent == [{'cmd': 'mark', 'kind': 'goto', 'name': 'approach lying', 'phase': 'start'}, {'cmd': 'mark', 'kind': 'goto', 'name': 'approach lying', 'phase': 'end', 'ok': True}]
    mgr.active = staticmethod(lambda: object())
    assert c.post(f'/poses/{pid}/run').status_code == 409 and c.post('/poses/nope/run').status_code == 404
