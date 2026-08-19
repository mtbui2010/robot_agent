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


def text2voice(text, lang=None, run_thread=True, slow=False, force=False):
    """Speak *text* through the local audio device via gTTS.

    Args:
        lang: ``'ko'`` / ``'en'``; auto-detected from *text* when None.
        run_thread: play in the background instead of blocking the caller.
        force: speak even while skill-level TTS is muted (announcer path).
    """
    if _SKILL_TTS_MUTED and not force:
        return
    if not DO_TEXT2VOICE:
        return

    lang = detect_lang(text) if lang is None else lang
    lang = 'en' if lang != 'ko' else lang

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
