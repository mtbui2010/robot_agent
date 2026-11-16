"""Option ``single_goals`` (default off): a request for ONE action with no destination (find this, pick this up, put it down here) is read as
that action (goal kinds ``find`` / ``pick`` / ``put_here``, ``goals.SINGLE_KINDS``), not as a fetch to the person or a question.
What: the understand schema gets the kinds the robot has the actions for and the prompt the language pack's rule lines (block ``single_goals``
``prompt`` of the prompt language, labelled); the engine plans and checks the kinds (``goals.py``). When the goals hold, a find is answered
with the place the robot saw the object at (phrase ``say_found_at``, the person's language).
Why: the reader made "Find the red cup" a delivery to the person and "Put it down here" a question (no kind for them).
Fail open: no labelled rule lines = schema and prompt unchanged; no phrase in the run's language or no remembered place = the usual "done".
Switch off: key ``enabled`` False (the default; run option ``single_goals``). Event: lab flag ``single_goals`` ``{part, kinds | object, place}``.
"""

from __future__ import annotations

from robot_agent.core.options import Key, Option, OptionSpec, register
from robot_agent.core.planning import goals as goal_kinds
from robot_agent.core.planning import langdata, speech
from robot_agent.core.planning.pack_data import labelled

SPEC = OptionSpec(
    name="single_goals",
    keys=(Key("enabled", "bool", False, aliases=("single_goals",)),),
    hooks=("on_understand_schema", "on_understand_prompt", "on_goals_met"),
    order=46,
    enabled="enabled",
    doc="One action with no destination (find, pick, put down here) is its own goal kind, and a find says where the object was seen.",
)

FOUND = "say_found_at"
_MARK = "{goal_kinds}"                 # the understand template's placeholder of the goal-kind lines: the rule lines follow it


def rule_lines(lang) -> str:
    """The pack's rule lines for the reader (block ``single_goals``, key ``prompt``: a list of lines or a text); '' when unlabelled or none."""
    blk = langdata.block(lang, "single_goals")
    if not labelled(blk):
        return ""
    p = blk.get("prompt")
    text = "\n".join(x for x in p if isinstance(x, str)) if isinstance(p, list) else (p if isinstance(p, str) else "")
    return text.rstrip("\n") if text.strip() else ""


def kinds_for(actions) -> list:
    """The single-action kinds a robot with ``actions`` can be given (every action a kind needs is there)."""
    return [k for k in goal_kinds.SINGLE_KINDS if not goal_kinds.missing_actions({"type": k}, actions)]


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
    def _lines(self) -> str:
        return rule_lines(langdata.prompt_lang())

    def on_understand_schema(self, schema):
        """``schema`` with the single-action kinds the robot can do appended to the goal type enum; None (unchanged) without rule lines."""
        typ = (((schema.get("properties") or {}).get("goals") or {}).get("items") or {}).get("properties", {}).get("type")
        add = kinds_for(getattr(self.svc.robot, "plan_actions", None) or ()) if self._lines() else []
        add = [k for k in add if isinstance(typ, dict) and k not in (typ.get("enum") or [])]
        if not add:
            return None
        typ["enum"] = list(typ.get("enum") or []) + add
        self.svc.lab("single_goals", part="schema", kinds=add)
        return schema

    def on_understand_prompt(self, text):
        return with_rules(text, self._lines()) if kinds_for(getattr(self.svc.robot, "plan_actions", None) or ()) else None

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
