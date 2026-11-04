"""The language layer: the user's language is only an I/O layer around an English core (SAGE-v2, round 3).

Understanding, planning, the gate, retrieval, the hazard rules and the logs work in English. A user who speaks
another language L (Korean, ...) reaches that core through ONE module, ``LanguageAdapter``, which the AssistLoop
holds as ``loop.language``.

* **IN** (``hear`` / ``understand_view``). What the user says in L (the instruction, the answers, interruptions)
  goes to English through the translator (``translate_model``), once per utterance, cached and counted. The
  original text is never replaced: the loop keeps it for the coverage check, the read-back and each goal's
  ``said``. The place names are given to the translator from the ENV aliases in L (a place grounds through its
  alias; the translator is the only cross-lingual component, there is no lexicon here). A translation is
  *distrusted* when the translator marks the text as noise (``UNCLEAR``: an ASR garble), when its output is not
  English, or when it lost a place / person the original names through an ENV alias; the loop then asks the user
  to repeat (``untrusted``).
* **OUT** (``speak``). Every text the user hears passes through ``speak``: questions, read-backs, Say texts, the
  announcer's lines and the final message (the run's ``ev`` calls it for each ``say``; the Ask and Say skills
  get its output). The robot's own phrases (``speech.LocalText``) come from the pack's i18n with no model call;
  an English slot inside one is translated alone (cached); free text goes through the translator with the
  output guard. When nothing works (the model is down, the budget is used up), the user hears the pack's plain
  phrase in L (``q_unclear_task`` for a question, ``say_untranslated`` otherwise), never English
  (``fallbacks`` counts it). English users: ``speak`` returns the text as is and ``hear`` costs nothing.
* ``input_pivot`` (option / ``$ROBOT_AGENT_INPUT_PIVOT`` / manifest ``planner.input_pivot``):
  ``none`` (the understand prompt gets the original only), ``translate`` (the English translation is the task;
  the original follows only as the source of ``said``), ``both`` (the original and its translation).

Cost: at most one translation call per user utterance (``hear``), only while more than ``reserve`` calls of the
translate budget are left, so what the robot says keeps calls; the robot's own phrases cost none.

Robot-agnostic: places are the ENV, phrases come from the pack's i18n; nothing here knows a house, an object or a
benchmark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import speech
from .understand import LLMBudgetExceeded

PIVOTS = ("none", "translate", "both")

_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
LANGS = {"ko": "Korean", "vi": "Vietnamese", "ja": "Japanese", "zh": "Chinese"}
REGISTER = {"ko": "polite spoken Korean, 해요체", "vi": "polite spoken Vietnamese", "ja": "polite spoken Japanese",
            "zh": "polite spoken Chinese"}

UNCLEAR = "UNCLEAR"
_HANGUL = re.compile(r"[가-힣]")
_NON_LATIN = re.compile(r"[가-힣぀-ヿ㐀-䶿一-鿿Ѐ-ӿ]")
_STRIP_AFTER = re.compile(r"(?:이|가|은|는|을|를|에|에서|으로|로|한테|에게|의|도|랑|이랑|와|과)$")

_IN_PROMPT = ("Translate what this person said to their household robot, from {name} into plain English. Keep the "
              "meaning, names and numbers. Do not add anything and do not answer it.{names} If the text is not an "
              "understandable sentence (speech-recognition noise), output exactly {unclear}. Output only the "
              "translation:")
_OUT_PROMPT = ("Translate into natural {register} ({name}). Keep the meaning, the names and any quoted sentence; "
               "do not add anything.{names} Output only the translation:")


@dataclass
class Heard:
    """One user utterance: the original words, their English (``""`` when none), and whether the translation can
    be trusted (``why`` says what was wrong)."""

    original: str
    english: str = ""
    trusted: bool = True
    why: str = ""
    lost: list = field(default_factory=list)      # ENV names in the original that the English does not carry


def localized(text, lang: str) -> bool:
    """Is ``text`` already said in ``lang`` (mostly its script)? English is always 'localized'."""
    lg = (lang or "en")[:2]
    t = str(text or "")
    if lg == "en" or lg not in LANGS:
        return True
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return True                                  # digits / punctuation only
    if lg == "ko":
        return sum(1 for c in letters if _HANGUL.match(c)) / len(letters) >= 0.7 and not speech._HAN_KANA.search(t)
    return sum(1 for c in letters if not c.isascii()) / len(letters) >= 0.5


class LanguageAdapter:
    """See the module doc. ``host``: the AssistLoop (cfg, robot, _llm_cfg, _llm(), and the run's ``_env_places``,
    ``_tr_budget``, ``_llm_unreachable``)."""

    reserve = 2                                       # translate-budget calls ``hear`` leaves for what the robot says

    def __init__(self, host) -> None:
        self.host = host
        self.cache: dict = {}
        self.spoken: list = []                        # everything ``speak`` returned (last 400)
        self.fallbacks = 0                            # texts replaced by the pack's plain phrase
        self.heard: dict = {}                         # original -> Heard (this run)

    # ── configuration ────────────────────────────────────────────────────
    @property
    def pivot(self) -> str:
        p = str((getattr(self.host, "cfg", None) or {}).get("input_pivot") or "none").lower()
        return p if p in PIVOTS else "none"

    def run_lang(self) -> str:
        return (getattr(self.host, "_run_lang", "en") or "en")[:2]

    def reset(self) -> None:
        """A new run: its own spoken log and heard texts (the translation cache is kept: same text, same words)."""
        self.spoken, self.fallbacks, self.heard = [], 0, {}

    def translator(self):
        """The client that translates (option / $ROBOT_AGENT_TRANSLATE_MODEL / the manifest's
        planner.translate_model); the planner's own client when none is set or it is the same model."""
        h = self.host
        tm = str((h.cfg or {}).get("translate_model") or "").strip()
        if not tm or tm == str((h._llm_cfg or {}).get("model") or ""):
            return h._llm()
        if getattr(h, "_tr_client", None) is None:
            from ...connect.llm import init_llm_client
            url = h.cfg.get("translate_url") or (h._llm_cfg or {}).get("url")
            h._tr_client = init_llm_client(cfg=dict(h._llm_cfg or {}, model=tm, url=url))
        return h._tr_client

    def _model(self) -> str:
        return str((self.host.cfg or {}).get("translate_model") or "")

    def _take(self, keep: int = 0) -> bool:
        """One translation call from the translate budget, leaving ``keep`` calls; False when there is none."""
        budget = getattr(self.host, "_tr_budget", None)
        if budget is None:
            return True
        if keep and budget.left() <= keep:
            return False
        try:
            budget.take(what="translate")
        except LLMBudgetExceeded:
            return False
        return True

    def _places(self):
        return getattr(self.host, "_env_places", None)

    # ── names from the ENV ───────────────────────────────────────────────
    def glossary(self, text, lang) -> dict:
        """{English name in ``text``: its name in ``lang``} for the ENV places / people the English text names."""
        places, lg, out = self._places(), (lang or "en")[:2], {}
        if places is None:
            return out
        t = str(text or "")
        for k in places.keys:
            if (places.env.get(k) or {}).get("speaker"):
                continue          # the listener: English "you" is a pronoun, and natural Korean leaves it out
            en, local = speech.say_name(places, k, "en"), speech.say_name(places, k, lg)
            if en and local and en != local and local.isascii() is False and \
                    re.search(r"(?<![\w-])" + re.escape(en) + r"(?![\w-])", t, re.I):
                out[en] = local   # a whole-word name only ("you" is not in "your", "door" not in "indoor")
        return out

    def _aliases_in(self, text: str, lang: str) -> dict:
        """{alias in ``text``: [ENV key, ...]} for the ENV entries (not the speaker) that have the alias in ``lang``."""
        places, out = self._places(), {}
        if places is None:
            return out
        for k in places.keys:
            e = places.env.get(k) or {}
            if e.get("speaker"):
                continue
            for a in e.get("aliases") or []:
                if a and not a.isascii() and a in text and not speech._speaker_alias(a):
                    out.setdefault(a, []).append(k)
        return out

    def names(self, text: str, lg: str) -> str:
        """``text`` with every ENV key it names said as the user names it (``speech.say_name`` in ``lg``)."""
        places = self._places()
        if places is None or not text:
            return text
        for k in sorted(places.keys, key=len, reverse=True):
            if ("@" in k or "_" in k) and k in text:
                local = speech.say_name(places, k, lg)
                if local:
                    text = text.replace(k, local)
        return text

    def plain_names(self, text: str) -> str:
        """English ``text`` with every ENV key said as a name (``say_name``) and every other snake_case id
        (``desk_chair``, ``room_a``: a place head or a room of the ENV) said as words."""
        text = self.names(text, "en")
        return _SNAKE.sub(lambda m: m.group(0).replace("_", " "), text)

    @staticmethod
    def _local_sentences(text: str, lg: str) -> str:
        """The sentences of ``text`` that are already in ``lg`` ("" when none or all)."""
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        kept = [p for p in parts if _NON_LATIN.search(p) and localized(p, lg)]
        return " ".join(kept) if kept and len(kept) < len(parts) else ""

    def local_slot(self, text) -> Optional[str]:
        """An English slot ("kimchi fridge") in the user's language: the ENV entry whose head it is (its alias),
        else one short translation (cached). None when neither works."""
        lg = self.run_lang()
        places, s = self._places(), " ".join(str(text or "").split())
        if not s or lg == "en":
            return s or None
        if places is not None:
            for k in places.keys:
                if " ".join(speech._words(k.split("@")[0]).lower().split()) == s.lower():
                    local = speech.say_name(places, k, lg)
                    if local and localized(local, lg):
                        return local
        out = self._speak_text(s, lg)
        return out if out is not None and localized(out, lg) else None

    # ── OUT: the one function every spoken text passes ───────────────────
    def speak(self, text, lang, kind: str = "") -> str:
        """``text`` as the user hears it. See the module doc."""
        out = self._speak(text, lang, kind)
        self.spoken.append(str(out))
        del self.spoken[:-400]
        return out

    def _speak(self, text, lang, kind: str):
        lg = (lang or "en")[:2]
        if not text:
            return text
        if lg not in LANGS:
            return self.plain_names(str(text))          # an English line never names an ENV key either
        if not isinstance(text, speech.LocalText):
            text = self.names(str(text), lg)             # no raw ENV key (kimchi_fridge@kitchen) reaches the user
            if _NON_LATIN.search(text):
                if localized(text, lg):
                    return text                          # already said in the user's language
                kept = self._local_sentences(text, lg)
                if kept:
                    return kept                          # a pack line with an English reason after it: the line
        done = speech.render(text, lang, self.host.robot, self._places(), slot_tr=self.local_slot)
        if done is not None and localized(done, lg):
            return done
        if isinstance(text, speech.LocalText) and text.key == "_join" and len(text.parts) > 1:
            # phrases said one after the other ("Sorry, I didn't hear you." + the question asked before): each part
            # on its own, so the robot's own phrase needs no call and a part said before is cached
            return " ".join(str(self._speak(p, lang, kind)) for p in text.parts)
        out = None if getattr(self.host, "_llm_unreachable", False) else self._speak_text(str(text), lg)
        if out is not None:
            return out
        return self._fallback(text, lg, kind)

    def _speak_text(self, text: str, lg: str) -> Optional[str]:
        """``text`` translated (cache, then the translator with its output guard), None when that fails."""
        key = ("out", text, lg, self._model())
        if key not in self.cache:
            self.cache[key] = self._translate_out(text, lg, LANGS[lg])
        return self.cache[key]

    def _translate_out(self, text, lg, name) -> Optional[str]:
        """One translation with the output check and one retry naming what was wrong; None when it fails."""
        gl = self.glossary(text, lg)
        names = " Use these names: " + "; ".join(f"{a} = {b}" for a, b in gl.items()) + "." if gl else ""
        base = _OUT_PROMPT.format(register=REGISTER.get(lg, name), name=name, names=names)
        why = None
        for _k in range(2):
            try:
                if not self._take():
                    break
                prompt = base if why is None else base.replace(" Output only", f" (Your last answer was wrong: {why}.) Output only")
                out = str(self.translator().chat(prompt=f"{prompt}\n{text}", temperature=0, seed=0)).strip().strip('"')
            except Exception:
                break
            why = speech.guard(out, lg, keep=list(gl.values()))
            if why is None:
                return out
        return None

    def _fallback(self, text, lg: str, kind: str) -> str:
        """Nothing could say ``text`` in ``lg``: the pack's plain phrase in that language (a question: ask to put
        the task in other words). The pack has none: the English text, counted in ``_say_not_in_lang``."""
        h = self.host
        is_q = kind == "question" or (isinstance(text, speech.LocalText) and text.key.startswith("q_")) \
            or str(text).rstrip().endswith("?")
        for key in (("q_unclear_task",) if is_q else ()) + ("say_untranslated",):
            p = speech.phrase(h.robot, key, lg)
            if p:
                self.fallbacks += 1
                return p
        h._say_not_in_lang = getattr(h, "_say_not_in_lang", 0) + 1
        return str(text)

    # ── IN: what the user said ───────────────────────────────────────────
    def hear(self, text, lang=None) -> Heard:
        """The English of an utterance in the user's language, cached per text. An English run, an empty text or
        a text without that language's script: no call. ``trusted`` False: ask the user to say it again."""
        lg = (lang or self.run_lang())[:2]
        t = str(text or "").strip()
        if lg == "en" or lg not in LANGS or not t or self.pivot == "none" or not _NON_LATIN.search(t):
            return Heard(t, trusted=True)
        if t in self.heard:
            return self.heard[t]
        key = ("in", t, lg, self._model())
        if key not in self.cache:
            self.cache[key] = self._translate_in(t, lg)
        h = self.cache[key]
        self.heard[t] = h
        return h

    def _translate_in(self, t: str, lg: str) -> Heard:
        al = self._aliases_in(t, lg)
        places = self._places()
        gl = {}
        for a, ks in al.items():
            en = {speech.say_name(places, k, "en") for k in ks}
            if len(en) == 1:
                gl[a] = next(iter(en))
        names = " Use these names: " + "; ".join(f"{a} = {b}" for a, b in gl.items()) + "." if gl else ""
        prompt = _IN_PROMPT.format(name=LANGS[lg], names=names, unclear=UNCLEAR)
        if getattr(self.host, "_llm_unreachable", False) or not self._take(keep=self.reserve):
            return Heard(t, trusted=True, why="no translation")       # the original only (pivot none)
        try:
            out = str(self.translator().chat(prompt=f"{prompt}\n{t}", temperature=0, seed=0)).strip().strip('"')
        except Exception:
            return Heard(t, trusted=True, why="no translation")
        if not out or out.upper().strip(". ") == UNCLEAR:
            return Heard(t, "", False, "the text is not an understandable sentence")
        if _NON_LATIN.search(out) or len(out) > 4 * len(t) + 80:
            return Heard(t, "", False, "the translation is not English")
        low = out.lower()
        lost = [a for a, en in gl.items() if not all(w in low for w in _head_words(en))]
        if lost:
            return Heard(t, out, False, "the translation lost " + ", ".join(lost), lost)
        return Heard(t, out)

    def untrusted(self, utterance: str) -> Optional[str]:
        """Why the translation of ``utterance`` cannot be trusted (None: it can, or there is none)."""
        h = self.hear(utterance)
        return None if h.trusted else (h.why or "untrusted")

    def understand_view(self, task: str, dialogue: list, robot_line) -> tuple:
        """(task text, original line or "", dialogue lines) for the understand prompt, by ``input_pivot``:
        ``none``: as is; ``translate``: the English translation is the task and the user's lines (the original
        follows only as the source of each goal's ``said``); ``both``: the original and its translation.
        ``dialogue``: [(who, text)]; ``robot_line(text, english)``: the loop's rendering of a robot line (as the user
        heard it, or the English text)."""
        lg = self.run_lang()
        if lg == "en" or self.pivot == "none":
            return task, "", [robot_line(t, False) if w == "robot" else f"User: {t}" for w, t in dialogue]
        h = self.hear(task)
        task_text, orig = task, ""
        if h.english:
            if self.pivot == "translate":
                task_text, orig = h.english, f"\n(the user's own words, for \"said\" only: {task})"
            else:
                task_text = f"{task}\n(English: {h.english})"
        lines = []
        for w, t in dialogue:
            if w != "user":
                lines.append(robot_line(t, self.pivot == "translate"))
                continue
            e = self.hear(t).english
            lines.append(f"User: {e}" if e and self.pivot == "translate" else
                         (f"User: {t} (English: {e})" if e else f"User: {t}"))
        return task_text, orig, lines


def _head_words(en: str) -> list:
    """The words of an English name that must all show in the translation (a compound loses its meaning when it is
    cut: "table" for "dining table"); a room after "in the" is not needed."""
    return [x for x in re.findall(r"[a-z']+", str(en).lower().split(" in the ")[0]) if len(x) > 1]


__all__ = ["LanguageAdapter", "Heard", "PIVOTS", "LANGS", "REGISTER", "localized"]
