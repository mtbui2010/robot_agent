"""Prompt templates (core/prompts.py), the recorder bridge (core/recorder.py) and /recorder, on a fake transport (no ROS)."""
import json
import random

import pytest

from robot_agent.core import prompts as P
from robot_agent.core import recorder as R
from robot_agent.core import teleop as T

SPEC = {'fields': ['action', 'target_object', 'target_location', 'destination_object', 'destination_location'],
        'templates': ['{action} the {target_location} {target_object}', '{action} {target_object}',
                      '{action} the {target_object} on the {target_location} and place it on the {destination_object}',
                      '{action} the {target_object} from the {target_location} and put it on the {destination_object} at the {destination_location}',
                      '{action} the {target_object} on the {target_location} then place it on the {destination_object} at the {destination_location}'],
        'synonyms': {'top': ['top', 'first', 'upper'], 'pick': ['pick up', 'grab']}}


def test_every_filled_field_appears_in_the_sentence():
    rng = random.Random(1)
    assert P.generate(SPEC, {'action': 'open', 'target_object': 'drawer'}, rng) == 'open drawer'
    s = P.generate(SPEC, {'action': 'open', 'target_object': 'drawer', 'target_location': 'bottom'}, rng)
    assert s == 'open the bottom drawer'
    for _ in range(20):
        s = P.generate(SPEC, {'action': 'put', 'target_object': 'cup', 'target_location': 'sink', 'destination_object': 'tray', 'destination_location': 'table'}, rng)
        assert all(w in s for w in ('cup', 'sink', 'tray', 'table')), s


def test_the_wording_varies_and_synonyms_replace_values():
    rng = random.Random(0)
    got = {P.generate(SPEC, {'action': 'pick', 'target_object': 'cup', 'target_location': 'sink', 'destination_object': 'tray', 'destination_location': 'table'}, rng) for _ in range(40)}
    assert len(got) > 3 and any(s.startswith('grab') for s in got) and any(s.startswith('pick up') for s in got)
    tops = {P.generate(SPEC, {'action': 'open', 'target_object': 'drawer', 'target_location': 'top'}, rng) for _ in range(40)}
    assert tops == {'open the top drawer', 'open the first drawer', 'open the upper drawer'}


def test_unusable_input_is_a_readable_error():
    with pytest.raises(ValueError, match='action'):
        P.generate(SPEC, {'target_object': 'cup'})
    with pytest.raises(ValueError, match='no template uses exactly'):
        P.generate(SPEC, {'action': 'open', 'destination_location': 'table'})            # no template for that mix
    with pytest.raises(ValueError):
        P.generate({}, {'action': 'open'})
    assert P.generate(SPEC, {'action': '  open ', 'target_object': ' drawer  ', 'target_location': ''}, random.Random(0)) == 'open drawer'   # spaces trimmed, empty = absent


# ── bridge + endpoints ────────────────────────────────────────────────────────
CFG = {'dir': '/remote_dir/vla_data/raw', 'teleop': 'arm', 'prompts': SPEC}


class FakeTransport:
    def __init__(self):
        self.subs, self.sent = {}, []

    def subscribe(self, topic, cb):
        self.subs[topic] = cb

    def publish(self, topic, text):
        self.sent.append((topic, json.loads(text)))


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import recorder as api
    tr = FakeTransport()
    rb = R.RecorderBridge(lambda: CFG, lambda: tr)
    tb = T.TeleopBridge(lambda: {'functions': [{'name': 'arm'}]}, lambda: tr)
    monkeypatch.setattr(api, 'bridge', lambda: rb)
    monkeypatch.setattr(api._teleop, 'bridge', lambda: tb)

    class _Mgr:
        active = staticmethod(lambda: None)
    monkeypatch.setattr(api.run_control, 'manager', lambda: _Mgr)
    app = FastAPI(); app.include_router(api.router)
    tb.listing(); rb.status()                         # both subscribe on their first look (as in a running agent)
    return TestClient(app), tr, rb, _Mgr


def up(tr, rec='idle', teleop=True):
    tr.subs[R.STATE_TOPIC](json.dumps({'state': rec}))
    if teleop:
        tr.subs['/teleop/arm/state'](json.dumps({'state': 'inactive'}))


def test_no_config_means_404_and_nothing_sent(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from robot_agent.api import recorder as api
    tr = FakeTransport()
    monkeypatch.setattr(api, 'bridge', lambda: R.RecorderBridge(lambda: None, lambda: tr))
    app = FastAPI(); app.include_router(api.router)
    c = TestClient(app)
    assert c.get('/recorder').status_code == 404 and c.post('/recorder/start', json={'prompt': 'x'}).status_code == 404 and tr.sent == []


def test_status_lists_the_fields_and_availability(client):
    c, tr, rb, _ = client
    r = c.get('/recorder').json()
    assert r['available'] is False and r['fields'][0] == 'action' and r['teleop'] == 'arm' and r['dir'] == CFG['dir']
    up(tr, 'recording')
    r = c.get('/recorder').json()
    assert r['available'] is True and r['state']['state'] == 'recording' and tr.sent == []            # reading sends nothing


def test_prompt_endpoint(client):
    c, *_ = client
    r = c.post('/recorder/prompt', json={'fields': {'action': 'open', 'target_object': 'drawer', 'target_location': 'bottom'}})
    assert r.status_code == 200 and r.json()['prompt'] == 'open the bottom drawer'
    assert c.post('/recorder/prompt', json={'fields': {'target_object': 'cup'}}).status_code == 400


def test_start_checks_then_starts_recording_and_teleop(client):
    c, tr, rb, mgr = client
    c.get('/recorder')
    assert c.post('/recorder/start', json={'prompt': ' '}).status_code == 400
    assert c.post('/recorder/start', json={'prompt': 'open drawer'}).status_code == 409             # recorder not running
    up(tr, teleop=False)
    assert c.post('/recorder/start', json={'prompt': 'open drawer'}).status_code == 409             # teleop not running
    up(tr, 'recording')
    assert c.post('/recorder/start', json={'prompt': 'open drawer'}).status_code == 409             # already recording
    up(tr, 'idle')
    mgr.active = staticmethod(lambda: object())
    assert c.post('/recorder/start', json={'prompt': 'open drawer'}).status_code == 409             # a run in flight
    mgr.active = staticmethod(lambda: None)
    assert tr.sent == []                                                                              # every refusal sent nothing
    assert c.post('/recorder/start', json={'prompt': ' open drawer ', 'meta': {'a': 1}}).status_code == 200
    assert tr.sent == [('/record/command', {'cmd': 'start', 'prompt': 'open drawer', 'meta': {'a': 1}}), ('/teleop/arm/command', {'cmd': 'enable'})]


def test_stop_switches_teleop_off_and_saves_or_discards(client):
    c, tr, *_ = client
    assert c.post('/recorder/stop', json={'label': 'maybe'}).status_code == 400
    assert c.post('/recorder/stop', json={'label': 'success'}).status_code == 200
    assert tr.sent == [('/teleop/arm/command', {'cmd': 'disable'}), ('/record/command', {'cmd': 'stop', 'label': 'success'})]
    assert c.post('/recorder/stop', json={'label': 'discard'}).status_code == 200
    assert tr.sent[-1] == ('/record/command', {'cmd': 'discard'}) and tr.sent[-2] == ('/teleop/arm/command', {'cmd': 'disable'})


def test_a_template_can_be_limited_to_some_actions():
    spec = {'templates': [{'text': '{action} the {target_location} {target_object}', 'actions': ['Open', 'close']},
                          {'text': '{action} the {target_object} on the {target_location}', 'not_actions': ['open', 'close']}]}
    assert P.generate(spec, {'action': 'OPEN', 'target_object': 'drawer', 'target_location': 'top'}) == 'OPEN the top drawer'
    assert P.generate(spec, {'action': 'pick', 'target_object': 'cup', 'target_location': 'table'}) == 'pick the cup on the table'
    with pytest.raises(ValueError):
        P.generate({'templates': [{'text': '{action} {target_object}', 'actions': ['open']}]}, {'action': 'pick', 'target_object': 'cup'})
