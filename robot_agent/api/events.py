"""The ``/ws/agent`` event stream, as a versioned JSON schema.

Every message the agent WebSocket sends is one JSON object with an ``event``
name. This module describes the events that exist today with pydantic models
(the fields each one carries now; nothing was renamed), and serves them as one
JSON schema at ``GET /schema/events`` so a client (robotapp) can generate its
types instead of guessing.

The models are descriptive, not a filter: every model allows extra fields, and
the agent does not validate what it sends against them. A field marked optional
is one that some emitters leave out (the open-loop path and the planning loops
do not send the same step fields: ``step`` / ``task`` vs ``index`` / ``action``).

Versioning (``EVENT_SCHEMA_VERSION``, SemVer): adding an event or an optional
field is a minor bump; renaming / removing one, or making a field required, is a
major bump and a CHANGELOG entry.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

EVENT_SCHEMA_VERSION = '1.0.0'

router = APIRouter()


class AgentEvent(BaseModel):
    """Fields any event may carry."""
    model_config = ConfigDict(extra='allow')

    event: str
    run_id: Optional[str] = Field(None, description='the run (RunManager) the event belongs to; the planning '
                                                    "loops' task_start / done carry their own task-run id")
    say: Optional[str] = Field(None, description='text the dashboard speaks')
    say_lang: Optional[str] = None
    world: Optional[dict] = Field(None, description='symbolic world belief after the event (WorldState.to_dict)')
    origin: Optional[str] = Field(None, description="'run_manager' on run-manager notices")


def _ev(name: str, doc: str, **fields: Any) -> type[AgentEvent]:
    """An event model: ``event`` fixed to ``name``, ``fields`` = {field: (type, description)}."""
    ann = {'event': Literal[name]}
    ns: dict[str, Any] = {'__doc__': doc, '__module__': __name__}
    for k, (tp, desc) in fields.items():
        ann[k] = Optional[tp]
        ns[k] = Field(None, description=desc)
    ns['__annotations__'] = ann
    cls_name = ''.join(p.capitalize() for p in name.split('_')) + 'Event'
    return type(cls_name, (AgentEvent,), ns)


# --- run lifecycle / API layer ------------------------------------------------
Start = _ev('start', 'The executive started (open-loop and direct paths).', prompt=(str, 'the command'))
LogDir = _ev('log_dir', 'Dataset logging is on; where it goes.', log_dir=(str, 'directory'))
Status = _ev('status', 'A progress line.', msg=(str, 'text'))
Translated = _ev('translated', 'The command translated to English.', text=(str, 'translation'))
Error = _ev('error', 'The run failed with an exception.', msg=(str, 'error'), trace=(str, 'traceback'))
Stopped = _ev('stopped', 'Open-loop run stopped early (failure, cancel, a step ended the plan).',
              msg=(str, 'why'))
Done = _ev('done', 'The run is over.',
           status=(str, 'success | failed | aborted | refused | planned (planning loops)'),
           success=(Any, 'open-loop: the last step result (bool)'), msg=(str, 'why, on failure'))
WorldEv = _ev('world', 'Robot State refresh (open-loop / direct).')

# --- planning ----------------------------------------------------------------
TaskStart = _ev('task_start', 'A planning loop started.', task=(str, 'the task (English)'))
Understand = _ev('understand', 'SAGE assist: what the task was understood as.',
                 status=(str, 'clear | ambiguous | unsafe | infeasible'), reason=(str, ''),
                 goals=(list, 'grounded goals {type, object, from, to}'), question=(str, 'what to ask, if any'))
Clarify = _ev('clarify', 'SAGE assist: a question to the user.', question=(str, ''), round=(int, ''),
              kind=(str, "'confirm' for a read-back"))
Gate = _ev('gate', "SAGE assist: the checker's verdict on a plan.", round=(int, '0 first plan, 1 after edits'),
           problems=(list, 'findings (text)'), plan=(list, "'Action Object' lines"), edits=(list, 'edit notes'))
PlanStep = _ev('plan_step', 'Live planner progress (decompose / expand / refine).')
PlanRaw = _ev('plan_raw', 'Open-loop: the raw LLM plan.', plan=(str, ''))
Plan = _ev('plan', 'The plan.', steps=(list, 'planning loops: PlanStep dicts {action, object, ...}'),
           plan_meta=(dict, 'planner metrics'), warnings=(list, ''), plan_text=(str, 'skill::inputs lines'),
           plan=(str, 'open-loop / direct: the command text'))
Warning = _ev('warning', 'A plan warning.', msg=(str, ''))
Replan = _ev('replan', 'A new plan suffix after a failure.', at_index=(int, ''), reason=(str, ''),
             problems=(list, ''), plan=(list, ''))
Recover = _ev('recover', 'SAGE assist: a recovery action.', at_index=(int, ''),
              kind=(str, 'dropped | retry | asked ...'), msg=(str, ''))

# --- steps -------------------------------------------------------------------
StepStart = _ev('step_start', 'A step starts.', index=(int, 'planning loops: 0-based'),
                action=(str, ''), object=(str, ''), skill=(str, ''),
                step=(int, 'open-loop: 1-based'), total=(int, 'open-loop'), task=(str, 'open-loop: the step text'),
                quiet=(bool, 'not narrated'))
StepVerify = _ev('step_verify', 'Layered verification of a step.', index=(int, ''),
                 verifies=(list, '{layer, ok, detail, confidence, latency_ms}'))
StepDone = _ev('step_done', 'A step ended.', index=(int, ''), step=(int, ''),
               status=(str, 'success | failed'), result=(Any, 'the skill result'), reason=(str, ''),
               skill=(str, ''))
StepLog = _ev('step_log', 'Data a skill logged during a step (log_data).', step=(int, ''), data=(Any, ''),
              log_image=(str, 'base64 JPEG'), ts=(float, ''), dataset=(Any, ''), speak=(str, ''),
              speak_lang=(str, ''), speak_id=(str, ''), speak_rate=(float, ''), listen=(dict, ''),
              alarm=(dict, ''), qa_state=(dict, ''), choose=(dict, ''), choose_close=(Any, ''),
              log_image_reset=(bool, ''))

# --- run manager -------------------------------------------------------------
RunState = _ev('run_state', 'Run state changed.', state=(str, 'running | paused | cancelling | done | failed'),
               outcome=(str, ''), action=(str, ''), user_id=(str, ''))
RunInput = _ev('run_input', 'Someone said something during the run.', text=(str, ''), user_id=(str, ''),
               role=(str, 'owner | member | guest'), intent=(str, ''), intent_source=(str, ''))
RunRefused = _ev('run_refused', "A user's control was refused (role).", action=(str, ''), user_id=(str, ''),
                 role=(str, ''), reason=(str, ''))
RunInjected = _ev('run_injected', 'Reply to a text / cancel sent on the socket during a run.', text=(str, ''),
                  action=(str, ''), ok=(bool, ''), intent=(str, ''), intent_source=(str, ''), reason=(str, ''),
                  code=(str, ''), status=(dict, ''))
RunAttached = _ev('run_attached', 'This client observes a run already active.', observer=(bool, ''),
                  decision=(str, "'busy'"), run=(dict, 'the run'), injected=(dict, 'what its prompt did'))

EVENT_MODELS: list[type[AgentEvent]] = [
    Start, LogDir, Status, Translated, Error, Stopped, Done, WorldEv,
    TaskStart, Understand, Clarify, Gate, PlanStep, PlanRaw, Plan, Warning, Replan, Recover,
    StepStart, StepVerify, StepDone, StepLog,
    RunState, RunInput, RunRefused, RunInjected, RunAttached,
]
EVENT_NAMES = [m.model_fields['event'].annotation.__args__[0] for m in EVENT_MODELS]


def event_model(name: str) -> type[AgentEvent]:
    """The model of event ``name`` (the generic :class:`AgentEvent` for an unknown one)."""
    for n, m in zip(EVENT_NAMES, EVENT_MODELS):
        if n == name:
            return m
    return AgentEvent


def parse_event(d: dict) -> AgentEvent:
    return event_model(str(d.get('event', ''))).model_validate(d)


def events_schema() -> dict:
    """One JSON schema: ``oneOf`` the event models, discriminated by ``event``."""
    from typing import Annotated
    from pydantic import TypeAdapter
    union = Annotated[Union[tuple(EVENT_MODELS)], Field(discriminator='event')]   # type: ignore[valid-type]
    schema = TypeAdapter(union).json_schema(ref_template='#/$defs/{model}')
    schema.update({
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': f'robot_agent/ws-agent-events/{EVENT_SCHEMA_VERSION}',
        'title': '/ws/agent events',
        'version': EVENT_SCHEMA_VERSION,
        'description': (__doc__ or '').strip().split('\n\n')[0],
        'events': EVENT_NAMES,
    })
    return schema


@router.get('/schema/events')
def get_events_schema() -> dict:
    """Versioned JSON schema of the ``/ws/agent`` events."""
    return events_schema()
