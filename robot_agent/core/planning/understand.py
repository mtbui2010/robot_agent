"""Robust understanding: the understand LLM call and its checks, robot-agnostic.

* ``LLMBudget``: a per-run cap on the LLM calls the core makes (understand, its repairs and samples, planner calls).
* ``ask_json``: one JSON call with a bounded repair retry when the answer is not JSON; a client error (server down,
  timeout) becomes ``LLMUnavailable`` with a FailureCode, so the run can end with a clear message.
* ``sampling``: the understand call's temperature / seed (default 0 / fixed: the same sentence, the same goals).
* ``typed_refusal``: a refusal only for a typed reason (capability / safety / the person declined), never for the
  model's free text alone; a refusal that only a misheard answer could have caused is asked again instead.
* ``coverage``: a deterministic read-back of the understood goals against the person's words: every place the
  words name (outside conditions, negations and self-corrections) must be used by a goal, every open / close the
  words ask for must have its goal, every object the words name must be moved, and no goal may go to a place the
  person never said. The issues found decide whether the loop samples the understanding again (CLARA-style
  consistency) or asks the person.

The places come from the live ENV through the duck-typed ``Places`` of ``assist_loop`` (keys, head, room, env).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from robot_agent.contract import FailureCode


# ─────────────────────────────────────────────────────────────────────────────
# LLM calls: errors, budget, JSON repair
# ─────────────────────────────────────────────────────────────────────────────
_UNREACHABLE = re.compile(r"connect|refused|unreachable|timed? ?out|timeout|http|50[234]|name resolution|"
                          r"remote end closed|max retries", re.I)


class LLMUnavailable(Exception):
    """A language-model / planner call raised: the server is down or timed out (``unreachable``), or the call
    failed otherwise (a bug, a bad answer it choked on). ``code`` is the run's FailureCode."""

    def __init__(self, cause: BaseException, where: str = "understand"):
        super().__init__(f"{where}: {cause}")
        self.cause, self.where = cause, where
        self.unreachable = isinstance(cause, (ConnectionError, TimeoutError)) or \
            bool(_UNREACHABLE.search(f"{type(cause).__name__} {cause}"))
        self.code = llm_error_code(cause) if self.unreachable else FailureCode.SOFTWARE

    def message(self, what: str = "language model") -> str:
        return f"the {what} is not reachable: {self.cause}" if self.unreachable else f"the {what} failed: {self.cause}"


class LLMBudgetExceeded(Exception):
    """The run used its LLM-call budget."""


class LLMBadOutput(Exception):
    """The model kept answering something that is not the JSON asked for."""


def llm_error_code(e: BaseException) -> FailureCode:
    """TIMEOUT for a timed-out call, HW_FAULT (a server down / unreachable) otherwise."""
    txt = f"{type(e).__name__} {e}".lower()
    if isinstance(e, TimeoutError) or "timed out" in txt or "timeout" in txt:
        return FailureCode.TIMEOUT
    return FailureCode.HW_FAULT


class LLMBudget:
    """Counts the core's LLM calls in one run; ``take`` raises LLMBudgetExceeded past ``limit`` (0 = no cap)."""

    def __init__(self, limit: int):
        self.limit, self.used = int(limit), 0

    def take(self, n: int = 1, what: str = "LLM call") -> None:
        if self.limit and self.used + n > self.limit:
            raise LLMBudgetExceeded(f"{what}: the LLM-call budget of this run ({self.limit}) is used up")
        self.used += n

    def left(self) -> int:
        return max(0, self.limit - self.used) if self.limit else 1 << 30


REPAIR_PROMPT = ("Your previous answer was not valid JSON:\n{bad}\n\nAnswer the request below again with ONLY the "
                 "JSON object (no prose, no code fence).\n\n{prompt}")


def parse_json_object(raw) -> Optional[dict]:
    """The JSON object in a model answer (bare, fenced or inside prose), else None."""
    if isinstance(raw, dict):
        return raw
    s = str(raw or "")
    for cand in (s, *re.findall(r"\{.*\}", s, re.S)):
        try:
            d = json.loads(cand)
        except Exception:
            continue
        if isinstance(d, dict):
            return d
    return None


def sampling(temperature, seed=None) -> dict:
    """The sampling kwargs of a chat call (UND_T0): ``temperature`` (None = the client's default) and a fixed
    ``seed`` (None / '' / negative = none); the clients pass them on where the server takes them."""
    out = {}
    if temperature is not None and str(temperature).strip() != "":
        out["temperature"] = float(temperature)
    if seed is not None and str(seed).strip() != "" and int(seed) >= 0:
        out["seed"] = int(seed)
    return out


def ask_json(llm, prompt: str, schema: dict, budget: Optional[LLMBudget] = None, retries: int = 1,
             where: str = "understand", call=None, passthrough=None, options: Optional[dict] = None) -> dict:
    """One JSON answer from ``llm.chat``: on a non-JSON answer, up to ``retries`` repair calls (the bad answer
    quoted back). Raises LLMUnavailable (client error), LLMBudgetExceeded, LLMBadOutput (still not JSON).
    ``call(fn, **kw)`` runs each request (e.g. run_control.call_cancellable); an exception ``passthrough(e)``
    accepts (the run's stop) is raised as is, not as LLMUnavailable. ``options``: extra chat kwargs
    (``sampling()``: temperature / seed)."""
    p, last, kw = prompt, "", dict(options or {})
    for _k in range(1 + max(0, int(retries))):
        if budget is not None:
            budget.take(what=where)
        try:
            raw = call(llm.chat, prompt=p, format=schema, **kw) if call else llm.chat(prompt=p, format=schema, **kw)
        except Exception as e:                       # connection refused, read timeout, HTTP 5xx ...
            if passthrough is not None and passthrough(e):
                raise
            raise LLMUnavailable(e, where) from e
        d = parse_json_object(raw)
        if d is not None:
            return d
        last = str(raw)[:400]
        p = REPAIR_PROMPT.format(bad=last, prompt=prompt)
    raise LLMBadOutput(f"{where}: the language model did not answer JSON: {last[:120]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# goal read-back against the person's words
# ─────────────────────────────────────────────────────────────────────────────
_COND = re.compile(r"\b(?:if|unless|whether|in case)\b[^,;.]*", re.I)
_NEG = re.compile(r"\b(?:don'?t|do not|never|except|without|but not|not)\b[^,;.]*", re.I)
_CORR = re.compile(r"(?:\.\.\.|,|-)?\s*\b(?:no wait|wait,? no|no,? i mean|i mean|actually|sorry|rather)\b", re.I)
_SOURCE = re.compile(r"\b(?:from|out of|off)\s+(?:the\s+|my\s+|your\s+)?(?:\w+\s+){0,2}$", re.I)
_VERB = {"open": re.compile(r"(?:^|[,.;:]\s*|\b(?:and|then|please|to|also|first)\s+)open\b(?!\s+(?:kitchen\s+)?\w*\s*(?:is|are)\b)", re.I),
         "close": re.compile(r"(?:^|[,.;:]\s*|\b(?:and|then|please|to|also|first)\s+)(?:close|shut)\b(?!\s+to\b)", re.I)}
_DET = re.compile(r"\b(?:the|a|an|my|your|this|that|these|those|both|all)\s+(?=((?:[a-z][a-z'-]*\s+){0,2}[a-z][a-z'-]*))", re.I)
_BOUND = {"on", "in", "into", "onto", "from", "to", "at", "and", "then", "out", "back", "off", "away", "together",
          "with", "is", "are", "of", "for", "by", "near", "next", "before", "after", "inside", "it", "so", "but", "or",
          "first", "too", "also", "again", "there", "here", "up", "down", "over", "under", "beside", "behind"}
# words after a determiner that are not objects to move
_NOT_OBJ = {"way", "one", "ones", "rest", "same", "other", "floor", "edge", "task", "user", "person", "robot", "room",
            "house", "end", "time", "trip", "hand", "gripper", "right", "left", "top", "bottom", "side", "middle",
            "thing", "things", "stuff", "lot", "place", "spot", "front", "back", "it", "them", "everything", "kitchen's"}


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").split())


def _phrases(places) -> dict:
    """phrase -> the ENV keys it may name (heads, aliases, and a multi-word head's last word: 'table')."""
    out: dict = {}
    for k in places.keys:
        head = _norm(places.head(k))
        names = {head} | {_norm(a) for a in places.env.get(k, {}).get("aliases") or [] if a and "@" not in str(a)}
        if len(head.split()) > 1:
            names.add(head.split()[-1])
        for n in names:
            if n:
                out.setdefault(n, set()).add(k)
    return out


def _find(phrase: str, text: str):
    if phrase.isascii():
        return [m.span() for m in re.finditer(r"(?<![\w-])" + re.escape(phrase) + r"(?:s|es)?(?![\w-])", text)]
    return [m.span() for m in re.finditer(re.escape(phrase), text)]


def place_mentions(text: str, places) -> list:
    """[(start, end, phrase, {keys}, is_source)] of the places *text* names, longest phrase first, narrowed by a
    room said next to it ('shelf in the restroom', 'kitchen drawer'). Rooms alone are not mentions."""
    t = _norm(text)
    taken, out = [], []
    rooms = sorted({_norm(r) for r in places.rooms()}, key=len, reverse=True)
    phr = _phrases(places)
    for p in sorted(phr, key=len, reverse=True):
        if p in rooms:
            continue
        for s, e in _find(p, t):
            if any(s < e2 and s2 < e for s2, e2 in taken):
                continue
            taken.append((s, e))
            keys = set(phr[p])
            for rm in rooms:
                if re.search(re.escape(rm) + r"(?:'s)?\s*$", t[max(0, s - 24):s]) or \
                        re.match(r"\s*(?:in|of|at)\s+(?:the\s+)?" + re.escape(rm), t[e:e + 24]):
                    in_room = {k for k in keys if _norm(places.room(k)) == rm}
                    if in_room:
                        keys = in_room
                    break
            out.append((s, e, p, keys, bool(_SOURCE.search(t[max(0, s - 30):s]))))
    return sorted(out)


def _active(text: str) -> str:
    """*text* without the parts that are not requests: conditions ('if the drawer is open'), negations ('don't
    touch the mug') and what a self-correction takes back ('on the stool... no wait, on the sofa')."""
    t = _norm(text)
    out = []
    for seg in re.split(r"(?<=[.;!?])\s+", t):
        seg = _COND.sub(" ", seg)
        seg = _NEG.sub(" ", seg)
        out.append(seg)
    return " ".join(out)


def _drop_corrected(t: str, mentions: list) -> list:
    """Mentions minus the last one before each self-correction marker."""
    gone = set()
    for m in _CORR.finditer(t):
        before = [x for x in mentions if x[1] <= m.start() + 1 and m.start() - x[1] < 40]
        if before:
            gone.add(before[-1][:2])
    return [x for x in mentions if x[:2] not in gone]


def _goal_places(goals) -> set:
    out = set()
    for g in goals:
        for s in ("to", "from"):
            if g.get(s):
                out.add(g[s])
        if g.get("type") in ("open", "close") and g.get("object"):
            out.add(g["object"])
        out.update(g.get("from_any") or [])
    return out


def _object_mentions(text: str, places, user_words) -> list:
    """Nouns after a determiner in English *text* that are not places, rooms or generic words: 'tennis ball'."""
    t = _active(text)
    t = _CORR.sub(" ", t)
    place_words = {w for p in _phrases(places) for w in p.split()} | {w for r in places.rooms() for w in _norm(r).split()}
    out = []
    for m in _DET.finditer(t):
        toks = []
        for w in m.group(1).split():
            if w in _BOUND:
                break
            toks.append(w)
        toks = [w for w in toks if w not in user_words]
        if not toks or toks[-1] in _NOT_OBJ or toks[-1].rstrip("s") in _NOT_OBJ:
            continue
        if any(w in place_words or w.rstrip("s") in place_words for w in toks):
            continue
        out.append(" ".join(toks))
    return list(dict.fromkeys(out))


def _obj_covered(phrase: str, goals) -> bool:
    stem = lambda w: w[:-2] if w.endswith("es") and len(w) > 4 else (w[:-1] if w.endswith("s") and len(w) > 3 else w)
    ws = {stem(w) for w in phrase.split()}
    return any(ws & {stem(w) for w in str(g.get("object") or "").split()} for g in goals)


def find_issues(goals, text: str, places, english: str = "", user_words=frozenset()) -> list:
    """What the person's words ask for that the goals do not cover, and goals the words do not support.
    *text*: everything the person said (task, own words, answers); *english*: the English part (object check).
    Returns [{kind: place | unsaid | verb | object, what, keys?, object?}]."""
    issues = []
    if not goals:
        return issues
    used = _goal_places(goals)
    full = place_mentions(text, places)
    act_t = _active(text)
    for _s, _e, p, keys, is_src in _drop_corrected(act_t, place_mentions(act_t, places)):
        if keys & used:
            continue
        if is_src and any(g.get("type") in ("on", "in") and not g.get("from") for g in goals):
            continue                                 # a source the goals search instead of trusting
        issues.append({"kind": "place", "what": p, "keys": sorted(keys)})
    said = set().union(*(m[3] for m in full)) if full else set()
    for g in goals:
        dst = g.get("object") if g.get("type") in ("open", "close") else g.get("to")
        if dst and dst in places.env and dst not in said:
            issues.append({"kind": "unsaid", "what": _norm(places.head(dst)), "object": g.get("object", "")})
    for verb, rx in _VERB.items():
        if rx.search(act_t) and not any(g.get("type") == verb for g in goals):
            issues.append({"kind": "verb", "what": verb})
    for o in _object_mentions(english or text, places, user_words):
        if not _obj_covered(o, goals):
            issues.append({"kind": "object", "what": o})
    return [x for k, x in enumerate(issues) if x not in issues[:k]]


_SAY = {"place": "the {what} you mentioned is not in my plan", "unsaid": "you did not say the {what}",
        "verb": "you asked me to {what} something", "object": "the {what} is not in my plan"}


def describe(issue: dict) -> str:
    return _SAY[issue["kind"]].format(**issue)


def coverage(goals, text: str, places, english: str = "", user_words=frozenset()) -> list:
    """``find_issues`` as short sentences ('the counter you mentioned is not in my plan', ...); [] = consistent."""
    return [describe(i) for i in find_issues(goals, text, places, english, user_words)]


def issue_question(issues: list, places) -> str:
    """One open question to the person about the issues (not a yes / no: a wrong reading must not be settled
    by a reflex 'yes'). Places are named as the person would ('side table in the living room')."""
    nice = lambda k: k.replace("@", " in the ")
    qs = []
    multi = next((i for i in issues if i["kind"] == "place" and len(i.get("keys") or ()) > 1), None)
    for i in issues:
        if i["kind"] == "unsaid":
            qs.append(f"Which {multi['what']} do you mean: {' or '.join(nice(k) for k in multi['keys'])}?" if multi
                      else f"Where should I put the {i['object']}?")
        elif i["kind"] == "place" and i is not multi:
            qs.append(f"You mentioned the {i['what']}. What should I do there?")
        elif i["kind"] == "verb":
            qs.append(f"What should I {i['what']}?")
        elif i["kind"] == "object":
            qs.append(f"What should I do with the {i['what']}?")
    if multi is not None and not any(i["kind"] == "unsaid" for i in issues):
        qs.insert(0, f"You mentioned the {multi['what']}. What should I do there?")
    return " ".join(list(dict.fromkeys(qs))[:2])


def signature(goals) -> tuple:
    """A goal set's identity for a consistency check (the source is left out: it is searched for anyway)."""
    return tuple(sorted((g.get("type", ""), g.get("object", ""), g.get("to", "")) for g in goals))


def consensus(samples: list, need: int = 2):
    """CLARA-style: among grounded understandings [(goals, issues)], the issue-free goal set that at least
    ``need`` of them agree on, else None."""
    count: dict = {}
    for goals, issues in samples:
        if goals and not issues:
            sig = signature(goals)
            n, _g = count.get(sig, (0, goals))
            count[sig] = (n + 1, goals)
    best = max(count.values(), key=lambda x: x[0], default=None)
    return best[1] if best and best[0] >= need else None


# ─────────────────────────────────────────────────────────────────────────────
# refusals from a typed reason (ASR1)
# ─────────────────────────────────────────────────────────────────────────────
CAPABILITY, SAFETY, DECLINED = "capability", "safety", "declined"
# words that make a request unsafe / beyond a household robot (the understand prompt's "It cannot" list):
# a refusal the model gives after an answer heard through the microphone needs one of them in the person's words
_MARK = {
    SAFETY: re.compile(r"\b(?:knife|knives|blade|scissors|razor|fire|flame|stove|burn|hot|boil|window|throw|"
                       r"hit|hurt|poison|bleach|medicine|pills?|child|baby|kid|outside)\b|칼|불|창문|던지|가위|독|약|"
                       r"아이|dao|lửa|cửa sổ|ném|thuốc|trẻ", re.I),
    CAPABILITY: re.compile(r"\b(?:throw|pour|cook|wash|washer|climb|window|door|tools?|turn on|switch on|lift|"
                           r"furniture|sofa|bed|leave|outside|clean)\b|던지|붓|요리|씻|세탁|올라|창문|문을|켜|"
                           r"ném|đổ|nấu|giặt|leo|bật", re.I),
}


@dataclass(frozen=True)
class Refusal:
    """Why the robot will not do a request. ``kind``: capability (the manifest has no skill a goal needs,
    or the request asks for something no household robot does) | safety | declined (the person said no).
    ``misheard``: the model refused only after an answer heard through the microphone that names nothing
    unsafe or impossible: not a refusal, the question is asked again."""
    kind: str
    msg: str = ""
    say: str = ""
    missing: str = ""
    misheard: bool = False


def typed_refusal(status: str, reason: str, missing: Optional[str], words: str,
                  answer_heard: bool) -> Optional[Refusal]:
    """The refusal of one understanding, or None. ``status`` / ``reason``: the model's typed status and its
    sentence; ``missing``: the actions the goals need that the manifest lacks; ``words``: everything the
    person said; ``answer_heard``: the last thing in play is an answer heard through the robot's I/O (a
    typed one cannot be misheard). The model's free text never refuses by itself."""
    if status in ("unsafe", "infeasible"):
        kind = SAFETY if status == "unsafe" else CAPABILITY
        if answer_heard and not _MARK[kind].search(words or ""):
            return Refusal(kind, misheard=True)
        msg = (reason or "").strip() or "I can't do that."
        return Refusal(kind, msg=msg, say=f"I can't do that: {msg}")
    if missing:
        return Refusal(CAPABILITY, msg=f"this robot has no {missing} skill", missing=missing)
    return None


def misheard_question(dialogue: list) -> tuple:
    """(dialogue without the misheard answer, the question to ask again)."""
    if len(dialogue) >= 2 and dialogue[-1][0] == "user" and dialogue[-2][0] == "robot":
        return dialogue[:-2], f"Sorry, I may have misheard you. {dialogue[-2][1]}"
    return dialogue, "Sorry, I may have misheard you. Could you say that again?"
