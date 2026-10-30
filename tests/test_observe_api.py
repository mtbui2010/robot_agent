"""API side of observability: /runs/{id}/record + /metrics, the /ws/agent event
schema, response models that keep the JSON as it was, one POST /skills."""

import threading
import types

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')
from fastapi import FastAPI
from fastapi.testclient import TestClient

from robot_agent.api import agent as agent_api
from robot_agent.api import configs as configs_api
from robot_agent.api import events as events_api
from robot_agent.api import skills as skills_api
from robot_agent.core import run_control
from robot_agent.core.planning.base import WorldState
from robot_agent.interact.run_manager import reset_manager
from robot_agent.observe import record as R

SKILLS = [{'name': 'move', 'type': 'internal', 'description': 'go', 'module_path': 'm', 'func_name': 'move',
           'url': '', 'timeout': 30.0, 'method': 'POST', 'headers': {}, 'plan': '', 'aliases': ['go']},
          {'name': 'tidy', 'type': 'plan', 'description': '', 'module_path': '', 'func_name': '', 'url': '',
           'timeout': None, 'method': 'POST', 'headers': {}, 'plan': 'move::desk', 'aliases': []}]


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    reset_manager()
    run_control._CANCEL.clear()
    world = WorldState()
    world.update_from_dict({'arrived': 'desk', 'holding': 'cup'})
    st = types.SimpleNamespace(
        world=world, save_world=lambda: None, common_dir=tmp_path, robot_pkg='fakebot',
        sr=types.SimpleNamespace(all=lambda: [dict(s) for s in SKILLS], lock=threading.RLock()),
        cm=types.SimpleNamespace(get_all=lambda: {'ENV': {'desk': {'loc': {'x': 1}}}}),
        dm=types.SimpleNamespace(_ros_node=None))
    import robot_agent.state as state_mod
    for m in (agent_api, skills_api, configs_api, state_mod):
        monkeypatch.setattr(m, 'current', lambda: st)
    monkeypatch.setattr(configs_api, 'PROXY_REGISTRY', {})
    monkeypatch.setenv('ROBOT_AGENT_HOST', 'test')
    monkeypatch.delenv('ROBOT_AGENT_DATA_DIR', raising=False)
    yield st
    reset_manager()


@pytest.fixture
def client(app_state):
    app = FastAPI()
    # the order of create_app(): skills before agent
    for r in (skills_api.router, configs_api.router, agent_api.router, events_api.router):
        app.include_router(r)
    with TestClient(app) as c:
        yield c


def _routes(routes):
    for r in routes:
        inner = getattr(r, 'original_router', None)       # FastAPI >= 0.14x wraps included routers
        if inner is not None:
            yield from _routes(inner.routes)
        else:
            yield r


def test_post_skills_is_registered_once(client):
    routes = [r for r in _routes(client.app.routes) if hasattr(r, 'methods')]
    pairs = [(r.path, m) for r in routes for m in r.methods]
    assert pairs.count(('/skills', 'POST')) == 1
    assert pairs.count(('/skills/{name}', 'DELETE')) == 1
    # the one left is skills.py's (plan skills, aliases)
    post = next(r for r in routes if r.path == '/skills' and 'POST' in r.methods)
    assert post.endpoint is skills_api.add_skill


def test_response_models_keep_the_json(client, app_state):
    assert client.get('/skills').json() == SKILLS
    assert client.get('/agent/world').json() == app_state.world.to_dict()
    assert client.get('/skill-configs').json() == {'ENV': {'desk': {'loc': {'x': 1}}}}
    mgr = run_control.manager()
    run = mgr.start(goal='tidy up', user_id='alice').run
    assert client.get('/runs').json() == {'active': run.run_id, 'runs': [run.to_dict()]}
    got = client.get(f'/runs/{run.run_id}').json()
    assert got == run.to_dict(events=True)
    res = client.post(f'/runs/{run.run_id}/pause', json={'user_id': 'alice'}).json()
    assert res == {'run_id': run.run_id, 'action': 'paused', 'ok': True}
    # OpenAPI now names the shapes
    spec = client.get('/openapi.json').json()
    ok = spec['paths']['/runs']['get']['responses']['200']['content']['application/json']['schema']
    assert ok == {'$ref': '#/components/schemas/RunList'}
    assert 'SkillInfo' in spec['components']['schemas']


def test_events_schema_is_versioned_and_complete(client):
    s = client.get('/schema/events').json()
    assert s['version'] == events_api.EVENT_SCHEMA_VERSION
    assert s['discriminator']['propertyName'] == 'event'
    for name in ('task_start', 'plan', 'step_start', 'step_verify', 'step_done', 'replan', 'done', 'error',
                 'step_log', 'run_state', 'run_input', 'run_refused', 'run_attached', 'clarify', 'gate'):
        assert name in s['events'] and name in s['discriminator']['mapping']
    sd = s['$defs']['StepDoneEvent']['properties']
    assert {'index', 'step', 'status', 'result', 'run_id', 'say', 'world'} <= set(sd)
    # extra fields pass; the generic model takes unknown events
    ev = events_api.parse_event({'event': 'step_done', 'index': 2, 'status': 'success', 'new_field': 1})
    assert ev.index == 2 and ev.model_dump()['new_field'] == 1
    assert type(events_api.parse_event({'event': 'later'})) is events_api.AgentEvent


def test_record_and_metrics_endpoints(client, app_state, tmp_path):
    mgr = run_control.manager()
    run = mgr.start(goal='tidy up', user_id='alice').run
    rid = run.run_id
    assert R.get_open(rid) is not None              # data dir = common_dir (no ROBOT_AGENT_DATA_DIR)
    run.publish({'event': 'step_done', 'index': 0, 'status': 'success', 'world': {'holding': 'cup'}})
    with R.bound(R.get_open(rid)):
        from robot_agent.observe.llm import RecordingLLM
        RecordingLLM(types.SimpleNamespace(chat=lambda **kw: 'ok')).chat(prompt='hi')
    live = client.get(f'/runs/{rid}/metrics').json()
    assert live['live'] is True and live['llm_calls'] == 1
    rec = client.get(f'/runs/{rid}/record', params={'kinds': 'llm'}).json()
    assert rec['live'] is True and [r['kind'] for r in rec['rows']] == ['llm']
    assert rec['rows'][0]['request'] == {'method': 'chat', 'prompt': 'hi'} and rec['rows'][0]['response'] == 'ok'
    run.publish({'event': 'done', 'status': 'success'})
    mgr.finish(rid, ok=True, outcome='success')
    m = client.get(f'/runs/{rid}/metrics').json()
    assert m['live'] is False and m['status'] == 'success' and m['llm_calls'] == 1
    assert m['verified_success'] is False                   # no step_verify
    assert (tmp_path / 'run_records' / 'test' / rid / 'record.jsonl').is_file()
    assert (tmp_path / 'run_records' / 'test' / 'metrics.jsonl').is_file()
    assert client.get('/runs/nope/record').status_code == 404
    assert client.get('/runs/nope/metrics').status_code == 404
