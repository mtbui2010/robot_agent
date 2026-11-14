"""Option ``name_recover`` (default off): an object name the speech recogniser garbled is matched to the robot's object catalogue, strictly.

The failure it closes: recognition turns "teapot" into "teapod" (a slip of one or two letters) and the reader hands the garbled word on as the
goal's object; the robot searches for something it does not know or asks about it. After the goals are read (hook ``on_goals``), each goal's
object that is NOT in the catalogue (the robot's object memory and the manifest's ``limits.objects``) is compared with the catalogue names:

* ONE catalogue name within the limit: the goal takes it (the read-back that follows says it, as for any goal);
* TWO OR MORE within the limit: the person is asked "Did you mean A or B?" once per run; the answer that names exactly one of them decides, any
  other answer leaves the goal as read (never a guess);
* none within the limit: nothing changes, nothing is asked.

The limit (keys ``max_edits`` 2 and ``max_ratio`` 0.34 of the longer name, both assumed; ``min_len`` 4 units: a shorter name is never matched fuzzily)
is a distance in UNITS: ``understand.near_distance`` over letters, or over the letters inside a syllable when the language pack declares
``asr.distance_unit: "jamo"`` (Korean). A name already in the catalogue is never changed, nor one that is, as whole words, part of a catalogue name
or holds one ("lamp" / "red lamp"), nor a place or a person of the ENV.

What it cannot recover: a word that SOUNDS like the object but is spelled in another script or another language (a sound-alike across scripts), or
a garble of more than the limit's letters; those stay as the reader gave them (a question comes from the engine's own object check). No pack data
beyond ``asr.distance_unit``; no catalogue or no unknown object: nothing happens. Hook ``on_goals``. Keys: ``enabled`` (legacy ``name_recover``),
``max_edits``, ``max_ratio``, ``min_len``, ``ask`` (true). Lab flag ``name_recover``: ``{said, took | asked}``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import langdata, names, speech, understand

SPEC = OptionSpec(
    name="name_recover",
    keys=(Key("enabled", "bool", False, aliases=("name_recover",)), Key("max_edits", "int", 2), Key("max_ratio", "float", 0.34),
          Key("min_len", "int", 4), Key("ask", "bool", True)),
    hooks=("on_goals",),
    order=70,
    enabled="enabled",
    doc="A goal object the catalogue lacks takes the ONE catalogue name within a strict letter distance; two or more near names are asked; none changes nothing.",
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


def _question(options):
    head, last = ", ".join(options[:-1]), options[-1]
    return speech.LocalText("q_which_name", {"options": options}, text=f"Did you mean {head} or {last}?",
                            ask={"intent": "choose", "options": list(options), "about": ""})


class NameRecover(Option):
    def on_goals(self, run):
        """Replace a garbled goal object by the one near catalogue name; ask when there are several (once per run)."""
        langs = names.langs_of(run)
        cat = catalogue(run, self.svc.robot, langs)
        if not cat or not getattr(run, "goals", None):
            return None
        return (yield from self._each(run, cat, langs))

    def _each(self, run, cat, langs):
        env = getattr(getattr(run, "places", None), "env", None) or {}
        known = {names.plain(x, langs) for k, v in env.items() for x in [run.places.head(k)] + list(v.get("aliases") or []) if x}
        for g in run.goals:
            word = names.plain(g.get("object"), langs) if g.get("type") != "say" else ""
            if not word or word in known or any(related(word, c, langs) for c in cat):
                continue
            hits = near(word, cat, langs, self.cfg)
            if len(hits) == 1:
                self.svc.lab(SPEC.name, said=word, took=hits[0])
                g["object"] = hits[0]
            elif len(hits) > 1 and self.cfg["ask"] and not getattr(run, "name_asked", False):
                run.name_asked = True
                q = _question(hits)
                yield run.ev("clarify", question=q, round=run.asked + 1, kind="name_recover")
                ans = self.svc.ask(run, q)
                stop = self.svc.cancelled(run)
                if stop:
                    yield stop
                    return STOP
                said = [c for c in hits if names.has(c, ans, langs)]
                self.svc.lab(SPEC.name, said=word, asked=hits, took=said[0] if len(said) == 1 else "")
                if len(said) == 1:
                    g["object"] = said[0]
        return None


register(SPEC, NameRecover)
