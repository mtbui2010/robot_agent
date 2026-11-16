"""Option ``heat_safety`` (default off): a goal that puts a thing that can burn on or in a heat-source place is refused (docs/heat_safety.md).

What: for an ``on`` / ``in`` goal whose ``to`` is a declared heat place, the pure ``heat_check`` reads the object phrase: a head noun in the
declared ``cookware`` passes; a declared burnable word refuses; else one model call (``ask_model``, the language pack's ``judge`` prompt)
names its material and judges it: burns / unsure or a burnable material refuses, safe or no answer passes (one call per phrase in
the run). All goals refused: the run ends ``refused``; some: dropped, named at the end. Services ``forbids`` (lists only) and ``refused_goals``.
Why: the reader reads "put the lighter on the stove" as clear, and a word list alone missed odd things and refused a pan by its handle.
Data ``planner.options.heat_safety``: ``places`` {key or head: {clearance_m, label}}, ``burnable`` / ``cookware`` {words, label}; language
block ``heat_safety``: ``refuse`` / ``part`` ({m}), ``after_head``, ``judge`` ({request}, {object}, {place}). No heat place: nothing, no call.
Switch off: key ``enabled`` (run option ``heat_safety``), off by default; key ``judge`` (``heat_safety_judge``) off skips the model call.
Event: lab flag ``heat_safety`` {part: refuse | drop | no_phrase, ...} and {part: judge, object, place, answer, material}.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import langdata, names, pack_data

FLAG = "heat_safety"
PUT_KINDS = ("on", "in")          # the goal kinds that leave their object at their ``to`` place (the engine's goal contract)
REFUSE = ("burns", "unsure")                           # the judge's verdicts that refuse (unsure is the safe side); else "safe" or "none"
SCHEMA = {"type": "object", "properties": {"material": {"type": "string"}, "burns": {"type": "boolean"}, "unsure": {"type": "boolean"}},
          "required": ["material", "burns", "unsure"]}  # the material first: the declared burnable words judge it too

SPEC = OptionSpec(
    name=FLAG,
    keys=(Key("enabled", "bool", False, aliases=(FLAG,)), Key("judge", "bool", True, aliases=(FLAG + "_judge",)),
          Key("places", "dict", {}, sources=("manifest",)), Key("burnable", "dict", {}, sources=("manifest",)),
          Key("cookware", "dict", {}, sources=("manifest",))),
    hooks=("on_goals", "on_goals_met", "on_finish"),
    services=("forbids", "refused_goals"),
    order=11,
    enabled="enabled",
    doc="A goal that puts a burnable thing on or in a declared heat-source place is refused, or dropped and named at the end: the object's head "
        "noun in the declared cookware passes, a declared burnable word refuses, else one model call judges it (planner.options.heat_safety).",
)


class Rule(NamedTuple):          # heat {place: clearance m}; burnable words; cookware heads; cuts: post-modifier words (the head is before them)
    heat: dict
    burnable: tuple = ()
    cookware: tuple = ()
    cuts: tuple = ()


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


def word_list(data, label=True) -> tuple:
    """The declared ``words`` of ``data`` (lower case; ``data`` itself when a list and ``label`` False), () when missing or unlabelled."""
    v = data if isinstance(data, list) and not label else data.get("words") if pack_data.labelled(data) else None
    return tuple(dict.fromkeys(_norm(w) for w in v if _norm(w))) if isinstance(v, list) else ()


def same_place(declared: str, to: str) -> bool:
    """Is ``to`` the declared place: the same head, and the same room when both name one (``stove`` is every ``stove@<room>``)."""
    (h1, _s1, r1), (h2, _s2, r2) = _norm(declared).partition("@"), _norm(to).partition("@")
    return bool(h1) and h1 == h2 and (not r1 or not r2 or r1 == r2)


def phrase_of(goal) -> str:
    """The object phrase the rule reads: ``object``, else the person's ``said``."""
    return _norm(goal.get("object")) or _norm(goal.get("said"))


def head_words(phrase: str, cuts=(), langs=None) -> list:
    """The words of ``phrase`` up to its head noun: before the first post-modifier word (``cuts``), plurals folded."""
    toks = names.plain(phrase, langs).split()
    cut = next((i for i, w in enumerate(toks) if i and w in cuts), len(toks))
    return [names.fold(w, langs) for w in toks[:cut]]


def is_cookware(phrase: str, cookware=(), cuts=(), langs=None) -> bool:
    """Does the head noun of ``phrase`` (its last words before a post-modifier) name a declared cookware head?"""
    head = head_words(phrase, cuts, langs)
    return any(want and head[-len(want):] == want for want in ([names.fold(w, langs) for w in e.split()] for e in cookware))


def heat_check(goal, rule: Rule, langs=None) -> Optional[tuple]:
    """(heat place, step) for a put on or in a place of ``rule.heat``: step ``pass`` (a cookware head, or no object), ``refuse`` (a
    declared burnable word in ``object`` or ``said``) or ``judge`` (the lists do not decide); None for any other goal."""
    if goal.get("type") not in PUT_KINDS:
        return None
    place = next((p for p in rule.heat if same_place(p, goal.get("to"))), None)
    if place is None:
        return None
    phrase = phrase_of(goal)
    if not phrase or is_cookware(phrase, rule.cookware, rule.cuts, langs):
        return place, "pass"
    text = " ".join(_norm(goal.get(k)) for k in ("object", "said"))
    return place, "refuse" if any(names.has(w, text, langs) for w in rule.burnable) else "judge"


def judgement(reply, burnable=(), langs=None) -> tuple:
    """(verdict, material) of the judge's JSON reply: ``unsure``, ``burns``, or ``safe`` unless the ``material`` it names holds a declared
    ``burnable`` word (then ``burns``); ``none`` (fail open) when there is no reply or ``burns`` / ``unsure`` is not a boolean."""
    reply = reply if isinstance(reply, dict) else {}
    burns, unsure, material = reply.get("burns"), reply.get("unsure"), _norm(reply.get("material"))
    if not isinstance(burns, bool) or not isinstance(unsure, bool):
        return "none", material
    if unsure:
        return "unsure", material
    return ("burns" if burns or any(names.has(w, material, langs) for w in burnable) else "safe"), material


def lang_text(lang, key: str):
    """The labelled language-pack value ``key`` of block ``heat_safety`` in ``lang`` (None when the block has no label)."""
    block = langdata.block(str(lang or "")[:2], FLAG)
    return block.get(key) if pack_data.labelled(block) else None


def sentence(lang, key: str, m: float) -> Optional[str]:
    """The labelled language-pack sentence ``key`` of ``lang`` with the clearance ``m`` in it, None when the pack has none."""
    tpl = lang_text(lang, key)
    return tpl.replace("{m}", f"{m:g}") if isinstance(tpl, str) and tpl.strip() else None


def judge_prompt(phrase: str, place: str, request: str = "") -> Optional[str]:
    """The prompt language's ``judge`` prompt for ``phrase`` at the heat place ``place`` (its head), with the person's own ``request``
    (the reader may have renamed the object); None when the pack has none."""
    tpl = lang_text(langdata.prompt_lang(), "judge")
    if not isinstance(tpl, str) or not tpl.strip():
        return None
    head = _norm(place).partition("@")[0]
    return tpl.replace("{object}", phrase).replace("{place}", head).replace("{request}", " ".join(str(request).split()))


def record(goal, place: str, why: str) -> dict:
    """One entry of the ``done`` event's ``refusals`` (the shape option ``hazard`` writes, docs/safety.md)."""
    return {"kind": "safety", "rule": f"{FLAG}:{place}", "why": why,
            "goal": {k: str(goal.get(k) or "") for k in ("type", "object", "said", "from", "to", "near_person")}}


class HeatSafety(Option):
    def _state(self, run) -> dict:
        return run.__dict__.setdefault("opt", {}).setdefault(FLAG, {})

    def _rule(self) -> Rule:
        """The declared rule, read again now (pack data may arrive after the loop was built); the post-modifiers are the prompt language's."""
        planner = pack_data.planner_of(self.svc)
        data = {k: pack_data.fresh(SPEC, self.cfg, planner, k) for k in ("places", "burnable", "cookware")}
        return Rule(heat_places(data["places"]), word_list(data["burnable"]), word_list(data["cookware"]),
                    word_list(lang_text(langdata.prompt_lang(), "after_head"), label=False))

    def _judge(self, run, phrase: str, place: str, burnable) -> str:
        """The model's verdict on ``phrase`` at ``place`` (one call per phrase and place in the run, logged); ``none`` when not asked or failed."""
        seen = self._state(run).setdefault("judged", {})
        key = f"{phrase}@{place}"
        if key not in seen:
            prompt = judge_prompt(phrase, place, getattr(run, "task", "") or "") if self.cfg.get("judge") else None
            ask = getattr(self.svc, "ask_model", None)
            if prompt is None or ask is None:
                return "none"
            seen[key], material = judgement(ask(run, prompt, SCHEMA), burnable)
            self.svc.lab(FLAG, part="judge", object=phrase, place=place, answer=seen[key], material=material)
        return seen[key]

    def _refused_at(self, run, goal, rule: Rule) -> Optional[str]:
        """The heat place where ``goal`` is refused, else None; ``run`` None (service ``forbids``): the lists only, no model call."""
        c = heat_check(goal, rule)
        if c and c[1] == "judge" and run is not None:
            c = (c[0], "refuse" if self._judge(run, phrase_of(goal), c[0], rule.burnable) in REFUSE else "pass")
        return c[0] if c and c[1] == "refuse" else None

    def _found(self, run, goals) -> list:
        """[(goal, (place, clearance))] of the goals the rule refuses (none, and nothing read, without a declared heat place)."""
        rule = self._rule()
        return [(g, (p, rule.heat[p])) for g in (goals if rule.heat else ()) for p in [self._refused_at(run, g, rule)] if p]

    def _say(self, run, key: str, m: float) -> tuple:
        """(sentence, localized): the run's language, else the prompt language (translated by the engine); (None, False) when neither has it."""
        own = sentence(getattr(run, "lang", None), key, m)
        return (own, True) if own else (sentence(langdata.prompt_lang(), key, m), False)

    # ── services ──
    def forbids(self, goal, places):
        found = self._found(None, [goal])
        return sentence(langdata.prompt_lang(), "refuse", found[0][1][1]) if found else None

    def refused_goals(self, run):
        return list(self._state(run).get("parts", []))

    # ── hooks ──
    def on_goals(self, run):
        """Every goal refused: the run ends ``refused`` (STOP). Some: they are dropped and said at the end. None: nothing to refuse."""
        found, goals = self._found(run, run.goals), list(run.goals)
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
__all__ = ["FLAG", "SPEC", "Rule", "HeatSafety", "heat_places", "word_list", "same_place", "phrase_of", "head_words", "is_cookware", "heat_check",
           "judgement", "lang_text", "sentence", "judge_prompt", "record"]
