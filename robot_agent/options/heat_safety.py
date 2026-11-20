"""Option ``heat_safety`` (default off): a goal that puts a thing that can burn on or in a heat-source place is refused (docs/heat_safety.md).

What: the pure ``heat_verdict`` fires for an ``on`` / ``in`` goal whose ``to`` is a declared heat place and whose object (``object``, ``said``)
holds a declared burnable word. If every goal fires, the run ends ``refused`` (kind safety). If only some do, they are dropped, named at the end
and listed in the ``refusals`` of ``done``. Services ``forbids`` and ``refused_goals``, as option ``hazard`` serves them.
Why: the reader read "put the lighter on the stove" as a clear goal (20 of 20 probe requests) and no rule related the object to the place.
Data ``planner.options.heat_safety``: ``places`` {ENV key or head: {clearance_m, source | assumed}}, ``burnable`` {words, source | assumed}.
The sentences are in the language-pack block ``heat_safety`` (``refuse``, ``part``, slot ``{m}``). With data missing or unlabelled, nothing happens.
Switch off: key ``enabled`` (run option ``heat_safety``), off by default. Event: lab flag ``heat_safety`` {part, places, refused, goals}.
"""

from __future__ import annotations

from typing import Optional

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import langdata, names, pack_data

FLAG = "heat_safety"
PUT_KINDS = ("on", "in")          # the goal kinds that leave their object at their ``to`` place (the engine's goal contract)

SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", False, aliases=(FLAG,)), Key("places", "dict", {}, sources=("manifest",)),
          Key("burnable", "dict", {}, sources=("manifest",))),
    hooks=("on_goals", "on_goals_met", "on_finish"),
    services=("forbids", "refused_goals"),
    order=11,
    enabled="enabled",
    doc="A goal that puts a declared burnable thing on or in a declared heat-source place is refused, or dropped and named at the end "
        "(planner.options.heat_safety: places with a cited clearance, burnable words).",
)


def _norm(s) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").split())


def heat_places(data) -> dict:
    """{place: clearance in metres} of the declared heat places that carry a label and a clearance above 0."""
    out = {}
    for k, v in (data.items() if isinstance(data, dict) else ()):
        m = v.get("clearance_m") if pack_data.labelled(v) else None
        if isinstance(m, (int, float)) and not isinstance(m, bool) and m > 0 and _norm(k):
            out[_norm(k)] = float(m)
    return out


def burnable_words(data) -> tuple:
    """The declared words of things that can burn (lower case), () when the list is missing or unlabelled."""
    words = data.get("words") if pack_data.labelled(data) else None
    return tuple(dict.fromkeys(_norm(w) for w in words if _norm(w))) if isinstance(words, list) else ()


def same_place(declared: str, to: str) -> bool:
    """Is ``to`` the declared place: the same head, and the same room when both name one (``stove`` is every ``stove@<room>``)."""
    (h1, _s1, r1), (h2, _s2, r2) = _norm(declared).partition("@"), _norm(to).partition("@")
    return bool(h1) and h1 == h2 and (not r1 or not r2 or r1 == r2)


def heat_verdict(goal, heat: dict, burnable, langs=None) -> Optional[tuple]:
    """(heat place, clearance) when ``goal`` puts a thing with a ``burnable`` word on or in a place of ``heat`` (distance 0, inside any
    clearance), else None. Words are whole words of the goal's ``object`` and ``said``, plurals folded by the language packs."""
    if goal.get("type") not in PUT_KINDS:
        return None
    place = next((p for p in heat if same_place(p, goal.get("to"))), None)
    text = " ".join(_norm(goal.get(k)) for k in ("object", "said"))
    if place is None or not any(names.has(w, text, langs) for w in burnable):
        return None
    return place, heat[place]


def sentence(lang, key: str, m: float) -> Optional[str]:
    """The labelled language-pack sentence ``key`` of ``lang`` with the clearance ``m`` in it, None when the pack has none."""
    block = langdata.block(str(lang or "")[:2], FLAG)
    tpl = block.get(key) if pack_data.labelled(block) else None
    return tpl.replace("{m}", f"{m:g}") if isinstance(tpl, str) and tpl.strip() else None


def record(goal, place: str, why: str) -> dict:
    """One entry of the ``done`` event's ``refusals`` (the shape option ``hazard`` writes, docs/safety.md)."""
    return {"kind": "safety", "rule": f"{FLAG}:{place}", "why": why,
            "goal": {k: str(goal.get(k) or "") for k in ("type", "object", "said", "from", "to", "near_person")}}


class HeatSafety(Option):
    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(FLAG, {})

    def _found(self, goals) -> list:
        """[(goal, (place, clearance))] of the goals the declared rule refuses (data read again now: it may arrive after the loop was built)."""
        planner = pack_data.planner_of(self.svc)
        heat = heat_places(pack_data.fresh(SPEC, self.cfg, planner, "places"))
        burn = burnable_words(pack_data.fresh(SPEC, self.cfg, planner, "burnable"))
        return [(g, v) for g in goals for v in [heat_verdict(g, heat, burn)] if v] if heat and burn else []

    def _say(self, run, key: str, m: float) -> tuple:
        """(sentence, localized): the run's language, else the prompt language (translated by the engine); (None, False) when neither has it."""
        own = sentence(getattr(run, "lang", None), key, m)
        return (own, True) if own else (sentence(langdata.prompt_lang(), key, m), False)

    # ── services ──
    def forbids(self, goal, places):
        found = self._found([goal])
        return sentence(langdata.prompt_lang(), "refuse", found[0][1][1]) if found else None

    def refused_goals(self, run):
        return list(self._state(run).get("parts", []))

    # ── hooks ──
    def on_goals(self, run):
        """Every goal refused: the run ends ``refused`` (STOP). Some: they are dropped and said at the end. None: nothing to refuse."""
        found, goals = self._found(run.goals), list(run.goals)
        if not found:
            return None
        whole, m = len(found) == len(goals), min(v[1] for _g, v in found)
        say, local = self._say(run, "refuse" if whole else "part", m)
        part = "no_phrase" if say is None else "refuse" if whole else "drop"
        self.svc.lab(FLAG, part=part, places=sorted({v[0] for _g, v in found}), refused=len(found), goals=len(goals))
        if say is None:
            return None
        st = self._state(run)
        st["refusals"] = list(st.get("refusals", [])) + [record(g, v[0], str(say)) for g, v in found]
        if whole:
            yield self.svc.finish(run, "refused", refusal="safety", msg=f"unsafe: {goal_kinds.describe(found[0][0])} ({FLAG})",
                                  say=say, localized=local)
            return STOP
        st["parts"], st["say"] = list(st.get("parts", [])) + [g for g, _v in found], (say, local)
        run.goals = [g for g in goals if all(g is not b for b, _v in found)]
        return None

    def on_goals_met(self, run):
        """The goals hold but some were dropped here: the run ends saying which were not done and why."""
        st = self._state(run)
        if not st.get("parts"):
            return None
        say, local = st["say"]
        return self.svc.finish(run, "success", say=say, localized=local, refused_parts=[goal_kinds.describe(g) for g in st["parts"]])

    def on_finish(self, run, fields) -> None:
        """This option's refusals join the ``refusals`` of every final status."""
        mine = self._state(run).get("refusals") or []
        have = list(fields.get("refusals") or [])
        if mine:
            fields["refusals"] = have + [r for r in mine if r not in have]


register(SPEC, HeatSafety)
__all__ = ["FLAG", "SPEC", "HeatSafety", "heat_places", "burnable_words", "same_place", "heat_verdict", "sentence", "record"]
