"""Response models of the routes the dashboard uses (OpenAPI for robotapp).

They describe the JSON these routes already return; they do not change it.
Every model allows extra fields, and the routes use
``response_model_exclude_unset=True`` (:data:`OPEN`), so a key the handler did
not send is not added and a key the model does not list is not dropped.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

# route kwargs: keep the handler's keys exactly
OPEN = {'response_model_exclude_unset': True}


class _Open(BaseModel):
    model_config = ConfigDict(extra='allow')


# --- generic -----------------------------------------------------------------
class Ok(_Open):
    ok: bool
    error: Optional[str] = None


class OkName(Ok):
    name: Optional[str] = None


class OkCount(Ok):
    count: Optional[int] = None


class OkValue(Ok):
    value: Any = None


class OkText(Ok):
    text: Optional[str] = None


# --- robot -------------------------------------------------------------------
class RobotLanguages(_Open):
    robot: str
    language: str                 # the default language (manifest `language`)
    languages: list[str]          # the languages on at this site (a subset of `supported`), the default first; [] when the manifest names none
    declared: bool = True         # the manifest names a language at all; False: the dashboard offers only "detect"
    supported: list[str] = []     # what the manifest says it can talk in; `languages` is what this site has switched on (a subset)


class LanguagesIn(_Open):
    languages: list[str]          # the languages to switch on: a non-empty subset of `supported`


# --- teleop ------------------------------------------------------------------
class TeleopFunction(_Open):
    name: str
    label: str
    available: bool               # a fresh state has arrived (the function is running)
    state: Optional[dict] = None  # its last state (core/teleop.py): state, ready, gripper_open, speed, input_age_s, stalled
    age_s: Optional[float] = None


class TeleopList(_Open):
    functions: list[TeleopFunction]


class TeleopEnableIn(_Open):
    on: bool
    confirm: bool = False         # required for on=true: a person is next to the robot


class TeleopSpeedIn(_Open):
    scale: float                  # 0..1, a fraction of the function's top speed


# --- poses ---------------------------------------------------------------------
class Pose(_Open):
    id: str
    label: str
    plan: str                     # one or more steps, written like a shortcut button's plan


class PoseIn(_Open):
    label: str
    plan: str


class PoseUpdate(_Open):
    label: Optional[str] = None
    plan: Optional[str] = None


class PoseList(_Open):
    speed: float                  # the one speed every pose runs at, 0.05..1
    poses: list[Pose]
    defaults_missing: int = 0     # seed poses of the robot package that are not in the list


class PoseSpeed(_Open):
    speed: float


class PoseDefaults(_Open):
    added: int


class PoseRun(_Open):
    isdone: bool
    msg: str = ''
    plan: str = ''                # the plan that ran (with the speed applied)


# --- recorder ----------------------------------------------------------------
class RecorderStatus(_Open):
    available: bool               # a fresh state has arrived: robot_teleop's recorder is running
    state: Optional[dict] = None  # {state: idle|recording|saving, episode, prompt, elapsed_s, counts, last, error, ready, root}
    age_s: Optional[float] = None
    dir: Optional[str] = None
    teleop: Optional[str] = None  # the teleop function switched on / off with the recording
    fields: list[str] = []        # the prompt fields the form shows, in order


class RecorderPromptIn(_Open):
    fields: dict[str, str]


class RecorderPrompt(_Open):
    prompt: str


class RecorderStartIn(_Open):
    prompt: str
    meta: Optional[dict] = None


class RecorderStopIn(_Open):
    label: str                    # success | failure | discard


class RecordingList(_Open):
    dir: str
    episodes: list[dict]          # rows: id, day, episode, prompt, label, summary{...}, edit?, or {id, error}
    totals: dict                  # episodes, hours, success, failure, excluded


class RecordingDetail(_Open):
    id: str
    prompt: str = ''
    summary: dict = {}
    series: dict = {}             # t, joints (7 x N), gripper, gripper_cmd: down-sampled curves
    cameras: dict = {}            # name -> {frames, first_t}
    edit: Optional[dict] = None


class RecordingEditIn(_Open):
    trim_start_s: Optional[float] = None
    trim_end_s: Optional[float] = None
    label: Optional[str] = None   # success | failure: overrides the label given when recording
    exclude: Optional[bool] = None
    note: Optional[str] = None


class RecordingEdit(_Open):
    version: int = 1


class RecordingAutoCleanIn(_Open):
    ids: Optional[list[str]] = None   # only these episodes (default: all)
    overwrite: bool = False           # also replace an existing trim


class RecordingAutoClean(_Open):
    cleaned: int
    skipped: int
    rows: list[dict] = []


class RecordingExportIn(_Open):
    name: str
    fps: int = 30
    action: str = 'future_state'  # future_state | servo | ref
    horizon_s: float = 0.3
    labels: list[str] = ['success']


class RecordingExport(_Open):
    manifest: str
    episodes: int
    hours: float
    command: str = ''


# --- skills ------------------------------------------------------------------
class SkillInfo(_Open):
    name: str
    type: str
    description: Optional[str] = None
    module_path: Optional[str] = None
    func_name: Optional[str] = None
    url: Optional[str] = None
    timeout: Optional[float] = None
    method: Optional[str] = None
    headers: Optional[dict] = None
    plan: Optional[str] = None
    aliases: list[str] = []


class SkillStatus(_Open):
    ok: bool
    error: str = ''


class AgentValue(_Open):
    value: Any = None


# --- agent -------------------------------------------------------------------
class WorldStateOut(_Open):
    arrived: Optional[str] = None
    found: Optional[str] = None
    holding: Optional[str] = None
    opened: list[str] = []
    on: list[Any] = []
    holding_since: Optional[float] = None
    found_pose: Optional[Any] = None
    holding_pose: Optional[Any] = None
    # per field: {source: sensor | operator | belief | unknown, ts, ...}; opened also has containers /
    # unconfirmed (docs/guard.md)
    provenance: Optional[dict[str, Any]] = None


class CancelResult(_Open):
    ok: bool
    cancelled: list = []
    errors: dict = {}
    run_id: Optional[str] = None


class QaEndResult(_Open):
    ok: bool
    listens_released: Any = None


# --- runs --------------------------------------------------------------------
class RunInfo(_Open):
    run_id: str
    robot_id: Optional[str] = None
    user_id: Optional[str] = None
    role: Optional[str] = None
    state: str
    goal: Optional[str] = None
    created: Optional[float] = None
    updated: Optional[float] = None
    outcome: Optional[str] = None
    cancel_requested: Optional[bool] = None
    awaiting: Optional[str] = None
    pending: list = []
    n_events: Optional[int] = None
    events: Optional[list[dict]] = None


class RunList(_Open):
    active: Optional[str] = None
    runs: list[RunInfo] = []


class RunControlResult(_Open):
    run_id: Optional[str] = None
    action: str
    ok: bool
    reason: Optional[str] = None
    code: Optional[str] = None
    intent: Optional[str] = None
    intent_source: Optional[str] = None
    status: Optional[dict] = None


class RunMetrics(_Open):
    run_id: Optional[str] = None
    task_run_id: Optional[str] = None
    robot_id: Optional[str] = None
    user_id: Optional[str] = None
    goal: Optional[str] = None
    loop: Optional[str] = None
    started: Optional[float] = None
    duration_s: Optional[float] = None
    outcome: Optional[str] = None
    status: Optional[str] = None
    llm_calls: int = 0
    llm_errors: int = 0
    llm_latency_ms: float = 0.0
    planner_calls: int = 0
    skill_calls: int = 0
    replans: int = 0
    failure_codes: dict[str, int] = {}
    success: bool = False
    verified_success: bool = False
    live: Optional[bool] = None


class RunRecordOut(_Open):
    run_id: str
    path: Optional[str] = None
    live: bool = False
    n_rows: int = 0
    rows: list[dict] = []
