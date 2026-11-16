"""The teleop bridge (core/teleop.py) and its endpoints, on a fake transport (no ROS)."""
import json

import pytest

from robot_agent.core import teleop as T

CFG = {'functions': [{'name': 'arm', 'label': 'Arm + gripper'}, {'name': 'base'}, {'name': 'arm'}, {'label': 'no name'}, 'junk']}


class FakeTransport:
    def __init__(self):
        self.subs, self.sent = {}, []

    def subscribe(self, topic, cb):
        self.subs[topic] = cb

    def publish(self, topic, text):
        self.sent.append((topic, json.loads(text)))


def make(cfg=CFG, tr=None):
    tr = tr or FakeTransport()
    return T.TeleopBridge(lambda: cfg, lambda: tr), tr


def test_functions_come_from_the_config_with_default_topics():
    b, _ = make()
    assert [f['name'] for f in b.functions()] == ['arm', 'base']                  # duplicates, nameless and junk entries dropped
    assert b.functions()[0] == {'name': 'arm', 'label': 'Arm + gripper', 'state_topic': '/teleop/arm/state', 'command_topic': '/teleop/arm/command'}
    assert b.functions()[1]['label'] == 'base'
    assert make(None)[0].functions() == [] and make({})[0].functions() == []      # nothing configured: nothing shown


def test_explicit_topics_win():
    b, _ = make({'functions': [{'name': 'a', 'state_topic': '/x/s', 'command_topic': '/x/c'}]})
    f = b.functions()[0]
    assert (f['state_topic'], f['command_topic']) == ('/x/s', '/x/c')


def test_listing_before_and_after_a_state_arrives():
    b, tr = make()
    rows = b.listing()
    assert [r['available'] for r in rows] == [False, False] and 'state' not in rows[0]      # subscribed, nothing yet
    assert set(tr.subs) == {'/teleop/arm/state', '/teleop/base/state'}
    tr.subs['/teleop/arm/state'](json.dumps({'state': 'active', 'speed': 0.5}))
    row = b.listing()[0]
    assert row['available'] and row['state']['state'] == 'active' and row['age_s'] >= 0
    tr.subs['/teleop/arm/state']('not json')                                                 # garbage is ignored
    assert b.listing()[0]['state']['state'] == 'active'


def test_a_stale_state_is_not_available(monkeypatch):
    b, tr = make()
    b.listing()
    tr.subs['/teleop/arm/state'](json.dumps({'state': 'active'}))
    t0 = T.time.monotonic()
    monkeypatch.setattr(T.time, 'monotonic', lambda: t0 + T.STALE_S + 1)
    row = b.listing()[0]
    assert row['available'] is False and row['state']['state'] == 'active'                   # kept, but marked not running
    assert b.is_active() is False


def test_commands_and_stop_all():
    b, tr = make()
    assert b.command('arm', 'speed', 0.4) is True
    assert tr.sent == [('/teleop/arm/command', {'cmd': 'speed', 'value': 0.4})]
    assert b.command('nope', 'enable') is False
    tr.sent.clear()
    b.stop_all()
    assert tr.sent == [('/teleop/arm/command', {'cmd': 'disable'}), ('/teleop/base/command', {'cmd': 'disable'})]


def test_no_ros_means_no_commands():
    b = T.TeleopBridge(lambda: CFG, lambda: None)
    assert b.command('arm', 'enable') is False
    b.stop_all()                                                                              # never raises
    assert [r['available'] for r in b.listing()] == [False, False]


def test_a_new_ros_node_resubscribes():
    trs = [FakeTransport(), FakeTransport()]
    cur = {'i': 0}
    b = T.TeleopBridge(lambda: CFG, lambda: trs[cur['i']])
    b.listing()
    cur['i'] = 1
    b.listing()
    assert set(trs[1].subs) == {'/teleop/arm/state', '/teleop/base/state'}


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import teleop as api
    b, tr = make()
    monkeypatch.setattr(api, 'bridge', lambda: b)
    app = FastAPI(); app.include_router(api.router)
    return TestClient(app), b, tr, api


def test_list_endpoint(client):
    c, b, tr, _ = client
    r = c.get('/teleop').json()
    assert [f['name'] for f in r['functions']] == ['arm', 'base'] and r['functions'][0]['available'] is False
    assert tr.sent == []                                                                      # reading sends nothing


def test_enable_needs_confirm_a_running_function_and_no_run(client, monkeypatch):
    c, b, tr, api = client
    b.listing()
    assert c.post('/teleop/arm/enable', json={'on': True}).status_code == 400                # no confirm
    assert c.post('/teleop/arm/enable', json={'on': True, 'confirm': True}).status_code == 409  # not running yet
    tr.subs['/teleop/arm/state'](json.dumps({'state': 'inactive'}))

    class _Mgr:
        active = staticmethod(lambda: object())
    monkeypatch.setattr(api.run_control, 'manager', lambda: _Mgr)
    assert c.post('/teleop/arm/enable', json={'on': True, 'confirm': True}).status_code == 409  # a run in flight
    _Mgr.active = staticmethod(lambda: None)
    assert c.post('/teleop/arm/enable', json={'on': True, 'confirm': True}).status_code == 200
    assert tr.sent[-1] == ('/teleop/arm/command', {'cmd': 'enable'})
    assert c.post('/teleop/nope/enable', json={'on': True, 'confirm': True}).status_code == 404


def test_disable_needs_no_confirm_and_no_running_function(client):
    c, b, tr, _ = client
    assert c.post('/teleop/arm/enable', json={'on': False}).status_code == 200
    assert tr.sent[-1] == ('/teleop/arm/command', {'cmd': 'disable'})


def test_speed_endpoint_clips(client):
    c, b, tr, _ = client
    assert c.post('/teleop/arm/speed', json={'scale': 3}).status_code == 200
    assert tr.sent[-1] == ('/teleop/arm/command', {'cmd': 'speed', 'value': 1.0})
    assert c.post('/teleop/arm/speed', json={'scale': -1}).status_code == 200 and tr.sent[-1][1]['value'] == 0.0


def test_a_run_starting_and_cancel_switch_teleop_off(monkeypatch):
    from robot_agent.core import run_control as rc
    b, tr = make()
    monkeypatch.setattr(T, 'bridge', lambda: b)
    rc._teleop_off()
    assert [s[1]['cmd'] for s in tr.sent] == ['disable', 'disable']


ARM = {'range': {'x': [0, 1]}, 'base_x': 0.19, 'home': {'front': [0, 15, 0, 15, -180, 0, 0]},
       'approach_lying': {'right': [1.0, 2.0], 'left': [3.0, 4.0]}, 'approach_drawer': {'front': [[1, 2], [3, 4]]}, 'approach_bad': {'x': 'text'}}


def poses_of(spec, group=ARM):
    b = T.TeleopBridge(lambda: {'functions': [{'name': 'arm', 'poses': spec}]}, lambda: None, lambda name: group if name == 'ARM_CONFIGS' else None)
    return b.listing()[0]['poses']


def test_poses_are_read_from_the_robots_config_group():
    rows = poses_of({'from': 'ARM_CONFIGS', 'skill': 'movej', 'param': 'inputs', 'prefix': 'approach_'})
    assert [r['name'] for r in rows] == ['approach_lying', 'approach_drawer']                       # only poses (dict of joint lists), only the prefix
    assert rows[0] == {'name': 'approach_lying', 'label': 'approach lying', 'skill': 'movej', 'params': {'inputs': 'approach_lying'}}
    modes = ['front', 'left', 'right']
    assert [r['name'] for r in poses_of({'from': 'ARM_CONFIGS', 'skill': 'movej', 'keys': modes})] == ['home', 'approach_lying', 'approach_drawer']   # keys tell a pose from `range`
    assert 'range' in [r['name'] for r in poses_of({'from': 'ARM_CONFIGS', 'skill': 'movej'})]                       # without them a table of numbers looks like a pose
    assert [r['name'] for r in poses_of({'from': 'ARM_CONFIGS', 'skill': 'movej', 'names': ['home'], 'keys': ['front']})] == ['home']


def test_no_poses_when_the_spec_or_the_group_is_missing():
    assert poses_of(None) == [] and poses_of({'from': 'ARM_CONFIGS'}) == [] and poses_of({'from': 'NOPE', 'skill': 'movej'}) == []
    assert poses_of({'from': 'ARM_CONFIGS', 'skill': 'movej'}, group='not a dict') == []
