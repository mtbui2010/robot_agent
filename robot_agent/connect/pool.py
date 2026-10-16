"""Server pools: one connection = one role (vlm, stt, …) served by an ordered list of servers.

The dev PC's 3090 runs Whisper, Ollama and visionserve together. When it is full a server fails (Whisper could
not load again on 2026-10-15, `ask` failed) or slips to the CPU (Ollama puts part of a model in RAM and an answer
takes 10× longer). A connection of kind ``vlms`` / ``visions`` / ``voices`` therefore lists several servers in
fallback order; a call goes to the first usable one and moves on to the next when it fails or is too slow::

    {"type": "vlms", "name": "vlm", "config": {"agent_name": "vlm", "cooldown_sec": 60, "servers": [
        {"provider": "ollama", "url": "http://192.168.0.6:11434", "model": "qwen3-vl:8b-instruct", "num_ctx": 8192},
        {"provider": "anthropic", "model": "claude-haiku-5-5"}]}}

- A failed server (no connection, timeout, HTTP error, "out of memory") is skipped for ``cooldown_sec`` (60 s);
  after that it is tried first again, so the pool returns to the main server once it works. A server found on
  the CPU (Ollama: ``/api/ps`` says part of the model is not in VRAM; Whisper / visionserve: two calls in a row
  slower than ``slow_sec``) is skipped the same way. When every server is in its cooldown they are still tried
  in order — a slow answer beats none.
- Only a whole request is sent again: these are inference calls, never robot motion.
- Every switch is logged (``vlm: #1 192.168.0.6 failed (…) → #2 …``).

Kinds and their servers:

| kind | server keys | client |
|---|---|---|
| ``vlms`` | ``provider`` (ollama / openai / gemini / anthropic), ``url``, ``model``, ``num_ctx``, ``api_key_env`` | :class:`VlmPool` ``chat(body)``; :class:`PoolLLM` for LLMobj callers |
| ``visions`` | ``url`` | visionserve ``Client`` methods through :class:`PoolProxy` |
| ``voices`` | ``url``, ``model`` | :class:`~robot_agent.connect.stt.WhisperClient` through :class:`PoolProxy` |

Every server may also carry ``enabled`` (false = skipped), ``slow_sec`` and ``timeout``. Cloud API keys are read
from the environment (the site's ``.env``, set from the Connections panel): ``OPENAI_API_KEY``,
``GOOGLE_API_KEY`` (or ``GEMINI_API_KEY``), ``ANTHROPIC_API_KEY`` — ``api_key_env`` names another variable.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

KINDS = ('vlms', 'visions', 'voices')
# Older single-server connection types and the kind they belong to.
LEGACY_KIND = {'llm': 'vlms', 'visionserve': 'visions', 'stt': 'voices'}

PROVIDERS = ('ollama', 'openai', 'gemini', 'anthropic')
CLOUD = ('openai', 'gemini', 'anthropic')
# Older provider names (the `llm` connection's `name`).
_PROVIDER_ALIAS = {'llama': 'ollama', 'chatgpt': 'openai', 'claude': 'anthropic', 'google': 'gemini'}
KEY_ENV = {'openai': ('OPENAI_API_KEY',), 'gemini': ('GOOGLE_API_KEY', 'GEMINI_API_KEY'),
           'anthropic': ('ANTHROPIC_API_KEY',)}
CLOUD_URL = {'openai': 'https://api.openai.com', 'gemini': 'https://generativelanguage.googleapis.com',
             'anthropic': 'https://api.anthropic.com'}
DEFAULT_SLOW = {'vlms': 20.0, 'visions': 8.0, 'voices': 5.0}
DEFAULT_TIMEOUT = {'vlms': 60.0, 'visions': 30.0, 'voices': 30.0}
CONNECT_TIMEOUT = 3.0
_OOM = re.compile(r'out of memory|cuda|cublas|memory', re.I)


class PoolError(RuntimeError):
    """Every server of a pool failed; the message lists why, per server."""


class _OnCpu(RuntimeError):
    """Ollama answered, but with part of the model in RAM."""


def provider_of(server: dict) -> str:
    p = str(server.get('provider') or server.get('name') or 'ollama').lower()
    return _PROVIDER_ALIAS.get(p, p)


def normalize_servers(kind: str, servers) -> list[dict]:
    """The `servers` list as saved, cleaned: dicts with a url (Ollama, visionserve, Whisper) or a model (cloud)."""
    out = []
    for s in servers or []:
        if not isinstance(s, dict):
            continue
        s = {k: v for k, v in s.items() if v not in (None, '')}
        if kind == 'vlms':
            s['provider'] = provider_of(s)
            if s['provider'] not in PROVIDERS:
                raise ValueError(f"unknown provider {s['provider']!r} (use one of {', '.join(PROVIDERS)})")
            if s['provider'] == 'ollama' and not s.get('url'):
                raise ValueError('an Ollama server needs a url, e.g. http://192.168.0.6:11434')
            if s['provider'] in CLOUD and not s.get('model'):
                raise ValueError(f"a {s['provider']} server needs a model")
        elif not s.get('url'):
            raise ValueError(f'every {kind} server needs a url')
        if 'url' in s:
            s['url'] = str(s['url']).strip().rstrip('/')
        s.setdefault('enabled', True)
        out.append(s)
    if not out:
        raise ValueError('add at least one server')
    return out


def legacy_servers(conn_type: str, config: dict) -> list[dict]:
    """The one server an older `llm` / `visionserve` / `stt` connection stands for."""
    if conn_type == 'llm':
        s = {'provider': provider_of(config)}
        for k in ('url', 'model', 'num_ctx', 'api_key_env'):
            if config.get(k):
                s[k] = config[k]
        if s['provider'] == 'ollama':
            s.setdefault('url', 'http://localhost:11434')
        return [s]
    s = {'url': str(config.get('url') or '').rstrip('/')}
    if conn_type == 'stt' and config.get('model'):
        s['model'] = config['model']
    if config.get('timeout'):
        s['timeout'] = config['timeout']
    return [s]


def server_label(kind: str, s: dict) -> str:
    """'qwen3-vl:8b-instruct @ 192.168.0.6' / 'anthropic claude-haiku-5-5' / '192.168.0.6:11435'."""
    host = urlparse(str(s.get('url', ''))).netloc or s.get('url', '')
    if kind == 'vlms':
        p = provider_of(s)
        return f"{s.get('model', '?')} @ {host}" if p == 'ollama' else f"{p} {s.get('model', '?')}"
    if kind == 'voices':
        return f"{host} {s['model'].split('/')[-1]}" if s.get('model') else host
    return host


def api_key(server: dict) -> str:
    names = [server['api_key_env']] if server.get('api_key_env') else list(KEY_ENV.get(provider_of(server), ()))
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    raise PermissionError(f"no API key: set {' or '.join(names)} (Connections panel → the server's key)")


def _short(e: Exception) -> str:
    if isinstance(e, requests.ConnectTimeout):
        return f'unreachable (no answer in {CONNECT_TIMEOUT:.0f} s)'
    if isinstance(e, requests.ReadTimeout):
        return 'timed out'
    if isinstance(e, requests.ConnectionError):
        return 'unreachable'
    if isinstance(e, requests.HTTPError) and e.response is not None:
        txt = e.response.text.strip().replace('\n', ' ')
        return f'HTTP {e.response.status_code}: {txt[:120]}'
    return str(e)[:160] or type(e).__name__


@dataclass
class ServerState:
    status: str = 'unknown'      # unknown | ok | slow (on the CPU / too slow) | down
    error: str = ''
    last_sec: float | None = None
    until: float = 0.0           # skipped until then (slow / down)
    checked: float = 0.0
    slow_hits: int = 0           # consecutive slow calls (Whisper / visionserve)


class ServerPool:
    """Ordered servers of one connection, with their state. Thread-safe."""

    def __init__(self, cid: str, kind: str, servers: list[dict], cooldown_sec: float = 60.0):
        self.cid, self.kind = cid, kind
        self.servers = servers
        self.cooldown = float(cooldown_sec or 60.0)
        self.state = [ServerState() for _ in servers]
        self.last_used: int | None = None
        self._clients: dict[int, object] = {}
        self._lock = threading.Lock()

    # ── order / state ────────────────────────────────────────────────────
    def enabled(self) -> list[int]:
        return [i for i, s in enumerate(self.servers) if s.get('enabled', True)]

    def primary(self) -> int | None:
        en = self.enabled()
        return en[0] if en else None

    def order(self) -> list[int]:
        """Enabled servers, the ones not in a cooldown first (each group in the configured order)."""
        now = time.time()
        en = self.enabled()
        return [i for i in en if self.state[i].until <= now] + [i for i in en if self.state[i].until > now]

    def label(self, i: int) -> str:
        return f'#{i + 1} {server_label(self.kind, self.servers[i])}'

    def mark(self, i: int, status: str, error: str = '', sec: float | None = None):
        with self._lock:
            st = self.state[i]
            st.status, st.error, st.checked = status, error, time.time()
            if sec is not None:
                st.last_sec = round(sec, 2)
            st.until = time.time() + self.cooldown if status in ('slow', 'down') else 0.0
            if status != 'slow':
                st.slow_hits = 0

    def level(self) -> str:
        """ok: the main server works · warn: it does not, another does · error: none does · unknown: not tried."""
        en = self.enabled()
        if not en:
            return 'error'
        sts = [self.state[i].status for i in en]
        if sts[0] == 'ok':
            return 'ok'
        if 'ok' in sts or 'slow' in sts:
            return 'warn'
        if all(s == 'down' for s in sts):
            return 'error'
        return 'unknown'

    def status(self) -> dict:
        now = time.time()
        return {
            'level': self.level(),
            'using': self.last_used,
            'servers': [{'idx': i, 'label': server_label(self.kind, s), 'enabled': bool(s.get('enabled', True)),
                         'provider': provider_of(s) if self.kind == 'vlms' else None,
                         'status': self.state[i].status if s.get('enabled', True) else 'off',
                         'error': self.state[i].error, 'last_sec': self.state[i].last_sec,
                         'cooldown': max(0, round(self.state[i].until - now))}
                        for i, s in enumerate(self.servers)],
        }

    # ── calling ──────────────────────────────────────────────────────────
    def client(self, i: int):
        """The per-server client (visionserve Client / WhisperClient / a requests.Session)."""
        with self._lock:
            if i not in self._clients:
                self._clients[i] = self._make_client(self.servers[i])
            return self._clients[i]

    def _make_client(self, s: dict):
        if self.kind == 'visions':
            from visionserve import Client
            # one timeout for connect + answer (urllib): the TCP check in call() catches a dead host sooner
            return Client(s['url'], timeout=float(s.get('timeout') or DEFAULT_TIMEOUT['visions']))
        if self.kind == 'voices':
            from .stt import WhisperClient
            return WhisperClient(url=s['url'], model=s.get('model', ''),
                                 timeout=float(s.get('timeout') or DEFAULT_TIMEOUT['voices']))
        return requests.Session()

    def _slow_sec(self, s: dict) -> float:
        return float(s.get('slow_sec') or DEFAULT_SLOW[self.kind])

    def call(self, fn, what: str = ''):
        """fn(i, client) on the first server that answers; PoolError when none does."""
        errors, first = [], None
        for i in self.order():
            first = i if first is None else first
            t0 = time.time()
            try:
                if self.kind == 'visions':
                    _tcp_check(self.servers[i]['url'])
                out = fn(i, self.client(i))
            except Exception as e:
                why = _short(e)
                self.mark(i, 'down', why, time.time() - t0)
                errors.append(f'{self.label(i)}: {why}')
                continue
            dt = time.time() - t0
            self._after_call(i, dt)
            self.last_used = i
            if errors:
                _log(f"{self.cid}: {'; '.join(errors)} → {self.label(i)} ({dt:.1f} s){' ' + what if what else ''}")
            return out
        if first is None:
            raise PoolError(f"{self.cid}: no server enabled")
        raise PoolError(f"{self.cid}: every server failed — {'; '.join(errors)}")

    def _after_call(self, i: int, dt: float):
        s = self.servers[i]
        if dt <= self._slow_sec(s):
            self.mark(i, 'ok', '', dt)
            return
        if self.kind == 'vlms' and provider_of(s) == 'ollama':
            cpu = self._ollama_cpu(i)                # a long answer is fine; part of the model in RAM is not
            if cpu:
                self.mark(i, 'slow', cpu, dt)
                _log(f'{self.cid}: {self.label(i)} {cpu} — the next calls go to the next server for '
                     f'{self.cooldown:.0f} s')
            else:
                self.mark(i, 'ok', '', dt)
            return
        # Whisper / visionserve: the first call of a model loads it, so only a second slow call in a row counts
        with self._lock:
            hits = self.state[i].slow_hits + 1
            self.state[i].slow_hits = hits
        if hits >= 2:
            self.mark(i, 'slow', f'slow: {dt:.1f} s (> {self._slow_sec(s):.0f} s), on the CPU?', dt)
            _log(f'{self.cid}: {self.label(i)} slow twice ({dt:.1f} s) — next server for {self.cooldown:.0f} s')
        else:
            with self._lock:
                self.state[i].status, self.state[i].last_sec, self.state[i].checked = 'ok', round(dt, 2), time.time()

    def _ollama_cpu(self, i: int) -> str:
        """'' or 'on the CPU: 40 % of qwen3-vl:8b in RAM' from Ollama's /api/ps."""
        s = self.servers[i]
        try:
            r = requests.get(f"{s['url']}/api/ps", timeout=(CONNECT_TIMEOUT, 5))
            r.raise_for_status()
            for m in r.json().get('models', []):
                if m.get('name') == s.get('model') or m.get('model') == s.get('model'):
                    size, vram = float(m.get('size') or 0), float(m.get('size_vram') or 0)
                    if size and vram < 0.95 * size:
                        return f'on the CPU: {100 * (1 - vram / size):.0f} % of {s.get("model")} in RAM'
        except Exception:
            pass
        return ''

    # ── health / warm-up (one server) ────────────────────────────────────
    def check(self, i: int) -> bool:
        """Probe server i (cheap, no model load); updates its state. Cloud servers are not probed (it costs a
        request and says little): they keep the state of their last call."""
        s = self.servers[i]
        if self.kind == 'vlms' and provider_of(s) in CLOUD:
            return self.state[i].status in ('ok', 'unknown')
        t0 = time.time()
        try:
            if self.kind == 'vlms':
                requests.get(f"{s['url']}/api/version", timeout=(CONNECT_TIMEOUT, 3)).raise_for_status()
                cpu = self._ollama_cpu(i)
                if cpu:
                    self.mark(i, 'slow', cpu, None)
                    return True
            elif self.kind == 'visions':
                h = self.client(i).health()
                if (h or {}).get('status') != 'ok':
                    raise RuntimeError(f'health: {h}')
            else:
                if not self.client(i).ping():
                    raise RuntimeError(f"no answer from {s['url']}/health")
        except Exception as e:
            self.mark(i, 'down', _short(e))
            return False
        st = self.state[i]
        if st.status in ('unknown', 'down') or (st.status == 'slow' and st.until <= time.time()):
            self.mark(i, 'ok', '', time.time() - t0 if st.last_sec is None else None)
        return True

    def warm(self, i: int, vision_models=()) -> float:
        """Load the model on server i (or, for a cloud API, check the key and model). Seconds taken; raises."""
        s, t0 = self.servers[i], time.time()
        try:
            if self.kind == 'vlms':
                p = provider_of(s)
                if p == 'ollama':
                    r = requests.post(f"{s['url']}/api/generate",
                                      json={'model': s.get('model'), 'prompt': '', 'keep_alive': s.get('keep_alive', '30m')},
                                      timeout=(CONNECT_TIMEOUT, 180))
                    r.raise_for_status()
                    cpu = self._ollama_cpu(i)
                    if cpu:
                        raise _OnCpu(cpu)
                else:
                    _cloud_model_check(s)
            elif self.kind == 'voices':
                import numpy as np
                self.client(i).transcribe(np.zeros(8000, dtype=np.float32), lang='ko')   # 0.5 s of silence
            else:
                import numpy as np
                img = np.full((240, 320, 3), 128, dtype=np.uint8)
                c = self.client(i)
                for m in vision_models:
                    if m.startswith('siglip'):
                        c.predict(model=m, image=img)
                    else:
                        c.predict(model=m, image=img, prompt='object.')
        except Exception as e:
            self.mark(i, 'slow' if isinstance(e, _OnCpu) else 'down', _short(e), time.time() - t0)
            raise
        dt = time.time() - t0
        self.mark(i, 'ok', '', dt)
        return dt

    def close(self):
        for c in self._clients.values():
            try:
                c.close()
            except Exception:
                pass


def _tcp_check(url: str):
    """Raise unless something listens at url's host:port within CONNECT_TIMEOUT (~1 ms on the LAN)."""
    import socket
    u = urlparse(url)
    port = u.port or (443 if u.scheme == 'https' else 80)
    try:
        socket.create_connection((u.hostname, port), timeout=CONNECT_TIMEOUT).close()
    except OSError as e:
        raise ConnectionError(f'unreachable ({e.strerror or e})') from None


def _log(msg: str):
    print(f'[pool] {msg}')
    try:
        from robot_agent.skills import log_data
        log_data({'msg': msg})
    except Exception:
        pass


# ── vlms: one chat call, four APIs ──────────────────────────────────────────

def _cloud_model_check(s: dict):
    p, key = provider_of(s), api_key(s)
    base = (s.get('url') or CLOUD_URL[p]).rstrip('/')
    if p == 'openai':
        r = requests.get(f"{base}/v1/models/{s['model']}", headers={'Authorization': f'Bearer {key}'},
                         timeout=(CONNECT_TIMEOUT, 10))
    elif p == 'gemini':
        r = requests.get(f"{base}/v1beta/models/{s['model']}", headers={'x-goog-api-key': key},
                         timeout=(CONNECT_TIMEOUT, 10))
    else:
        r = requests.get(f"{base}/v1/models/{s['model']}",
                         headers={'x-api-key': key, 'anthropic-version': '2023-06-01'}, timeout=(CONNECT_TIMEOUT, 10))
    r.raise_for_status()


def _mime(b64: str) -> str:
    return 'image/png' if b64.startswith('iVBOR') else 'image/jpeg'


def _json_hint(fmt) -> str:
    if not fmt:
        return ''
    if isinstance(fmt, dict):
        return ('\n\nAnswer with JSON only (no code fences), matching this JSON schema:\n'
                + json.dumps(fmt, ensure_ascii=False))
    return '\n\nAnswer with JSON only (no code fences).'


def _strip_fences(text: str) -> str:
    m = re.match(r'^\s*```(?:json)?\s*(.*?)\s*```\s*$', text or '', re.S)
    return m.group(1) if m else (text or '')


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    system = '\n\n'.join(str(m.get('content', '')) for m in messages if m.get('role') == 'system')
    return system, [m for m in messages if m.get('role') != 'system']


def _chat_ollama(sess, s: dict, body: dict, timeout: float) -> str:
    b = dict(body)
    b['model'] = s.get('model') or b.get('model')
    b['stream'] = False
    opts = dict(b.get('options') or {})
    if s.get('num_ctx'):
        opts['num_ctx'] = int(s['num_ctx'])
    b['options'] = opts
    r = sess.post(f"{s['url']}/api/chat", json=b, timeout=(CONNECT_TIMEOUT, timeout))
    r.raise_for_status()
    out = r.json()
    if out.get('error'):
        raise RuntimeError(out['error'])
    return out.get('message', {}).get('content', '')


def _chat_openai(sess, s: dict, body: dict, timeout: float) -> str:
    system, msgs = _split_system(body.get('messages', []))
    system += _json_hint(body.get('format'))
    out = [{'role': 'system', 'content': system}] if system else []
    for m in msgs:
        imgs = m.get('images') or []
        if imgs:
            content = [{'type': 'text', 'text': str(m.get('content', ''))}] + [
                {'type': 'image_url', 'image_url': {'url': f'data:{_mime(b)};base64,{b}'}} for b in imgs]
        else:
            content = str(m.get('content', ''))
        out.append({'role': m.get('role', 'user'), 'content': content})
    opts = body.get('options') or {}
    req = {'model': s['model'], 'messages': out}
    if 'temperature' in opts:
        req['temperature'] = float(opts['temperature'])
    if opts.get('num_predict'):
        req['max_completion_tokens'] = int(opts['num_predict'])
    if body.get('format'):
        req['response_format'] = {'type': 'json_object'}
    base = (s.get('url') or CLOUD_URL['openai']).rstrip('/')
    headers = {'Authorization': f'Bearer {api_key(s)}'}
    r = sess.post(f'{base}/v1/chat/completions', json=req, headers=headers, timeout=(CONNECT_TIMEOUT, timeout))
    if r.status_code == 400 and 'temperature' in r.text and 'temperature' in req:
        req.pop('temperature')                       # some models only take the default
        r = sess.post(f'{base}/v1/chat/completions', json=req, headers=headers, timeout=(CONNECT_TIMEOUT, timeout))
    r.raise_for_status()
    return _strip_fences(r.json()['choices'][0]['message'].get('content') or '')


def _chat_gemini(sess, s: dict, body: dict, timeout: float) -> str:
    system, msgs = _split_system(body.get('messages', []))
    system += _json_hint(body.get('format'))
    contents = []
    for m in msgs:
        parts = [{'text': str(m.get('content', ''))}] + [
            {'inline_data': {'mime_type': _mime(b), 'data': b}} for b in (m.get('images') or [])]
        contents.append({'role': 'model' if m.get('role') == 'assistant' else 'user', 'parts': parts})
    opts = body.get('options') or {}
    gen = {}
    if 'temperature' in opts:
        gen['temperature'] = float(opts['temperature'])
    if opts.get('num_predict'):
        gen['maxOutputTokens'] = int(opts['num_predict'])
    if body.get('format'):
        gen['responseMimeType'] = 'application/json'
    if 'flash' in s['model'] and not body.get('think'):
        gen['thinkingConfig'] = {'thinkingBudget': 0}   # thinking would eat a short answer's token budget
    req = {'contents': contents, 'generationConfig': gen}
    if system:
        req['systemInstruction'] = {'parts': [{'text': system}]}
    base = (s.get('url') or CLOUD_URL['gemini']).rstrip('/')
    r = sess.post(f"{base}/v1beta/models/{s['model']}:generateContent", json=req,
                  headers={'x-goog-api-key': api_key(s)}, timeout=(CONNECT_TIMEOUT, timeout))
    r.raise_for_status()
    cands = r.json().get('candidates') or []
    parts = (cands[0].get('content') or {}).get('parts', []) if cands else []
    return _strip_fences(''.join(p.get('text', '') for p in parts if not p.get('thought')))


def _chat_anthropic(sess, s: dict, body: dict, timeout: float) -> str:
    system, msgs = _split_system(body.get('messages', []))
    system += _json_hint(body.get('format'))
    out = []
    for m in msgs:
        blocks = [{'type': 'image', 'source': {'type': 'base64', 'media_type': _mime(b), 'data': b}}
                  for b in (m.get('images') or [])]
        blocks.append({'type': 'text', 'text': str(m.get('content', '')) or '.'})
        out.append({'role': 'assistant' if m.get('role') == 'assistant' else 'user', 'content': blocks})
    opts = body.get('options') or {}
    req = {'model': s['model'], 'messages': out, 'max_tokens': int(opts.get('num_predict') or 1024)}
    if system:
        req['system'] = system
    if 'temperature' in opts:
        req['temperature'] = float(opts['temperature'])
    base = (s.get('url') or CLOUD_URL['anthropic']).rstrip('/')
    r = sess.post(f'{base}/v1/messages', json=req, timeout=(CONNECT_TIMEOUT, timeout),
                  headers={'x-api-key': api_key(s), 'anthropic-version': '2023-06-01'})
    r.raise_for_status()
    return _strip_fences(''.join(b.get('text', '') for b in r.json().get('content', []) if b.get('type') == 'text'))


_CHAT = {'ollama': _chat_ollama, 'openai': _chat_openai, 'gemini': _chat_gemini, 'anthropic': _chat_anthropic}


class VlmPool(ServerPool):
    """kind 'vlms'. ``chat(body)`` takes an Ollama /api/chat body (messages with base64 ``images``, ``format``,
    ``options.temperature / num_predict``) and returns the answer text, from whichever server answered."""

    def chat(self, body: dict, timeout: float | None = None) -> str:
        def one(i, sess):
            s = self.servers[i]
            t = float(timeout or s.get('timeout') or DEFAULT_TIMEOUT['vlms'])
            return _CHAT[provider_of(s)](sess, s, body, t)
        return self.call(one)

    def planner_server(self) -> dict | None:
        """The first usable Ollama server (the GRACE planner talks to Ollama directly)."""
        for i in self.order():
            if provider_of(self.servers[i]) == 'ollama':
                return self.servers[i]
        return None


def make_pool(cid: str, kind: str, servers: list[dict], cooldown_sec: float = 60.0) -> ServerPool:
    return (VlmPool if kind == 'vlms' else ServerPool)(cid, kind, servers, cooldown_sec)


# ── facades the callers use ─────────────────────────────────────────────────

class PoolProxy:
    """visionserve Client / WhisperClient look-alike: each method call goes through the pool; plain attributes
    (``model``, ``url``, ``last_dropped``) come from the server that served the last call."""

    def __init__(self, pool: ServerPool):
        self._pool = pool

    @property
    def pool(self):
        return self._pool

    def _current(self):
        i = self._pool.last_used if self._pool.last_used is not None else (self._pool.primary() or 0)
        return self._pool.client(i)

    def ping(self) -> bool:
        return any(self._pool.check(i) for i in self._pool.enabled())

    @property
    def server_connected(self) -> bool:
        return self.ping()

    def close(self):
        self._pool.close()

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        attr = getattr(self._current(), name)
        if not callable(attr):
            return attr

        def call(*a, **kw):
            return self._pool.call(lambda i, c: getattr(c, name)(*a, **kw), what=name)
        return call


def pool_llm(pool: VlmPool):
    """An LLMobj (``chat`` / ``chat_guide`` / history) whose requests go through the pool."""
    import cv2
    from .helpers import crop_image
    from .llm.llm_object import LLMobj

    class PoolLLM(LLMobj):
        def init(self, **_):
            self.pool = pool
            s = pool.servers[pool.primary() or 0]
            self.url, self.text_model = s.get('url', ''), s.get('model', '')
            self.vision_model = self.text_model

        def make_msg(self, prompt, rgb=None, rgb_path=None, crop_roi=None, **kwargs):
            msg = kwargs['guide'].replace('COMMAND_HERE', prompt) if 'guide' in kwargs else prompt
            msg = {'role': 'user', 'content': msg}
            if rgb is not None or rgb_path is not None:
                rgb = cv2.imread(rgb_path)[..., ::-1] if rgb is None else rgb
                rgb = crop_image(im=rgb, crop_roi=crop_roi, keep_size=False)
                msg['images'] = [self.encode_b64(rgb=rgb)]
            return msg

        def send_msgs(self, msgs, **kwargs):
            body = {'messages': msgs, 'stream': False}
            if kwargs.get('format') is not None:
                body['format'] = kwargs['format']
            out = self.pool.chat(body)
            used = self.pool.last_used
            self.current_model = self.pool.servers[used].get('model') if used is not None else None
            return out

    return PoolLLM()


# ── tidying a site's connections.json ───────────────────────────────────────

TIDY_ID = {'vlms': 'vlm', 'visions': 'visionserve', 'voices': 'whisper'}


def _server_key(kind: str, s: dict) -> tuple:
    if kind == 'vlms':
        return provider_of(s), s.get('url', ''), s.get('model', '')
    return (s.get('url', ''), s.get('model', '') if kind == 'voices' else '')


def tidy_connections(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """(new items, report) — every AI-server connection of a kind (vlms / visions / voices, and the older
    llm / visionserve / stt) merged into ONE connection of that kind (ids: vlm / visionserve / whisper), its
    servers in this order: the connection that already has the id first, then the others as they appear in the
    file, duplicates (same server and model) once. The new connection takes the place of the first one it
    replaces; every other item is kept as it is. The one vlms connection is the active one (the planner's).
    Pure: nothing is read or written."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        t = it.get('type')
        kind = t if t in KINDS else LEGACY_KIND.get(t)
        if kind:
            groups.setdefault(kind, []).append(it)
    out, report, placed = [], [], set()
    for it in items:
        t = it.get('type')
        kind = t if t in KINDS else LEGACY_KIND.get(t)
        if not kind:
            out.append(it)
            continue
        if kind in placed:
            continue
        placed.add(kind)
        members = groups[kind]
        cid = TIDY_ID[kind]
        ident = lambda m: (m.get('config') or {}).get('agent_name') or m.get('name')
        members = sorted(members, key=lambda m: ident(m) != cid)          # stable: the one named cid first
        servers, seen = [], set()
        for m in members:
            conf = m.get('config') or {}
            ss = conf.get('servers') if m.get('type') in KINDS else legacy_servers(m['type'], conf)
            for s in ss or []:
                key = _server_key(kind, s)
                if key in seen:
                    continue
                seen.add(key)
                servers.append(dict(s, enabled=s.get('enabled', True)))
        conf = {'agent_name': cid, 'servers': servers}
        if kind == 'vlms':
            conf['is_active'] = True          # the only vlms connection: the planner's too
        out.append({'type': kind, 'name': cid, 'config': conf})
        report.append({'kind': kind, 'id': cid, 'replaces': [f"{ident(m)} ({m.get('type')})" for m in members],
                       'servers': [server_label(kind, s) for s in servers]})
    return out, report
