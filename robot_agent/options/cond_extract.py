"""Option ``cond_extract`` (default off): a request with a condition ("if X is at P, do A, otherwise B") is split by ONE extra model call and handed to ``cond_goal``.

The failure it closes: a reader that was not trained on a condition turns "if X is at P, do A, otherwise B" into plain goals (both branches, or the
condition's object as a goal), so the robot does the wrong branch or both. Here the reader stays as it is. When the request holds a condition word of
its language (the language packs' ``patterns.condition`` plus the robot pack's ``planner.options.cond_extract.clauses``, the same shape as
``lang_words.clauses``), the pack's extraction prompt is sent once to the planner's model with the reader's goals numbered; the answer says the
condition (object, place, present or not) and which goals belong to the branch when it holds (``if_goals``), to the other branch (``else_goals``) and
which only state the condition (``drop``). The option writes the ``when`` field of ``cond_goal`` on those goals; ``cond_goal`` (order 20, this one
runs at 15) goes to the place, looks and keeps the branch that matches. No option imports the other: the ``when`` field on the goals is the hand-over.

* no condition word, no prompt text, no goals, a goal that already has a ``when``, or a second pass of the same run: no extra call, goals unchanged
  (a later reading that reads exactly as the first one, by ``goal_sig``, gets the first plan again; any other reading stands as read);
* an answer that is not JSON, says ``conditional`` false, has bad or clashing indexes (a goal in two lists), no object, no ``present`` or no branch goal: goals unchanged (the reader's reading stands);
* the condition is real but unclear (the model says ``unsure``, or the place is missing or not one the robot knows): the
  person is asked once (``q_unclear``, phrase key ``cond_unclear``, slot ``{object}``; no text = not asked); an answer that names a place the robot knows
  completes the condition, any other answer leaves the goals as the reader gave them. Never a guess;
* what a place cannot complete (the cases of the first point) is not asked about: the goals stay.

Pack data (manifest ``planner.options.cond_extract.<key>``): ``prompt`` (slots ``{request}``, ``{goals}``; the model answers the fixed JSON of
``SCHEMA``) and ``q_unclear``, used only when the section (or the run options) carries ``source`` (a string) or ``assumed: true``, else the option
does nothing; ``clauses`` (``{lang: {"condition": regex | [regex], "source" | "assumed": true}}``, each entry labelled). A key the loop resolved empty
is read again from the manifest at run time. Hook ``on_goals`` (order 15). Uses the service ``ask_model``; serves ``refused_goals`` and
``goal_dropped`` for the goals it dropped (as ``cond_goal`` does for its branches), so the read-back counts them as covered and a re-sampled
reading does not bring them back. Keys: ``enabled`` (legacy ``cond_extract``),
``ask_when_unsure`` (true), ``prompt``, ``clauses``, ``q_unclear``, ``source``, ``assumed``. Lab flag ``cond_extract``: ``{state, object, at, if, else, drop}``.
"""

from __future__ import annotations

from robot_agent.core.options import STOP, Key, Option, OptionSpec, register
from robot_agent.core.planning import clauses, dialog_converge, pack_data

SPEC = OptionSpec(
    name="cond_extract",
    keys=(Key("enabled", "bool", False, aliases=("cond_extract",)),
          Key("ask_when_unsure", "bool", True),
          Key("prompt", "str", ""),
          Key("clauses", "dict", {}, sources=("manifest",)),
          Key("q_unclear", "str", ""),
          Key("source", "str", ""),
          Key("assumed", "bool", False)),
    hooks=("on_goals",),
    services=("refused_goals", "goal_dropped"),
    order=15,
    enabled="enabled",
    doc="A request with a condition word is split by one extra model call into condition / goals-if / goals-else and handed to cond_goal as `when`.",
)

_INDEXES = {"type": "array", "items": {"type": "integer"}}
SCHEMA = {"type": "object",
          "properties": {"conditional": {"type": "boolean"}, "object": {"type": "string"}, "at": {"type": "string"}, "present": {"type": "boolean"},
                         "if_goals": _INDEXES, "else_goals": _INDEXES, "drop": _INDEXES, "unsure": {"type": "boolean"}},
          "required": ["conditional", "object", "at", "present", "if_goals", "else_goals", "drop", "unsure"]}


def _indexes(v, n):
    """The distinct 1-based goal numbers of ``v`` (a list of ints in 1..n) as a list, or None when it is not one."""
    if not isinstance(v, list) or any(isinstance(x, bool) or not isinstance(x, int) or not 1 <= x <= n for x in v):
        return None
    return list(dict.fromkeys(v))


def parse(d, n):
    """The extraction as {state, object, at, present, if, else, drop}; ``state``: ``none`` (not a condition or not usable: goals unchanged),
    ``ready`` (complete) or ``unsure`` (a condition, but the place is missing or the model is not sure, or the lists clash)."""
    if not isinstance(d, dict) or d.get("conditional") is not True:
        return {"state": "none"}
    lists = [_indexes(d.get(k) or [], n) for k in ("if_goals", "else_goals", "drop")]
    obj, at = str(d.get("object") or "").strip(), str(d.get("at") or "").strip()
    if None in lists or not obj or not isinstance(d.get("present"), bool) or not (lists[0] or lists[1]):
        return {"state": "none"}
    if sum(map(len, lists)) != len(set(lists[0]) | set(lists[1]) | set(lists[2])):      # a goal in two lists: contradictory, the reader's goals stand
        return {"state": "none"}
    out = {"object": obj, "at": at, "present": d["present"], "if": lists[0], "else": lists[1], "drop": lists[2]}
    return {**out, "state": "unsure" if d.get("unsure") is True or not at else "ready"}


def condition_regex(lang, declared):
    """The compiled condition words of the run's language (packs plus declared), or None."""
    cl = clauses.build(lang, declared if isinstance(declared, dict) else {})
    return cl.cond if cl is not None else None


class CondExtract(Option):
    def _texts(self, run) -> list:
        """The request as the person said it and, when there is one, in the prompt language."""
        en = getattr(self.svc, "request_english", lambda r: "")(run)
        return [t for t in dict.fromkeys([str(getattr(run, "task", "") or ""), str(en or "")]) if t.strip()]

    def _data(self) -> dict:
        """The pack's texts (used only when labelled) and clause words, read again from the manifest when the resolved keys are empty."""
        planner = pack_data.planner_of(self.svc)
        return {**pack_data.labelled_keys(SPEC, self.cfg, planner, ("prompt", "q_unclear")),
                "clauses": pack_data.fresh(SPEC, self.cfg, planner, "clauses")}

    def _has_condition(self, run, data) -> bool:
        rx = condition_regex(getattr(run, "lang", None), data["clauses"])
        return bool(rx) and any(rx.search(t) for t in self._texts(run))

    def _prompt(self, run, text) -> str:
        goals = "\n".join(f"{i}. {self._phrase(run, g)}" for i, g in enumerate(run.goals, 1))
        request = " / ".join(self._texts(run))
        return text.replace("{request}", request).replace("{goals}", goals)

    def _phrase(self, run, g) -> str:
        fn = getattr(self.svc, "goal_phrase", None)
        try:
            return str(fn(g, run.places)) if fn else str({k: v for k, v in g.items() if v})
        except Exception:
            return str({k: v for k, v in g.items() if v})

    def _place_of(self, run, text):
        return self.svc.ground_place(run, text) if text else None

    def _ask_place(self, run, plan, text):
        """Flow: one question (pack text) when the condition is real but unclear; the place the answer names (words), else None."""
        from robot_agent.core.planning import speech
        if not text or not self.cfg["ask_when_unsure"]:
            return None
        en = text.replace("{object}", plan["object"])
        ans = self.svc.ask(run, speech.LocalText("cond_unclear", {"object": plan["object"]}, text=en)) or ""
        return ans.strip() if ans.strip() and self._place_of(run, ans.strip()) else None

    def _apply(self, run, plan) -> None:
        """Write ``when`` on the branch goals, drop the goals that only state the condition (listed in ``run.filtered``)."""
        base = {"object": plan["object"], "at": plan["at"]}
        out, dropped = [], []
        for i, g in enumerate(run.goals, 1):
            if i in plan["drop"]:
                dropped.append(g)
            elif i in plan["if"] or i in plan["else"]:
                out.append({**g, "when": {**base, "present": plan["present"] if i in plan["if"] else not plan["present"]}})
            else:
                out.append(g)
        run.goals = out
        run.filtered = list(getattr(run, "filtered", None) or []) + dropped
        self._dropped(run).extend(g for g in dropped if g not in self._dropped(run))

    def _dropped(self, run) -> list:
        return run.__dict__.setdefault("opt", {}).setdefault("cond_extract_dropped", [])

    def refused_goals(self, run):
        """The goals this option dropped as a statement of the condition (the read-back counts them as covered)."""
        return list(self._dropped(run))

    def goal_dropped(self, run, goal):
        """True when ``goal`` reads like one this option dropped (``goal_sig``), so a re-sampled reading does not bring it back; None otherwise."""
        return True if dialog_converge.goal_sig([goal])[0] in dialog_converge.goal_sig(self._dropped(run)) else None

    def on_goals(self, run):
        """Flow: the goals of a request with a condition word get the ``when`` of ``cond_goal`` (one model call); the person is asked once when unclear."""
        goals = getattr(run, "goals", None) or []
        done = run.__dict__.setdefault("opt", {}).setdefault("cond_extract", {})
        if done.get("seen"):                                 # a later reading of the same request: the plan of the first one is applied again, no new call
            return self._again(run, done, goals)
        data = self._data() if goals else {}
        if not goals or not data["prompt"].strip() or any("when" in g for g in goals) or not self._has_condition(run, data):
            return None
        done["seen"] = True                                  # one extra call per run, whatever the reader reads next
        plan = parse(self.svc.ask_model(run, self._prompt(run, data["prompt"]), SCHEMA), len(goals))
        if plan["state"] == "unsure" or (plan["state"] == "ready" and not self._place_of(run, plan["at"])):
            at = self._ask_place(run, plan, data["q_unclear"])
            stop = self.svc.cancelled(run)
            if stop:
                yield stop
                return STOP
            plan = {**plan, "at": at, "state": "ready"} if at else {"state": "none"}
        self.svc.lab("cond_extract", **{k: plan.get(k) for k in ("state", "object", "at", "if", "else", "drop")})
        if plan["state"] == "ready":
            done["plan"], done["sig"] = plan, dialog_converge.goal_sig(goals)
            self._apply(run, plan)
        return None

    def _again(self, run, done, goals):
        """A goal list read again after the first one (the person answered a question, the reader was called again): the first plan applies when the
        list reads exactly as the first one did (the same goals, in the same order: ``goal_sig``) and has no ``when`` yet; else the goals stand (the
        numbers of another reading are not the plan's: a correction that changed an object but not the count would move a branch onto another goal)."""
        if done.get("plan") and goals and dialog_converge.goal_sig(goals) == done.get("sig") and not any("when" in g for g in goals):
            self._apply(run, done["plan"])
            self.svc.lab("cond_extract", state="again")
        return None


register(SPEC, CondExtract)
