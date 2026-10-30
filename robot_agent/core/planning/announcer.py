"""Plan-level voice announcer.

Computes one localized phrase per milestone, optionally speaking it on the
robot speaker (backend TTS) in a daemon thread so speech never blocks the
control loop. The driver attaches the returned text to streamed events as
`say`, and the frontend speaks the same string via the browser. One
phrasebook, shared contract — no duplicated phrasing in TS + Python.

Planner/robot-agnostic; lives under robot_agent.core.planning. The core
ships English and Vietnamese; a robot adds its languages (and overrides any
phrase) through its adapter's ``i18n``: ``{kind: {lang: template}}`` and
``{'verb.<action>': {lang: verb}}``.
"""
from __future__ import annotations

import threading


# Map run language -> gTTS voice code (a two-letter code is used as is).
_GTTS_CODE = {'vi': 'vi', 'en': 'en'}

# Per-action localized verbs (resolved into {verb} when ctx carries 'action').
# Keys cover both GRACE actions (CamelCase) and the skill names used by the
# direct/structured path (lowercase, e.g. ``find::apple``).
VERBS: dict[str, dict[str, str]] = {
    'vi': {
        'MoveTo': 'đi tới', 'GoTo': 'đi tới', 'Navigate': 'đi tới',
        'Find': 'tìm', 'Search': 'tìm', 'Detect': 'tìm',
        'Pick': 'lấy', 'PickUp': 'lấy', 'Grasp': 'lấy',
        'Place': 'đặt', 'PlaceAt': 'đặt', 'PutDown': 'đặt', 'PutIn': 'đặt',
        'Open': 'mở', 'Close': 'đóng',
        # direct-path skills
        'navigate': 'đi tới', 'move': 'đi tới', 'moveto': 'đi tới',
        'find': 'tìm', 'detect': 'tìm', 'detect_object': 'tìm',
        'pick': 'lấy', 'grasp': 'lấy',
        'place': 'đặt', 'put': 'đặt',
        'open': 'mở', 'close': 'đóng',
    },
    'en': {
        'MoveTo': 'move to', 'GoTo': 'move to', 'Navigate': 'move to',
        'Find': 'find', 'Search': 'find', 'Detect': 'find',
        'Pick': 'pick', 'PickUp': 'pick', 'Grasp': 'pick',
        'Place': 'place', 'PlaceAt': 'place', 'PutDown': 'place', 'PutIn': 'place',
        'Open': 'open', 'Close': 'close',
        # direct-path skills
        'navigate': 'move to', 'move': 'move to', 'moveto': 'move to',
        'find': 'find', 'detect': 'find', 'detect_object': 'find',
        'pick': 'pick', 'grasp': 'pick',
        'place': 'place', 'put': 'place',
        'open': 'open', 'close': 'close',
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
        'llm_down':     'Tôi không kết nối được với mô hình ngôn ngữ, nên phải dừng nhiệm vụ này',
    },
    'en': {
        'task_start':   'Starting the task',
        'step_start':   'I will {verb} {object}',
        'step_success': 'Done: {verb} {object}',
        'step_fail':    'Failed to {verb} {object}. {reason}',
        'replan':       'Replanning',
        'done_success': 'Task completed',
        'done_fail':    'Task failed',
        'plan_ready':   'Plan ready',
        'unsupported':  'Unsupported action',
        'llm_down':     "I can't reach my language model right now, so I have to stop this task",
    },
}

# Last-resort generic phrase per language (when a kind is missing everywhere).
_GENERIC = {'vi': 'Đang thực hiện', 'en': 'Working'}

# Skills that speak to the user themselves (HRI reply / ask). Their steps are
# not narrated: announcing "doing reply" talks over the skill's own question,
# and with source='dashboard' the browser mic is open right then, so the
# narration gets transcribed as if the user had said it. 'reply' / 'ask' are
# this stack's conventional HRI entry points (robot_template emits the same
# pair); a robot adds its own through the adapter's ``quiet_skills``.
SILENT_ACTIONS = {'reply', 'ask', 'announce', 'wait'}   # wait: a pause, nothing to tell

# Only per-step narration is suppressed. Plan-level milestones (plan_ready,
# done_success, ...) carry no action and play when no mic is open.
_STEP_KINDS = ('step_start', 'step_success', 'step_fail')

# Milestones never spoken, in any language: "plan ready" only delays the robot
# getting on with it (and talked over HRI skills right as the mic opened).
SILENT_KINDS = {'plan_ready'}


def _eul(word) -> str:
    """Korean object particle for *word*: 을 after a final consonant (이동을),
    를 after a vowel (찾기를). A verb written in the plan (``이동->move``) can
    end either way, so the template cannot hard-code one."""
    w = str(word or '').rstrip()
    c = ord(w[-1]) if w else 0
    return '을' if 0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28 else '를'


class _BlankDict(dict):
    """Format mapping that yields '' for any missing key."""

    def __missing__(self, key: str) -> str:  # noqa: D401
        return ''


class Announcer:
    """Localized milestone announcer with optional backend speech.

    ``i18n`` (a ``robot_agent.contract.Messages``) and ``quiet`` (skills that speak for
    themselves) come from the robot's adapter; left out, the running robot's are used.
    """

    def __init__(self, lang: str = 'en', speak_backend: bool = True, i18n=None, quiet=None) -> None:
        if i18n is None or quiet is None:
            from .robot import current_robot
            robot = current_robot()
            if i18n is None and robot is not None:
                i18n = robot.i18n
            if quiet is None and robot is not None:
                quiet = robot.quiet_skills
        self.phrases: dict = dict(getattr(i18n, 'phrases', None) or {})
        self.quiet = SILENT_ACTIONS | set(quiet or ())
        robot_langs = {lg for by_lang in self.phrases.values() if isinstance(by_lang, dict) for lg in by_lang}
        self.lang = lang if (lang in PHRASES or lang in robot_langs) else 'en'
        self.speak_backend = speak_backend

    # -- phrase resolution ---------------------------------------------------
    def _robot(self, key: str) -> str | None:
        by_lang = self.phrases.get(key)
        return by_lang.get(self.lang) if isinstance(by_lang, dict) else None

    def _template(self, kind: str) -> str:
        tmpl = self._robot(kind)
        if tmpl is None:
            tmpl = (PHRASES.get(self.lang) or {}).get(kind)
        if tmpl is None:
            tmpl = PHRASES['en'].get(kind)
        if tmpl is None:
            tmpl = self._robot('generic') or _GENERIC.get(self.lang, _GENERIC['en'])
        return tmpl

    def _resolve_verb(self, ctx: dict) -> dict:
        """Inject {verb} from {action} using the language verb table."""
        if 'verb' in ctx or 'action' not in ctx:
            return ctx
        action = ctx.get('action')
        verbs = VERBS.get(self.lang, VERBS['en'])
        verb = self._robot(f'verb.{action}') or verbs.get(action)
        if verb is None and isinstance(action, str):
            verb = action.lower().replace('_', ' ')     # pick_top → "pick top"
        out = dict(ctx)
        out['verb'] = verb or ''
        return out

    def say_for(self, kind: str, **ctx) -> str:
        """Return the localized phrase for `kind` without speaking.

        Returns '' for a step of a conversational skill (see SILENT_ACTIONS):
        `announce` then speaks nothing, and the dashboard skips a falsy `say`.
        """
        if kind in SILENT_KINDS:
            return ''
        if kind in _STEP_KINDS and str(ctx.get('action') or '').strip() in self.quiet:
            return ''
        tmpl = self._template(kind)
        ctx = self._resolve_verb(ctx)
        ctx.setdefault('eul', _eul(ctx.get('verb', '')))
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
        code = _GTTS_CODE.get(self.lang, self.lang if len(self.lang) == 2 else 'en')

        def _run() -> None:
            try:
                from robot_agent import utils
                # force=True so plan-level speech bypasses the skill mute flag.
                utils.text2voice(text, lang=code, force=True)
            except Exception as e:  # never let speech break the loop
                print(e)

        threading.Thread(target=_run, daemon=True).start()
