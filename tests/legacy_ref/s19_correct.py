"""Reference copy for step S19: the correction, patch and understanding-grounding methods of AssistLoop EXACTLY as they were in
assist_loop.py at 4ca0504 (lines 773-1008, before they moved to phases/correct.py): _complete_override, _ground_understood,
_ground_v2, _canon, _unverified_objects, _local_names, _correction_texts, _correction_edit, _patch_goals and the constants
_OVERRIDE_NOTE and _NAMES_PROMPT. Only the imports changed (absolute names; the module names the old methods read as globals of
assist_loop.py are imported here). Used by tests/test_correct_phase.py as the differential reference; never edit the bodies."""
import re

from robot_agent.core.options import STOP as _STOP
from robot_agent.core.planning import dialog_converge, goal_filter, goal_fix, langdata, speech
from robot_agent.core.planning.understand import LLMBudgetExceeded, LLMUnavailable, describe, without_places


class RefCorrect:
    _OVERRIDE_NOTE = ("Note: this robot has a skill for every part of this task ({skills}). How an object is moved "
                      "into or onto a place (sliding, pushing, tucking it in) does not matter: it is a put. Give the "
                      "goals for ALL of the task, with status \"clear\".")

    def _complete_override(self, r):
        """F4.1b: a capability refusal the contract overrides keeps only the goals the model did not refuse
        ("cannot slide an object into the fridge" came with open(fridge) alone, and the plate was lost). When
        the person's words name something those goals leave out, the task is understood once more with the
        robot's skills stated; its goals are taken when they leave out less. This call replaces the
        consistency samples of the read-back (r.sampled). _STOP when the LLM failed."""
        issues = [i for i in self._issues(r, r.goals) if i["kind"] in ("object", "verb", "place")]
        if not issues or self.cfg["understand_samples"] <= 0:
            return None
        r.sampled = True
        skills = ", ".join(sorted(a for a in self.robot.plan_actions if a[:1].isupper()))
        try:
            u2 = self._understand(r.task, r.places, r.memory, r.world, r.dialogue, budget=r.llm_budget,
                                  note=self._OVERRIDE_NOTE.format(skills=skills))
        except LLMBudgetExceeded:
            return None
        except LLMUnavailable as e:
            yield self._llm_failed(r, e); return _STOP
        g2, q2 = self._ground_understood(r, u2)
        left = [i for i in self._issues(r, g2) if i["kind"] in ("object", "verb", "place")] if g2 else issues
        took = bool(g2) and not q2 and u2["status"] != "unsafe" and len(left) < len(issues) \
            and not self._cannot_do(g2)
        if took:
            r.goals = g2
        yield r.ev("understand_check", issues=[describe(i) for i in issues], override_completed=took,
                   goals=g2 if took else r.goals)
        return None

    def _ground_understood(self, r, u):
        """An understanding's goals grounded on ENV (with the read-back's pinned destination): (goals, question)."""
        if r.pin_to:
            # "no, the shelf in the restroom" after a read-back names the destination (a 7B model put
            # it in `from`): pin it instead of trusting the model's slots
            for g in u["goals"]:
                if g.get("type") in ("on", "in"):
                    if r.places.ground(g.get("from") or "", g.get("from") or "") == r.pin_to:
                        g["from"] = ""
                    g["to"] = r.pin_to
        answers = [t for w_, t in r.dialogue if w_ == "user"]
        words_ = " ".join([r.task, self.original or ""] + answers)
        on = self.cfg["goal_filter"]                                        # goals not asked for (goal_filter.py)
        raw, r.filtered = goal_filter.before_grounding(u["goals"], r.task, r.places) if on else (u["goals"], [])
        raw, empty = goal_fix.drop_empty(raw)                                # F3.4: no "put the ?" questions
        r.filtered += empty
        raw, _nob = goal_fix.unsaid_speaker(raw, words_, r.places, self.robot)   # F2.5: no recipient, no hand-over
        goals, q = self._ground_goals(raw, r.places, r.memory, words_, latest=answers[-1] if answers else "")
        goals, more = goal_filter.after_grounding(goals, " ".join([r.task] + answers), r.places) if on else (goals, [])
        r.filtered += more
        r.places.guard = self.options.query("goal_guard", r, goals)         # option goal_words: whole words, shared objects keep their receiver
        r.places.listener_rule = (lambda g, t: self.options.decide("on_listener_rule", r, g, t)) if self.options.provides("on_listener_rule") else None
        said_ = " ".join([without_places(" ".join([r.task, self.original or ""]), r.places, getattr(r, "replaced", ()))] + answers)
        goals, fixed = goal_fix.repair_destination(goals, said_, r.places)          # F4.7
        r.repaired = list(getattr(r, "repaired", [])) + fixed
        goals, _kinds = goal_fix.kind_from_destination(goals, words_, r.places, "PutIn" in self.robot.plan_actions)
        goals, invented = goal_fix.drop_invented(goals, words_, r.places, self.options.query("goal_keep", r, goals))    # F4.4; goal_keep: option goal_ledger
        r.filtered += invented
        if self.cfg.get("understand_give_kind"):                # a say to the speaker that repeats the request is no message
            goals, echo = goal_fix.drop_echo_say(goals, r.task, r.places)
            r.filtered += echo
        goals, rq = goal_fix.relay_check(goals, r.task, r.places)           # F4.6
        if not self.cfg.get("no_shared_listener"):
            goals, shared = goal_fix.shared_listener(goals, words_, r.places)    # a listener is not also the recipient
            r.filtered += shared
        goals, q = self._ground_v2(r, goals, q, words_)
        q = q or (speech.LocalText("q_what_say") if rq else None)
        if not goals and empty and not q:
            q = speech.LocalText("q_what_bring")
        return goals, q

    def _ground_v2(self, r, goals, q, words_):
        """Solution-lab round 2, grounding chain v2 (options ``go_steps``, ``kind_anchor``, ``name_canon``; each default
        OFF, all off = no change): deterministic fixes of the grounded goals, no LLM call. Returns (goals, question)."""
        c = self.cfg
        if c.get("go_steps"):
            made = []
            goals, gone = goal_fix.go_steps(goals, r.task, r.places, made)
            if gone or made:
                self._lab("go_steps", dropped=len(gone), delivery=len(made))
            r.filtered += gone
        if c.get("kind_anchor"):
            heard = None if langdata.is_prompt_lang(getattr(r, "lang", None) or "en") else getattr(self.language, "heard", {}).get(r.task)
            args = (words_, r.places, self.robot, getattr(r, "lang", None) or "en",
                    heard.english if heard is not None and heard.trusted else "")
            if c.get("kind_anchor_ask"):               # keep the open goal; the read-back names the doubt
                doubt = goal_fix.unrequested_open(goals, *args)
                r.kind_doubts = [f"I am not sure you asked me to open the "
                                 f"{str(g.get('object') or g.get('to') or 'it').replace('@', ' in the ')}" for g in doubt]
                if doubt:
                    self._lab("kind_anchor", asked=len(doubt))
            else:
                goals, gone = goal_fix.kind_anchor_open(goals, *args)
                if gone:
                    self._lab("kind_anchor", dropped=len(gone))
                r.filtered += gone
        if c.get("name_canon") or c.get("inventory_ask"):
            before = [g.get("object") for g in goals]
            goals, which, _notes = self._canon(r, goals)
            after = [g.get("object") for g in goals]
            if which or before != after:
                self._lab("inventory_ask" if self.cfg.get("inventory_ask") else "name_canon",
                          renamed=sum(a != b for a, b in zip(before, after)), asked_which=bool(which))
            for g in goals:                                   # a renamed object: the sighting names where it is
                if g.get("said") and g.get("type") in ("on", "in") and not g.get("from") and not g.get("from_any"):
                    g["from"] = self._remembered(r.memory, g["object"]) or ""
                    if g["from"] and g["from"] == g.get("to"):
                        g["from"] = ""
            if which and not q:
                said, names = which
                text = " or ".join(f"the {n}" for n in names)
                # ``options`` is the list slot a localized phrase says with the pack's separator (``sep_or``); ``text`` is the
                # English wording. The declared kind is the core's own neutral schema (docs/env.md), not a host's.
                q = speech.LocalText("q_which_known", {"object": said, "text": text, "options": list(names)},
                                     text=f"I know of more than one {said}: {text}. Which one do you mean?",
                                     ask={"intent": "choose", "options": list(names), "about": said})
        return goals, q

    def _canon(self, r, goals) -> tuple:
        """name_canon (English labels, place groups) or, with ``inventory_ask``, its general form: the pack's
        ``planner.canonical_lang`` and ``plural_fold`` data, the user's words first, then one closed which-question."""
        mem = r.memory.data.get("objects")
        if not self.cfg.get("inventory_ask"):
            return goal_fix.canon_names(goals, mem, r.places)
        lang = str(self._manifest_planner("canonical_lang", "en")).lower()
        eng = ""
        if langdata.is_prompt_lang(getattr(r, "lang", None) or "en"):
            eng = " ".join([r.task] + [t for w_, t in r.dialogue if w_ == "user"])
        else:                                          # only a reading the language layer already holds (no new call)
            heard = getattr(self.language, "heard", {}).get(r.task)
            eng = heard.english if heard is not None and heard.trusted else ""
        return goal_fix.canon_inventory(goals, mem, r.places, lang, goal_fix.fold_rules(self.robot, lang), eng,
                                        goal_fix.alias_groups(self.robot, lang), self.options.query("shared_names", r))   # option shared_instances

    _NAMES_PROMPT = ("Give the usual Korean name of each household object below, one per line, in the same order, "
                     "as a plain noun in Hangul (no numbering, nothing else):")

    def _unverified_objects(self, r) -> list:
        """F4.8: the English object names of a Korean request that nothing the person said supports. The model
        names objects in English (and leaves ``said`` empty), so a misheard noun ("회색 수이건") came out as an
        invented object ("gray sock") that no check saw. The translator gives each object's usual Korean name
        once (one call for all, cached, the translator's own budget); an object whose head noun is not in the
        person's words is read back before planning (r.unsure). No lexicon; no call for English requests."""
        lg = (r.lang or "en")[:2]
        if not langdata.grammar(lg, "object_check") or str(self.cfg.get("object_check", "1")) in ("0", "false", "False"):
            return []                                         # only a language whose pack declares the check (grammar.object_check)
        mine = langdata.script_rx(lg)
        if mine is None:
            return []
        objs = list(dict.fromkeys(str(g.get("object")) for g in r.goals if g.get("type") in ("on", "in")
                                  and g.get("object") and not g.get("said") and not mine.search(str(g["object"]))))
        if not objs or not mine.search(r.task or ""):
            return []
        heard = re.sub(r"\s+", "", " ".join([r.task, self.original or ""] + [t for w_, t in r.dialogue if w_ == "user"]))
        names = self._local_names(objs, lg)
        for g in r.goals:                    # the read-back names the object in the user's language: no call
            if names.get(g.get("object")) and not g.get("said"):
                g["name_local"] = names[g["object"]]
        out = []
        for o in objs:
            toks = re.findall(langdata.block(lg, "script")["letters"] + "+", names.get(o) or "")
            while len(toks) > 1 and len(toks[-1]) < 2:
                toks.pop()                                  # a one-syllable packaging noun ("우유 팩"): the noun before
            if toks and toks[-1] not in heard:
                out.append(o)
        return out

    def _local_names(self, objs, lg: str) -> dict:
        """{object: its name in ``lg``} from the translator (one call, cached; {} when it fails or is over budget)."""
        cache = self.__dict__.setdefault("_tr_cache", {})
        dm = self.language.dialog_model(lg)
        key = ("names", lg, tuple(objs), str(self.cfg.get("translate_model") or "") + (f"|{dm}" if dm else ""))
        if key not in cache:
            out = {}
            lang = self.language
            for o in objs:                                      # a declared name needs no model (pack i18n / ENV)
                d = lang.declared_name(o, lg)
                if d:
                    out[o] = d
            rest = [o for o in objs if o not in out]
            if rest:
                try:
                    if not lang._take():
                        raise LLMBudgetExceeded("translate budget")
                    txt = lang.chat_out(lg, self._NAMES_PROMPT + "\n" + "\n".join(rest))
                    lines = [re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", x).strip() for x in txt.splitlines() if x.strip()]
                    if len(lines) == len(rest):
                        out.update({o: n for o, n in zip(rest, lines) if lang.plausible_name(n, o, lg) is None})
                except Exception:
                    pass
            cache[key] = out
            for o, n in out.items():                            # a name checked here is the name the robot says (no second call)
                lang.names_cache.setdefault((" ".join(str(o).split()).lower(), lg), n)
        return cache[key]

    def _correction_texts(self, r, rest) -> list:
        """The correction as said, and (for another language) its English."""
        out = [rest]
        if not langdata.is_prompt_lang(getattr(r, "lang", None) or "en"):
            h = self.language.hear(rest)
            if h.english:
                out.append(h.english)
        return out

    def _correction_edit(self, r, rest):
        """(``"edit"``, edits) | (``"same"``, []) | (None, []): what a correction after a "no" asks for
        (``dialog_converge.correction_edit``); None = the correction names no single object, the old path stands."""
        if not self._converge():
            return None, []
        kind, edits = dialog_converge.correction_edit(r.goals, self._correction_texts(r, rest), r.places)
        return (None if kind == "none" else kind), edits

    def _patch_goals(self, r, rest, edits):
        """The read-back goals with the edited ones sent to their new destination, grounded again as any goal
        (a person's hand-over, a surface beside them). None when the grounding has a question to ask. Only the
        goals the person named change; the others keep what they were understood as."""
        goals = [dict(g) for g in r.goals]
        old_to = set()
        for i, dest in edits:
            g = goals[i]
            old_to.add(g.get("to"))
            for k in ("hand", "near_person", "user_at", "from_any"):
                g.pop(k, None)
            g["to"] = "me" if dest == "@speaker" else dest
        text = " ".join([r.task] + [t for w_, t in r.dialogue if w_ == "user"])
        done, q = self._ground_goals([goals[i] for i, _d in edits], r.places, r.memory, text, latest=rest)
        if q or len(done) != len(edits):
            return None
        done, _k = goal_fix.kind_from_destination(done, text, r.places, "PutIn" in self.robot.plan_actions)
        for (i, _d), g in zip(edits, done):
            goals[i] = g
        r.replaced = set(getattr(r, "replaced", ())) | {p for p in old_to if p}
        yield r.ev("understand_check", issues=[], correction="patched", goals=[goals[i] for i, _d in edits])
        return goals
