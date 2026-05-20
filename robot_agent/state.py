"""Per-app state container.

`AgentState` bundles the four singletons that used to live as module-level
globals (dm / sr / cm / ua) so that each FastAPI app instance owns its own.

`create_app()` builds one `AgentState` per app and:
  - attaches it to `app.state.agent_state` (the FastAPI-idiomatic way; API
    routers read it via `request.app.state.agent_state`),
  - and stores it in `_CURRENT` as a per-process reference for code that runs
    outside the request scope (e.g. skill functions calling `utils.voice2text`,
    `skill_configs` proxies).

E1 = one robot per Python process, so a single process-global is sufficient.
Running multiple robots in parallel means multiple processes (different ports),
each with its own `_CURRENT`.
"""

from pathlib import Path

from .core.device_manager import DeviceManager
from .core.skill_registry import SkillRegistry
from .core.unified_agent import UnifiedAgent
from .core.config_manager import ConfigManager
from .core.button_manager import ButtonManager


class AgentState:
    def __init__(self, robot_pkg: str, data_dir: Path, node_name: str = 'robot_agent'):
        self.robot_pkg = robot_pkg
        self.data_dir = Path(data_dir)
        self.dm = DeviceManager(data_dir=self.data_dir, node_name=node_name)
        self.sr = SkillRegistry(data_dir=self.data_dir)
        self.cm = ConfigManager(data_dir=self.data_dir, robot_pkg=robot_pkg)
        self.bm = ButtonManager(data_dir=self.data_dir)
        self.ua = UnifiedAgent(skill_registry=self.sr, device_manager=self.dm)
        # Populated during create_app lifespan. Each item:
        #   {'phase': str, 'msg': str, 'traceback': str, 'timestamp': float}
        # Exposed via GET /diagnostics/boot.
        self.boot_errors: list[dict] = []


_CURRENT: AgentState | None = None


def set_current(state: AgentState) -> None:
    global _CURRENT
    _CURRENT = state


def current() -> AgentState:
    if _CURRENT is None:
        raise RuntimeError(
            'No active AgentState. robot_agent.create_app() must be called '
            'before code that depends on state.current().'
        )
    return _CURRENT
