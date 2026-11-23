"""Two objects fit the user's words: ask which one (R4b item 3, the "duplicate objects" class), as a mixin of
:class:`~robot_agent.core.planning.assist_loop.AssistLoop`.

Contract (docs/contract.md, Find): when a Find sees more than one object that fits its query, the skill result
may carry ``candidates``: a list of short descriptions of the instances in view (the way a person would tell them
apart, e.g. "red mug", "blue mug"; never an internal id). Nothing else is assumed; a pack whose Find has no
``candidates`` is not affected. The robot then

* narrows the candidates by the words the person used (they said "the red mug": one candidate left, no question);
* otherwise asks once per object ("I see more than one mug here: the red mug or the blue mug. Which one do you
  mean?") when a Pick of that object follows, and reads the answer by its words that tell the candidates apart,
  or by one model call when the words do not decide (another language, "the first one");
* looks for / picks up the chosen description from then on (``r.queries``: the skill's input, the plan's object
  name stays), and looks again when the Find had seen the other instance.

An answer that does not choose leaves the robot where it was: it is not asked twice.
"""

from __future__ import annotations

import re
from typing import Optional

from . import speech
from .understand import LLMBadOutput, LLMBudgetExceeded, LLMUnavailable, ask_json

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_FILLER = {"the", "a", "an", "one", "ones", "please", "i", "mean", "want", "this", "that", "it", "is", "its", "of"}
CHOICE_SCHEMA = {"type": "object", "properties": {"choice": {"type": "integer"}}, "required": ["choice"]}


def _toks(s) -> list:
    return [w.lower() for w in _WORD.findall(str(s or ""))]


def candidate_names(raw) -> list:
    """The distinct, non-empty descriptions of a Find result's ``candidates`` (order kept)."""
    out = []
    for c in raw if isinstance(raw, (list, tuple)) else []:
        c = " ".join(str(c or "").split())
        if c and c.lower() not in [x.lower() for x in out]:
            out.append(c)
    return out


def narrow(cands: list, said: str) -> list:
    """The candidates that contain every word the person used for the object (all of them when that leaves none)."""
    want = [w for w in _toks(said) if w not in _FILLER]
    keep = [c for c in cands if all(w in _toks(c) for w in want)] if want else list(cands)
    return keep or list(cands)


def choose(answer: str, cands: list, said: str = "") -> Optional[int]:
    """The index of the one candidate the answer's words pick out (a word in that candidate and in no other), None
    when the words do not decide."""
    ans = {w for w in _toks(answer) if w not in _FILLER}
    common = set.intersection(*[set(_toks(c)) for c in cands]) if cands else set()
    ans -= common | set(_toks(said))
    hit = [i for i, c in enumerate(cands) if ans & (set(_toks(c)) - common)]
    return hit[0] if len(hit) == 1 else None


class WhichOne:
    """Mixin: ``_which_one(r, st, rec)`` after a successful Find, and ``_query_of(r, st)`` for the skill's input."""

    def _query_of(self, st, default):
        """The skill input of a Find / Pick: the description the person chose, else ``default``."""
        q = (self.__dict__.get("_run_queries") or {}).get(st.get("object"))
        return q if q and st.get("action") in ("Find", "Pick") else default

    def _which_one(self, r, st, rec):
        """After a successful Find with ``candidates``: narrow, else ask which one. A generator (events); sets
        ``r.queries[object]`` (also ``self._run_queries``, read by ``_bind``) and may insert a Find of the chosen one. Returns None."""
        o = st["object"]
        cands = candidate_names((rec.result or {}).get("candidates"))
        queries = r.__dict__.setdefault("queries", {})
        if st.get("action") != "Find" or len(cands) < 2 or o in queries or ("which", o) in r.helped:
            return None
        if not any(s.get("action") == "Pick" and s.get("object") == o for s in r.steps[r.i + 1:]):
            return None                                # nothing is picked up: which one does not matter
        said = self._said_of(r, o)
        left = narrow(cands, said)
        if len(left) == 1:
            self._choose_instance(r, o, left[0], rec)
            return None
        if not self.cfg["ask_help"]:
            return None
        r.helped.add(("which", o))
        names = " or ".join(f"the {c}" for c in left)
        q = speech.LocalText("q_which_one", {"object": said, "text": names},
                             text=f"I see more than one {said} here: {names}. Which one do you mean?")
        yield r.ev("clarify", question=q, round=0, kind="which_one")
        ans, _n = self._ask_heard(q, r.node, self._log_fn(r.i), r.lang)
        if not ans:
            return None
        r.memory.remember_answer(r.task, q, ans)
        r.dialogue += [("robot", q), ("user", ans)]
        k = choose(ans, left, said)
        if k is None:
            k = self._choose_with_model(r, ans, left, said)
        if k is not None:
            self._choose_instance(r, o, left[k], rec)
            yield r.ev("recover", at_index=r.i, kind="which_one", msg=f"the {left[k]}")
        return None

    def _choose_instance(self, r, o, name, rec) -> None:
        """Take ``name`` as the object from now on: the skills get it as their input; the Find is repeated when it
        had seen another instance."""
        r.queries[o] = name
        seen = str((rec.result or {}).get("found") or "").lower()
        if seen and seen != name.lower():
            r.steps.insert(r.i + 1, {"action": "Find", "object": o})
            r.world.found = None

    def _choose_with_model(self, r, answer, cands, said) -> Optional[int]:
        """One model call when the answer's words do not decide (a reply in the user's language, "the second
        one"): the index of the chosen candidate, None for none / failure."""
        from ..run_control import call_cancellable, is_cancel
        opts = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(cands))
        prompt = (f"A robot asked which {said} the person means. The choices are:\n{opts}\n"
                  f"The person answered: {answer}\nWhich choice did they pick? Return JSON "
                  f'{{"choice": <number of the choice, 0 if the answer picks none>}}.')
        try:
            d = ask_json(self._llm(), prompt, CHOICE_SCHEMA, budget=r.llm_budget, retries=0, where="which_one",
                         call=call_cancellable, passthrough=is_cancel)
            k = int(d.get("choice"))
        except (LLMUnavailable, LLMBudgetExceeded, LLMBadOutput, TypeError, ValueError):
            return None
        return k - 1 if 1 <= k <= len(cands) else None
