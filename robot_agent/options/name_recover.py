"""Option ``name_recover`` (default off): an object name the speech recogniser garbled is matched to the robot's object catalogue, strictly.

The failure it closes: recognition turns "teapot" into "teapod" (a slip of one or two letters) and the reader hands the garbled word on as the
goal's object; the robot searches for something it does not know or asks about it. After the goals are read (hook ``on_goals``), each goal's
object that is NOT in the catalogue (the robot's object memory and the manifest's ``limits.objects``) is compared with the catalogue names:

* ONE catalogue name within the limit: the person is asked "Did you mean the X?" (the core phrase ``q_did_you_mean``); a yes (or the name
  said) takes it, a no or anything else keeps the goal as read. The catalogue is often only what the robot has seen, so a real word one letter
  from a known name ("handle" / "candle") may be a thing it has not seen yet: the name is taken WITHOUT a question only when the robot declares
  its catalogue complete (key ``complete``) AND the language packs declare their words (``asr.words``) and no word of the name is one of them;
* TWO OR MORE within the limit: the person is asked "Did you mean A or B?" (``q_which_name``); the answer that names exactly one of them
  decides, any other answer leaves the goal as read (never a guess);
* none within the limit: nothing changes, nothing is asked.

The limit (keys ``max_edits`` 2 and ``max_ratio`` 0.34 of the longer name, both assumed; ``min_len`` 4 units: a shorter name is never matched fuzzily)
is a distance in UNITS: ``understand.near_distance`` over letters, or over the letters inside a syllable when the language pack declares
``asr.distance_unit: "jamo"`` (Korean). A name already in the catalogue is never changed, nor one that is, as whole words, part of a catalogue name
or holds one ("lamp" / "red lamp"), nor a place or a person of the ENV.

A word is settled once per run (a later reading of the same request takes the same answer, no second question). The questions are phrases
(the robot's i18n, else the language pack's ``speech`` block, else the core English one); a language with no text for the question, or the key
``ask`` off: nothing is asked and nothing changes (fail open).

What it cannot recover: a word that SOUNDS like the object but is spelled in another script or another language (a sound-alike across scripts), or
a garble of more than the limit's letters; those stay as the reader gave them (a question comes from the engine's own object check). Pack data:
``asr.distance_unit``, ``asr.words``, ``speech.q_which_name``; no catalogue or no unknown object: nothing happens. Hook ``on_goals``. Keys:
``enabled`` (legacy ``name_recover``), ``max_edits``, ``max_ratio``, ``min_len``, ``ask`` (true), ``complete`` (false). Lab flag
``name_recover``: ``{said, took, asked | near}``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import langdata, names, speech, understand

SPEC = OptionSpec(
    name="name_recover",
    keys=(Key("enabled", "bool", False, aliases=("name_recover",)), Key("max_edits", "int", 2), Key("max_ratio", "float", 0.34),
          Key("min_len", "int", 4), Key("ask", "bool", True), Key("complete", "bool", False)),
    hooks=("on_goals",),
    order=70,
    enabled="enabled",
    doc="A goal object the catalogue lacks is asked about the catalogue names within a strict letter distance (one: did you mean X; several: A or B); none changes nothing.",
)


def catalogue(run, robot, langs) -> list:
    """The object catalogue: the robot's object memory and the manifest's ``limits.objects`` (normalised, no duplicates)."""
    mem = getattr(getattr(run, "memory", None), "data", None) or {}
    lim = getattr(getattr(robot, "manifest", None), "limits", None) or {}
    return list(dict.fromkeys(n for n in (names.plain(x, langs) for x in list(mem.get("objects") or {}) + list(lim.get("objects") or {})) if n))


def unit_fn(langs):
    """The text as the units the packs count a name distance in: the letters inside a syllable when a pack says ``jamo``, else its characters."""
    if any((langdata.value(lg, "asr", {}) or {}).get("distance_unit") == "jamo" for lg in langs):
        return understand._jamo
    return lambda s: s


def related(a: str, b: str, langs) -> bool:
    """One name is, as whole words, part of the other (or equal)."""
    return names.has(a, b, langs) or names.has(b, a, langs)


def near(word: str, cat, langs, cfg) -> list:
    """The catalogue names within the limit of ``word`` (not related to it as whole words), nearest first."""
    unit, hits = unit_fn(langs), []
    a = unit(word)
    if len(a) < cfg["min_len"]:
        return []
    for c in cat:
        b = unit(c)
        if len(b) < cfg["min_len"] or related(word, c, langs):
            continue
        d = understand.near_distance(a, b)
        if d <= cfg["max_edits"] and d / max(len(a), len(b)) <= cfg["max_ratio"]:
            hits.append((d, c))
    return [c for _d, c in sorted(hits)]


def lang_words(langs):
    """The words of the languages (the packs' ``asr.words``, lower case), or None when no pack of ``langs`` declares any."""
    got = [str(w).lower() for lg in langs for w in ((langdata.value(lg, "asr", {}) or {}).get("words") or [])]
    return set(got) if got else None


def sure(word: str, langs, cfg) -> bool:
    """The one near name may be taken without a question: the robot declares its catalogue ``complete`` AND the packs declare the
    words of the language and no word of ``word`` is one of them (a real word near a catalogue name is a request for something else)."""
    words = lang_words(langs)
    return bool(cfg["complete"]) and words is not None and not any(t in words for t in word.split())


def question(hits):
    """"Did you mean the X?" (one name, yes / no: the core's ``q_did_you_mean``) or "Did you mean A or B?" (``q_which_name``)."""
    if len(hits) == 1:
        return speech.LocalText("q_did_you_mean", {"object": hits[0], "place": hits[0]})
    text = speech.CORE_PHRASES["q_which_name"].format(options=speech.CORE_PHRASES["sep_or"].join(hits))
    return speech.LocalText("q_which_name", {"options": list(hits)}, text=text, ask={"intent": "choose", "options": list(hits), "about": ""})


def chosen(hits, ans, langs) -> str:
    """The name the answer takes: one name and a yes (or the name said, not with a no); several and exactly one of them named; else ""."""
    from robot_agent.core.run_control import yes_no
    said = [c for c in hits if names.has(c, ans, langs)]
    if len(hits) == 1:
        yes = yes_no(ans)[0]
        return hits[0] if yes is True or (yes is None and said) else ""
    return said[0] if len(said) == 1 else ""


class NameRecover(Option):
    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(SPEC.name, {"took": {}, "kept": []})

    def on_goals(self, run):
        """Replace a garbled goal object by the near catalogue name the person confirms (or, declared sure, the one near name)."""
        langs = names.langs_of(run)
        cat = catalogue(run, self.svc.robot, langs)
        if not cat or not getattr(run, "goals", None):
            return None
        for holder, word in self._slots(run, cat, langs):
            took = yield from self._resolve(run, word, cat, langs)
            if took is STOP:
                return STOP
            if took:
                holder["object"] = took
        return None

    def _slots(self, run, cat, langs) -> list:
        """(the dict that holds the name, the name) of every goal object to recover: not a say, not a place or person of the ENV,
        not (as whole words) a catalogue name, part of one or holding one."""
        env = getattr(getattr(run, "places", None), "env", None) or {}
        known = {names.plain(x, langs) for k, v in env.items() for x in [run.places.head(k)] + list(v.get("aliases") or []) if x}
        out = []
        for g in run.goals:
            word = names.plain(g.get("object"), langs) if isinstance(g, dict) and g.get("type") != "say" else ""
            if word and word not in known and not any(related(word, c, langs) for c in cat):
                out.append((g, word))
        return out

    def _resolve(self, run, word, cat, langs):
        """Flow: the catalogue name ``word`` stands for ("" = keep it as read), STOP. A word settled before in the run is settled again
        without a question (a later reading of the same request)."""
        st = self._state(run)
        if word in st["took"] or word in st["kept"]:
            return st["took"].get(word, "")
        hits = near(word, cat, langs, self.cfg)
        if not hits:
            return ""
        if len(hits) == 1 and sure(word, langs, self.cfg):
            took = hits[0]
            self.svc.lab(SPEC.name, said=word, took=took)
        else:
            took = yield from self._ask(run, word, hits, langs)
            if took is STOP:
                return STOP
        if took:
            st["took"][word] = took
        else:
            st["kept"].append(word)
        return took

    def _ask(self, run, word, hits, langs):
        """Flow: the question about ``hits``, the name the answer takes ("" when it takes none, the key ``ask`` is off or the robot has no
        text for the question in the run's language: fail open), STOP."""
        q = question(hits)
        if not self.cfg["ask"] or speech.phrase(self.svc.robot, q.key, getattr(run, "lang", None) or "en") is None:
            self.svc.lab(SPEC.name, said=word, near=list(hits), took="")
            return ""
        yield run.ev("clarify", question=q, round=run.asked + 1, kind="name_recover")
        ans = self.svc.ask(run, q)
        stop = self.svc.cancelled(run)
        if stop:
            yield stop
            return STOP
        took = chosen(hits, ans or "", langs)
        self.svc.lab(SPEC.name, said=word, asked=list(hits), took=took)
        return took


register(SPEC, NameRecover)
