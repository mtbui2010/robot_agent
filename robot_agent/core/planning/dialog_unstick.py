"""Dialogue unstick (solution-lab round 4, option ``dialog_unstick``; default OFF, off = the earlier behaviour byte for byte).

Three ways a dialogue ended or went round in circles although the robot could have gone on (evidence: the cached
episodes of the 100-task dev slice, clarify / read-back loops that give up):

* **A restatement that gives the refused reading again.** The person said "no" to a read-back, then said the whole
  request again, and the model read it the same way. Asking "which part is wrong?" and stopping at the next "no" throws
  away the one new fact: the person's own second wording led to the same reading. The reading is read back once more,
  said as a confirmation of what they just repeated (``restates`` tells that an answer says the request again).
* **A "not sure" answer to a where-is question.** The robot can find objects itself; "I don't know where it is" is
  the cue to search, not to ask the same question again (``unsure_answer``).
* **Stacked apologies.** A question that opens with its own "Sorry" (``speech.APOLOGY_KEYS``) was asked again after
  silence as "Sorry, I didn't hear you. Sorry. ...". It is asked again as it is (``speech.calm``).

Language- and robot-agnostic; every word and every number is DATA. What counts as "not sure" is the language pack's
``answer_unsure`` phrases (``lang/<code>.json``) plus the robot pack's i18n key ``answer_unsure`` ({lang: [phrases]},
matched as substrings of the casefolded answer, so a language without word breaks works too). The numbers are the
manifest's ``planner.unstick`` (``fallback_words``, ``fallback_chars``, ``restate_frac``, ``restate_word_len``,
``restate_min_words``, ``restate_min_bigrams``). Without data for the answer's language, a structural fallback applies
when (and only when) the manifest declares its two numbers: a short answer that names no place of the ENV cannot be
used as a location, so the robot searches. Missing data = that part does nothing. No benchmark text.
"""

from __future__ import annotations

import re

from . import langdata

KEY = "answer_unsure"


def _fold(text) -> str:
    return " ".join(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", str(text or "").casefold()))


def unsure_phrases(phrases, lang: str) -> tuple:
    """The phrases that say "I don't know" in ``lang``: the language pack's and the robot pack's i18n data."""
    lg = (lang or "")[:2]
    by = ((phrases or {}).get(KEY) or {}) if isinstance(phrases, dict) else {}
    out = langdata.phrases(lg, KEY) + [str(p) for p in (by.get(lg) or ()) if str(p).strip()]
    return tuple(_fold(p) for p in out if _fold(p))


def unsure_answer(answer, places, phrases=None, lang: str = "en", params=None) -> bool:
    """Is ``answer`` a "not sure" to a where-question? Data decides when the language has any (language pack + robot pack
    phrases); only a language WITHOUT data gets the structural fallback, and only when ``params`` (the manifest's
    ``planner.unstick``) declares ``fallback_words`` and ``fallback_chars``: more than both means a real answer; else no
    place of the ENV named, so the answer cannot be used as a location. An empty answer is no answer, not a "not sure"."""
    text = _fold(answer)
    if not text:
        return False
    data = unsure_phrases(phrases, lang)
    if data:                       # this language has data: the phrases decide, nothing is guessed
        return any(p in text for p in data)
    fw, fc = langdata.number(params, "fallback_words"), langdata.number(params, "fallback_chars")
    if fw is None or fc is None:   # no numbers declared: no fallback
        return False
    if len(text.split()) > fw and len(text) > fc:
        return False
    named = [k for k in (places.candidates(str(answer), str(answer)) if places is not None else []) if not str(k).startswith("@")]
    return not named


def _bigrams(text) -> set:
    t = "".join(re.findall(r"[^\W_]+", str(text or "").casefold()))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def restates(original, answer, params=None) -> bool:
    """Does ``answer`` say the request ``original`` again? Words of ``restate_word_len`` letters or more when the request
    has at least ``restate_min_words`` of them, else (a short request, or a script without word breaks) character pairs
    (at least ``restate_min_bigrams``); ``restate_frac`` of the request's units must come back. The numbers are the
    manifest's ``planner.unstick``; any missing: False (nothing is claimed)."""
    frac, wlen, nwords, nbig = (langdata.number(params, k) for k in (
        "restate_frac", "restate_word_len", "restate_min_words", "restate_min_bigrams"))
    if None in (frac, wlen, nwords, nbig):
        return False
    words = lambda t: {w for w in re.findall(r"\w+", str(t or "").lower()) if len(w) >= wlen}      # noqa: E731
    base = words(original)
    if len(base) >= nwords:
        return len(base & words(answer)) >= frac * len(base)
    b = _bigrams(original)
    return len(b) >= nbig and len(b & _bigrams(answer)) >= frac * len(b)


def _entities(text, places) -> set:
    from .understand import _norm, place_mentions
    return {k for m in place_mentions(_norm(str(text or "")), places) for k in m[3]} if places is not None else set()


def adds_entity(original, answer, places) -> bool:
    """Does ``answer`` name an ENV entity (place, container, person) the ``original`` request did not? Only then is a
    restatement more than the same words again: the person's second wording holds a fact the read-back can show.
    A bare restatement re-draws the same reading (and, with a noisy channel, the same noise): not a reason to ask
    the same read-back again."""
    return bool(_entities(answer, places) - _entities(original, places))


__all__ = ["adds_entity", "KEY", "unsure_phrases", "unsure_answer", "restates"]
