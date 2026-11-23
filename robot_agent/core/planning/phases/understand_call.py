"""The understand model call and the capability check of AssistLoop, as a phase mixin.

What: ``UnderstandCall`` is a mixin with the sampling options (``_sampling``), the understand call (``_understand``), the model
that reads the request (``_planner_model``, ``_um_for``, ``_understand_llm``, ``runaway``, ``_ask_understand_routed``), the goal
kinds and the prompt flags (``_kinds``, ``_flag_prompt``, ``_verify_verdict``), the refusal reasons (``_cannot_line``, ``_over_limits``,
``_capability_check``, ``_cannot_do``) and the schema (``_understand_schema``, ``_safety_profile``, ``_forbids``), plus the two
class texts ``_CANNOT`` and ``_CAN_OPEN``. They were moved VERBATIM out of ``assist_loop.py`` (Step S16a of the core
generalization); the only changes are three relative imports that gained a dot and the two constants below.
Why: the loop keeps the common procedure; the model call and the refusal checks are one phase with one interface, the seam
where the goal kinds, the prompt and the refusal reasons become data of the robot pack
(docs/proposals/generalize_core_design.md, "Phase pipeline"). ``UNDERSTAND_PROMPT`` and ``UNDERSTAND_SCHEMA`` stay in
``assist_loop.py`` (98 files import them there, a golden mutation patches ``assist_loop.UNDERSTAND_PROMPT``): the moved methods read
them from that module on every call (``_core()``), so a patched value still reaches the model. ``run_blocking`` and
``_with_understand_events`` stay in ``assist_loop.py`` (tests/test_wiring_function_size.py looks ``run_blocking`` up there); the hooks
``on_understand_prompt`` and ``on_understand_schema`` are called at the same two points as before. A name patched on ``AssistLoop`` still
wins, because the class dict is searched before the mixin.
Switch off: remove the ``UnderstandCall`` line from ``PHASE_MIXINS`` in ``phases/__init__.py``, put the methods back into
``assist_loop.py`` (after ``_llm``, before ``run_blocking``; ``_kinds`` .. ``_cannot_do`` after ``_with_understand_events``) and delete
this module.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .. import goals as goal_kinds
from ..places import strip_article as _strip_article, words
from ..understand import LLMBadOutput, LLMBudgetExceeded, LLMUnavailable, ask_json, sampling
from ...pack import goal_semantics


def _core():
    """The assist_loop module, looked up when a method runs (it holds ``UNDERSTAND_PROMPT`` and ``UNDERSTAND_SCHEMA``)."""
    from .. import assist_loop
    return assist_loop


class UnderstandCall:
    def _sampling(self, sample: int = 0) -> dict:
        """The understand call's temperature / seed (0 = the first reading, k = the k-th re-sample)."""
        c, seed = self.cfg, self.cfg["understand_seed"]
        seed = int(seed) + sample if str(seed).strip() not in ("", "none", "None") and int(seed) >= 0 else None
        return sampling(c["understand_sample_temperature"] if sample else c["understand_temperature"], seed)

    def _understand(self, task, places, memory, world, dialogue, budget=None, sample: int = 0, note: str = "",
                    pivot: Optional[str] = None) -> dict:
        """The understand call (understand.ask_json: one repair retry on a non-JSON answer). Raises
        LLMUnavailable / LLMBudgetExceeded; a model that still answers no JSON gives no goals (``unparsed``)."""
        # the language layer (input_pivot): the task and the user's answers as the model reads them
        task_text, pivot_orig, dlines = self.language.understand_view(
            task, dialogue, lambda t, en: f"Robot: {t if en else self._heard_as(t)}", pivot=pivot)
        prompt = self.options.transform("on_understand_prompt", self._flag_prompt(_core().UNDERSTAND_PROMPT)).format(
            abilities=self.robot.robot_description(), cannot=self._cannot_line(),
            goal_kinds=goal_kinds.prompt_lines(self._kinds(), goal_semantics.pack_of(self)),
            # no object memory here: a 7B model refused / asked about every object it did not list
            # (the memory grounds `from` after this call instead)
            places=places.describe(), answers=memory.answers_text(),
            robot=world.as_text() if hasattr(world, "as_text") else "",
            dialogue=("You already asked the user, and the answers below complete the task (a later answer corrects "
                      "an earlier one, and a \"no, ...\" corrects what the robot read back): take the object, source "
                      "and destination from them, and say \"clear\" unless something is still missing.\n"
                      + "\n".join(dlines))
            if dialogue else "", task=task_text,
            # the user's own words: a translation loses names (식탁 'dining table' came out as 'table')
            original=(f"\n(the user's own words: {self.original})" if self.original and self.original != task else "")
            + pivot_orig + (f"\n{note}" if note else ""))
        from ...run_control import call_cancellable, is_cancel
        try:                                     # every call (repairs too) cancellable: a stop does not wait
            if self._um_for(getattr(self, "_run_lang", "en")):
                d = self._ask_understand_routed(prompt, budget, sample, call_cancellable, is_cancel)
            else:
                d = ask_json(self._llm(), prompt, self._understand_schema(), budget=budget, retries=self.cfg["understand_retries"],
                             call=call_cancellable, passthrough=is_cancel, options=self._sampling(sample))
        except LLMBadOutput:
            d = {"unparsed": True}
        d.setdefault("status", "clear"); d.setdefault("question", "")
        d["goals"] = [g for g in d.get("goals") or [] if isinstance(g, dict)] if isinstance(d.get("goals"), list) else []
        if self.cfg.get("understand_give_kind"):
            d["goals"] = goal_kinds.canonical(d["goals"])           # give -> on (to = the person)
        for g in d["goals"]:
            # a sentence to say stays as said; object names are normalised
            g["object"] = (str(g.get("object") or "").strip() if g.get("type") == "say"
                           else _strip_article(words(g.get("object"))))
            for k in ("from", "to"):
                g[k] = str(g.get(k) or "").strip()
        d["goals"] = goal_kinds.speaker_slots(d["goals"])     # "me" only as a destination (FROM_ME)
        if self.cfg.get("understand_verify_verdict"):
            self._verify_verdict(d)
        return d

    # ── lab round 7: the model that reads the request ─────────────────────
    def _planner_model(self) -> str:
        return str((self._llm_cfg or {}).get("model") or "")

    def _um_for(self, lang) -> str:
        """The understand model of a run in ``lang`` when it is NOT the planner's: ``understand_model_lang[lang[:2]]``, else
        ``understand_model``, else "" (the planner reads the request). An unknown or missing language takes the default."""
        c = self.cfg
        m = c.get("understand_model_lang") or {}
        lg = str(lang or "en").strip().lower()[:2]
        cand = str(m.get(lg) or c.get("understand_model") or "").strip()
        return "" if not cand or cand == self._planner_model() else cand

    def _understand_llm(self):
        """The client that reads the request (mirrors ``LanguageAdapter.translator``): the planner's own when the model for
        this run is the planner's, else a client of the understand model (role ``understand``) on ``understand_url``,
        else ``translate_url``, else the planner's server."""
        um = self._um_for(getattr(self, "_run_lang", "en"))
        if not um:
            return self._llm()
        clients = self.__dict__.setdefault("_um_clients", {})
        if um not in clients:
            from ....connect.llm import init_llm_client
            url = self.cfg.get("understand_url") or self.cfg.get("translate_url") or (self._llm_cfg or {}).get("url")
            cfg = dict(self._llm_cfg or {}, model=um, url=url, role="understand")
            if self.cfg.get("understand_think") is not None:
                cfg["think"] = self.cfg["understand_think"]
            clients[um] = init_llm_client(cfg=cfg)
        return clients[um]

    @staticmethod
    def runaway(d: dict, max_goals) -> str:
        """Why an understanding is a runaway ("goals": more goals than ``max_goals``; "repeat": three identical goals in a
        row), else ""."""
        goals = [g for g in (d.get("goals") if isinstance(d.get("goals"), list) else []) if isinstance(g, dict)]
        if max_goals and len(goals) > int(max_goals):
            return "goals"
        key = [json.dumps(g, sort_keys=True, ensure_ascii=False) for g in goals]
        if any(key[k] == key[k + 1] == key[k + 2] for k in range(len(key) - 2)):
            return "repeat"
        return ""

    def _ask_understand_routed(self, prompt, budget, sample, call, passthrough) -> dict:
        """The understand call when another model than the planner's reads the request (guarded): no quoting repair, a
        runaway (no JSON, too many goals, three identical goals in a row) or an unavailable model is answered by ONE call to
        the planner with the same prompt, with an ``understand_fallback`` event. Raises as ``ask_json`` does."""
        um = self._um_for(getattr(self, "_run_lang", "en"))
        schema, opts, reason = self._understand_schema(), dict(self._sampling(sample)), ""
        if self.cfg.get("understand_max_tokens"):
            opts["num_predict"] = int(self.cfg["understand_max_tokens"])
        try:
            client = self._understand_llm()
            d = ask_json(client, prompt, schema, budget=budget, retries=0, call=call, passthrough=passthrough,
                         options=opts)
            reason = self.runaway(d, self.cfg.get("understand_max_goals"))
        except LLMBadOutput:
            reason = "non_json"
        except LLMUnavailable as e:
            reason = "unavailable"
            self._lab("understand_model", unavailable=str(e)[:120])
        except LLMBudgetExceeded:
            raise
        except Exception as e:                        # noqa: BLE001 - a client that cannot even be built: the planner reads it
            if passthrough is not None and passthrough(e):
                raise
            reason = "unavailable"
        if not reason:
            self.um_last = {"model": um, "fallback": None}
            return d
        self.um_last = {"model": self._planner_model(), "fallback": reason, "tried": um}
        self.__dict__.setdefault("um_fallbacks", []).append(reason)
        self.__dict__.setdefault("_um_pending", []).append({"reason": reason, "model": um})
        return ask_json(self._llm(), prompt, schema, budget=budget, retries=self.cfg["understand_retries"], call=call,
                        passthrough=passthrough, options=self._sampling(sample))

    def _kinds(self) -> list:
        return goal_kinds.kinds_for(self.robot.plan_actions, ("give",) if self.cfg.get("understand_give_kind") else (), goal_semantics.pack_of(self))

    def _flag_prompt(self, text: str) -> str:
        """UNDERSTAND_PROMPT with the lines of the switched-on schema flags (none on = the text itself)."""
        c = self.cfg
        if c.get("understand_schema_strict"):
            text = text.replace("for \"say\", \"content\": only the message to pass on, in the user's words.",
                                "for \"say\", \"content\": only the message to pass on, in the user's words. "
                                "Write \"said\" and \"content\" in every goal (an empty string when there is none).")
        if c.get("understand_verify_verdict"):
            text = text.replace("- question: when ambiguous,",
                                "- missing_action: for \"infeasible\" the one action this robot lacks (an infeasible answer "
                                "without one is dropped); else \"\". A task the abilities above cover is never infeasible; "
                                "an object whose place is unknown is \"clear\" (reason_type \"location_unknown\").\n"
                                "- question: when ambiguous,")
        return text

    def _verify_verdict(self, d: dict) -> None:
        """Option ``understand_verify_verdict``: an "infeasible" verdict stands only when it names, in ``missing_action``, an
        action this robot lacks; one that does not, on goals the contract covers (no action missing, within the limits), is
        dropped (status clear). ``reason_type`` "location_unknown" on a reading with goals is "clear" (the plan searches)."""
        st, rt = d.get("status"), str(d.get("reason_type") or "")
        if not d.get("goals"):
            return
        if rt == "location_unknown" and st in ("infeasible", "ambiguous"):
            d["status"], d["question"] = "clear", ""
            self._lab("understand_verify_verdict", part="location_unknown")
        elif st == "infeasible" and (str(d.get("missing_action") or "").strip()
                                     not in goal_kinds.lacking(self.robot.plan_actions, goal_semantics.pack_of(self))):
            if not self._cannot_do(d["goals"]) and not self._over_limits(d["goals"]):
                d["status"], d["question"] = "clear", ""
                self._lab("understand_verify_verdict", part="dropped")

    # "doors" are room doors: an appliance's / cabinet's door is the place itself, which Open covers (a model
    # refused "open the microwave door" as "cannot open doors")
    _CANNOT = ("It cannot: throw, pour, cook, wash, climb, open windows or room doors, use tools, handle knives near "
               "people, or leave the house.")
    _CAN_OPEN = " (Opening a place listed below, or its door, is fine.)"

    def _cannot_line(self) -> str:
        """The prompt's "It cannot" line: the robot's i18n ``cannot_list`` (else the core's), with the manifest's
        payload / size limits when it declares them (SAGE-v2 F4.1)."""
        robot = self.robot
        line = robot.text("cannot_list") if robot.has_text("cannot_list") else self._CANNOT
        if "Open" in (robot.plan_actions or ()) and not robot.has_text("cannot_list"):
            line += self._CAN_OPEN
        lim = getattr(robot.manifest, "limits", None) or {}
        if lim.get("payload_kg"):
            line += f" It can carry at most {lim['payload_kg']} kg"
            line += f" and objects up to {lim['max_object_size_m']} m." if lim.get("max_object_size_m") else "."
        return line

    def _over_limits(self, goals) -> Optional[str]:
        """A goal whose object is over the manifest's limits, by the robot's own estimates (manifest
        ``limits.objects``: {name: {weight_kg, size_m}}, its object-class prior): why, else None."""
        lim = getattr(self.robot.manifest, "limits", None) or {}
        est = lim.get("objects") or {}
        pay, size = lim.get("payload_kg"), lim.get("max_object_size_m")
        if not est or not (pay or size):
            return None
        for g in goals:
            if g.get("type") not in ("on", "in"):
                continue
            ws = set(words(g.get("object")).split())
            hit = max(((n, e) for n, e in est.items() if set(words(n).split()) <= ws and isinstance(e, dict)),
                      key=lambda x: len(x[0]), default=None)
            if hit is None:
                continue
            n, e = hit
            if pay and float(e.get("weight_kg") or 0) > float(pay):
                return f"the {g['object']} is too heavy for me (about {e['weight_kg']} kg; I can carry {pay} kg)"
            if size and float(e.get("size_m") or 0) > float(size):
                return f"the {g['object']} is too big for me to carry"
        return None

    def _capability_check(self, r, u) -> Optional[str]:
        """The model's "infeasible" against the contract (F4.1 / F4.2): "repeat" (no goals and nothing heard to
        refuse: say it again), "override" (goals the contract can do and a reason that names a manner it
        covers: read them back instead of refusing), else None (the refusal stands)."""
        rt = str(u.get("reason_type") or "").strip()
        if not r.goals:
            content = [w for w in re.findall(r"[\w']+", r.task) if len(w) > 1]
            nothing = re.search(r"\bno (?:task|request|instruction)|not (?:a )?(?:clear|complete|understand)|unclear|"
                                r"garbled|incomplete|empty|nonsens|meaningless", str(u.get("reason") or ""), re.I)
            if u.get("unparsed") or len(content) < 3 or nothing:
                return "repeat"
            # F4.2b: a goal-less refusal that comes with the model's own question ("Do you want me to check the
            # microwave?") is a guess about a request it did not get (a misheard word read as an impossible
            # action): the person says it again once; refused again after that, the refusal stands
            unsure = bool(str(u.get("question") or "").strip())
            return "repeat" if unsure and not r.dialogue and not getattr(r, "repeat_asked", False) else None
        if self._cannot_do(r.goals) or self._over_limits(r.goals):
            return None
        return "override" if rt in ("", "missing_skill", "other") else None

    def _safety_profile(self):
        """The robot's safety profile (``core/safety``): the refusal marks of a typed refusal come from it."""
        from ...safety import profile_of
        return profile_of(self.robot)

    def _forbids(self, goal, places):
        """Why the robot's safety rules forbid ``goal`` (the sentence), None: the answer of the option that holds the rules."""
        return self.options.query("forbids", goal, places)

    def _understand_schema(self) -> dict:
        """UNDERSTAND_SCHEMA with the goal kinds this robot may be given (``goals.kinds_for``)."""
        schema = json.loads(json.dumps(_core().UNDERSTAND_SCHEMA))
        schema["properties"]["goals"]["items"]["properties"]["type"]["enum"] = self._kinds()
        if self.cfg.get("understand_schema_strict"):      # `said` and `content` are written in every goal (empty allowed)
            schema["properties"]["goals"]["items"]["required"] = ["type", "object", "said", "content", "from", "to"]
        if self.cfg.get("understand_verify_verdict"):     # an infeasible verdict names the action the robot lacks
            props = schema["properties"]
            props["reason_type"]["enum"] = list(props["reason_type"]["enum"]) + ["location_unknown"]
            new = {}
            for k, v in props.items():
                new[k] = v
                if k == "reason_type":
                    new["missing_action"] = {"type": "string", "enum": [""] + goal_kinds.lacking(self.robot.plan_actions, goal_semantics.pack_of(self))}
            schema["properties"] = new
            schema["required"] = list(schema["required"]) + ["missing_action"]
        if self.cfg.get("understand_goals_first"):       # the goals are written before the verdict (key order binds
            first = ("goals",)                           # where the backend decodes in schema order)
            schema["properties"] = {k: schema["properties"][k] for k in
                                    list(first) + [k for k in schema["properties"] if k not in first]}
            schema["required"] = list(first) + [k for k in schema["required"] if k not in first]
        return self.options.transform("on_understand_schema", schema)       # e.g. cond_goal: the optional `when`

    def _cannot_do(self, goals) -> Optional[str]:
        """Why the robot cannot do these goals (an action a goal kind needs is not in its manifest),
        or None. Checked before any planning or motion."""
        return goal_kinds.cannot_do(goals, self.robot.plan_actions, goal_semantics.pack_of(self))
