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


def _make_log_fn(emit, step_index: int):
    """Build a per-step ``log_fn`` that turns a raw skill dict into a
    ``step_log`` WebSocket event. Extracts ``log_image`` (numpy RGB →
    base64 JPEG); the rest of the dict is JSON-serialised and shown
    under the step in the execution panel.
    """
    def log_fn(raw: Any):
        if not isinstance(raw, dict):
            raw = {'value': raw}
        img = raw.get('log_image')
        data = {k: v for k, v in raw.items() if k != 'log_image'}
        emit({
            'event': 'step_log',
            'step': step_index,
            'data': _serialize_result(data),
            'log_image': _encode_log_image(img),
            'ts': time.time(),
        })
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

    # ------------------------------------------------------------------
    # Main async entry — yields events to WebSocket
    # ------------------------------------------------------------------
    async def run(self, prompt: str, lang: str = 'en') -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def emit(event: dict):
            asyncio.run_coroutine_threadsafe(q.put(event), loop)

        def _blocking():
            try:
                emit({'event': 'start', 'prompt': prompt})

                prompt_en = prompt
                if lang != 'en':
                    emit({'event': 'status', 'msg': 'Translating...'})
                    from pyconnect.utils import translate
                    prompt_en = translate(prompt, to_language='english',
                                         llm_cfg=self._llm_cfg)
                    emit({'event': 'translated', 'text': prompt_en})

                emit({'event': 'status', 'msg': 'Generating task plan...'})
                from pyconnect.utils import init_llm_client
                llm = init_llm_client(cfg=self._llm_cfg)

                from ..state import current
                _pkg = current().robot_pkg
                try:
                    import importlib
                    _m = importlib.import_module(f'{_pkg}.configs.guide_struct')
                    plan_raw = llm.chat(prompt=prompt_en, guide=_m.GUIDE, format=_m.FORMAT)
                except Exception:
                    import importlib
                    _m = importlib.import_module(f'{_pkg}.configs.guide')
                    plan_raw = llm.chat_guide(prompt=prompt_en, guide=_m.GUIDE, reuse=False)

                emit({'event': 'plan_raw', 'plan': str(plan_raw)})

                from pyconnect.ros.node_taskmanager import recontruct_plan
                try:
                    plan_dict = eval(str(plan_raw))
                    plan = recontruct_plan(plan_dict)
                except Exception:
                    plan = str(plan_raw)

                emit({'event': 'plan', 'plan': plan})

                if '::' not in plan:
                    emit({'event': 'error', 'msg': 'LLM returned no valid task plan'})
                    return

                tasks = self._parse_plan(plan)
                node = self.device_manager._ros_node
                ctx: dict = {'isdone': True}

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

                emit({'event': 'done', 'success': _serialize_result(ctx.get('isdone', True))})

            except Exception as e:
                import traceback
                emit({'event': 'error', 'msg': str(e), 'trace': traceback.format_exc()})
            finally:
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
    async def run_direct(self, plan: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def emit(event: dict):
            asyncio.run_coroutine_threadsafe(q.put(event), loop)

        def _blocking():
            try:
                emit({'event': 'start', 'prompt': plan})
                tasks = self._parse_plan(plan)
                if not tasks:
                    emit({'event': 'error', 'msg': 'No valid commands found'})
                    return

                emit({'event': 'plan', 'plan': plan})
                node = self.device_manager._ros_node
                ctx: dict = {'isdone': True}

                for i, task_group in enumerate(tasks):
                    emit({'event': 'step_start', 'step': i + 1,
                          'total': len(tasks), 'task': str(task_group)})

                    if not ctx.get('isdone', True):
                        emit({'event': 'stopped', 'msg': 'Previous step failed'})
                        break

                    log_fn = _make_log_fn(emit, i + 1)
                    if len(task_group) == 1:
                        ret = self._exec_task_direct(task_group[0], node, ctx, log_fn=log_fn)
                    else:
                        ret = self._exec_parallel_direct(task_group, node, ctx, log_fn=log_fn)

                    ctx.update(ret)
                    emit({'event': 'step_done', 'step': i + 1, 'result': _serialize_result(ret)})

                emit({'event': 'done', 'success': _serialize_result(ctx.get('isdone', True))})

            except Exception as e:
                import traceback
                emit({'event': 'error', 'msg': str(e), 'trace': traceback.format_exc()})
            finally:
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

        result = self.skill_registry.execute(action, params, node=node, log_fn=log_fn)
        if 'not registered' not in result.get('msg', ''):
            return result

        # Fallback: direct ROS device agent call
        try:
            if node is None:
                return {'isdone': False, 'msg': f'No ROS node — "{action}" not found'}
            agent = node.agents.get(action)
            if agent is None:
                return {'isdone': False, 'msg': f'No skill or device agent "{action}"'}
            conn = self.device_manager._connects.get(action)
            if conn is not None and conn.type == 'ros_topic':
                ret = agent.get()
            else:
                ret = agent.send(params if params else {})
            return ret if isinstance(ret, dict) else {'isdone': True, 'data': ret}
        except Exception as e:
            return {'isdone': False, 'msg': str(e)}

    def _exec_parallel_direct(self, task_group: list, node: Any, ctx: dict, log_fn=None) -> dict:
        from pyconnect.utils import run_parallel_check
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
        from pyconnect.utils import run_parallel_check
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
