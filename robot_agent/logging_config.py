"""Logging setup for robot_agent.

Env vars:
    ROBOT_AGENT_LOG_LEVEL      DEBUG | INFO | WARNING | ERROR (default INFO)
    ROBOT_AGENT_DEBUG_RESPONSE 1 / true / yes -- include full traceback in
                               skill error responses (default off)

Logs to stderr and to a rotating file inside the caller-supplied log dir
(5 x 10 MB).
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_INITIALIZED = False


def setup_logging(log_dir: Path, log_name: str = 'robot_agent') -> logging.Logger:
    """Configure the 'robot_agent' logger tree. Idempotent.

    Args:
        log_dir: directory the rotating log file will live in.
        log_name: base name of the log file (without extension).
    """
    global _INITIALIZED
    root = logging.getLogger('robot_agent')
    if _INITIALIZED:
        return root

    level_str = os.environ.get('ROBOT_AGENT_LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_str, logging.INFO)

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f'{log_name}.log'

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    root.setLevel(level)
    root.handlers.clear()
    root.propagate = False

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    root.addHandler(sh)

    try:
        fh = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5,
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception as e:
        root.warning(f'Could not open log file {log_file}: {e}')

    _INITIALIZED = True
    root.info(f'Logging configured (level={level_str}, file={log_file})')
    return root


def debug_response_enabled() -> bool:
    """True if skill error responses should include full traceback."""
    return os.environ.get('ROBOT_AGENT_DEBUG_RESPONSE', '').lower() in (
        '1', 'true', 'yes', 'on',
    )
