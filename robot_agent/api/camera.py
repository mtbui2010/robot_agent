import asyncio, base64, json, queue, threading, time, zlib
import cv2
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..state import current

router = APIRouter()

_streams: dict[str, '_Stream'] = {}
_streams_lock = threading.Lock()


def _encode_rgb(rgb) -> str:
    _, buf = cv2.imencode('.jpg', rgb[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 75])
    return base64.b64encode(buf).decode()


def _percentile_range(depth) -> tuple[float, float]:
    """Auto-range using 2nd / 98th percentiles of valid (>0) pixels."""
    d = depth.astype('float32')
    valid = d[d > 0]
    if valid.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(valid, [2, 98])
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


def _encode_depth_colored(depth, dmin: float, dmax: float) -> str:
    d = depth.astype('float32')
    norm = np.clip((d - dmin) / (dmax - dmin), 0.0, 1.0)
    norm = (norm * 255).astype('uint8')
    colored = cv2.applyColorMap(norm, cv2.COLORMAP_TURBO)
    colored[d == 0] = 0   # invalid pixels → black
    _, buf = cv2.imencode('.jpg', colored, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()


def _encode_depth_raw(depth) -> tuple[str, int, int]:
    """zlib-compressed little-endian uint16 buffer + (h, w)."""
    d = np.ascontiguousarray(depth.astype('<u2'))   # little-endian uint16
    h, w = d.shape
    compressed = zlib.compress(d.tobytes(), 1)
    return base64.b64encode(compressed).decode(), h, w


def _process_depth(depth, settings: dict) -> dict:
    """Encode depth per current settings; always emits depth_meta."""
    dmin = settings.get('dmin')
    dmax = settings.get('dmax')
    if dmin is None or dmax is None:
        dmin, dmax = _percentile_range(depth)
    mode = settings.get('mode', 'colored')
    out: dict = {'depth_meta': {'dmin': dmin, 'dmax': dmax, 'mode': mode}}
    if mode == 'raw':
        b64, h, w = _encode_depth_raw(depth)
        out['depth_raw'] = b64
        out['depth_w']   = w
        out['depth_h']   = h
    else:
        out['depth'] = _encode_depth_colored(depth, dmin, dmax)
    return out


def _to_depth_mm(arr: np.ndarray) -> np.ndarray:
    """Normalise a 2D depth array to uint16 mm."""
    if arr.dtype == np.float32 or arr.dtype == np.float64:
        # Heuristic: float typically encodes metres → mm
        return (arr * 1000.0).astype('uint16')
    return arr.astype('uint16')


def _is_depth_array(arr: np.ndarray) -> bool:
    if not isinstance(arr, np.ndarray):
        return False
    if arr.ndim != 2:
        return False
    return arr.dtype in (np.uint16, np.float32, np.float64)


def _encode_image_payload(arr: np.ndarray, depth_settings: dict) -> dict:
    """Pick depth vs rgb encoding based on dtype/shape."""
    if _is_depth_array(arr):
        return _process_depth(_to_depth_mm(arr), depth_settings)
    return {'rgb': _encode_rgb(arr)}


def _decode_frame(data, depth_settings: dict) -> dict:
    """Convert any ROS image data to {"rgb": base64, ...} or depth payload."""
    msg: dict = {}
    if data is None:
        return msg
    try:
        if hasattr(data, 'format') and hasattr(data, 'data'):
            # CompressedImage — could be color jpeg OR ROS compressedDepth (PNG-16)
            fmt = str(getattr(data, 'format', '')).lower()
            raw = bytes(data.data)
            if 'compresseddepth' in fmt or '16uc1' in fmt or '32fc1' in fmt:
                # ROS compressedDepth: 12-byte header (depth quantization) + PNG-16
                if len(raw) > 12:
                    arr = cv2.imdecode(np.frombuffer(raw[12:], np.uint8), cv2.IMREAD_UNCHANGED)
                    if arr is not None:
                        msg.update(_encode_image_payload(arr, depth_settings))
            else:
                # color jpeg/png — forward as-is
                msg['rgb'] = base64.b64encode(raw).decode()
        elif hasattr(data, 'encoding') and hasattr(data, 'data'):
            # sensor_msgs/Image
            enc = str(getattr(data, 'encoding', '')).lower()
            if enc in ('16uc1', 'mono16'):
                arr = np.frombuffer(bytes(data.data), dtype=np.uint16).reshape((data.height, data.width))
                msg.update(_process_depth(arr, depth_settings))
            elif enc == '32fc1':
                arr = np.frombuffer(bytes(data.data), dtype=np.float32).reshape((data.height, data.width))
                msg.update(_process_depth(_to_depth_mm(arr), depth_settings))
            else:
                arr = np.frombuffer(bytes(data.data), dtype=np.uint8).reshape((data.height, data.width, -1))
                msg['rgb'] = _encode_rgb(arr)
        elif isinstance(data, dict):
            # Generic dict — may have explicit 'depth', or just 'im'/'rgb' that
            # is actually a uint16 depth array.
            depth = data.get('depth')
            if depth is not None and isinstance(depth, np.ndarray):
                msg.update(_process_depth(_to_depth_mm(depth), depth_settings))
            rgb = data.get('rgb') if data.get('rgb') is not None else data.get('im')
            if rgb is not None:
                if isinstance(rgb, np.ndarray):
                    msg.update(_encode_image_payload(rgb, depth_settings))
                else:
                    msg['rgb'] = rgb  # already encoded string
        elif isinstance(data, np.ndarray):
            msg.update(_encode_image_payload(data, depth_settings))
    except Exception as e:
        print(f'[camera] _decode_frame error: {e}')
    return msg


# ---------------------------------------------------------------------------
# Streaming: one worker per camera, fanned out to every browser watching it.
#
# Each websocket used to start its own stream thread keyed by connect_id, so a
# second browser killed the first one's stream (both held the same stop Event)
# and the first browser's auto-reconnect killed it right back — a permanent
# reconnect war, 3 s of thread-join per round. The encoding cost scaled with
# viewers too: every client re-encoded every frame for itself.
#
# Now a camera has exactly one worker, whatever the number of viewers. It
# encodes a frame once per distinct depth setting and sends the same JSON text
# to every subscriber sharing it, skips the work entirely when no new frame has
# arrived, and drops a frame for any client whose previous send has not
# finished — so a slow browser can no longer pile sends up in the event loop.
# ---------------------------------------------------------------------------

_IDLE_GRACE = 15.0   # keep a camera worker alive this long after the last viewer left


class _Subscriber:
    """One connected websocket, with its own depth settings."""

    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop):
        self.ws   = websocket
        self.loop = loop
        # Mutated by this client's receive loop, read by the camera worker.
        self.settings: dict = {'mode': 'colored', 'dmin': None, 'dmax': None}
        self.inflight = None   # Future of the last send, for backpressure
        self.dropped  = 0
        # A viewer joining a stream that is already running must get the frame
        # the worker is holding, without waiting for the camera's next message.
        self.needs_frame = True

    def key(self) -> tuple:
        """Subscribers with the same key share one encoded frame."""
        s = self.settings
        return (s.get('mode', 'colored'), s.get('dmin'), s.get('dmax'))

    def send_text(self, text: str) -> None:
        fut = self.inflight
        if fut is not None and not fut.done():
            self.dropped += 1      # client is behind — drop this frame for it
            return
        try:
            self.inflight = asyncio.run_coroutine_threadsafe(self.ws.send_text(text), self.loop)
            self.needs_frame = False
        except Exception:
            pass


class _Stream:
    """A camera's worker thread and the subscribers it feeds."""

    def __init__(self, connect_id: str):
        self.connect_id = connect_id
        self.subs: list[_Subscriber] = []
        self.stop     = threading.Event()
        self.captures: queue.Queue = queue.Queue()   # capture requests from any client
        self.idle_since: float | None = None
        self.thread: threading.Thread | None = None


def _snapshot(stream: _Stream) -> list:
    with _streams_lock:
        return list(stream.subs)


def _fanout(subs: list, build) -> None:
    """Encode once per distinct depth setting, then send the same text to all."""
    groups: dict = {}
    for s in subs:
        groups.setdefault(s.key(), []).append(s)
    for members in groups.values():
        try:
            msg = build(members[0].settings)
        except Exception as e:
            print(f'[camera] encode error: {e}')
            continue
        if not msg:
            continue
        text = json.dumps(msg)
        for s in members:
            s.send_text(text)


def _broadcast(stream: _Stream, msg: dict) -> None:
    text = json.dumps(msg)
    for s in _snapshot(stream):
        s.send_text(text)


def _retire(stream: _Stream) -> bool:
    """True once the worker has had no viewer for the whole grace period.

    The grace period keeps a page reload (or a switch between grid and tab
    view) from tearing the worker down and building it straight back up.
    """
    with _streams_lock:
        if stream.subs:
            stream.idle_since = None
            return False
        now = time.time()
        if stream.idle_since is None:
            stream.idle_since = now
            return False
        if now - stream.idle_since < _IDLE_GRACE:
            return False
        if _streams.get(stream.connect_id) is stream:
            _streams.pop(stream.connect_id, None)
        stream.stop.set()
        return True


def _run_topic(stream: _Stream) -> None:
    """ros_topic camera: poll the agent's last message, encode only new frames."""
    last_seq = None
    while not stream.stop.is_set():
        if _retire(stream):
            return
        subs = _snapshot(stream)
        if not subs:
            time.sleep(0.2)          # no viewer — nothing to encode
            continue
        entry  = current().dm.get_connect(stream.connect_id)
        client = None if entry is None else entry.client
        data   = None if client is None else client.rev_data
        if data is not None:
            # TopicAgent bumps rev_seq on every message received, so an
            # unchanged frame costs nothing here (a 5 fps camera used to be
            # re-encoded 20 times a second, per viewer).
            seq = getattr(client, 'rev_seq', None)
            if seq is None or seq != last_seq or any(s.needs_frame for s in subs):
                last_seq = seq
                _fanout(subs, lambda st, d=data: _decode_frame(d, st))
        time.sleep(0.05)             # ~20 fps cap


def _run_webrtc(stream: _Stream) -> None:
    """webrtc camera: one fetch generator, shared by every viewer."""
    entry = current().dm.get_connect(stream.connect_id)
    if entry is None:
        return
    for frame in entry.client.fetch(timeout=2.0):
        if stream.stop.is_set() or _retire(stream):
            return
        subs = _snapshot(stream)
        if not subs:
            continue                 # keep the feed running, skip the encoding
        rgb = frame.get('rgb')
        # rgb does not depend on the depth settings — encode it once for all.
        rgb_b64 = None if rgb is None else (rgb if isinstance(rgb, str) else _encode_rgb(rgb))
        depth, cam_params = frame.get('depth'), frame.get('cam_params')

        def build(settings, _rgb=rgb_b64, _depth=depth, _cam=cam_params):
            msg: dict = {}
            if _rgb is not None:
                msg['rgb'] = _rgb
            if _depth is not None:
                msg.update(_process_depth(_depth, settings))
            if _cam is not None:
                msg['cam_params'] = list(_cam)
            return msg

        _fanout(subs, build)


def _run_ondemand(stream: _Stream) -> None:
    """ros_service / ros_action camera: one shot per capture request."""
    while not stream.stop.is_set():
        if _retire(stream):
            return
        try:
            stream.captures.get(timeout=1.0)
        except queue.Empty:
            continue
        subs = _snapshot(stream)
        if stream.stop.is_set() or not subs:
            continue
        entry = current().dm.get_connect(stream.connect_id)
        if entry is None:
            continue
        try:
            data = entry.client.send({})
        except Exception as e:
            _broadcast(stream, {'error': str(e)})
            continue
        _fanout(subs, lambda st, d=data: _decode_frame(d, st))


def _run_stream(stream: _Stream) -> None:
    try:
        entry = current().dm.get_connect(stream.connect_id)
        if entry is None:
            return
        if entry.type == 'ros_topic':
            _run_topic(stream)
        elif entry.type == 'webrtc':
            _run_webrtc(stream)
        else:
            _run_ondemand(stream)
    except Exception as e:
        _broadcast(stream, {'error': str(e)})
    finally:
        stream.stop.set()
        with _streams_lock:
            if _streams.get(stream.connect_id) is stream:
                _streams.pop(stream.connect_id, None)


@router.websocket('/ws/camera/{connect_id:path}')
async def camera_ws(websocket: WebSocket, connect_id: str):
    await websocket.accept()

    entry = current().dm.get_connect(connect_id)
    if entry is None or not entry.is_camera:
        await websocket.send_text(json.dumps({'error': 'Not a camera client'}))
        await websocket.close()
        return

    sub = _Subscriber(websocket, asyncio.get_running_loop())
    with _streams_lock:
        stream = _streams.get(connect_id)
        start  = stream is None or stream.stop.is_set()
        if start:
            stream = _Stream(connect_id)
            _streams[connect_id] = stream
        stream.subs.append(sub)
        stream.idle_since = None
    if start:
        stream.thread = threading.Thread(target=_run_stream, args=(stream,), daemon=True)
        stream.thread.start()

    try:
        while not stream.stop.is_set():
            try:
                text = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(text)
            except Exception:
                continue
            if msg.get('capture'):
                stream.captures.put(True)
            if 'depth_range' in msg:
                rng = msg['depth_range']
                if rng is None:
                    sub.settings['dmin'] = None
                    sub.settings['dmax'] = None
                else:
                    try:
                        sub.settings['dmin'] = float(rng[0])
                        sub.settings['dmax'] = float(rng[1])
                    except (TypeError, ValueError, IndexError):
                        pass
            if 'depth_mode' in msg and msg['depth_mode'] in ('colored', 'raw'):
                # Per-client: one browser's one-shot raw grab must not push raw
                # frames at the others.
                sub.settings['mode'] = msg['depth_mode']
    except WebSocketDisconnect:
        pass
    finally:
        with _streams_lock:
            if sub in stream.subs:
                stream.subs.remove(sub)
            if not stream.subs:
                stream.idle_since = time.time()
