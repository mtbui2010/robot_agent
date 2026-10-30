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
