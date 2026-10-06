"""Speech-to-text over an OpenAI-compatible transcription API.

Connection type ``stt`` (Connections panel): ``{url, model, timeout}``, e.g. a
speaches / faster-whisper-server container::

    {'url': 'http://192.168.0.6:8000', 'model': 'deepdml/faster-whisper-large-v3-turbo-ct2'}

Used by ``robot_agent.utils.speech_to_text`` for both microphones: the robot's
(float32 samples at 16 kHz) and the dashboard's (a webm / ogg recording posted
by the browser). ``prompt`` is Whisper's initial prompt — words it should
expect (와인잔, 머그컵, …), which steers it on names it would otherwise mishear.
"""
from __future__ import annotations

import io
import re
import wave
from collections import Counter

import numpy as np
import requests


def _wav_bytes(samples, samplerate: int = 16000) -> bytes:
    """float32 mono in [-1, 1] → 16-bit PCM WAV."""
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1, 1) * 32767).astype('<i2')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# What Whisper says over silence or room noise (learnt from video subtitles):
# a transcript that is only one of these is dropped. Compared without
# punctuation / spaces.
_HALLUCINATIONS = {
    '감사합니다', '시청해주셔서감사합니다', '다음영상에서만나요', '구독과좋아요부탁드립니다',
    '구독과좋아요', '시청해주셔서고맙습니다', '좋아요와구독부탁드립니다',
    'thankyou', 'thanksforwatching', 'thankyouforwatching', 'pleasesubscribe', 'bye',
    'cảmơncácbạnđãxemvideo', 'hẹngặplạicácbạn',
}
_NORM = re.compile(r'[\W_]+', re.UNICODE)


def _repetitive(text: str) -> bool:
    """'아, 아, 아, …' — one word making up most of a longer transcript."""
    words = [w for w in _NORM.split(text.lower()) if w]
    if len(words) < 4:
        return False
    return Counter(words).most_common(1)[0][1] / len(words) >= 0.6


def clean_transcript(text: str) -> str:
    """'' for a known hallucination or a repetition loop, else `text`."""
    t = (text or '').strip()
    if not t or _NORM.sub('', t.lower()) in _HALLUCINATIONS or _repetitive(t):
        return ''
    return t


class WhisperClient:
    def __init__(self, url: str, model: str = '', timeout: float = 30.0, vad_filter: bool = True, **_):
        self.url = url.rstrip('/')
        self.model = model
        self.timeout = float(timeout)
        # Silero VAD on the server: cuts silence / noise before Whisper sees
        # it. Without it, 2 s of silence came back as "다음 영상에서 만나요."
        self.vad_filter = bool(vad_filter)
        self.last_dropped = ''           # the raw text of the last rejected transcript
        self._session = requests.Session()

    def ping(self) -> bool:
        try:
            return self._session.get(f'{self.url}/health', timeout=2.0).ok
        except requests.RequestException:
            return False

    @property
    def server_connected(self) -> bool:
        return self.ping()

    def transcribe(self, audio, lang: str | None = None, prompt: str | None = None,
                   mime: str = 'audio/wav', samplerate: int = 16000) -> str:
        """Transcript of `audio` — float32 samples, or the bytes of an encoded
        file (wav / webm / ogg; `mime` says which). '' when nothing was said."""
        if isinstance(audio, (bytes, bytearray)):
            data, name = bytes(audio), 'audio.' + (mime.split('/')[-1].split(';')[0] or 'webm')
        else:
            data, name, mime = _wav_bytes(audio, samplerate), 'audio.wav', 'audio/wav'
        form = {'response_format': 'verbose_json', 'temperature': '0',
                'vad_filter': 'true' if self.vad_filter else 'false'}
        if self.model:
            form['model'] = self.model
        if lang:
            form['language'] = lang
        if prompt:
            form['prompt'] = prompt
        r = self._session.post(f'{self.url}/v1/audio/transcriptions', data=form,
                               files={'file': (name, data, mime)}, timeout=self.timeout)
        r.raise_for_status()
        out = r.json()
        # Per segment: drop repetition loops (high compression ratio — '아, 아,
        # 아, …' compresses well) before joining.
        segs = out.get('segments')
        if segs:
            raw = ' '.join(str(sg.get('text', '')).strip() for sg in segs).strip()
            kept = ' '.join(str(sg.get('text', '')).strip() for sg in segs
                            if float(sg.get('compression_ratio') or 0) <= 2.4).strip()
        else:
            raw = kept = str(out.get('text', '') or '').strip()
        text = clean_transcript(kept)
        self.last_dropped = raw if raw and not text else ''
        return text

    def close(self):
        self._session.close()
