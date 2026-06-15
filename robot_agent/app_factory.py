"""FastAPI app factory.

Robot packages call `create_app(robot_pkg, ...)` from their own entry script
(e.g. `kcare_robot/main.py`). The factory builds a FastAPI app that wraps the
same `bootstrap()` used by CLI and Python-API modes — so all three modes share
one init path.

Pass either ``config_dir`` (new split layout: ``configs/common`` +
``configs/locations/<site>``) or the legacy ``data_dir`` (single folder). See
``runtime.bootstrap`` for the layout details.

`robot_agent` itself contains no entry point; running ``uvicorn`` directly on
this module is unsupported -- the factory must be called by the robot package.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def _json_default(o: Any):
    if hasattr(o, 'item') and callable(o.item):
        try:
            return o.item()
        except Exception:
            pass
    if hasattr(o, 'tolist') and callable(o.tolist):
        return o.tolist()
    if isinstance(o, (bytes, bytearray)):
        return o.decode('utf-8', errors='replace')
    return str(o)


class NumpyJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            default=_json_default,
            separators=(',', ':'),
        ).encode('utf-8')


def create_app(robot_pkg: str,
               data_dir: Optional[Path] = None,
               config_dir: Optional[Path] = None,
               location: Optional[str] = None) -> FastAPI:
    """Build a FastAPI app bound to `robot_pkg`.

    Pass ``config_dir`` for the split layout (recommended) or ``data_dir`` for
    the legacy single-folder layout. ``location`` forces a starting site;
    otherwise the persisted ``active_location`` is used.
    """
    from .runtime import bootstrap

    # UI mode wants snappy uvicorn startup -> load devices in a background
    # thread (load_devices=False), then bootstrap() returns immediately.
    agent_state = bootstrap(
        robot_pkg=robot_pkg,
        data_dir=data_dir,
        config_dir=config_dir,
        location=location,
        load_devices=False,
        verbose=False,
    )

    from .api.devices import router as devices_router
    from .api.skills import router as skills_router
    from .api.agent import router as agent_router
    from .api.camera import router as camera_router
    from .api.configs import router as configs_router
    from .api.buttons import router as buttons_router
    from .api.diagnostics import router as diagnostics_router
    from .api.locations import router as locations_router
    from .api.guides import router as guides_router

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import logging
        logger = logging.getLogger('robot_agent')
        logger.info(f'lifespan: startup (robot_pkg={robot_pkg})')
        if agent_state.boot_errors:
            logger.warning(
                f'{len(agent_state.boot_errors)} boot error(s) -- see GET /diagnostics/boot'
            )
        yield
        logger.info('lifespan: shutdown')
        if agent_state.dm._ros_node:
            agent_state.dm._ros_node.stop()

    app = FastAPI(
        title=f'Robot Agent ({robot_pkg})',
        lifespan=lifespan,
        default_response_class=NumpyJSONResponse,
    )
    app.state.agent_state = agent_state

    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    app.include_router(devices_router,     prefix='')
    app.include_router(skills_router,      prefix='')
    app.include_router(configs_router,     prefix='')
    app.include_router(buttons_router,     prefix='')
    app.include_router(agent_router,       prefix='')
    app.include_router(camera_router,      prefix='')
    app.include_router(diagnostics_router, prefix='')
    app.include_router(locations_router,   prefix='')
    app.include_router(guides_router,      prefix='')

    @app.get('/')
    def root():
        return {'status': 'ok', 'app': 'robot_agent', 'robot_pkg': robot_pkg}

    return app
