"""Plan-level voice announcer.

Computes one localized phrase per milestone, optionally speaking it on the
robot speaker (backend TTS) in a daemon thread so speech never blocks the
control loop. The driver attaches the returned text to streamed events as
`say`, and the frontend speaks the same string via the browser. One
phrasebook, shared contract — no duplicated phrasing in TS + Python.

Planner/robot-agnostic; lives under robot_agent.core.planning.
"""
from __future__ import annotations

import threading


# Map run language -> gTTS voice code.
_GTTS_CODE = {'vi': 'vi', 'ko': 'ko', 'en': 'en'}

# Per-action localized verbs (resolved into {verb} when ctx carries 'action').
VERBS: dict[str, dict[str, str]] = {
    'vi': {
        'MoveTo': 'đi tới', 'GoTo': 'đi tới', 'Navigate': 'đi tới',
        'Find': 'tìm', 'Search': 'tìm', 'Detect': 'tìm',
        'Pick': 'lấy', 'PickUp': 'lấy', 'Grasp': 'lấy',
        'Place': 'đặt', 'PlaceAt': 'đặt', 'PutDown': 'đặt', 'PutIn': 'đặt',
        'Open': 'mở', 'Close': 'đóng',
    },
    'ko': {
        'MoveTo': '이동', 'GoTo': '이동', 'Navigate': '이동',
        'Find': '찾기', 'Search': '찾기', 'Detect': '찾기',
        'Pick': '집기', 'PickUp': '집기', 'Grasp': '집기',
        'Place': '놓기', 'PlaceAt': '놓기', 'PutDown': '놓기', 'PutIn': '놓기',
        'Open': '열기', 'Close': '닫기',
    },
    'en': {
        'MoveTo': 'move to', 'GoTo': 'move to', 'Navigate': 'move to',
        'Find': 'find', 'Search': 'find', 'Detect': 'find',
        'Pick': 'pick', 'PickUp': 'pick', 'Grasp': 'pick',
        'Place': 'place', 'PlaceAt': 'place', 'PutDown': 'place', 'PutIn': 'place',
        'Open': 'open', 'Close': 'close',
    },
}

# Localized templates keyed by [lang][kind]. step_* use {verb} and {object}.
PHRASES: dict[str, dict[str, str]] = {
    'vi': {
        'task_start':   'Bắt đầu nhiệm vụ',
        'step_start':   'Đang {verb} {object}',
        'step_success': 'Đã {verb} {object}',
        'step_fail':    'Lỗi khi {verb} {object}. {reason}',
        'replan':       'Đang lập lại kế hoạch',
        'done_success': 'Đã hoàn thành nhiệm vụ',
        'done_fail':    'Nhiệm vụ thất bại',
        'plan_ready':   'Đã tạo kế hoạch',
        'unsupported':  'Hành động không được hỗ trợ',
    },
    'ko': {
        'task_start':   '작업을 시작합니다',
        'step_start':   '{object} {verb}를 진행합니다',
        'step_success': '{object} {verb}를 완료했습니다',
        'step_fail':    '{object} {verb}에 실패했습니다. {reason}',
        'replan':       '계획을 다시 세웁니다',
        'done_success': '작업을 완료했습니다',
        'done_fail':    '작업에 실패했습니다',
        'plan_ready':   '계획을 생성했습니다',
        'unsupported':  '지원하지 않는 동작입니다',
    },
    'en': {
        'task_start':   'Starting the task',
        'step_start':   'I will {verb} {object}',
        'step_success': 'I have {verb} {object}',
        'step_fail':    'Failed to {verb} {object}. {reason}',
        'replan':       'Replanning',
        'done_success': 'Task completed',
        'done_fail':    'Task failed',
        'plan_ready':   'Plan ready',
        'unsupported':  'Unsupported action',
    },
}

# Last-resort generic phrase per language (when a kind is missing everywhere).
_GENERIC = {'vi': 'Đang thực hiện', 'ko': '진행 중입니다', 'en': 'Working'}


class _BlankDict(dict):
    """Format mapping that yields '' for any missing key."""

    def __missing__(self, key: str) -> str:  # noqa: D401
        return ''


class Announcer:
    """Localized milestone announcer with optional backend speech."""

    def __init__(self, lang: str = 'en', speak_backend: bool = True) -> None:
        self.lang = lang if lang in PHRASES else 'en'
        self.speak_backend = speak_backend

    # -- phrase resolution ---------------------------------------------------
    def _template(self, kind: str) -> str:
        table = PHRASES.get(self.lang) or PHRASES['en']
        tmpl = table.get(kind)
        if tmpl is None:
            tmpl = PHRASES['en'].get(kind)
        if tmpl is None:
            tmpl = _GENERIC.get(self.lang, _GENERIC['en'])
        return tmpl

    def _resolve_verb(self, ctx: dict) -> dict:
        """Inject {verb} from {action} using the language verb table."""
        if 'verb' in ctx or 'action' not in ctx:
            return ctx
        action = ctx.get('action')
        verbs = VERBS.get(self.lang, VERBS['en'])
        verb = verbs.get(action)
        if verb is None and isinstance(action, str):
            verb = action.lower()
        out = dict(ctx)
        out['verb'] = verb or ''
        return out

    def say_for(self, kind: str, **ctx) -> str:
        """Return the localized phrase for `kind` without speaking."""
        tmpl = self._template(kind)
        ctx = self._resolve_verb(ctx)
        try:
            return tmpl.format_map(_BlankDict(ctx)).strip()
        except Exception:
            return tmpl

    # -- speech --------------------------------------------------------------
    def announce(self, kind: str, **ctx) -> str:
        """Compute the phrase and (if enabled) speak it on the robot."""
        text = self.say_for(kind, **ctx)
        if text and self.speak_backend:
            self._speak(text)
        return text

    def _speak(self, text: str) -> None:
        code = _GTTS_CODE.get(self.lang, 'en')

        def _run() -> None:
            try:
                from robot_agent import utils
                # force=True so plan-level speech bypasses the skill mute flag.
                utils.text2voice(text, lang=code, force=True)
            except Exception as e:  # never let speech break the loop
                print(e)

        threading.Thread(target=_run, daemon=True).start()
