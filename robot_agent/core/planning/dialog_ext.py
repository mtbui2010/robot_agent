"""Dialogue data and the agreement guard (solution-lab round 14, option ``dialog_agree2``; default OFF, off = the earlier
behaviour byte for byte).

Two parts, both DATA-driven; nothing here names a language, a robot, an object or a place.

1. **Language data** (``Ext``). The language pack (``lang/<code>.json``, block ``dialogue_ext``) and the robot pack (manifest
   ``planner.room_names``) declare facts the earlier code hard-coded or lacked:

   * ``polite_endings``: endings a noun may take at the end of a word ("<noun>요") besides the case particles. Added to the
     particle list of the whole-word place match (``understand._find``).
   * ``self_correction``: regexes that mark a self-correction ("on the stool... no wait, on the sofa"); the mention just
     before one is taken back. They replace the English-only ``understand._CORR`` for the run; the English markers are
     pack data too (``lang/en.json``), so an English run reads the same.
   * ``answer_unsure``: phrases that say "I do not know" (read with ``dialog_unstick.unsure_answer``).
   * ``room_names``: {room: [names]} the language has for the rooms of the ENV (the ENV spells a room in one language).
     A name is a room mention (``Places.candidates``), so "the bedroom" said in the person's language settles a doubt about it.

   A language without the block, an ENV room without a name: no ``Ext`` (``build`` gives None) and today's code runs.

2. **The agreement guard** (``Guard``). ``dialog_agree`` may call a refused reading confirmed when the person's answers agree
   with it. Under a noisy channel the "agreement" can be a garble: a word the recognizer turned into a near-name. The guard
   asks for more than a similarity:

   * an agreeing answer must name a slot of the reading (a place, room, person or option) by an EXACT name, not only an object,
     and not through a name the recognizer garbled (``Guard.exact_score``);
   * no utterance since the reading was first read back holds a garbled name: a token that is not a name of the ENV, the goals
     or the speaker forms but is close to one (similarity of the Unicode decomposition, so Hangul compares by letter
     (jamo), at least ``agree_guard.garble_ratio``, the manifest's number);
   * the agreeing answer is plausible in the language's script (the pack's ``script`` block, as ``speech.guard``).

   When the answers agree but the guard objects, the reading is NOT taken as confirmed: the robot asks the person once more,
   as a yes / no read-back. A genuine "no" still ends the run. No ``agree_guard.garble_ratio`` in the manifest: no guard.

Pure functions; ``AssistLoop`` builds the objects once per run.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

from . import langdata, persons

BLOCK = "dialogue_ext"
_END = r"(?=$|[\s.,!?;:)\]\"'~])"            # a polite ending closes the word


def _norm(s) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").split())


def _block(lang) -> dict:
    b = langdata.get(lang).get(BLOCK)
    return b if isinstance(b, dict) else {}


def _strs(v) -> list:
    return [str(x) for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


class Ext:
    """What the packs declare for one run: particle endings, the self-correction regex, the unsure phrases and the room names."""

    def __init__(self, endings=(), corr=None, unsure=(), rooms=None, particles=()):
        self.endings = tuple(endings)
        self.particles = tuple(particles)
        self.part_rx = "|".join(f"(?:{e}{_END})" for e in sorted(self.endings, key=len, reverse=True)) if self.endings else ""
        self.corr = corr
        self.unsure = tuple(unsure)
        self.rooms = dict(rooms or {})            # name -> ENV room
        self._room_rx = (re.compile("(?<![\\w])(" + "|".join(re.escape(n) for n in sorted(self.rooms, key=len, reverse=True))
                                    + ")(?=$|[\\s.,!?;:)\\]\"'~]"
                                    + "".join("|" + re.escape(p) for p in sorted(self.particles, key=len, reverse=True)) + ")")
                         if self.rooms else None)

    def rewrite_rooms(self, text: str) -> str:
        """``text`` with every declared room name replaced by the room the ENV spells (a particle stays after it)."""
        if self._room_rx is None:
            return text
        return self._room_rx.sub(lambda m: " " + self.rooms[m.group(1)] + " ", text)


def _room_ids(places) -> dict:
    """{spelling: ENV room} the pack names may match: the room as the ENV spells it, with / without a trailing 'room'."""
    out = {}
    for rm in places.rooms():
        n = _norm(rm)
        out[n] = rm
        out[n + " room"] = rm
        if n.endswith(" room"):
            out[n[:-5]] = rm
    return out


def build(lang, places, planner: Optional[dict] = None) -> Optional[Ext]:
    """The run's ``Ext`` for ``lang`` and the ENV's rooms; None when no pack declares anything for it (today's code runs)."""
    lg = (lang or "en")[:2]
    declared = ((planner or {}).get("room_names") or {}).get(lg)
    if not _block(lg) and not isinstance(declared, dict):
        return None                                     # nothing declared for this language: the flag does nothing
    blocks = [_block(lg)]
    if lg != langdata.PROMPT_LANG:
        blocks.append(_block(langdata.PROMPT_LANG))      # the held English of a translated answer is read too
    blocks = [b for b in blocks if b]
    endings = _strs(_block(lg).get("polite_endings"))
    marks = [m for b in blocks for m in _strs(b.get("self_correction"))]
    corr = None
    if marks:
        try:
            corr = re.compile("|".join(f"(?:{m})" for m in marks), re.I)
        except re.error:
            corr = None
    unsure = _strs(_block(lg).get("answer_unsure"))
    ids, rooms = _room_ids(places), {}
    named = dict(_block(lg).get("room_names") or {})
    for src in (named, declared if isinstance(declared, dict) else {}):
        for key, names in src.items():
            rm = ids.get(_norm(key))
            if rm:
                for n in _strs(names):
                    rooms[_norm(n)] = rm
    if not (endings or corr or unsure or rooms):
        return None
    return Ext(endings, corr, unsure, rooms, langdata.phrases(lg, "particles"))


# ── the guard ────────────────────────────────────────────────────────────────────────────────────────────────

def _jamo(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def _tokens(s) -> list:
    return re.findall(r"[^\W_]+", str(s or "").lower())


class Guard:
    """The agreement guard of one run: the names the garble test knows (ENV heads / aliases / rooms / persons, the goals'
    object names, the speaker forms), the garble ratio and the language's script data."""

    def __init__(self, lang, places, goals, ratio: float, ext: Optional[Ext] = None):
        self.lang, self.ratio, self.places = (lang or "en")[:2], float(ratio), places
        names = set()
        for k in places.keys:
            names.update(_tokens(places.head(k)))
            for a in places.env.get(k, {}).get("aliases") or []:
                if a and "@" not in str(a):
                    names.update(_tokens(a))
        names.update(t for rm in places.rooms() for t in _tokens(rm))
        if ext is not None:
            names.update(t for n in ext.rooms for t in _tokens(n))
        for g in goals or []:
            for f in ("object", "said", "name_local"):
                names.update(_tokens(g.get(f)))
        names.update(t for f in persons.SPEAKER_FORMS for t in _tokens(f))
        self.names = {n for n in names if n}
        parts = list(langdata.phrases(self.lang, "particles")) + (list(ext.endings) if ext is not None else [])
        sc = langdata.get(self.lang).get("script")
        try:
            self._letters = re.compile(sc["letters"]) if isinstance(sc, dict) and sc.get("letters") else None
        except re.error:
            self._letters = None
        self._parts = tuple(dict.fromkeys(parts))
        self._tail = (re.compile("(?:" + "|".join(re.escape(p) for p in sorted(parts, key=len, reverse=True)) + "){1,2}")
                      if parts else None)

    # a token the name list holds, with or without case particles after it (the language pack's own list)
    def _exact(self, tok: str) -> bool:
        if tok in self.names:
            return True
        return bool(self._tail) and any(self._own(n) and tok.startswith(n) and self._tail.fullmatch(tok[len(n):])
                                        for n in self.names)

    def _own(self, s: str) -> bool:
        """Is ``s`` written in the language's own script (the pack's ``script.letters``)? No script data: nothing is."""
        return self._letters is not None and bool(self._letters.search(s))

    def _bad_tail(self, tok: str) -> bool:
        """A name with a tail that is no particle but is close to one (a particle the recognizer garbled)."""
        for n in self.names:
            if not self._own(n) or not tok.startswith(n) or len(tok) == len(n):
                continue
            tail = tok[len(n):]
            if any(len(p) >= 2 and p != tail and len(tail) == len(p)
                   and SequenceMatcher(None, _jamo(p), _jamo(tail)).ratio() >= self.ratio for p in self._parts):
                return True
        return False

    def _pack_words(self) -> set:
        """Every token of every string the run language's pack declares: the language's own known words."""
        words, todo = set(), [langdata.get(self.lang)]
        while todo:
            o = todo.pop()
            if isinstance(o, str):
                words.update(_tokens(o))
            elif isinstance(o, dict):
                todo.extend(o.values())
            elif isinstance(o, (list, tuple)):
                todo.extend(o)
        return words

    def garbled(self, texts) -> list:
        """The tokens of ``texts`` that are not names but come close to one. Only words in the language's own script are
        judged (the pack's ``script.letters``): a language with no such data has none garbled (fail open)."""
        out = []
        known = self._pack_words()
        for t in texts:
            for tok in _tokens(t):
                if not self._own(tok) or self._exact(tok):
                    continue
                if tok in known or any(
                        tok[:len(tok) - k] in known for k in range(1, min(3, len(tok)))
                        if self._tail is not None and self._tail.fullmatch(tok[len(tok) - k:])):
                    continue        # a word of the language's own pack (or one with a declared tail): not garbled
                if self._bad_tail(tok):
                    out.append(tok)
                    continue
                for n in self.names:
                    if self._own(n) and len(tok) >= len(n):
                        cand = tok[:len(n)]
                        if cand != n and SequenceMatcher(None, _jamo(n), _jamo(cand)).ratio() >= self.ratio:
                            out.append(tok)
                            break
        return list(dict.fromkeys(out))

    def plausible(self, text) -> bool:
        """Is ``text`` in the language's script (the pack's ``script`` block, names of the ENV taken out)? No data: yes."""
        sc = langdata.get(self.lang).get("script")
        if not isinstance(sc, dict) or not sc.get("letters"):
            return True
        t = str(text or "")
        for n in sorted(self.names, key=len, reverse=True):
            t = re.sub(re.escape(n), " ", t, flags=re.I) if n.isascii() else t
        try:
            letters = [c for c in t if c.isalpha()]
            mine = [c for c in letters if re.match(sc["letters"], c)]
            if letters and len(mine) / len(letters) < float(sc.get("min_share", 0.7)):
                return False
            return not (sc.get("other_script") and re.search(sc["other_script"], t))
        except (re.error, TypeError, ValueError):
            return True

    def exact_score(self, goal: dict, texts) -> int:
        """``dialog_converge._score`` without the similarity branches: the head noun / the Korean head said as is."""
        if goal.get("type") not in ("on", "in") or not goal.get("object"):
            return 0
        obj = _tokens(goal["object"])
        best = 0
        for t in texts:
            toks = _tokens(t)
            sc = 0
            if obj and obj[-1] in toks:
                sc = 2 + sum(1 for m in obj[:-1] if m in toks)
            for key in ("said", "name_local"):
                hs = [x for x in _tokens(goal.get(key)) if self._own(x)]
                if hs and any(hs[-1] in w for w in toks if self._own(w)):
                    sc = max(sc, 2 + sum(1 for m in hs[:-1] if m in str(t)))
            best = max(best, sc)
        return best


def guard_for(lang, places, goals, planner: Optional[dict], ext: Optional[Ext] = None) -> Optional[Guard]:
    """The run's guard when the manifest declares ``planner.agree_guard.garble_ratio`` (0 < x <= 1), else None (no guard)."""
    r = langdata.number((planner or {}).get("agree_guard"), "garble_ratio")
    return Guard(lang, places, goals, r, ext) if r is not None and 0 < r <= 1 else None


__all__ = ["Ext", "Guard", "build", "guard_for", "BLOCK"]
