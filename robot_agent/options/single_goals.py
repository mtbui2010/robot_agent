"""Option ``single_goals`` (default off): a request for ONE action with no destination (find this, pick this up, put it down here) is read as
that action (goal kinds ``find`` / ``pick`` / ``put_here``, ``goals.SINGLE_KINDS``), not as a fetch to the person or a question.
What: the kinds of key ``kinds`` (default ``find`` only) that the robot has the actions for and that have a rule line go into the understand
schema, their lines (block ``single_goals``, ``prompt`` {kind: [lines]} of the prompt language, labelled) into the prompt; the engine plans
and checks the kinds (``goals.py``). When the goals hold, a find says where the object was seen (phrase ``say_found_at``, the person's language).
Why: the reader made "Find the red cup" a delivery to the person and "Put it down here" a question (no kind for them).
Fail open: a kind without a labelled rule line is not offered; no phrase in the run's language or no remembered place = the usual "done".
Switch off: key ``enabled`` False (the default; run option ``single_goals``). Event: lab flag ``single_goals`` ``{part, kinds | object, place}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import langdata, speech
from robot_agent.core.planning.pack_data import labelled

SPEC = OptionSpec(
    name="single_goals",
    keys=(Key("enabled", "bool", False, aliases=("single_goals",)),
          Key("kinds", "str", "find", aliases=("single_goals_kinds",))),     # comma list of goals.SINGLE_KINDS names
    hooks=("on_understand_schema", "on_understand_prompt", "on_goals_met"),
    order=46,
    enabled="enabled",
    doc="One action with no destination (find, pick, put down here) is its own goal kind, and a find says where the object was seen.",
)

FOUND = "say_found_at"
_MARK = "{goal_kinds}"                 # the understand template's placeholder of the goal-kind lines: the rule lines follow it


def rule_lines(lang, kinds) -> dict:
    """{kind: its rule lines as one text} of the pack (block ``single_goals``, ``prompt`` {kind: [lines]}) for ``kinds``, in that order;
    {} when the block is unlabelled; a kind without lines is left out."""
    blk = langdata.block(lang, "single_goals")
    p = blk.get("prompt") if labelled(blk) else None
    out = {}
    for k in kinds:
        lines = p.get(k) if isinstance(p, dict) else None
        text = "\n".join(x for x in lines if isinstance(x, str) and x.strip()) if isinstance(lines, list) else ""
        if text:
            out[k] = text
    return out


def kinds_for(actions, wanted=tuple(goal_kinds.SINGLE_KINDS)) -> list:
    """The kinds of ``wanted`` (single-action kinds) a robot with ``actions`` can be given (every action a kind needs is there)."""
    return [k for k in wanted if k in goal_kinds.SINGLE_KINDS and not goal_kinds.missing_actions({"type": k}, actions)]


def with_rules(template: str, lines: str):
    """The understand template with ``lines`` right after the goal-kind lines (braces escaped: it is formatted next); None when it has no mark."""
    if not lines or _MARK not in template:
        return None
    return template.replace(_MARK, _MARK + "\n" + lines.replace("{", "{{").replace("}", "}}"), 1)


def found(goals, where) -> list:
    """[(object, place)] of the find goals whose object ``where(object)`` places (pure: ``where`` is the caller's lookup)."""
    out = []
    for g in goals or []:
        o = g.get("object") if g.get("type") == "find" else ""
        place = where(o) if o else None
        if place and (o, place) not in out:
            out.append((o, place))
    return out


class SingleGoals(Option):
    def _lines(self) -> dict:
        """{kind: rule lines} of the kinds this robot is offered: listed in key ``kinds``, its actions there, a labelled line in the pack."""
        wanted = [k.strip() for k in str(self.cfg.get("kinds") or "").split(",") if k.strip()]
        return rule_lines(langdata.prompt_lang(), kinds_for(getattr(self.svc.robot, "plan_actions", None) or (), wanted))

    def on_understand_schema(self, schema):
        """``schema`` with the single-action kinds the robot can do appended to the goal type enum; None (unchanged) without rule lines."""
        typ = (((schema.get("properties") or {}).get("goals") or {}).get("items") or {}).get("properties", {}).get("type")
        add = [k for k in self._lines() if isinstance(typ, dict) and k not in (typ.get("enum") or [])]
        if not add:
            return None
        typ["enum"] = list(typ.get("enum") or []) + add
        self.svc.lab("single_goals", part="schema", kinds=add)
        return schema

    def on_understand_prompt(self, text):
        return with_rules(text, "\n".join(self._lines().values()))

    def on_goals_met(self, run):
        """A find goal met: finish saying where its object was seen, in the run's language; None (the usual done) without a phrase or a place."""
        seen = found(run.goals, lambda o: self.svc.remembered(run, o))
        if not seen:
            return None
        lang = getattr(run, "lang", None) or "en"
        for o, place in seen:
            self.svc.lab("single_goals", part="found", object=o, place=place)
        en, own = speech.phrase(self.svc.robot, FOUND, "en"), speech.phrase(self.svc.robot, FOUND, lang)
        if not en or not own:
            return None
        texts = [speech.LocalText(FOUND, {"object": self.svc.said_of(run, o), "place": p},
                                  text=en.format(object=o, place=self.svc.say_name(run.places, p))) for o, p in seen]
        return self.svc.finish(run, "success", say=speech.LocalText.join(*texts), found=[{"object": o, "place": p} for o, p in seen])


register(SPEC, SingleGoals)
