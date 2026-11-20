"""The fleet seams (docs/fleet_seams.md): ``GET /robot/manifest``, typed ``goals`` on ``/ws/agent``, ``client_ref`` (idempotent keyed
start), ``busy: "refuse"``, the read-only ``WS /ws/events`` and the option ``goals_only`` (an understand-only run). FakeUA / TestClient as
in tests/test_run_manager_api.py; ``goals_only`` through the real AssistLoop (make_assist). Made-up robot, flat, places and objects
(zorbot, a galley and a den, zorbel / vumpa); no ROS, no LLM, no network, no benchmark data."""

import asyncio
import json
import threading
import types

import pytest

pytest.importorskip('fastapi')
pytest.importorskip('httpx')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from fakes import FakeLLM, FakeRobot, ScriptedPlanner, done, run_task, scrub, steps
from robot_agent.api import agent as agent_api
from robot_agent.api import event_stream, robot_info, run_start
from robot_agent.core import run_control
from robot_agent.core.options import REGISTRY
from robot_agent.core.planning.assist_loop import ASSIST_OPTIONS
from robot_agent.core.planning.base import WorldState
from robot_agent.interact.run_manager import reset_manager
from robot_agent.options import goals_only as G

SHELF, CRATE, TABLE = "shelf@galley", "crate@galley", "zorbik table@den"
ENV = {SHELF: {"aliases": ["shelf"], "loc": {"x": 1.0, "y": 1.0}, "surface": True},
       CRATE: {"aliases": ["crate"], "loc": {"x": 2.0, "y": 1.0}, "surface": True},
       TABLE: {"aliases": ["zorbik table"], "loc": {"x": 4.0, "y": 1.0}, "surface": True}}
GOAL = {"type": "on", "object": "zorbel", "from": SHELF, "to": CRATE}
MOTION = {"move", "pick", "place", "open_drawer", "close_drawer"}


# ── the API harness (as tests/test_run_manager_api.py) ─────────────────────────────────────────────────────────────────
class FakeUA:
    """ua.run(): start, one step, then waits until released or cancelled; records each prompt and its options."""

    def __init__(self):
        self.release = threading.Event()
        self.calls = []

    @property
    def prompts(self):
        return [p for p, _o in self.calls]

    async def run(self, prompt, **kw):
        self.calls.append((prompt, kw.get('options')))
        run_control.begin_run()
        yield {'event': 'start', 'prompt': prompt}
        yield {'event': 'step_start', 'step': 1}
        while not self.release.is_set():
            if run_control.cancel_requested():
                yield {'event': 'done', 'status': 'aborted', 'success': False}
                return
            await asyncio.sleep(0.01)
        yield {'event': 'done', 'status': 'success', 'success': True}

    run_direct = run


class View:
    """The bits of a RobotView the manifest reads."""
    manifest = types.SimpleNamespace(robot="zorbot", version="2.1", capabilities={"base": True, "arms": 1})
    plan_actions = {"Pick", "MoveTo", "Place"}


@pytest.fixture
def client(monkeypatch):
    reset_manager()
    run_control._CANCEL.clear()
    ua = FakeUA()
    monkeypatch.setattr(agent_api, 'current', lambda: types.SimpleNamespace(ua=ua))
    monkeypatch.setattr(robot_info, 'current', lambda: types.SimpleNamespace(robot_pkg='zorbot', location='flat_one'))
    import robot_agent.core.planning.robot as robot_mod
    import robot_agent.skill_configs as sc
    monkeypatch.setattr(robot_mod, 'robot_for', lambda pkg: View() if pkg == 'zorbot' else None)
    monkeypatch.setattr(sc, 'ENV', dict(ENV))
    app = FastAPI()
    for r in (agent_api.router, robot_info.router, event_stream.router):
        app.include_router(r)
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


def _closed(ws) -> bool:
    try:
        ws.receive_json()
    except WebSocketDisconnect:
        return True
    return False


def _start(ws, **msg):
    ws.send_json(msg)
    return _until(ws, lambda e: e['event'] == 'start')['run_id']


REF = {'plan_id': 'p7', 'goal_id': 'g1', 'attempt': 1, 'hash': 'h-aa'}


# ── (1) GET /robot/manifest ────────────────────────────────────────────────────────────────────────────────────────────
def test_manifest_has_the_spec_fields_of_the_running_robot_and_starts_nothing(client):
    m = client.get('/robot/manifest').json()
    assert m == {'robot': 'zorbot', 'version': '2.1', 'plan_actions': ['MoveTo', 'Pick', 'Place'],
                 'capabilities': {'base': True, 'arms': 1}, 'places': [SHELF, CRATE, TABLE], 'site': 'flat_one'}
    assert client.get('/runs').json() == {'active': None, 'runs': []}


def test_manifest_of_is_pure_and_an_unloadable_package_is_503(client, monkeypatch):
    assert robot_info.manifest_of(View(), {}, None)['places'] == [] and robot_info.manifest_of(View(), {}, None)['site'] == ''
    monkeypatch.setattr(robot_info, 'current', lambda: types.SimpleNamespace(robot_pkg='nopkg'))
    assert client.get('/robot/manifest').status_code == 503


# ── (2) goals on /ws/agent ─────────────────────────────────────────────────────────────────────────────────────────────
def test_goals_reach_the_run_options_and_no_prompt_is_written_from_them(client):
    with client.websocket_connect('/ws/agent') as ws:
        _start(ws, goals=[GOAL, 'not a goal'], options={'accept_goals': '1'})
        client.ua.release.set()
        _until(ws, lambda e: e['event'] == 'done')
    prompt, opts = client.ua.calls[0]
    assert opts == {'accept_goals': '1', 'goals': [GOAL]}
    assert prompt == f"on zorbel from {SHELF} to {CRATE}"           # goals.goal_text: the planner's own text of the goals


def test_a_prompt_given_with_goals_is_kept_and_no_goals_leave_the_options_alone(client):
    assert run_start.with_goals({'goals': [GOAL]}, 'put the zorbel away', None) == ('put the zorbel away', {'goals': [GOAL]})
    assert run_start.with_goals({}, 'x', {'a': 1}) == ('x', {'a': 1})
    assert run_start.with_goals({'goals': []}, '', None) == ('', None)
    assert run_start.with_goals({'goals': 'zorbel'}, '', None) == ('', None)


def test_the_goals_go_from_the_options_to_the_assist_loop_only(monkeypatch):
    """UnifiedAgent.run hands ``options['goals']`` to AssistLoop.run_blocking (the loop takes them with accept_goals); the plain loop
    gets no ``goals`` argument (its run_blocking has none)."""
    from robot_agent.core import unified_agent as U
    import robot_agent.core.planning.assist_loop as AL
    seen = []

    class Loop:
        cfg = {}

        def __init__(self, agent, emit=None, **kw):
            self.kw = kw

        def run_blocking(self, task, lang='en', plan_only=False, **kw):
            seen.append((task, kw, self.kw.get('accept_goals')))
            yield {'event': 'done', 'status': 'success'}

    monkeypatch.setattr(AL, 'AssistLoop', Loop)
    ua = U.UnifiedAgent(None, types.SimpleNamespace(_ros_node=None))

    async def drain():
        return [e async for e in ua.run('on zorbel', planner='sage', options={'accept_goals': '1', 'goals': [GOAL]})]
    assert [e['event'] for e in asyncio.run(drain())] == ['start', 'done']
    assert seen == [('on zorbel', {'goals': [GOAL]}, '1')]
    assert U._given_goals(False, {'goals': [GOAL]}) == {} and U._given_goals(True, None) == {} and U._given_goals(True, {'goals': []}) == {}


# ── (3) client_ref: idempotent keyed start ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize('ref, kind', [
    ({**REF}, 'duplicate'),                                    # same key, same hash
    ({**REF, 'attempt': 2}, 'duplicate'),                      # a later attempt of the same content: not a second run
    ({**REF, 'hash': 'h-bb'}, 'conflict'),                     # same key, other content
    ({**REF, 'attempt': 0}, 'stale'),                          # an older attempt than one seen
    ({**REF, 'goal_id': 'g2'}, 'start'),                       # another goal
    ({**REF, 'plan_id': 'p8'}, 'start'),                       # another plan
])
def test_verdict_table(ref, kind):
    run = object()
    assert run_start.verdict(ref, [({**REF}, run)])[0] == kind
    assert run_start.verdict(ref, [])[0] == 'start'


def test_verdict_compares_with_the_newest_run_of_the_key_and_ref_key_rejects_malformed_refs():
    old, new = object(), object()
    seen = [({**REF, 'attempt': 2, 'hash': 'h-bb'}, new), ({**REF}, old)]
    assert run_start.verdict({**REF, 'attempt': 2, 'hash': 'h-bb'}, seen) == ('duplicate', new)
    assert run_start.verdict({**REF, 'attempt': 1}, seen)[0] == 'stale'
    assert [run_start.ref_key(r) for r in (REF, {'plan_id': 'p'}, {'plan_id': '', 'goal_id': 'g'}, 'p7/g1', {**REF, 'attempt': 'x'})] == \
        [('p7', 'g1'), None, None, None, None]


def test_a_resent_ref_while_the_run_is_active_observes_it_without_a_second_run(client):
    with client.websocket_connect('/ws/agent') as hub:
        rid = _start(hub, prompt='on zorbel', client_ref=REF, busy='refuse')
        assert client.get(f'/runs/{rid}').json()['client_ref'] == REF
        with client.websocket_connect('/ws/agent') as again:
            again.send_json({'prompt': 'on zorbel', 'client_ref': REF, 'busy': 'refuse'})
            assert again.receive_json() == {'event': 'duplicate', 'run_id': rid, 'state': 'running'}
            att = again.receive_json()
            assert att['event'] == 'run_attached' and att['run_id'] == rid and 'injected' not in att
            client.ua.release.set()
            assert _until(again, lambda e: e['event'] == 'done')['run_id'] == rid
        _until(hub, lambda e: e['event'] == 'done')
    assert client.ua.prompts == ['on zorbel']
    assert [e['event'] for e in client.get(f'/runs/{rid}').json()['events']].count('run_injected') == 0


def test_a_resent_ref_after_the_run_ended_answers_duplicate_and_starts_nothing(client):
    with client.websocket_connect('/ws/agent') as hub:
        rid = _start(hub, prompt='on zorbel', client_ref=REF)
        client.ua.release.set()
        _until(hub, lambda e: e['event'] == 'done')
    with client.websocket_connect('/ws/agent') as again:
        again.send_json({'prompt': 'on zorbel', 'client_ref': REF})
        assert again.receive_json() == {'event': 'duplicate', 'run_id': rid, 'state': 'done'}
        assert _closed(again)
    assert client.ua.prompts == ['on zorbel'] and len(client.get('/runs').json()['runs']) == 1


@pytest.mark.parametrize('ref, reason', [({**REF, 'hash': 'h-bb'}, 'conflict'), ({**REF, 'attempt': 0}, 'stale')])
def test_a_conflicting_or_stale_ref_is_refused(client, ref, reason):
    with client.websocket_connect('/ws/agent') as hub:
        rid = _start(hub, prompt='on zorbel', client_ref=REF)
        with client.websocket_connect('/ws/agent') as again:
            again.send_json({'prompt': 'on vumpa', 'client_ref': ref})
            assert again.receive_json() == {'event': 'refused', 'reason': reason, 'run_id': rid}
            assert _closed(again)
        client.ua.release.set()
        _until(hub, lambda e: e['event'] == 'done')
    assert client.ua.prompts == ['on zorbel']


def test_a_malformed_ref_is_refused_and_starts_nothing(client):
    with client.websocket_connect('/ws/agent') as ws:
        ws.send_json({'prompt': 'on zorbel', 'client_ref': {'plan_id': 'p7'}})
        assert ws.receive_json() == {'event': 'refused', 'reason': 'bad_ref'}
    assert client.ua.prompts == [] and client.get('/runs').json()['runs'] == []


def test_a_run_without_a_ref_has_no_client_ref_key_off(client):
    with client.websocket_connect('/ws/agent') as ws:
        rid = _start(ws, prompt='bring the zorbel')
        assert 'client_ref' not in client.get(f'/runs/{rid}').json()
        assert 'client_ref' not in client.get('/runs').json()['runs'][0]
        client.ua.release.set()
        _until(ws, lambda e: e['event'] == 'done')


def test_the_ref_is_in_the_run_record_header_and_absent_without_one(tmp_path, monkeypatch):
    from robot_agent.observe import record as R
    monkeypatch.setenv('ROBOT_AGENT_RECORD', '1')
    with_ref = R.open_record('r-ref', directory=tmp_path, client_ref=REF)
    plain = R.open_record('r-plain', directory=tmp_path)
    for rid in ('r-ref', 'r-plain'):
        R.close_record(rid)
    head = lambda rec: next(r for r in R.read_rows(rec.path) if r['kind'] == 'header')       # noqa: E731
    snap = lambda rec: next(r for r in R.read_rows(rec.path) if r['kind'] == 'snapshot')['record']   # noqa: E731
    assert head(with_ref)['client_ref'] == REF and snap(with_ref)['client_ref'] == REF
    assert 'client_ref' not in head(plain) and 'client_ref' not in snap(plain)


# ── (4) busy: "refuse" ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_busy_refuse_answers_with_the_active_run_and_injects_nothing(client):
    with client.websocket_connect('/ws/agent') as owner:
        rid = _start(owner, prompt='bring the zorbel')
        with client.websocket_connect('/ws/agent') as hub:
            hub.send_json({'prompt': 'stop', 'busy': 'refuse', 'role': 'owner'})
            assert hub.receive_json() == {'event': 'refused', 'reason': 'busy', 'run_id': rid}
            assert _closed(hub)
        run = client.get(f'/runs/{rid}?events=true').json()
        assert run['state'] == 'running' and not run['cancel_requested'] and run['pending'] == []
        assert not [e for e in run['events'] if e['event'] in ('run_injected', 'run_input')]
        client.ua.release.set()
        assert _until(owner, lambda e: e['event'] == 'done')['status'] == 'success'
    assert client.ua.prompts == ['bring the zorbel']


def test_without_busy_the_second_client_still_observes_and_its_text_goes_in_off(client):
    """Today's behaviour for people stays the default (pinned by tests/test_run_manager_api.py: the observer's text is an event)."""
    with client.websocket_connect('/ws/agent') as owner:
        rid = _start(owner, prompt='bring the zorbel')
        with client.websocket_connect('/ws/agent') as other:
            other.send_json({'prompt': 'also bring the vumpa'})
            att = other.receive_json()
            assert att['event'] == 'run_attached' and att['run_id'] == rid and att['injected']['action'] == 'queued'
        client.ua.release.set()
        _until(owner, lambda e: e['event'] == 'done')


def test_busy_refuse_with_no_run_starts_as_usual(client):
    with client.websocket_connect('/ws/agent') as ws:
        rid = _start(ws, prompt='bring the zorbel', busy='refuse')
        client.ua.release.set()
        assert _until(ws, lambda e: e['event'] == 'done')['run_id'] == rid


# ── (6) WS /ws/events ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_ws_events_never_starts_a_run_and_refuses_a_prompt(client):
    with client.websocket_connect('/ws/events') as ev:
        assert ev.receive_json() == {'event': 'events_open', 'active': None}
        ev.send_json({'prompt': 'bring the zorbel'})
        assert ev.receive_json() == {'event': 'refused', 'reason': 'read_only'}
    assert client.ua.prompts == [] and client.get('/runs').json() == {'active': None, 'runs': []}


def test_ws_events_streams_runs_that_start_later_and_its_close_cancels_nothing(client):
    with client.websocket_connect('/ws/events') as ev:
        assert ev.receive_json()['active'] is None
        with client.websocket_connect('/ws/agent') as ws:
            rid = _start(ws, prompt='bring the zorbel')
            assert _until(ev, lambda e: e['event'] == 'run_state')['run_id'] == rid
            assert _until(ev, lambda e: e['event'] == 'step_start')['run_id'] == rid
            ev.close()
            assert client.get(f'/runs/{rid}').json()['state'] == 'running'
            client.ua.release.set()
            assert _until(ws, lambda e: e['event'] == 'done')['status'] == 'success'
    assert client.ua.prompts == ['bring the zorbel']


def test_ws_events_joins_the_active_run(client):
    with client.websocket_connect('/ws/agent') as ws:
        rid = _start(ws, prompt='bring the zorbel')
        with client.websocket_connect('/ws/events') as ev:
            assert ev.receive_json() == {'event': 'events_open', 'active': rid}
            client.ua.release.set()
            assert _until(ev, lambda e: e['event'] == 'done')['run_id'] == rid
        _until(ws, lambda e: e['event'] == 'done')


def test_follow_unhooks_everything_it_added():
    from robot_agent.interact.run_manager import RunManager
    mgr, got = RunManager(), []
    stop = event_stream.follow(mgr, got.append)
    run = mgr.start(goal='x').run
    run.publish({'event': 'tick'})
    mgr.finish(run.run_id)
    assert [e['event'] for e in got] == ['run_state', 'tick', 'run_state']
    stop()
    assert mgr.on_start == [] and mgr.on_finish == []
    mgr.start(goal='y').run.publish({'event': 'tick'})
    assert len(got) == 3


# ── (5) option goals_only, through the real AssistLoop ─────────────────────────────────────────────────────────────────
@pytest.fixture
def env():
    return dict(ENV)


def read(*goals):
    return {"status": "clear", "reason": "", "question": "", "goals": list(goals)}


SAID = {"type": "on", "object": "zorbel", "from": "shelf", "to": "crate"}
PLAN = steps(f"MoveTo {SHELF}", "Find zorbel", "Pick zorbel", f"MoveTo {CRATE}", f"Place {CRATE}")


@pytest.fixture
def run_loop(make_assist, tmp_path, agent_state):
    n = iter(range(100))

    def run(task="Put the zorbel from the shelf on the crate.", said=SAID, answers=(), plan_only=False, rank=None, **ov):
        agent_state.world = WorldState()
        robot = FakeRobot(ENV, {"zorbel": SHELF, "vumpa": TABLE}, start=SHELF, answers=list(answers))
        script = [(r"Rank the places", {"places": [rank]})] if rank else []         # the engine's ranked-search prompt (search.RANK_PROMPT)
        llm, planner = FakeLLM(*script, default=read(said)), ScriptedPlanner(PLAN)
        loop = make_assist(robot, llm, planner, search_legs=1, ask_help=False, detector_miss_rate=0,
                           memory_path=str(tmp_path / f"memory_{next(n)}.json"), **ov)
        return loop, robot, llm, planner, run_task(loop, task, plan_only=plan_only, max_events=3000)
    return run


def _moves(robot):
    return [t for t in robot.trace if t[0] in MOTION]


def test_registered_off_by_default_and_a_run_option(run_loop):
    assert "goals_only" in ASSIST_OPTIONS and REGISTRY.get("goals_only").key("enabled").default is False
    loop, robot, _llm, planner, evs = run_loop()
    assert not loop.options.option("goals_only") and done(evs)["status"] == "success" and planner.calls


def test_off_is_byte_identical_to_the_option_set_to_zero_off(run_loop):
    def run(**kw):
        loop, robot, llm, planner, evs = run_loop(**kw)
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, [c[0] for c in llm.calls], planner.calls, loop.lab_log
    assert run() == run(goals_only="0")


def test_on_the_run_ends_understood_with_the_goals_and_nothing_is_planned_or_moved(run_loop):
    loop, robot, llm, planner, evs = run_loop(goals_only="1")
    d = done(evs)
    assert d["status"] == "understood" and d["goals"] == [{**d["goals"][0], "object": "zorbel", "from": SHELF, "to": CRATE}]
    assert planner.calls == [] and _moves(robot) == [] and not any(e["event"] == "plan" for e in evs)
    u = [e for e in evs if e["event"] == "understand"][-1]
    assert [(g["object"], g["from"], g["to"]) for g in u["goals"]] == [(g["object"], g["from"], g["to"]) for g in d["goals"]]
    assert [e for e in loop.lab_log if e["flag"] == "goals_only"][0]["goals"] == 1
    assert "zorbel" in str(d["say"]) and "failed" not in str(d["say"]).lower()      # the read-back sentence, never "Task failed"


def test_on_with_the_callers_goals_no_model_call_and_no_plan(make_assist, agent_state):
    """accept_goals + goals_only: the caller's goals are grounded and handed back; the loop asks no model and plans nothing."""
    agent_state.world = WorldState()
    robot, llm, pl = FakeRobot(ENV, {"zorbel": SHELF}, start=SHELF), FakeLLM(), ScriptedPlanner(PLAN)     # any model call raises
    lp = make_assist(robot, llm, pl, goals_only="1", accept_goals="1")
    evs = list(lp.run_blocking("on zorbel", goals=[GOAL]))
    d = done(evs)
    assert d["status"] == "understood" and [(g["object"], g["to"]) for g in d["goals"]] == [("zorbel", CRATE)]
    assert llm.calls == [] and pl.calls == [] and _moves(robot) == []


def test_the_read_back_stays_the_loops_own_confirm_task_asks_first(run_loop):
    loop, robot, llm, planner, evs = run_loop(goals_only="1", confirm_task=True, answers=["yes"])
    assert robot.asked and any(e["event"] == "clarify" and e.get("kind") == "confirm" for e in evs)
    assert done(evs)["status"] == "understood" and planner.calls == [] and _moves(robot) == []


def test_plan_only_wins_and_the_option_does_nothing_empty(run_loop):
    def run(**kw):
        loop, robot, llm, planner, evs = run_loop(plan_only=True, **kw)
        return json.dumps(scrub(evs), sort_keys=True, default=str), robot.trace, planner.calls, loop.lab_log
    on, off = run(goals_only="1"), run()
    assert on == off and json.loads(on[0])[-1]["status"] == "planned"


def test_regression_an_understand_call_never_guesses_a_source_the_person_did_not_say(run_loop):
    """The class: a fleet manager asks one robot to understand a request; the goals it gets back go to any robot. With the engine's ranked
    first place on (fetch_guard), the plan phase's search set-up would fill an unsaid ``from`` from THIS robot's belief (one model call).
    goals_only reports the goals as understood: no ranking call, no invented source, no plan, no motion."""
    unsaid = {"type": "on", "object": "zorbel", "from": "", "to": "crate"}
    kw = dict(task="Put the zorbel on the crate.", said=unsaid, fetch_guard="1", rank=TABLE)
    _l, _r, off_llm, _p, off = run_loop(**kw)
    assert len(off_llm.calls) == 2 and next(e for e in off if e["event"] == "search")["ranked"] == [TABLE]    # off: the ranking call is made
    loop, robot, llm, planner, evs = run_loop(goals_only="1", **kw)
    d = done(evs)
    assert d["status"] == "understood" and d["goals"][0]["from"] in ("", None) and d["goals"][0]["to"] == CRATE
    assert len(llm.calls) == 1 and planner.calls == [] and _moves(robot) == []


def test_the_goals_are_kept_as_handed_over_at_the_first_rank_ask():
    """A source the search set-up fills after the first ``rank_first`` ask is not reported (the snapshot); no ask: the run's goals."""
    class Svc:
        labs, said = [], None

        def lab(self, flag, **kw):
            self.labs.append(kw)

        def readback(self, run, goals):
            return "I will " + str(goals[0].get("object") or goals[0].get("to"))

        def finish(self, run, status, **kw):
            return {"event": "done", "status": status, **kw}

    opt = G.GoalsOnly({"enabled": True}, Svc())
    run = types.SimpleNamespace(goals=[{"type": "on", "object": "vumpa", "from": "", "to": CRATE}])
    assert opt.rank_first(run, "vumpa") is False and opt.runs_goals(run) is True
    run.goals[0]["from"] = TABLE                                 # what the search set-up may write after the ask
    gen = opt.on_run_goals(run)
    ev = next(gen)
    assert ev == {"event": "done", "status": "understood", "goals": [{"type": "on", "object": "vumpa", "from": "", "to": CRATE}],
                  "say": "I will vumpa"}
    with pytest.raises(StopIteration) as stop:
        next(gen)
    assert stop.value.value == G.STOP and opt.svc.labs == [{"goals": 1}]
    bare = types.SimpleNamespace(goals=[{"type": "go", "to": TABLE}])
    assert next(G.GoalsOnly({}, Svc()).on_run_goals(bare))["goals"] == [{"type": "go", "to": TABLE}]


def test_reported_is_pure():
    assert G.reported([{"a": 1}], None) == [{"a": 1}] and G.reported([{"a": 1}], [{"b": 2}]) == [{"b": 2}]
    assert G.reported(None, None) == []
