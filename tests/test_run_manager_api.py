"""/ws/agent + /runs with a fake agent (no ROS, no LLM)."""

import asyncio
import threading
import types

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')          # starlette's TestClient needs it
from fastapi import FastAPI
from fastapi.testclient import TestClient

from robot_agent.api import agent as agent_api
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager


class FakeUA:
    """ua.run(): yields start, then one step per tick until released or
    cancelled, then done — with the same cooperative points as a plan loop."""

    def __init__(self):
        self.release = threading.Event()
        self.prompts = []

    async def run(self, prompt, **kw):
        self.prompts.append(prompt)
        run_control.begin_run()
        yield {'event': 'start', 'prompt': prompt}
        step = 0
        while not self.release.is_set():
            run_control.wait_if_paused(0.01)
            if run_control.cancel_requested():
                yield {'event': 'done', 'status': 'aborted', 'success': False, 'msg': 'Cancelled by user'}
                return
            step += 1
            if step == 1:
                yield {'event': 'step_start', 'step': 1}
            await asyncio.sleep(0.01)
        yield {'event': 'done', 'success': True}

    run_direct = run


@pytest.fixture
def client(monkeypatch):
    reset_manager()
    run_control._CANCEL.clear()
    ua = FakeUA()
    monkeypatch.setattr(agent_api, 'current', lambda: types.SimpleNamespace(ua=ua))
    app = FastAPI()
    app.include_router(agent_api.router)
    with TestClient(app) as c:
        c.ua = ua
        yield c
    ua.release.set()
    reset_manager()


def _until(ws, pred, limit=50):
    for _ in range(limit):
        ev = ws.receive_json()
        if pred(ev):
            return ev
    raise AssertionError('event not seen')


def test_events_carry_run_id_and_runs_endpoints(client):
    with client.websocket_connect('/ws/agent') as ws:
        ws.send_json({'prompt': 'bring the cup', 'user_id': 'alice'})
        start = ws.receive_json()
        assert start['event'] == 'start' and start['prompt'] == 'bring the cup'
        rid = start['run_id']
        assert _until(ws, lambda e: e['event'] == 'step_start')['run_id'] == rid

        runs = client.get('/runs').json()
        assert runs['active'] == rid and runs['runs'][0]['state'] == 'running'
        assert client.post(f'/runs/{rid}/pause', json={'role': 'guest'}).status_code == 403
        assert client.post(f'/runs/{rid}/pause').json()['action'] == 'paused'
        assert client.get(f'/runs/{rid}').json()['state'] == 'paused'
        assert _until(ws, lambda e: e['event'] == 'run_state')['state'] == 'paused'
        assert client.post(f'/runs/{rid}/resume', json={'user_id': 'alice'}).json()['action'] == 'resumed'
        assert client.post(f'/runs/{rid}/cancel').json()['action'] == 'cancelled'
        done = _until(ws, lambda e: e['event'] == 'done')
        assert done['status'] == 'aborted' and done['run_id'] == rid

    run = client.get(f'/runs/{rid}').json()
    assert run['state'] == 'done' and run['outcome'] == 'aborted'
    assert any(e['event'] == 'step_start' for e in run['events'])
    assert client.get('/runs/nope').status_code == 404
    assert client.post('/runs/nope/cancel').status_code == 404
    assert client.post(f'/runs/{rid}/cancel').status_code == 409


def test_second_client_observes_and_injects(client):
    with client.websocket_connect('/ws/agent') as owner:
        owner.send_json({'prompt': 'bring the cup', 'user_id': 'alice'})
        rid = owner.receive_json()['run_id']
        with client.websocket_connect('/ws/agent') as other:
            other.send_json({'prompt': 'also bring water', 'user_id': 'bob', 'role': 'member'})
            att = other.receive_json()
            assert att['event'] == 'run_attached' and att['run_id'] == rid and att['observer']
            # a member cannot change the owner's run; told why
            assert att['injected']['action'] == 'refused' and att['injected']['intent'] == 'add'
            assert 'member' in att['injected']['reason']
            assert client.ua.prompts == ['bring the cup']          # no parallel run

            other.send_json({'text': 'stop'})
            res = _until(other, lambda e: e['event'] == 'run_injected')
            assert res['action'] == 'refused' and 'member' in res['reason']   # owner's run

            owner.send_json({'text': 'stop'})
            assert _until(owner, lambda e: e['event'] == 'run_injected')['action'] == 'cancelled'
            done = _until(owner, lambda e: e['event'] == 'done')
            assert done['status'] == 'aborted'
            # the observer sees the same run end
            assert _until(other, lambda e: e['event'] == 'done')['run_id'] == rid
    assert client.get(f'/runs/{rid}').json()['pending'] == []


def test_same_role_second_client_queues(client):
    with client.websocket_connect('/ws/agent') as owner:
        owner.send_json({'prompt': 'bring the cup'})          # no role: owner, as before
        rid = owner.receive_json()['run_id']
        with client.websocket_connect('/ws/agent') as other:
            other.send_json({'prompt': 'also bring water'})
            att = other.receive_json()
            assert att['injected']['action'] == 'queued' and att['injected']['intent'] == 'add'
        # the observer leaving does not cancel the run
        assert client.get(f'/runs/{rid}').json()['state'] == 'running'
        assert client.get(f'/runs/{rid}').json()['pending'][0]['text'] == 'also bring water'
        client.ua.release.set()
        assert _until(owner, lambda e: e['event'] == 'done')['success'] is True


def test_normal_finish(client):
    with client.websocket_connect('/ws/agent') as ws:
        ws.send_json({'prompt': 'x'})
        rid = ws.receive_json()['run_id']
        client.ua.release.set()
        assert _until(ws, lambda e: e['event'] == 'done')['success'] is True
    r = client.get(f'/runs/{rid}').json()
    assert r['state'] == 'done' and r['outcome'] == 'success'
    assert client.get('/runs').json()['active'] is None
