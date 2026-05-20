import asyncio, base64, json, threading, zlib
import cv2
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..state import current

router = APIRouter()

_streams: dict[str, tuple[threading.Event, threading.Thread]] = {}
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


@router.websocket('/ws/camera/{connect_id:path}')
async def camera_ws(websocket: WebSocket, connect_id: str):
    await websocket.accept()

    entry = current().dm.get_connect(connect_id)
    if entry is None or not entry.is_camera:
        await websocket.send_text(json.dumps({'error': 'Not a camera client'}))
        await websocket.close()
        return

    with _streams_lock:
        prev = _streams.get(connect_id)
    if prev is not None:
        old_stop, old_thread = prev
        old_stop.set()
        await asyncio.to_thread(old_thread.join, 3.0)

    loop = asyncio.get_event_loop()
    stop = threading.Event()
    # Queue for capture requests from client messages
    capture_queue: asyncio.Queue = asyncio.Queue()
    # Per-connection depth settings; mutated by the WS-receive loop, read by stream thread
    depth_settings: dict = {'mode': 'colored', 'dmin': None, 'dmax': None}

    def _send(msg: dict):
        asyncio.run_coroutine_threadsafe(
            websocket.send_text(json.dumps(msg)), loop
        )

    def stream():
        try:
            if entry.type == 'ros_topic':
                import time
                while not stop.is_set():
                    data = entry.client.rev_data
                    if data is not None:
                        msg = _decode_frame(data, depth_settings)
                        if msg:
                            _send(msg)
                    time.sleep(0.05)  # ~20 fps cap
            else:
                # webrtc: continuous fetch; ros_service/ros_action: wait for capture signal
                if entry.type == 'webrtc':
                    for frame in entry.client.fetch(timeout=2.0):
                        if stop.is_set():
                            break
                        msg: dict = {}
                        rgb = frame.get('rgb')
                        if rgb is not None:
                            msg['rgb'] = rgb if isinstance(rgb, str) else _encode_rgb(rgb)
                        depth = frame.get('depth')
                        if depth is not None:
                            msg.update(_process_depth(depth, depth_settings))
                        cam_params = frame.get('cam_params')
                        if cam_params is not None:
                            msg['cam_params'] = list(cam_params)
                        if msg:
                            _send(msg)
                else:
                    # ros_service / ros_action: trigger on capture signal
                    import time
                    while not stop.is_set():
                        try:
                            asyncio.run_coroutine_threadsafe(
                                capture_queue.get(), loop
                            ).result(timeout=1.0)
                        except Exception:
                            continue
                        if stop.is_set():
                            break
                        try:
                            data = entry.client.send({})
                            msg = _decode_frame(data, depth_settings)
                            if msg:
                                _send(msg)
                        except Exception as e:
                            _send({'error': str(e)})
        except Exception as e:
            _send({'error': str(e)})

    t = threading.Thread(target=stream, daemon=True)
    with _streams_lock:
        _streams[connect_id] = (stop, t)
    t.start()

    try:
        while not stop.is_set():
            try:
                text = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                try:
                    msg = json.loads(text)
                    if msg.get('capture'):
                        await capture_queue.put(True)
                    if 'depth_range' in msg:
                        rng = msg['depth_range']
                        if rng is None:
                            depth_settings['dmin'] = None
                            depth_settings['dmax'] = None
                        else:
                            try:
                                depth_settings['dmin'] = float(rng[0])
                                depth_settings['dmax'] = float(rng[1])
                            except (TypeError, ValueError, IndexError):
                                pass
                    if 'depth_mode' in msg and msg['depth_mode'] in ('colored', 'raw'):
                        depth_settings['mode'] = msg['depth_mode']
                except Exception:
                    pass
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()
        with _streams_lock:
            _streams.pop(connect_id, None)
