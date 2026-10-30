"""Classify what someone says while a run is active.

Nine kinds (proposal 3.3): STOP, PAUSE, RESUME, MODIFY, ADD, DROP, NEW, STATUS,
ANSWER. The router only decides; :meth:`RunManager.inject` acts.

Order, cheapest and safest first:

  1. STOP / PAUSE / RESUME by keyword, never through an LLM, so a stop is
     recognised in microseconds and also when the LLM server is down. English,
     Korean and Vietnamese. A keyword counts only in a short utterance (a
     command, not a sentence that mentions "stop"), as a whole word, and not
     under a negation ("don't stop", "멈추지 마", "đừng dừng");
  2. ANSWER when the run is waiting on a question;
  3. otherwise the injectable LLM classifier, ``llm(text, context) -> str |
     dict | IntentDecision``; when it is missing, raises, or returns something
     unusable, a deterministic keyword fallback.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional


class Intent(str, enum.Enum):
    STOP = 'stop'
    PAUSE = 'pause'
    RESUME = 'resume'
    MODIFY = 'modify'
    ADD = 'add'
    DROP = 'drop'
    NEW = 'new'
    STATUS = 'status'
    ANSWER = 'answer'


@dataclass
class IntentDecision:
    intent: Intent
    text: str
    source: str                     # keyword | awaiting | llm | fallback
    confidence: float = 1.0
    reason: str = ''

    def to_dict(self) -> dict:
        return {'intent': self.intent.value, 'text': self.text, 'source': self.source,
                'confidence': self.confidence, 'reason': self.reason}


# A control keyword is honoured only in an utterance at most this many words long.
MAX_CONTROL_WORDS = 4

# Whole phrases (lowercase, accents kept for Vietnamese). After the polite /
# vocative fillers around it are dropped, the utterance must BE the phrase,
# optionally followed by one of _TAIL ("stop now", "wait a second"). "bus stop",
# "stop at the door" or "đi thôi" (let's go) are therefore not commands.
_STOP = ['stop', 'halt', 'abort', 'cancel', 'freeze', 'emergency stop', 'stop the robot',
         'enough', 'that is enough', "that's enough",
         'dừng', 'dừng lại', 'ngừng', 'ngừng lại', 'thôi', 'huỷ', 'hủy']
_PAUSE = ['pause', 'wait', 'hold on', 'hang on', 'one moment', 'just a moment',
          'wait a moment', 'wait a second', 'wait a minute', 'one second',
          'tạm dừng', 'chờ', 'chờ đã', 'chờ chút', 'đợi', 'đợi đã', 'đợi chút', 'khoan', 'khoan đã']
_RESUME = ['resume', 'continue', 'go on', 'carry on', 'keep going', 'go ahead', 'proceed',
           'tiếp tục', 'làm tiếp', 'tiếp đi', 'đi tiếp']
_TAIL = {'now', 'it', 'that', 'everything', 'right now', 'immediately', 'moving', 'please',
         'robot', 'ngay', 'ngay lập tức', 'đi', 'nhé', 'ạ', 'lại'}
_HEAD = ['please', 'robot', 'ok', 'okay', 'hey', 'oh', 'ah', 'um', 'uh', 'just',
         'làm ơn', 'robot ơi', 'này', 'ơi']
# Korean: a token starting with the word ("멈춰요", "멈춰줘") — endings attach.
_STOP_KO = ['멈춰', '멈춰라', '멈추세요', '그만', '정지', '스톱', '중지', '취소', '서']
_PAUSE_KO = ['잠깐', '잠시만', '잠시', '기다려', '일시정지', '일시 정지', '멈춰 봐', '멈춰봐']
_RESUME_KO = ['계속', '다시 시작', '진행해', '재개']
_FILLER_KO = {'제발', '좀', '이제', '로봇', '야', '로봇아', '빨리', '당장'}

_NEG_EN = {"don't", 'dont', 'do not', 'never', 'not', 'no need to', "doesn't", "won't"}
_NEG_VI = ['đừng', 'không được', 'không cần', 'chưa']
_NEG_KO = ['지 마', '지마', '지 말', '지말', '말고', '않']

# Fallback keywords for the LLM kinds (substring, lowercase).
_FALLBACK = [
    (Intent.STATUS, ['what are you doing', 'status', 'where are you', 'how is it going',
                     'are you done', 'progress', 'what is happening',
                     '뭐 해', '뭐해', '뭐 하고', '어디야', '어디 있', '다 했', '진행 상황',
                     'đang làm gì', 'xong chưa', 'đến đâu rồi', 'ở đâu']),
    (Intent.DROP, ['forget', 'skip', 'never mind the', "don't bring", 'do not bring', 'leave out',
                   'no longer need', "don't need", 'drop the',
                   '빼', '안 가져와도', '필요 없', '하지 말고',
                   'bỏ', 'không cần', 'khỏi']),
    (Intent.MODIFY, ['instead', 'actually', 'not the', 'change', 'rather', 'i meant',
                     '대신', '말고', '아니라', '바꿔',
                     'thay vì', 'đổi', 'không phải', 'thay bằng']),
    (Intent.ADD, ['also', 'as well', 'too', 'and then', 'after that', 'one more',
                  '그리고', '도 ', '하나 더', '그 다음',
                  'thêm', 'nữa', 'sau đó', 'cũng']),
]

_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)


def normalize(text: str) -> str:
    t = unicodedata.normalize('NFC', str(text or '')).lower().strip()
    t = _PUNCT.sub(' ', t)
    return ' '.join(t.split())


def _hangul(t: str) -> bool:
    return any('가' <= c <= '힣' for c in t)


def _negated(t: str) -> bool:
    words = t.split()
    if any(w in _NEG_EN for w in words) or 'do not' in t or 'no need' in t:
        return True
    if any(n in t for n in _NEG_VI):
        return True
    return _hangul(t) and any(n in t for n in _NEG_KO)


def _strip_fillers(t: str) -> str:
    words = t.split()
    # "stop stop stop" -> "stop"
    words = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
    t = ' '.join(words)
    changed = True
    while changed and t:
        changed = False
        for h in _HEAD:
            if t.startswith(h + ' '):
                t, changed = t[len(h) + 1:], True
    return t


def _match_phrases(t: str, phrases: list[str]) -> Optional[str]:
    """The control phrase the utterance is, allowing one trailing _TAIL."""
    for ph in sorted(phrases, key=len, reverse=True):
        if t == ph:
            return ph
        # "stop now", "stop please", "dừng lại đi nhé": only tail words may follow
        rest = t[len(ph) + 1:].split() if t.startswith(ph + ' ') else None
        if rest and all(w in _TAIL for w in rest):
            return ph
    return None


def _match_ko(t: str, words: list[str]) -> Optional[str]:
    """A Korean control word at the start of the only content token."""
    if not _hangul(t):
        return None
    for w in sorted(words, key=len, reverse=True):
        if ' ' in w and (t == w or t.startswith(w)):
            return w
    toks = [x for x in t.split() if x not in _FILLER_KO]
    if len(toks) != 1:
        return None
    tok = toks[0]
    for w in sorted(words, key=len, reverse=True):
        # A one-syllable word ("서" = stand still) must be the whole token.
        if tok == w or (len(w) > 1 and tok.startswith(w)):
            return w
    return None


def _control(t: str) -> Optional[tuple[Intent, str]]:
    if not t or len(t.split()) > MAX_CONTROL_WORDS or _negated(t):
        return None
    t = _strip_fillers(t)
    # PAUSE before STOP: "tạm dừng" holds "dừng", "일시정지" holds "정지".
    for intent, phrases, ko in ((Intent.PAUSE, _PAUSE, _PAUSE_KO),
                                (Intent.STOP, _STOP, _STOP_KO),
                                (Intent.RESUME, _RESUME, _RESUME_KO)):
        hit = _match_phrases(t, phrases) or _match_ko(t, ko)
        if hit:
            return intent, hit
    return None


def keyword_fallback(text: str) -> IntentDecision:
    """Deterministic classifier for MODIFY / ADD / DROP / NEW / STATUS."""
    t = ' ' + normalize(text) + ' '
    for intent, keys in _FALLBACK:
        for k in keys:
            if (' ' + k + ' ') in t or (_hangul(k) and k.strip() in t):
                return IntentDecision(intent, text, 'fallback', 0.5, f'keyword {k.strip()!r}')
    if t.strip().endswith('?') or str(text).strip().endswith('?'):
        return IntentDecision(Intent.STATUS, text, 'fallback', 0.3, 'a question')
    return IntentDecision(Intent.NEW, text, 'fallback', 0.3, 'no keyword')


LLMClassifier = Callable[[str, dict], object]
_LLM_KINDS = (Intent.MODIFY, Intent.ADD, Intent.DROP, Intent.NEW, Intent.STATUS)


class IntentRouter:
    """``classify(text, awaiting=None, goal='')`` -> :class:`IntentDecision`."""

    def __init__(self, llm: LLMClassifier | None = None):
        self.llm = llm

    def classify(self, text: str, awaiting: str | None = None, goal: str = '') -> IntentDecision:
        t = normalize(text)
        ctl = _control(t)
        if ctl is not None:
            return IntentDecision(ctl[0], text, 'keyword', 1.0, f'keyword {ctl[1]!r}')
        if awaiting:
            return IntentDecision(Intent.ANSWER, text, 'awaiting', 0.9, f'answer to: {awaiting}')
        if self.llm is not None:
            try:
                out = self._from_llm(self.llm(text, {'goal': goal}), text)
                if out is not None:
                    return out
            except Exception:
                pass
        return keyword_fallback(text)

    @staticmethod
    def _from_llm(out, text: str) -> Optional[IntentDecision]:
        if isinstance(out, IntentDecision):
            return out if out.intent in _LLM_KINDS else None
        conf, reason = 0.8, ''
        if isinstance(out, dict):
            conf = float(out.get('confidence', conf))
            reason = str(out.get('reason', ''))
            out = out.get('intent')
        try:
            intent = Intent(str(out).strip().lower())
        except Exception:
            return None
        # The LLM never decides a stop / pause / answer: those are keyword or state.
        if intent not in _LLM_KINDS:
            return None
        return IntentDecision(intent, text, 'llm', conf, reason)


def classify(text: str, awaiting: str | None = None, goal: str = '',
             llm: LLMClassifier | None = None) -> IntentDecision:
    return IntentRouter(llm).classify(text, awaiting=awaiting, goal=goal)
