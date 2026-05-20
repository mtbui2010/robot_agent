"""Diagnostics endpoints.

GET /diagnostics       -- one-shot snapshot of env / skills / devices health
GET /diagnostics/boot  -- errors captured during startup, with full tracebacks
"""

import importlib
import os
import sys

from fastapi import APIRouter

from ..state import current

router = APIRouter()

_ENV_KEYS = (
    'ROBOT_AGENT_LOG_LEVEL',
    'ROBOT_AGENT_DEBUG_RESPONSE',
    'ROS_DISTRO',
    'ROS_DOMAIN_ID',
    'RMW_IMPLEMENTATION',
)


@router.get('/diagnostics')
def diagnostics():
    state = current()
    return {
        'python':       sys.version.split()[0],
        'robot_pkg':    state.robot_pkg,
        'data_dir':     str(state.data_dir),
        'ros2':         _ros2_status(),
        'env':          {k: os.environ.get(k) for k in _ENV_KEYS},
        'sys_path':     _user_sys_path(),
        'skills':       _skills_status(),
        'devices':      _devices_status(),
        'boot_errors':  len(state.boot_errors),
        'log_file':     str(state.data_dir / 'logs' / f'{state.robot_pkg}.log'),
    }


@router.get('/diagnostics/boot')
def diagnostics_boot():
    state = current()
    return {
        'count':  len(state.boot_errors),
        'errors': state.boot_errors,
    }


def _ros2_status():
    try:
        import rclpy
        return {
            'ros_distro': os.environ.get('ROS_DISTRO', 'unset'),
            'rclpy_ok':   bool(rclpy.ok()),
            'ros_node_running': current().dm._ros_node is not None,
        }
    except Exception as e:
        return {'error': str(e)}


def _user_sys_path():
    """sys.path entries outside of site-packages -- helps spot wrong skill paths."""
    return [
        p for p in sys.path
        if p and 'site-packages' not in p and '/python3' not in p
    ]


def _skills_status():
    skills = current().sr.all()
    by_type = {'internal': 0, 'external': 0}
    importable = {'ok': 0, 'fail': 0}
    fails = []
    for s in skills:
        by_type[s['type']] = by_type.get(s['type'], 0) + 1
        if s['type'] != 'internal':
            continue
        mp = s.get('module_path', '')
        fn = s.get('func_name') or s['name']
        if not mp:
            importable['fail'] += 1
            fails.append({'name': s['name'], 'error': 'no module_path'})
            continue
        try:
            mod = importlib.import_module(mp)
            if hasattr(mod, fn):
                importable['ok'] += 1
            else:
                importable['fail'] += 1
                fails.append({
                    'name':  s['name'],
                    'error': f'no function "{fn}" in {mp}',
                })
        except Exception as e:
            importable['fail'] += 1
            fails.append({
                'name':  s['name'],
                'error': f'{type(e).__name__}: {e}',
                'module_path': mp,
            })
    return {
        'count':      len(skills),
        'by_type':    by_type,
        'importable': importable,
        'failures':   fails,
    }


def _devices_status():
    devs = current().dm.get_all()
    by_type = {}
    connected = 0
    for d in devs:
        by_type[d['type']] = by_type.get(d['type'], 0) + 1
        if d.get('connected'):
            connected += 1
    return {
        'count':     len(devs),
        'connected': connected,
        'by_type':   by_type,
        'details':   devs,
    }
