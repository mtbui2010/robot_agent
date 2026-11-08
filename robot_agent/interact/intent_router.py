"""Classify what someone says while a run is active.

Nine kinds (proposal 3.3): STOP, PAUSE, RESUME, MODIFY, ADD, DROP, NEW, STATUS,
ANSWER. The router only decides; :meth:`RunManager.inject` acts.

Order, cheapest and safest first:

  1. STOP / PAUSE / RESUME by keyword, never through an LLM, so a stop is
     recognised in microseconds and also when the LLM server is down. English
     and Korean. A keyword counts only in a short utterance (a
     command, not a sentence that mentions "stop"), as a whole word, and not
     under a negation ("don't stop", "멈추지 마");
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

from ..core.planning import langdata


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

# The words are language-pack data (``dialogue`` block of ``core/planning/lang/<code>.json``), read on use:
#   stop / pause / resume          whole phrases (lowercase). After the polite / vocative fillers around it (``head``) are
#                                  dropped, the utterance must BE the phrase, optionally followed by one of ``tail`` ("stop now",
#                                  "wait a second"). "bus stop", "stop at the door" are therefore not commands.
#   stop_prefix / pause_prefix /   a language whose endings attach to the verb ("멈춰요", "멈춰줘"): a token starting with the
#   resume_prefix, filler_tokens   word is the command; filler_tokens are dropped first. Used only for text in that
#                                  language's script (``script.letters``).
#   negation_words / negation_contains / negation_contains_native    "don't stop" and the like
#   fallback_<intent> / fallback_<intent>_sub    keywords for the LLM kinds (whole-word / substring)
# A language with no pack adds none; with no pack at all there is no keyword command (the LLM and the state decide).
_FALLBACK_KINDS = ((Intent.STATUS, 'status'), (Intent.DROP, 'drop'), (Intent.MODIFY, 'modify'), (Intent.ADD, 'add'))


def _dlg(key: str, lang=None) -> list:
    return langdata.dialogue_all(key, None if lang is None else [lang])


_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)


def normalize(text: str) -> str:
    t = unicodedata.normalize('NFC', str(text or '')).lower().strip()
    t = _PUNCT.sub(' ', t)
    return ' '.join(t.split())


def _negated(t: str) -> bool:
    words = t.split()
    neg = set(_dlg('negation_words'))
    if any(w in neg for w in words) or any(c in t for c in _dlg('negation_contains')):
        return True
    return any(langdata.in_script(t, lg) and any(n in t for n in _dlg('negation_contains_native', lg))
               for lg in langdata.languages())


def _strip_fillers(t: str) -> str:
    words = t.split()
    # "stop stop stop" -> "stop"
    words = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
    t = ' '.join(words)
    changed = True
    while changed and t:
        changed = False
        for h in _dlg('head'):
            if t.startswith(h + ' '):
                t, changed = t[len(h) + 1:], True
    return t


def _match_phrases(t: str, phrases: list[str]) -> Optional[str]:
    """The control phrase the utterance is, allowing one trailing ``tail`` word."""
    tail = set(_dlg('tail'))
    for ph in sorted(phrases, key=len, reverse=True):
        if t == ph:
            return ph
        # "stop now", "stop please": only tail words may follow
        rest = t[len(ph) + 1:].split() if t.startswith(ph + ' ') else None
        if rest and all(w in tail for w in rest):
            return ph
    return None


def _match_prefix(t: str, key: str) -> Optional[str]:
    """A control word of ``dialogue[key]`` (a language whose endings attach) at the start of the only content token."""
    for lg in langdata.languages():
        words = _dlg(key, lg)
        if not words or not langdata.in_script(t, lg):
            continue
        hit = _match_prefix_of(t, words, set(_dlg('filler_tokens', lg)))
        if hit:
            return hit
    return None


def _match_prefix_of(t: str, words: list[str], fillers: set) -> Optional[str]:
    for w in sorted(words, key=len, reverse=True):
        if ' ' in w and (t == w or t.startswith(w)):
            return w
    toks = [x for x in t.split() if x not in fillers]
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
    # PAUSE before STOP: "일시정지" holds "정지".
    for intent, key in ((Intent.PAUSE, 'pause'), (Intent.STOP, 'stop'), (Intent.RESUME, 'resume')):
        hit = _match_phrases(t, _dlg(key)) or _match_prefix(t, key + '_prefix')
        if hit:
            return intent, hit
    return None


def keyword_fallback(text: str) -> IntentDecision:
    """Deterministic classifier for MODIFY / ADD / DROP / NEW / STATUS."""
    t = ' ' + normalize(text) + ' '
    for intent, kind in _FALLBACK_KINDS:
        for lg in langdata.languages():
            for k in _dlg('fallback_' + kind, lg):                   # whole words
                if (' ' + k + ' ') in t:
                    return IntentDecision(intent, text, 'fallback', 0.5, f'keyword {k.strip()!r}')
            for k in _dlg('fallback_' + kind + '_sub', lg):          # a substring (a language with attached endings)
                if (' ' + k + ' ') in t or k.strip() in t:
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
