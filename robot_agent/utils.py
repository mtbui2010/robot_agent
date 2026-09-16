"""Robot-agnostic helpers shared by skills and the agent runtime.

Everything here is generic: error wrapping, input parsing, speech, angle math.
Robot-specific helpers (environment lookups, lift heights, camera intrinsics,
Korean announcement phrasing, …) belong in the robot package's own
``utils`` module — see ``kcare_robot/utils.py`` for the reference layout.
"""

import io
import math
import threading

import numpy as np

from robot_agent.connect.helpers import get_attrs, set_atrrs, update_dict  # noqa: F401
from robot_agent.connect.helpers import evaluate
from robot_agent.connect.llm import init_llm_client
from robot_agent.connect.parallel import run_parallel, run_parallel_check  # noqa: F401
from robot_agent.connect.serde import dict2str, str2dict  # noqa: F401
from robot_agent.skill_configs import DO_TEXT2VOICE

try:
    from langdetect import detect as detect_lang
    from gtts import gTTS
    from pydub import AudioSegment
    import sounddevice as sd

except Exception as e:
    print(e)


# ---------------------------------------------------------------------------
# LLM access
# ---------------------------------------------------------------------------

_llm_client = None
_llm_client_lock = threading.Lock()


def _llm(llm_cfg: dict | None = None):
    """The shared LLM client for utility calls (translation, phrasing).

    Lazily created and cached behind a lock so parallel skills share one
    connection. *llm_cfg* is only honoured on first use; when omitted, the
    dashboard's active ``type='llm'`` connection is used.
    """
    global _llm_client
    if _llm_client is None:
        with _llm_client_lock:
            if _llm_client is None:              # double-checked locking
                cfg = llm_cfg
                if not cfg:
                    from robot_agent.state import current
                    cfg = current().dm.active_llm_config()
                _llm_client = init_llm_client(cfg=cfg)
    return _llm_client


def translate(text: str, to_language: str = 'korean', llm_cfg: dict | None = None) -> str:
    """Translate *text* into *to_language* using the active LLM."""
    return _llm(llm_cfg).chat(
        prompt=f'Translate to daily {to_language}. Output result only: {text}')


# ---------------------------------------------------------------------------
# Speech
# ---------------------------------------------------------------------------
# The plan-level announcer owns milestone speech; the closed-loop driver mutes
# skill-level TTS to avoid double-speak. text2voice honors this flag unless
# called with force=True (the announcer path).
_SKILL_TTS_MUTED = False


def set_skill_tts_muted(v: bool):
    global _SKILL_TTS_MUTED
    _SKILL_TTS_MUTED = bool(v)


def skill_tts_muted() -> bool:
    return _SKILL_TTS_MUTED


_TTS_LANGS = ('ko', 'en', 'vi')


def text2voice(text, lang=None, run_thread=True, slow=False, force=False):
    """Speak *text* through the local audio device via gTTS.

    Args:
        lang: ``'ko'`` / ``'en'`` / ``'vi'``; auto-detected from *text* when
            None. Anything else is spoken with the English voice.
        run_thread: play in the background instead of blocking the caller.
        force: speak even while skill-level TTS is muted (announcer path).
    """
    if _SKILL_TTS_MUTED and not force:
        return
    if not DO_TEXT2VOICE:
        return

    lang = detect_lang(text) if lang is None else lang
    lang = lang if lang in _TTS_LANGS else 'en'

    def func():
        try:
            spk = gTTS(text=text, lang=lang, slow=slow)
            mp3_fp = io.BytesIO()
            spk.write_to_fp(mp3_fp)
            mp3_fp.seek(0)

            audio = AudioSegment.from_file(mp3_fp, format='mp3')
            audio = audio.set_channels(1).set_frame_rate(44100)
            samples = np.array(audio.get_array_of_samples()).astype(np.float32)
            samples /= np.iinfo(audio.array_type).max

            sd.play(samples, samplerate=audio.frame_rate)
            sd.wait()
            return True
        except Exception as e:
            print(e)
            return False

    if run_thread:
        threading.Thread(target=func, daemon=True).start()
    else:
        func()


def voice2text(audio_obj):
    """Transcribe *audio_obj* through the registered ``vlms`` TCP connection."""
    from robot_agent.state import current
    client = current().dm.get_client('vlms')
    if client is None:
        raise RuntimeError("TCP connect 'vlms' not registered — add it in the Connection panel")
    return client.send({'audio_obj': audio_obj, 'detector': 'audio'})


# ---------------------------------------------------------------------------
# Listening (HRI skills)
# ---------------------------------------------------------------------------
# Two ways to hear the user, picked per call by the skill:
#   * the robot's own microphone — record_phrase() + speech_to_text();
#   * the dashboard's browser mic — listen_dashboard(), answered through
#     POST /agent/listen/{id} (api/agent.py) calling submit_transcript().

MIC_SAMPLERATE = 16000       # what the Whisper server expects
_MIC_BLOCK = 1024            # samples per callback, ~64 ms at 16 kHz
_PREROLL_SEC = 0.3           # kept from before onset so the first syllable survives


def _mic_energy(block: np.ndarray) -> float:
    """RMS on the same x1e5 scale pydevice.audio.Micro thresholds against."""
    return float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))) * 1e5


def segment_phrase(blocks, samplerate: int = MIC_SAMPLERATE, max_sec: float = 8.0,
                   silence_sec: float = 1.5, energy_threshold: float = 800,
                   min_phrase_sec: float = 0.3, wait_sec: float | None = None):
    """Cut one spoken phrase out of a stream of float32 mono audio blocks.

    Energy VAD: a block louder than `energy_threshold` is speech. Recording
    starts at the first speech block (plus a short pre-roll) and ends once
    `silence_sec` passes without speech, or `max_sec` after onset. Returns None
    when nobody starts talking within `wait_sec` (default `max_sec`), when the
    stream runs out first, or when the speech was shorter than `min_phrase_sec`
    (a cough, a door).

    Time is counted in samples, not wall clock, so this is exact on recorded
    or synthetic audio as well as a live stream.
    """
    wait_sec = max_sec if wait_sec is None else wait_sec
    preroll_n = int(_PREROLL_SEC * samplerate)
    pre: list[np.ndarray] = []
    phrase: list[np.ndarray] = []
    t = 0                     # samples consumed
    onset = last_voice = None
    voiced = 0                # samples in blocks above threshold

    for block in blocks:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        n = len(block)
        loud = _mic_energy(block) > energy_threshold
        t += n
        if onset is None:
            if not loud:
                pre.append(block)
                while pre and sum(len(b) for b in pre) - len(pre[0]) >= preroll_n:
                    pre.pop(0)
                if t >= wait_sec * samplerate:
                    return None
                continue
            onset = t - n
            phrase = pre + [block]
            pre = []
        else:
            phrase.append(block)
        if loud:
            last_voice = t
            voiced += n
        if t - last_voice >= silence_sec * samplerate or t - onset >= max_sec * samplerate:
            break
    else:
        if onset is None:
            return None

    if voiced < min_phrase_sec * samplerate:
        return None
    return np.concatenate(phrase).astype(np.float32)


def record_phrase(max_sec: float = 8.0, silence_sec: float = 1.5,
                  energy_threshold: float = 800, min_phrase_sec: float = 0.3,
                  wait_sec: float | None = None, device=None):
    """Record one phrase from the robot's microphone (see segment_phrase).

    Returns float32 mono audio at 16 kHz in [-1, 1] — the format the ``vlms``
    Whisper server takes — or None when nobody spoke.
    """
    import queue
    q: queue.Queue = queue.Queue()
    wait_sec = max_sec if wait_sec is None else wait_sec
    # Hard stop in wall-clock time too, in case the device stalls and blocks
    # stop arriving.
    deadline_sec = wait_sec + max_sec + 2.0

    def callback(indata, frames, time_info, status):
        q.put(indata[:, 0].copy())

    def blocks():
        import time
        end = time.monotonic() + deadline_sec
        while time.monotonic() < end:
            try:
                yield q.get(timeout=0.5)
            except queue.Empty:
                continue

    with sd.InputStream(samplerate=MIC_SAMPLERATE, channels=1, dtype='float32',
                        blocksize=_MIC_BLOCK, device=device, callback=callback):
        return segment_phrase(blocks(), MIC_SAMPLERATE, max_sec=max_sec,
                              silence_sec=silence_sec, energy_threshold=energy_threshold,
                              min_phrase_sec=min_phrase_sec, wait_sec=wait_sec)


def speech_to_text(audio, lang: str | None = None) -> str:
    """Transcribe robot-mic audio with the ``vlms`` Whisper server.

    `lang` pins Whisper's language ('ko', 'en', 'vi', ...) instead of letting it
    guess from a few seconds of audio, which it gets wrong on short answers.
    Returns the transcript stripped, '' when nothing was recognised.
    """
    from robot_agent.state import current
    client = current().dm.get_client('vlms')
    if client is None:
        raise RuntimeError("TCP connect 'vlms' not registered — add it in the Connection panel")
    req = {'audio_obj': np.asarray(audio, dtype=np.float32), 'detector': 'audio'}
    if lang:
        req['language'] = lang       # forwarded to whisper.transcribe()
    res = client.send(req)
    if isinstance(res, dict):
        res = res.get('text', '')
    return str(res or '').strip()


_LISTEN_PENDING: dict[str, dict] = {}
_LISTEN_LOCK = threading.Lock()


def listen_dashboard(prompt: str | None = None, lang: str = 'ko', max_sec: float = 8.0,
                     timeout: float = 30.0):
    """Have the dashboard capture one spoken phrase with the browser mic.

    Emits a ``listen`` event over the running agent's WebSocket; the dashboard
    says `prompt` (if any), listens, and posts the transcript to
    ``/agent/listen/{id}``. Blocks until then or `timeout`.

    Returns the transcript, or None when nobody answered in time. Raises when
    there is no dashboard run to ask, or when the browser reports a real
    failure (microphone blocked, recognition unsupported) — re-asking would not
    help with either.
    """
    import uuid
    from robot_agent.skills import has_emitter, log_data
    if not has_emitter():
        raise RuntimeError("source='dashboard' needs a run started from the dashboard's "
                           "Agent panel — use source='robot' from the CLI or /skill")
    req_id = uuid.uuid4().hex[:12]
    slot = {'event': threading.Event(), 'text': None, 'error': None}
    with _LISTEN_LOCK:
        _LISTEN_PENDING[req_id] = slot
    try:
        log_data({'listen': {'id': req_id, 'lang': lang, 'max_sec': max_sec,
                             'prompt': prompt or ''}})
        slot['event'].wait(timeout)
    finally:
        with _LISTEN_LOCK:
            _LISTEN_PENDING.pop(req_id, None)
    if slot['error']:
        raise RuntimeError(f'dashboard microphone: {slot["error"]}')
    return (slot['text'] or '').strip() or None


def submit_transcript(req_id: str, text: str, error: str | None = None) -> bool:
    """Deliver the dashboard's answer to a waiting listen_dashboard() call.
    False when nothing is waiting under that id (timed out, or unknown)."""
    with _LISTEN_LOCK:
        slot = _LISTEN_PENDING.get(req_id)
    if slot is None:
        return False
    slot['text'], slot['error'] = text, error
    slot['event'].set()
    return True


def cancel_pending_listens() -> int:
    """Release every skill blocked on the dashboard microphone. Returns how many.

    Called when a run is cancelled: ``listen_dashboard`` would otherwise keep
    the plan alive until its timeout, waiting for an answer nobody is going to
    give. The waiter sees ``error='cancelled'`` and raises, which the HRI skill
    reports as a failed step.
    """
    with _LISTEN_LOCK:
        slots = list(_LISTEN_PENDING.values())
    for slot in slots:
        slot['error'] = 'cancelled'
        slot['event'].set()
    return len(slots)


def say_to_user(text: str, lang: str = 'ko', source: str = 'robot') -> None:
    """Say a line as part of a conversation, and return once it has been said.

    source='robot' plays it on the robot speaker, blocking, so a following
    listen does not record the robot's own voice. It speaks even while skill
    TTS is muted: that mute exists to stop skills narrating over the plan
    announcer, and a question is not narration. source='dashboard' sends it to
    the browser, which voices it in order before any listen queued after it.
    """
    if source == 'dashboard':
        from robot_agent.skills import has_emitter, log_data
        if has_emitter():
            log_data({'speak': text, 'speak_lang': lang})
            return
        print(f'[say_to_user] no dashboard run attached; using the robot speaker: {text}')
    text2voice(text, lang=lang, run_thread=False, force=True)


# ---------------------------------------------------------------------------
# Skill plumbing
# ---------------------------------------------------------------------------

def exception_handler(func):
    """Turn any exception into the ``{'isdone': False, 'msg': ...}`` skill contract.

    Every skill entry point is wrapped with this so a raised exception becomes a
    failed step the planner can react to, instead of killing the request. The
    traceback is logged, and echoed back to the caller only when
    ``ROBOT_AGENT_DEBUG_RESPONSE`` is on.
    """
    import logging, traceback as _tb
    _logger = logging.getLogger('robot_agent.skill')

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            tb = _tb.format_exc()
            _logger.error(f'Exception in {func.__name__}: {e}\n{tb}')
            result = {'isdone': False, 'msg': f"'Exception in {func.__name__}': {e}"}
            try:
                from robot_agent.logging_config import debug_response_enabled
                if debug_response_enabled():
                    result['func'] = func.__name__
                    result['traceback'] = tb
            except Exception:
                pass
            return result
    return wrapper


def refine_inputs(inputs):
    """Parse a skill's ``inputs`` string into a params dict.

    ``"x=1, y=2"`` → ``{'x': 1, 'y': 2}``; anything without ``=`` becomes
    ``{'inputs': <value>}``.

    Args:
        inputs: Input string (or a bare number) to parse.

    Returns:
        dict: Parsed input dictionary.
    """
    if isinstance(inputs, (int, float)):
        return {'inputs': inputs}

    if len(inputs.strip()) == 0:
        return {}
    if '=' not in inputs:
        inputs = evaluate(inputs, recursive=False)
        inputs = f'inputs="{inputs}"' if isinstance(inputs, str) else f'inputs={inputs}'
    return eval(f'dict({inputs})')


# ---------------------------------------------------------------------------
# Angle conversion
# ---------------------------------------------------------------------------

def quaternion2deg(qx, qy, qz, qw):
    """Quaternion → (roll, pitch, yaw) in degrees."""
    # Roll (x-axis rotation)
    sinr_cosp = 2 * (qw * qx + qy * qz)
    cosr_cosp = 1 - 2 * (qx * qx + qy * qy)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    # Pitch (y-axis rotation)
    sinp = 2 * (qw * qy - qz * qx)
    if abs(sinp) >= 1:
        pitch = math.copysign(math.pi / 2, sinp)  # 90 degrees if out of range
    else:
        pitch = math.asin(sinp)

    # Yaw (z-axis rotation)
    siny_cosp = 2 * (qw * qz + qx * qy)
    cosy_cosp = 1 - 2 * (qy * qy + qz * qz)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def deg2quaternion(roll_deg, pitch_deg, yaw_deg):
    """(roll, pitch, yaw) in degrees → quaternion (qx, qy, qz, qw)."""
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    yaw = math.radians(yaw_deg)

    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)

    qw = cr * cp * cy + sr * sp * sy
    qx = sr * cp * cy - cr * sp * sy
    qy = cr * sp * cy + sr * cp * sy
    qz = cr * cp * sy - sr * sp * cy

    return qx, qy, qz, qw
