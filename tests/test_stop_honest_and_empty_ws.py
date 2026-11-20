"""Two small defects found in a multi-robot review (2026-11-20). (1) ``request_cancel`` reported ok even when stopping a device raised: a hub that
reads the answer must not take a partial stop for a clean one. (2) Opening ``/ws/agent`` with an empty prompt and no run to watch STARTED a run
(a read must not start a run, robotapp rule 6). Made-up node and fake agent, no ROS, no LLM."""
import types

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')
from fastapi import FastAPI
from fastapi.testclient import TestClient

from robot_agent.api import agent as agent_api
from robot_agent.core import run_control
from robot_agent.interact.run_manager import reset_manager


class Node:
    """The bits of CustomNode a stop uses: ``cancel_all`` answers what it cancelled, or raises."""

    def __init__(self, cancelled=(), boom=None):
        self.cancelled, self.boom = list(cancelled), boom

    def cancel_all(self):
        if self.boom:
            raise RuntimeError(self.boom)
        return {'cancelled': list(self.cancelled)}

    def clear_cancel(self):
        pass


@pytest.fixture(autouse=True)
def _fresh():
    reset_manager()
    yield
    reset_manager()


def test_a_clean_stop_is_ok_and_names_what_it_cancelled():
    out = run_control.request_cancel(Node(['arm_movej']))
    assert out['ok'] is True and out['cancelled'] == ['arm_movej'] and out['errors'] == {}


def test_a_stop_with_nothing_in_flight_is_still_ok_empty():
    out = run_control.request_cancel(Node([]))
    assert out['ok'] is True and out['cancelled'] == []            # idle is no error: the caller reads the list


def test_a_stop_that_raised_is_not_ok():
    out = run_control.request_cancel(Node(boom='controller did not answer'))
    assert out['ok'] is False and 'node' in out['errors'] and 'controller' in out['errors']['node']
    assert run_control.cancel_requested()                            # the process flag was set all the same: the stop still blocks commands


class FakeUA:
    def __init__(self):
        self.prompts = []

    async def run(self, prompt, **kw):
        self.prompts.append(prompt)
        yield {'event': 'start', 'prompt': prompt}
        yield {'event': 'done', 'status': 'success', 'success': True}


@pytest.fixture
def client(monkeypatch):
    ua = FakeUA()
    monkeypatch.setattr(agent_api, 'current', lambda: types.SimpleNamespace(ua=ua))
    app = FastAPI()
    app.include_router(agent_api.router)
    with TestClient(app) as c:
        c.ua = ua
        yield c


@pytest.mark.parametrize('prompt', ['', '   ', None])
def test_an_empty_prompt_with_no_run_starts_nothing(client, prompt):
    with client.websocket_connect('/ws/agent') as ws:
        ws.send_json({'prompt': prompt} if prompt is not None else {})
        ev = ws.receive_json()
    assert ev['event'] == 'error' and 'no run was started' in ev['msg']
    assert client.ua.prompts == []                                   # the agent never ran


def test_a_real_prompt_still_starts_a_run_empty(client):
    with client.websocket_connect('/ws/agent') as ws:
        ws.send_json({'prompt': 'bring the cup'})
        assert ws.receive_json()['event'] == 'start'
    assert client.ua.prompts == ['bring the cup']
