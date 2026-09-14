import asyncio, base64, threading, time
from typing import AsyncIterator, Any


def _encode_log_image(img: Any) -> str | None:
    """Encode an image value to a base64 JPEG string.

    Skills produce numpy RGB (HxWx3 uint8); we convert to BGR and JPEG-encode.
    Other types (bytes already-jpeg, base64 str, PIL) are best-effort.
    Returns ``None`` if encoding fails or the input isn't an image.
    """
    if img is None:
        return None
    try:
        import numpy as np
        import cv2
        if isinstance(img, np.ndarray):
            arr = img
            if arr.ndim == 3 and arr.shape[2] == 3:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode('.jpg', arr)
            if not ok:
                return None
            return base64.b64encode(buf.tobytes()).decode('ascii')
    except Exception:
        pass
    try:
        from PIL import Image
        import io
        if isinstance(img, Image.Image):
            buf = io.BytesIO()
            img.convert('RGB').save(buf, format='JPEG')
            return base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:
        pass
    if isinstance(img, (bytes, bytearray)):
        return base64.b64encode(bytes(img)).decode('ascii')
    if isinstance(img, str):
        return img
    return None


def _emit_world(emit, node, skill=None, params=None, result=None) -> None:
    """Update the persisted WorldState and emit a ``world`` event so the
    dashboard's Robot State panel stays current in the open-loop / direct paths
    too (these run raw skills, so there is no symbolic ``apply_effect``).

    Two sources, both best-effort:
      * symbolic — if a ``skill`` just ran and the robot's ``grace_namemap``
        defines ``apply_skill_effect(world, skill, params, result)``, apply it
        (sets ``found``/``holding``/``opened`` from the executed skill);
      * sensor — ``reconcile_world`` refreshes ``arrived`` from localization.
    Never breaks a run.
    """
    try:
        from ..state import current
        from .planning.loop import reconcile_world, _robot_namemap, _read_robot_xy
        st = current()
        world = st.world
        if skill:
            namemap = _robot_namemap()
            fn = getattr(namemap, 'apply_skill_effect', None) if namemap else None
            if callable(fn):
                try:
                    fn(world, skill, params, result, node)
                except Exception:
                    pass
        reconcile_world(node, world)
        wd = world.to_dict()
        # Flag a cached object pose the base has moved away from (display + reuse).
        try:
            wd['found_pose_stale'] = world.found_pose_is_stale(_read_robot_xy(node))
        except Exception:
            pass
        emit({'event': 'world', 'world': wd})
        try:
            st.save_world()
        except Exception:
            pass
    except Exception:
        pass


def _kcare_data_root():
    """Client-side data root for captured datasets: ``~/.kcare_robot`` (on
    Windows ``~/Documents/.kcare_robot``)."""
    import sys
    from pathlib import Path
    home = Path.home()
    if sys.platform.startswith('win'):
        return home / 'Documents' / '.kcare_robot'
    return home / '.kcare_robot'


def _begin_dataset(enabled, mode: str = 'backend') -> str | None:
    """Start per-run vision capture when `log_data` is on. Best-effort.

    ``mode='backend'`` opens a capture dir under ~/.kcare_robot (thread-local,
    read by recognition) and returns its path. ``mode='frontend'`` instead turns
    on dataset streaming, so the vision inputs/outputs travel over the agent
    WebSocket and the dashboard stores them — the backend writes nothing.
    """
    if not enabled:
        return None
    if mode == 'frontend':
        try:
            from ..skills import set_stream_dataset
            set_stream_dataset(True)
        except Exception:
            pass
        return None
    try:
        import time
        from ..skills import set_dataset_dir
        d = _kcare_data_root() / 'vision_logs' / time.strftime('%Y%m%d-%H%M%S')
        d.mkdir(parents=True, exist_ok=True)
        set_dataset_dir(str(d))
        return str(d)
    except Exception:
        return None


def _end_dataset(keep: bool = True, path: str | None = None) -> None:
    """Stop capture. With ``keep=False`` (failed-only filter and the run did not
    fail) the backend capture dir is discarded so only failed cases survive."""
    try:
        from ..skills import clear_dataset_dir, clear_stream_dataset
        clear_dataset_dir()
        clear_stream_dataset()
    except Exception:
        pass
    if not keep and path:
        try:
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            pass


def _is_failure_event(ev: dict) -> bool:
    """True when an event marks this run as a failed case — used by the
    "log failed only" filter. Covers execution failures (a step returning
    ``isdone: False`` / ``status: failed``), run-level errors, and GRACE
    planning-time symbolic violations."""
    try:
        name = ev.get('event')
        if name == 'error':
            return True
        if name == 'step_done':
            if ev.get('status') == 'failed':
                return True
            res = ev.get('result')
            if isinstance(res, dict) and res.get('isdone') is False:
                return True
        if name == 'done':
            st = ev.get('status')
            if st is not None:
                return st not in ('success', 'planned')
            return ev.get('success') is False
        # GRACE: symbolic precondition violations found while planning.
        if name == 'plan_step' and ev.get('phase') == 'verified' and ev.get('ok') is False:
            return True
        if name == 'plan_step' and ev.get('violations'):
            return True
        if name == 'replan':
            return True
    except Exception:
        pass
    return False


def _make_log_fn(emit, step_index: int, allow_say: bool = True):
    """Build a per-step ``log_fn`` that turns a raw skill dict into a
    ``step_log`` WebSocket event. Extracts ``log_image`` (numpy RGB →
    base64 JPEG) and any streamed ``dataset`` payload (rgb/depth/results, sent
    when the frontend log target is active); the rest of the dict is
    JSON-serialised and shown under the step in the execution panel.
    """
    def log_fn(raw: Any):
        if not isinstance(raw, dict):
            raw = {'value': raw}
        img = raw.get('log_image')
        dataset = raw.get('dataset')
        # A skill can report progress mid-step with log_data({'say': '...'}) —
        # forwarded as the event's `say` so the dashboard speaks it.
        say = raw.get('say')
        # HRI skills (kcare hri.reply / hri.ask) drive the browser directly:
        # `speak` is voiced unconditionally — it is the skill's output, not
        # narration — and `listen` asks the dashboard to capture one phrase and
        # POST it to /agent/listen/{id}.
        speak = raw.get('speak')
        listen = raw.get('listen')
        data = {k: v for k, v in raw.items()
                if k not in ('log_image', 'dataset', 'say', 'speak', 'speak_lang', 'listen')}
        ev = {
            'event': 'step_log',
            'step': step_index,
            'data': _serialize_result(data),
            'log_image': _encode_log_image(img),
            'ts': time.time(),
        }
        if dataset is not None:
            ev['dataset'] = _serialize_result(dataset)
        if allow_say and isinstance(say, str) and say.strip():
            ev['say'] = say
        if isinstance(speak, str) and speak.strip():
            ev['speak'] = speak
            ev['speak_lang'] = raw.get('speak_lang')
        if isinstance(listen, dict):
            ev['listen'] = _serialize_result(listen)
        emit(ev)
    return log_fn


def _serialize_result(obj: Any) -> Any:
    """Recursively make a skill result JSON-safe.

    Primitive types are kept as-is.  Large / non-serialisable objects are
    replaced with a summary dict: {"__type__": "<name>", ...size info...}.
    """
    # numpy scalars must be checked FIRST: np.bool_ is a subclass of int in
    # modern numpy, so the primitive branch below would leak it through to
    # json.dumps and crash with "Object of type bool_ is not JSON serializable".
    try:
        import numpy as np
        if isinstance(obj, np.ndarray):
            return {'__type__': 'ndarray', 'shape': list(obj.shape), 'dtype': str(obj.dtype)}
        if isinstance(obj, np.generic):
            obj = obj.item()  # fall through to primitive / NaN handling below
    except ImportError:
        pass

    # NaN / +Inf / -Inf are not valid JSON — replace with None so the browser's
    # JSON.parse doesn't choke on tokens like `NaN`.
    if isinstance(obj, float):
        import math
        return obj if math.isfinite(obj) else None

    if obj is None or isinstance(obj, (bool, int, str)):
        return obj

    if isinstance(obj, dict):
        return {k: _serialize_result(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_serialize_result(v) for v in obj]

    # PIL Image
    try:
        from PIL import Image
        if isinstance(obj, Image.Image):
            return {'__type__': 'image', 'mode': obj.mode, 'size': list(obj.size)}
    except ImportError:
        pass

    # bytes / bytearray
    if isinstance(obj, (bytes, bytearray)):
        return {'__type__': type(obj).__name__, 'size': len(obj)}

    # fallback
    return {'__type__': type(obj).__name__, 'repr': repr(obj)[:200]}


class UnifiedAgent:
    def __init__(self, skill_registry, device_manager):
        self.skill_registry = skill_registry
        self.device_manager = device_manager
        self._llm_cfg: dict = {}

    def configure_llm(self, cfg: dict):
        self._llm_cfg = cfg

    def effective_llm_cfg(self) -> dict:
        """The LLM config the planner should use: an explicit ``_llm_cfg`` (set
        via ``/agent/llm-config``) takes precedence; otherwise fall back to the
        active ``type='llm'`` connection's config. This makes "select an LLM in
        the dashboard" actually drive planning, and survives a restart (the
        in-memory ``_llm_cfg`` is cleared on restart, the connection is not)."""
        if self._llm_cfg:
            return self._llm_cfg
        try:
            return self.device_manager.active_llm_config()
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Main async entry — yields events to WebSocket
    # ------------------------------------------------------------------
    async def run(self, prompt: str, lang: str = 'en',
                  planner: str | None = None,
                  plan_only: bool = False, log_data: bool = False,
                  log_mode: str = 'backend', log_all: bool = True) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        # Failure tracking for the "log failed cases only" filter: a step that
        # returns isdone False, a run-level error, or GRACE symbolic violations.
        failed = {'v': False}

        def emit(event: dict):
            if not log_all and not failed['v'] and _is_failure_event(event):
                failed['v'] = True
            asyncio.run_coroutine_threadsafe(q.put(event), loop)

        def _blocking():
            _ds_dir = None
            try:
                emit({'event': 'start', 'prompt':prompt})
                _ds_dir = _begin_dataset(log_data, log_mode)
                if _ds_dir:
                    emit({'event': 'log_dir', 'log_dir': _ds_dir})

                prompt_en = prompt
                if lang != 'en':
                    emit({'event': 'status', 'msg': 'Translating...'})
                    from ..utils import translate
                    prompt_en = translate(prompt, to_language='english',
                                         llm_cfg=self._llm_cfg)
                    emit({'event': 'translated', 'text': prompt_en})

                # Closed-loop path. Activated either by an explicit UI planner
                # choice ('grace' | 'direct') or the ROBOT_AGENT_CLOSED_LOOP=1 env
                # gate. The legacy open-loop flow below remains the default fallback.
                import os as _os
                _planner = {'grace': 'grace', 'direct': 'llm_direct'}.get(planner) if planner else None
                if (_planner is not None or plan_only
                        or _os.environ.get('ROBOT_AGENT_CLOSED_LOOP') == '1'):
                    from .planning.loop import ClosedLoop
                    _kw = {'planner': _planner} if _planner else {}
                    for _ev in ClosedLoop(self, emit=emit, **_kw).run_blocking(prompt_en, lang, plan_only=plan_only):
                        emit(_ev)
                    return

                emit({'event': 'status', 'msg': 'Generating task plan...'})
                from ..connect.llm import init_llm_client
                llm = init_llm_client(cfg=self.effective_llm_cfg())

                from ..state import current
                _pkg = current().robot_pkg
                # Active versioned guide (UI-editable), else the robot's guide
                # modules. `format` None → freeform; a JSON schema → structured.
                from .guide_manager import resolve_guide
                _guide, _fmt = resolve_guide(_pkg)
                try:
                    if _fmt is not None:
                        plan_raw = llm.chat(prompt=prompt_en, guide=_guide, format=_fmt)
                    else:
                        plan_raw = llm.chat_guide(prompt=prompt_en, guide=_guide, reuse=False)
                except Exception:
                    # Structured call failed (e.g. backend without JSON-schema
                    # output) — retry freeform with the same guide text.
                    plan_raw = llm.chat_guide(prompt=prompt_en, guide=_guide, reuse=False)

                emit({'event': 'plan_raw', 'plan': str(plan_raw)})

                from .guide_manager import reconstruct_plan
                try:
                    plan_dict = eval(str(plan_raw))
                    plan = reconstruct_plan(plan_dict)
                except Exception:
                    plan = str(plan_raw)

                emit({'event': 'plan', 'plan': plan})

                if '::' not in plan:
                    emit({'event': 'error', 'msg': 'LLM returned no valid task plan'})
                    return

                tasks = self._parse_plan(plan)
                node = self.device_manager._ros_node
                ctx: dict = {'isdone': True}
                _emit_world(emit, node)   # initial Robot State snapshot

                for i, task_group in enumerate(tasks):
                    emit({'event': 'step_start', 'step': i + 1,
                          'total': len(tasks), 'task': str(task_group)})

                    if not ctx.get('isdone', True):
                        emit({'event': 'stopped', 'msg': 'Previous step failed'})
                        break

                    log_fn = _make_log_fn(emit, i + 1)
                    if len(task_group) == 1:
                        ret = self._exec_task(task_group[0], node, ctx, log_fn=log_fn)
                    else:
                        ret = self._exec_parallel(task_group, node, ctx, log_fn=log_fn)

                    ctx.update(ret)
                    emit({'event': 'step_done', 'step': i + 1, 'result': _serialize_result(ret)})
                    _sk = task_group[0][0] if task_group and task_group[0] else None
                    _pa = task_group[0][1] if task_group and len(task_group[0]) > 1 else None
                    _emit_world(emit, node, skill=_sk, params=_pa, result=ret)   # refresh Robot State

                emit({'event': 'done', 'success': _serialize_result(ctx.get('isdone', True))})

            except Exception as e:
                import traceback
                failed['v'] = True
                emit({'event': 'error', 'msg': str(e), 'trace': traceback.format_exc()})
            finally:
                _end_dataset(keep=log_all or failed['v'], path=_ds_dir)
                emit({'event': '__end__'})

        threading.Thread(target=_blocking, daemon=True).start()

        while True:
            event = await q.get()
            if event.get('event') == '__end__':
                break
            yield event

    # ------------------------------------------------------------------
    # Direct execution (structured commands, no LLM)
    # ------------------------------------------------------------------
    async def run_direct(self, plan: str, log_data: bool = False,
                         log_mode: str = 'backend', log_all: bool = True,
                         lang: str = 'en') -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        failed = {'v': False}
        # Milestone phrases for the structured/direct path. speak_backend=False:
        # the dashboard speaks (it receives `say` on each event), the robot's own
        # speaker stays quiet.
        from .planning.announcer import Announcer
        announcer = Announcer(lang=lang, speak_backend=False)

        def _verb_obj(task_group):
            """(skill, first param) of a step — announcer's {verb} / {object}."""
            try:
                skill, inputs = task_group[0][0], task_group[0][1]
                obj = str(inputs).split(',')[0].split('=')[-1].strip()
                if obj in ('None', 'none', ''):
                    obj = ''
                return skill, obj
            except Exception:
                return '', ''

        def emit(event: dict):
            if not log_all and not failed['v'] and _is_failure_event(event):
                failed['v'] = True
            asyncio.run_coroutine_threadsafe(q.put(event), loop)

        def _blocking():
            _ds_dir = None
            try:
                emit({'event': 'start', 'prompt':plan})
                _ds_dir = _begin_dataset(log_data, log_mode)
                if _ds_dir:
                    emit({'event': 'log_dir', 'log_dir': _ds_dir})
                tasks = self._parse_plan(plan)
                if not tasks:
                    emit({'event': 'error', 'msg': 'No valid commands found'})
                    return

                emit({'event': 'plan', 'plan': plan,
                      'say': announcer.announce('plan_ready')})
                node = self.device_manager._ros_node
                ctx: dict = {'isdone': True}
                _emit_world(emit, node)   # initial Robot State snapshot

                for i, task_group in enumerate(tasks):
                    _act, _obj = _verb_obj(task_group)
                    emit({'event': 'step_start', 'step': i + 1,
                          'total': len(tasks), 'task': str(task_group),
                          'say': announcer.announce('step_start', action=_act, object=_obj)})

                    if not ctx.get('isdone', True):
                        emit({'event': 'stopped', 'msg': 'Previous step failed'})
                        break

                    log_fn = _make_log_fn(emit, i + 1)
                    if len(task_group) == 1:
                        ret = self._exec_task_direct(task_group[0], node, ctx, log_fn=log_fn)
                    else:
                        ret = self._exec_parallel_direct(task_group, node, ctx, log_fn=log_fn)

                    ctx.update(ret)
                    _ok = bool(ret.get('isdone', True)) if isinstance(ret, dict) else True
                    _reason = str(ret.get('msg', '')) if isinstance(ret, dict) and not _ok else ''
                    emit({'event': 'step_done', 'step': i + 1, 'result': _serialize_result(ret),
                          'say': announcer.announce(
                              'step_success' if _ok else 'step_fail',
                              action=_act, object=_obj, reason=_reason)})
                    _sk = task_group[0][0] if task_group and task_group[0] else None
                    _pa = task_group[0][1] if task_group and len(task_group[0]) > 1 else None
                    _emit_world(emit, node, skill=_sk, params=_pa, result=ret)   # refresh Robot State

                _done_ok = bool(ctx.get('isdone', True))
                emit({'event': 'done', 'success': _serialize_result(ctx.get('isdone', True)),
                      'say': announcer.announce('done_success' if _done_ok else 'done_fail')})

            except Exception as e:
                import traceback
                failed['v'] = True
                emit({'event': 'error', 'msg': str(e), 'trace': traceback.format_exc(),
                      'say': announcer.announce('done_fail')})
            finally:
                _end_dataset(keep=log_all or failed['v'], path=_ds_dir)
                emit({'event': '__end__'})

        threading.Thread(target=_blocking, daemon=True).start()

        while True:
            event = await q.get()
            if event.get('event') == '__end__':
                break
            yield event

    def _exec_task_direct(self, task: list, node: Any, ctx: dict, log_fn=None) -> dict:
        action, inputs_str = task
        params = self._parse_inputs(inputs_str)
        params.update({k: v for k, v in ctx.items() if k != 'node'})
        skip_fail, make_fail = '!' in action, '~' in action
        action = action.replace('!', '').replace('~', '')

        result = self.skill_registry.execute(action, params, node=node, log_fn=log_fn)
        if 'not registered' not in result.get('msg', ''):
            if skip_fail:
                result['isdone'] = True
            if make_fail:
                result['isdone'] = False
            return result

        # Fallback: direct ROS device agent call
        try:
            if node is None:
                return {'isdone': False, 'msg': f'No ROS node — "{action}" not found'}
            agent = node.agents.get(action.replace('!', ''))
            if agent is None:
                return {'isdone': False, 'msg': f'No skill or device agent "{action}"'}
            conn = self.device_manager._connects.get(action)
            if conn is not None and conn.type == 'ros_topic':
                ret = agent.get()
            else:
                ret = agent.send(params if params else {})
            ret  = ret if isinstance(ret, dict) else {'isdone': True, 'data': ret}
            if skip_fail:
                ret['isdone'] = True
            if make_fail:
                ret['isdone'] = False
            return ret
        except Exception as e:
            return {'isdone': False, 'msg': str(e)}

    def _exec_parallel_direct(self, task_group: list, node: Any, ctx: dict, log_fn=None) -> dict:
        from ..connect.parallel import run_parallel_check
        fns = [lambda t=task: self._exec_task_direct(t, node, ctx, log_fn=log_fn) for task in task_group]
        return run_parallel_check(funcs=fns)

    # ------------------------------------------------------------------
    # Plan parsing
    # ------------------------------------------------------------------
    def _parse_plan(self, plan: str) -> list:
        tasks = []
        for line in plan.replace('\\n', '\n').split('\n'):
            line = line.strip()
            if '::' not in line:
                continue
            group = [el.split('::') for el in line.split('&&')
                     if len(el.split('::')) == 2]
            if group:
                tasks.append(group)
        return tasks

    def _exec_task(self, task: list, node: Any, ctx: dict, log_fn=None) -> dict:
        action, inputs_str = task
        params = self._parse_inputs(inputs_str)
        params.update({k: v for k, v in ctx.items() if k != 'node'})
        return self.skill_registry.execute(action, params, node=node, log_fn=log_fn)

    def _exec_parallel(self, task_group: list, node: Any, ctx: dict, log_fn=None) -> dict:
        from ..connect.parallel import run_parallel_check
        fns = [lambda t=task: self._exec_task(t, node, ctx, log_fn=log_fn) for task in task_group]
        return run_parallel_check(funcs=fns)

    def _parse_inputs(self, s: str) -> dict:
        s = s.strip()
        if not s or s in ('None', ''):
            return {}
        if '=' not in s:
            return {'inputs': s}
        try:
            return eval(f'dict({s})')
        except Exception:
            return {'inputs': s}
