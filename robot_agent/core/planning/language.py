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

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from . import langdata, role_models, speech
from .understand import LLMBudgetExceeded

PIVOTS = ("none", "translate", "both")

_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


class _PackNames:
    """``{code: value}`` read from the language packs (``name`` / ``register``): a language is a translate target only
    when its pack names it. Data, not code: a language without a pack is not in it."""

    def __init__(self, key: str) -> None:
        self.key = key

    def get(self, lang, default=None):
        v = langdata.get(lang).get(self.key)
        return v if isinstance(v, str) and v else default

    def __getitem__(self, lang):
        v = self.get(lang)
        if v is None:
            raise KeyError(lang)
        return v

    def __contains__(self, lang) -> bool:
        return self.get(lang) is not None

    def keys(self):
        return [lg for lg in langdata.languages() if self.get(lg) is not None]

    def items(self):
        return [(lg, self[lg]) for lg in self.keys()]

    __iter__ = lambda self: iter(self.keys())      # noqa: E731


LANGS = _PackNames("name")              # the languages the robot can translate into / out of (language packs)
REGISTER = _PackNames("register")

UNCLEAR = "UNCLEAR"
_NON_LATIN = re.compile(r"[가-힣぀-ヿ㐀-䶿一-鿿Ѐ-ӿ]")

_IN_PROMPT = ("Translate what this person said to their household robot, from {name} into plain English. Keep the "
              "meaning, names and numbers. Do not add anything and do not answer it.{names} If the text is not an "
              "understandable sentence (speech-recognition noise), output exactly {unclear}. Output only the "
              "translation:")
_OUT_PROMPT = ("Translate into natural {register} ({name}). Keep the meaning, the names and any quoted sentence; "
               "do not add anything.{names} Output only the translation:")


_NAME_PROMPT = ("Translate into natural {register} ({name}) the name of this household object, as the short noun a person "
                "would say in a sentence such as \"Please bring the <name>.\" A noun only: no sentence, no greeting, no "
                "apology. Output only the name:\n{en}")


@dataclass
class Heard:
    """One user utterance: the original words, their English (``""`` when none), and whether the translation can
    be trusted (``why`` says what was wrong)."""

    original: str
    english: str = ""
    trusted: bool = True
    why: str = ""
    lost: list = field(default_factory=list)      # ENV names in the original that the English does not carry


# The lab-round-3 language fixes (a lead part that cannot be said is left out of a joined question, a read-back keeps its
# kind in the last resort, the per-language Latin-script threshold of the pack) act only with one of these options on, so that with every lab
# option off a run is the earlier behaviour byte for byte.
LAB_FIX_FLAGS = ("dialog_unstick", "inventory_ask")


def localized(text, lang: str, per_language: bool = False) -> bool:
    """Is ``text`` already said in ``lang``? Language-pack data decides: a language whose pack declares ``script``
    (``letters`` and ``min_share``, plus an ``other_script`` that must not occur) is checked by that script; any other
    language with a pack by the share of non-ASCII letters (0.5, or the pack's ``non_ascii_min`` when ``per_language``).
    A language without a pack (English included) is always 'localized': nothing can show it is not."""
    lg = (lang or "")[:2]
    if lg not in LANGS:
        return True
    t = str(text or "")
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return True                                  # digits / punctuation only
    script = langdata.get(lg).get("script") or {}
    if script.get("letters"):
        mine = re.compile(script["letters"])
        other = re.compile(script["other_script"]) if script.get("other_script") else None
        return (sum(1 for c in letters if mine.match(c)) / len(letters) >= float(script.get("min_share", 0.5))
                and not (other and other.search(t)))
    floor = langdata.number(langdata.get(lg), "non_ascii_min", 0.5) if per_language else 0.5
    return sum(1 for c in letters if not c.isascii()) / len(letters) >= floor


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
        self.names_cache: dict = {}                   # (English name, lang) -> its validated name, or None (this run)

    # ── configuration ────────────────────────────────────────────────────
    @property
    def pivot(self) -> str:
        p = str((getattr(self.host, "cfg", None) or {}).get("input_pivot") or "none").lower()
        return p if p in PIVOTS else "none"

    def run_lang(self) -> str:
        return (getattr(self.host, "_run_lang", "en") or "en")[:2]

    def reset(self) -> None:
        """A new run: its own spoken log and heard texts (the translation cache is kept: same text, same words)."""
        self.spoken, self.fallbacks, self.heard, self.names_cache = [], 0, {}, {}

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

    # ── lab round 8: a model that writes the robot's words in the user's language (role_models.py) ──
    def dialog_model(self, lg) -> str:
        """The model that writes the robot's words in ``lg``: ``dialog_model_lang[lg]``, else ``dialog_model``, else "" (the
        translator writes them). The translator's own model counts as "" too."""
        c = self.host.cfg or {}
        cand = str((c.get("dialog_model_lang") or {}).get(str(lg or "")[:2].lower()) or c.get("dialog_model") or "").strip()
        own = self._model() or str((self.host._llm_cfg or {}).get("model") or "")
        return "" if not cand or cand == own else cand

    def chat_out(self, lg, prompt, ok=None) -> str:
        """One generation in ``lg``. With no dialog model for ``lg`` it is the translator's call, exactly as before. With one:
        its answer when it is not a runaway (``role_models.runaway``) and ``ok(answer)`` holds, else ONE call of the translator
        (``role_models`` documents the guard; the fallback takes a translate-budget unit, and an answer with no unit left
        is returned as it is)."""
        dm = self.dialog_model(lg)
        if not dm:
            return str(self.translator().chat(prompt=prompt, temperature=0, seed=0))
        h = self.host
        cap = (h.cfg or {}).get("dialog_max_tokens")
        out, why = "", ""
        try:
            kw = {"num_predict": int(cap)} if cap else {}
            out = str(role_models.role_client(h, "dialog", dm, h.cfg.get("dialog_url"), h.cfg.get("dialog_think"))
                      .chat(prompt=prompt, temperature=0, seed=0, **kw))
            why = role_models.runaway(out, cap) or ("" if ok is None or ok(out.strip().strip('"')) else "rejected")
        except Exception as e:                       # noqa: BLE001 - a model that cannot answer: the translator writes it
            from ..run_control import is_cancel
            if is_cancel(e):
                raise
            why = "unavailable"
        if not why:
            return out
        h._lab("dialog_model", fallback=why, model=dm, lang=lg)
        if not self._take():
            return out
        return str(self.translator().chat(prompt=prompt, temperature=0, seed=0))

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

    def _fixes(self) -> bool:
        """Are the lab-round-3 language fixes on (one of ``LAB_FIX_FLAGS``)?"""
        cfg = getattr(self.host, "cfg", None) or {}
        return any(bool(cfg.get(k)) for k in LAB_FIX_FLAGS)

    def _loc(self, text, lg: str) -> bool:
        return localized(text, lg, self._fixes())

    def _local_sentences(self, text: str, lg: str) -> str:
        """The sentences of ``text`` that are already in ``lg`` ("" when none or all)."""
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        kept = [p for p in parts if _NON_LATIN.search(p) and self._loc(p, lg)]
        return " ".join(kept) if kept and len(kept) < len(parts) else ""

    def local_slot(self, text, slot: str = "") -> Optional[str]:
        """An English slot ("kimchi fridge") in the user's language: the ENV entry whose head it is (its alias),
        else one short translation (cached). None when neither works. ``slot`` "object": the slot is a bare NAME,
        said through ``local_name`` (declared name, else a constrained, guarded translation, else the English name
        itself inside the phrase), never sent alone as free text."""
        lg = self.run_lang()
        places, s = self._places(), " ".join(str(text or "").split())
        if not s or lg == "en":
            return s or None
        if places is not None:
            for k in places.keys:
                if " ".join(speech._words(k.split("@")[0]).lower().split()) == s.lower():
                    local = speech.say_name(places, k, lg)
                    if local and self._loc(local, lg):
                        return local
        if slot == "object":
            return self.local_name(s, lg) or s
        out = self._speak_text(s, lg)
        return out if out is not None and self._loc(out, lg) else None

    # ── a bare object / place name in the user's language (never sent to the translator alone) ──
    def declared_name(self, en: str, lg: str) -> Optional[str]:
        """The name ``en`` has in ``lg`` in DECLARED data, no model: the robot pack's i18n ``names``
        (``{lang: {english name: local name}}``), else an ENV entry that carries ``en`` (its head, or an English
        alias) and an alias in ``lg``. None when nothing declares it (or ``lg`` has no data)."""
        key = " ".join(speech._words(en).lower().split())
        if not key or lg == "en":
            return None
        by = ((getattr(getattr(self.host.robot, "i18n", None), "phrases", None) or {}).get("names") or {}).get(lg)
        if isinstance(by, dict):
            for k, v in by.items():
                if " ".join(speech._words(k).lower().split()) == key and isinstance(v, str) and v.strip():
                    return v.strip()
        places = self._places()
        if places is not None:
            for k in places.keys:
                e = places.env.get(k) or {}
                if e.get("speaker"):
                    continue
                heads = {" ".join(speech._words(k.split("@")[0]).lower().split())}
                heads |= {" ".join(a.lower().split()) for a in e.get("aliases") or [] if a.isascii()}
                if key in heads:
                    local = speech.say_name(places, k, lg)
                    if local and self._loc(local, lg):
                        return local
        return None

    def _stock_tokens(self, lg: str) -> list:
        """The word lists of every sentence the pack says in ``lg`` (the robot pack's i18n phrases with their slots cut
        out, the language pack's ``answer_unsure``): what a name must not be (an apology, a greeting, a question)."""
        out, texts = [], []
        ph = getattr(getattr(self.host.robot, "i18n", None), "phrases", None) or {}
        for by in ph.values():
            v = by.get(lg) if isinstance(by, dict) else None
            texts += [v] if isinstance(v, str) else [x for x in (v if isinstance(v, list) else []) if isinstance(x, str)]
        texts += langdata.phrases(lg, "answer_unsure")
        for t in texts:
            w = [x for x in re.findall(r"\w+", re.sub(r"\{[^}]*\}", " ", t)) if len(x) >= 2]
            if w:
                out.append(w)
        return out

    def plausible_name(self, name: str, en: str, lg: str) -> Optional[str]:
        """Why ``name`` cannot be the ``lg`` name of the object ``en`` (None: it can). Script (``speech.guard``), no
        sentence punctuation, a length near the English name's (one word more, 8 letters a word), and not a stock
        phrase of the pack (a word of it, or a word sharing its first syllables)."""
        n = " ".join(str(name or "").split())
        why = speech.guard(n, lg)
        if why:
            return why
        if re.search(r"[.!?,;:\n]", n):
            return "a sentence, not a name"
        nw = max(1, len(speech._words(en).split()))
        if len(n.split()) > nw + 1 or len(n) > 8 * nw:
            return "too long for a name"
        stock = self._stock_tokens(lg)
        for c in re.findall(r"\w+", n):
            if len(c) < 2:
                continue
            for toks in stock:
                for p in toks:
                    cp = len(os.path.commonprefix([c, p]))
                    if cp >= 2 and cp >= 0.5 * min(len(c), len(p)):
                        return "a stock phrase of the robot"
        return None

    def local_name(self, en: str, lg: str) -> Optional[str]:
        """The name of the object / place ``en`` in ``lg`` (cached for the run): declared data first; else ONE
        constrained translation (the name is asked as a noun for a sentence, not translated alone as free text) that
        ``plausible_name`` must accept; None when none works (the caller keeps the English name inside the sentence)."""
        if lg == "en" or lg not in LANGS:
            return None
        s = " ".join(str(en or "").split())
        key = (s.lower(), lg)
        if key in self.names_cache:
            return self.names_cache[key]
        out = self.declared_name(s, lg)
        if out is None and not getattr(self.host, "_llm_unreachable", False) and self._take():
            prompt = _NAME_PROMPT.format(register=REGISTER.get(lg, LANGS[lg]), name=LANGS[lg], en=s)
            try:
                cand = str(self.chat_out(lg, prompt, ok=lambda o: self.plausible_name(o.strip().strip('"'), s, lg) is None))
                cand = cand.strip().strip('"')
                out = cand if self.plausible_name(cand, s, lg) is None else None
            except Exception as e:                       # noqa: BLE001 - no name: the English one stays in the sentence
                from ..run_control import is_cancel
                if is_cancel(e):
                    raise
                out = None
        self.names_cache[key] = out
        return out

    # ── OUT: the one function every spoken text passes ───────────────────
    def speak(self, text, lang, kind: str = "") -> str:
        """``text`` as the user hears it. See the module doc."""
        out = self._speak(text, lang, kind)
        self.spoken.append(str(out))
        del self.spoken[:-400]
        return out

    def _speak(self, text, lang, kind: str, strict: bool = False):
        lg = (lang or "en")[:2]
        if not text:
            return text
        if lg not in LANGS:
            return self.plain_names(str(text))          # an English line never names an ENV key either
        if not isinstance(text, speech.LocalText):
            text = self.names(str(text), lg)             # no raw ENV key (kimchi_fridge@kitchen) reaches the user
            if _NON_LATIN.search(text):
                if self._loc(text, lg):
                    return text                          # already said in the user's language
                kept = self._local_sentences(text, lg)
                if kept:
                    return kept                          # a pack line with an English reason after it: the line
        done = speech.render(text, lang, self.host.robot, self._places(), slot_tr=self.local_slot)
        if done is not None and (self._loc(done, lg) or self._declared_line(text)):
            return done
        if isinstance(text, speech.LocalText) and text.key == "_join" and len(text.parts) > 1:
            # phrases said one after the other ("Sorry, I didn't hear you." + the question asked before): each part
            # on its own, so the robot's own phrase needs no call and a part said before is cached. A lead part that
            # cannot be said is left out (the last part, the question, keeps its kind: ``_fallback``).
            if not self._fixes():
                return " ".join(str(self._speak(p, lang, kind)) for p in text.parts)
            last = len(text.parts) - 1
            outs = [self._speak(p, lang, kind if i == last else "", strict=i != last) for i, p in enumerate(text.parts)]
            return " ".join(str(o) for o in outs if o is not None)
        out = None if getattr(self.host, "_llm_unreachable", False) else self._speak_text(str(text), lg)
        if out is not None:
            return out
        return None if strict else self._fallback(text, lg, kind)

    @staticmethod
    def _declared_line(text) -> bool:
        """Is ``text`` a step milestone (``announcer`` step kinds) said from the robot's own phrase? Its only free slot is an
        object's name (declared, cached or kept in English), so the sentence is declared data and a long English name in
        it must not send the whole line to the translator."""
        from .announcer import _STEP_KINDS
        return isinstance(text, speech.LocalText) and text.key in _STEP_KINDS

    def _speak_text(self, text: str, lg: str) -> Optional[str]:
        """``text`` translated (cache, then the translator with its output guard), None when that fails."""
        dm = self.dialog_model(lg)
        key = ("out", text, lg, self._model() + (f"|{dm}" if dm else ""))
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
                out = self.chat_out(lg, f"{prompt}\n{text}",
                                    ok=lambda o: speech.guard(o, lg, keep=list(gl.values())) is None).strip().strip('"')
            except Exception:
                break
            why = speech.guard(out, lg, keep=list(gl.values()))
            if why is None:
                return out
        return None

    def _fallback(self, text, lg: str, kind: str) -> str:
        """Nothing could say ``text`` in ``lg``: the pack's plain phrase in that language, of the SAME kind. A read-back
        falls back to the plain read-back phrase (``q_confirm`` with its summary), a question to "say the task in other
        words", anything else to the plain statement. A read-back never becomes "say the task in other words": that
        asks for something else. The pack has none: the English text, counted in ``_say_not_in_lang``."""
        h = self.host
        lt = text if isinstance(text, speech.LocalText) else None
        readback = self._fixes() and lt is not None and lt.key in speech.READBACK_KEYS
        if readback and lt.key in speech.READBACK_PLAIN:
            plain = speech.LocalText(speech.READBACK_PLAIN[lt.key], lt.slots)
            out = speech.render(plain, lg, h.robot, self._places(), slot_tr=self.local_slot)
            if out is not None and self._loc(out, lg):
                self.fallbacks += 1
                return out
        is_q = kind == "question" or (lt is not None and lt.key.startswith("q_")) or str(text).rstrip().endswith("?")
        for key in (("q_unclear_task",) if is_q and not readback else ()) + ("say_untranslated",):
            p = speech.phrase(h.robot, key, lg)
            if p:
                self.fallbacks += 1
                return p
        h._say_not_in_lang = getattr(h, "_say_not_in_lang", 0) + 1
        return str(text)

    # ── IN: what the user said ───────────────────────────────────────────
    def hear(self, text, lang=None, pivot: Optional[str] = None) -> Heard:
        """The English of an utterance in the user's language, cached per text. An English run, an empty text or
        a text without that language's script: no call. ``trusted`` False: ask the user to say it again.
        ``pivot``: the view asking (option ``understand_views``) instead of the run's ``input_pivot``."""
        lg = (lang or self.run_lang())[:2]
        t = str(text or "").strip()
        pv = pivot if pivot in PIVOTS else self.pivot
        if lg == "en" or lg not in LANGS or not t or pv == "none" or not _NON_LATIN.search(t):
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

    def understand_view(self, task: str, dialogue: list, robot_line, pivot: Optional[str] = None) -> tuple:
        """(task text, original line or "", dialogue lines) for the understand prompt, by ``input_pivot``:
        ``none``: as is; ``translate``: the English translation is the task and the user's lines (the original
        follows only as the source of each goal's ``said``); ``both``: the original and its translation.
        ``dialogue``: [(who, text)]; ``robot_line(text, english)``: the loop's rendering of a robot line (as the user
        heard it, or the English text). ``pivot``: this view's own setting (option ``understand_views``), else the
        run's ``input_pivot``."""
        lg = self.run_lang()
        pivot = pivot if pivot in PIVOTS else self.pivot
        if lg == "en" or pivot == "none":
            return task, "", [robot_line(t, False) if w == "robot" else f"User: {t}" for w, t in dialogue]
        h = self.hear(task, pivot=pivot)
        task_text, orig = task, ""
        if h.english:
            if pivot == "translate":
                task_text, orig = h.english, f"\n(the user's own words, for \"said\" only: {task})"
            else:
                task_text = f"{task}\n(English: {h.english})"
        lines = []
        for w, t in dialogue:
            if w != "user":
                lines.append(robot_line(t, pivot == "translate"))
                continue
            e = self.hear(t, pivot=pivot).english
            lines.append(f"User: {e}" if e and pivot == "translate" else
                         (f"User: {t} (English: {e})" if e else f"User: {t}"))
        return task_text, orig, lines


def _head_words(en: str) -> list:
    """The words of an English name that must all show in the translation (a compound loses its meaning when it is
    cut: "table" for "dining table"); a room after "in the" is not needed."""
    return [x for x in re.findall(r"[a-z']+", str(en).lower().split(" in the ")[0]) if len(x) > 1]


__all__ = ["LanguageAdapter", "Heard", "PIVOTS", "LANGS", "REGISTER", "localized"]
