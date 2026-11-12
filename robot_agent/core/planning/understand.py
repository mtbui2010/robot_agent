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

from . import langdata
from .speech import LocalText


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


def default_corr():
    """The self-correction regex when the pack's dialogue option is off: the markers every language pack declares under
    ``patterns.self_correction`` (English ships "no wait", "i mean" ...; the held English reading of a translated answer
    is read with them too). None when no pack declares any: no mention is taken back."""
    return langdata.rx("self_correction", re.I)


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


# A phrase in a script whose pack declares ``grammar.noun_particles`` is a mention only as a whole word: at the start /
# after a space or punctuation, and followed by the end, a space, punctuation or one of the particles (SAGE-v2 F3.2:
# 나 is not in 하나 / 나중에, 냉장고 is not inside 김치냉장고). ``grammar.verb_noun_suffix`` lets a noun made a verb count
# ("충전하러", "청소해 줘", "정리한 다음"). Particles longest first.
def _particle_rx(lang) -> str:
    return "|".join(sorted(langdata.grammar(lang, "noun_particles", []) or [], key=len, reverse=True))


def _particle_lang(phrase: str):
    """The language whose pack has noun particles and whose script ``phrase`` is written in, else None."""
    for lg, r in langdata.script_langs():
        if r.search(phrase) and langdata.grammar(lg, "noun_particles"):
            return lg
    return None


def _find(phrase: str, text: str, extra: str = ""):
    """``extra``: more word endings (regex alternatives, the language pack's ``polite_endings``; option ``dialog_agree2``)."""
    ascii_ = phrase.isascii()
    if ascii_ or langdata.latin_text(phrase):            # Latin letters (a Latin-script pack: accents and tone marks too)
        plural = r"(?:s|es)?" if ascii_ else ""
        return [m.span() for m in re.finditer(r"(?<![\w-])" + re.escape(phrase) + plural + r"(?![\w-])", text)]
    lg = _particle_lang(phrase)
    if lg:
        part, hada = _particle_rx(lg), langdata.grammar(lg, "verb_noun_suffix", "") or ""
        tail = (r"|" + hada if hada else "") + (r"|" + extra if extra else "")
        rx = r"(?<![\w])" + re.escape(phrase) + r"(?=$|[\s.,!?;:)\]\"'~]|(?:" + part + r")" + tail + r")"
        return [m.span() for m in re.finditer(rx, text)]
    return [m.span() for m in re.finditer(re.escape(phrase), text)]


def place_mentions(text: str, places) -> list:
    """[(start, end, phrase, {keys}, is_source)] of the places *text* names, longest phrase first, narrowed by a
    room said next to it ('shelf in the restroom', 'kitchen drawer'). Rooms alone are not mentions."""
    t = _norm(text)
    taken, out = [], []
    rooms = sorted({_norm(r) for r in places.rooms()}, key=len, reverse=True)
    phr = _phrases(places)
    ext = getattr(places, "ext", None)                       # option dialog_agree2: the pack's word endings
    extra = ext.part_rx if ext is not None else ""
    for p in sorted(phr, key=len, reverse=True):
        if p in rooms:
            continue
        for s, e in _find(p, t, extra):
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


def without_places(text: str, places, keys) -> str:
    """*text* with its mentions of the places *keys* cut out (a mention naming only those): a place the person
    replaced in a read-back correction ('on the dining table' -> 'on the kitchen counter') is no longer asked for."""
    keys = set(keys or ())
    if not keys:
        return text
    t = _norm(text)
    for s, e, _p, ks, _src in reversed(place_mentions(t, places)):
        if ks and ks <= keys:
            t = t[:s] + " " + t[e:]
    return t


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


def _corr_of(places):
    """The self-correction regex of the run: the language packs' markers (option ``dialog_agree2``), else ``default_corr()``."""
    ext = getattr(places, "ext", None)
    return ext.corr if ext is not None and ext.corr is not None else default_corr()


def _drop_corrected(t: str, mentions: list, corr=None) -> list:
    """Mentions minus the last one before each self-correction marker."""
    gone = set()
    corr = corr or default_corr()
    for m in (corr.finditer(t) if corr is not None else ()):
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
        if g.get("user_at") and not str(g["user_at"]).startswith("@"):
            out.add(g["user_at"])                 # where the person said they are (the delivery's reference)
    return out


# verbs that move an object: a noun group is an object the goals must cover only after one of them (through
# 'and' / comma chains), never as the subject of a stated clause (SAGE-v2 F3.3, clean-room core list)
_MANIP = re.compile(r"\b(?:put|puts|place|bring|brings|take|takes|grab|move|fetch|hand|give|pass|stash|carry|get|"
                    r"set|leave|return|throw|toss|drop|store|hang|pick|lay|keep|send|"
                    r"slide|slides|push|pushes|tuck|tucks|stick|sticks|pop|pops|insert|load|stow|shove|hide|"
                    r"deliver)\b", re.I)
_COPULA = re.compile(r"^\s*(?:is|are|was|were|will|has|have|had|can|could|should|would|might|must|need|needs|"
                     r"seems?|looks?|got|gets)\b")
_SKIP_OBJ = {"please", "thanks", "thank", "okay", "ok", "hey", "hi", "um", "uh", "oh", "well", "yes", "no"}


def _object_position(t: str, start: int, end: int) -> bool:
    """Is the noun group t[start:end] an object of a manipulation verb before it in its clause?"""
    head = t[:start]
    cut = max(head.rfind(c) for c in ".;!?")
    clause = head[cut + 1:]
    if not _MANIP.search(clause):
        return False
    return not _COPULA.match(t[end:])


def _object_mentions(text: str, places, user_words) -> list:
    """Nouns after a determiner in English *text* that are not places, rooms or generic words: 'tennis ball';
    only in object position (after a manipulation verb, not a clause's subject), no fillers or 1-2 letter words."""
    t = _active(text)
    t = _corr_of(places).sub(" ", t)
    place_words = {w for p in _phrases(places) for w in p.split()} | {w for r in places.rooms() for w in _norm(r).split()}
    out = []
    for m in _DET.finditer(t):
        if not _object_position(t, m.start(), m.end(1)):
            continue
        toks = []
        for w in m.group(1).split():
            if w in _BOUND:
                break
            toks.append(w)
        toks = [w for w in toks if w not in user_words and w not in _SKIP_OBJ]
        if not toks or toks[-1] in _NOT_OBJ or toks[-1].rstrip("s") in _NOT_OBJ or len(toks[-1]) < 3:
            continue
        if any(w in place_words or w.rstrip("s") in place_words for w in toks):
            continue
        out.append(" ".join(toks))
    return list(dict.fromkeys(out))


def _without_messages(text: str, goals) -> str:
    """*text* without what a say goal's message says (the places a message names are not places to act on:
    "tell grandpa to come to the kitchen"; SAGE-v2 F3.6), nor the rest of a tell-clause."""
    from . import lexicon
    says = [g for g in goals if g.get("type") == "say"]
    if not says:
        return text
    t = _norm(text)
    for g in says:
        msg = _norm(g.get("object"))
        if msg and msg in t:
            t = t.replace(msg, " ")
    for p in lexicon.patterns(lexicon.TELL):
        t = re.sub(r"(" + p + r")\s+(?:\S+\s+){0,2}?(?:to|that)\s+[^.;!?]*", r"\1 ", t)
    return t


def _user_word(phrase: str, user_words) -> bool:
    from .persons import is_speaker_word
    p = _norm(phrase)
    return p in {_norm(u) for u in user_words or ()} or is_speaker_word(p)


def _people_covered(goals, keys) -> bool:
    """Is every person entry of ``keys`` a goal's destination, source or listener?"""
    used = {g.get(s) for g in goals for s in ("to", "from", "person", "near_person") if g.get(s)}
    return set(keys) <= used


def _first_person(text: str) -> bool:
    from . import lexicon
    return lexicon.has(lexicon.FIRST_PERSON, text)


def _obj_covered(phrase: str, goals) -> bool:
    stem = lambda w: w[:-2] if w.endswith("es") and len(w) > 4 else (w[:-1] if w.endswith("s") and len(w) > 3 else w)
    ws = {stem(w) for w in phrase.split()}
    return any(ws & {stem(w) for w in str(g.get("object") or "").split()} for g in goals)


def _room_said(text: str, places, key: str) -> bool:
    """Does *text* name the room of the place *key* (a room alone is not a place mention, but it is a whole
    destination for a go goal)?"""
    rm = _norm(places.room(key) or "")
    return bool(rm) and bool(_find(rm, _norm(text)))


def find_issues(goals, text: str, places, english: str = "", user_words=frozenset()) -> list:
    """What the person's words ask for that the goals do not cover, and goals the words do not support.
    *text*: everything the person said (task, own words, answers); *english*: the English part (object check).
    Returns [{kind: place | unsaid | verb | object, what, keys?, object?}]."""
    issues = []
    if not goals:
        return issues
    text = _without_messages(text, goals)
    used = _goal_places(goals)
    full = place_mentions(text, places)
    act_t = _active(text)
    people = {k for k in places.keys if (places.env.get(k) or {}).get("person")}
    for _s, _e, p, keys, is_src in _drop_corrected(act_t, place_mentions(act_t, places), _corr_of(places)):
        if keys & used:
            continue
        if _user_word(p, user_words):
            continue                                 # "me" / 나한테: the speaker, not a place to act on (F3.1)
        if keys and keys <= people and _people_covered(goals, keys):
            continue
        if is_src and any(g.get("type") in ("on", "in") and not g.get("from") for g in goals):
            continue                                 # a source the goals search instead of trusting
        issues.append({"kind": "place", "what": p, "keys": sorted(keys)})
    said = set().union(*(m[3] for m in full)) if full else set()
    first_person = _first_person(text)
    for g in goals:
        dst = g.get("object") if g.get("type") in ("open", "close") else g.get("to")
        e = places.env.get(dst) or {} if dst else {}
        if e.get("person") and (e.get("speaker") or first_person or g.get("near_person")):
            continue                                 # the speaker's own destination ("me", or implied: 가져와 주세요)
        if e.get("person"):
            continue                                 # a person named in words the ENV aliases may not hold (아버님)
        if g.get("near_person") and dst and dst in places.env:
            continue                                 # the surface beside a person the answer placed
        if dst and g.get("type") == "go" and _room_said(text, places, dst):
            continue                                 # "wait in the bedroom": the room is the destination, any place of it will do
        if dst and dst in places.env and dst not in said:
            # an open / close goal is not a delivery: it has no "where to put" slot, so no object (no question)
            issues.append({"kind": "unsaid", "what": _norm(places.head(dst)),
                           "object": "" if g.get("type") in ("open", "close") else g.get("object", "")})
    for verb, rx in _VERB.items():
        if rx.search(act_t) and not any(g.get("type") == verb for g in goals):
            issues.append({"kind": "verb", "what": verb})
    for o in _object_mentions(english or text, places, user_words):
        if not _obj_covered(o, goals):
            near = near_name(o, [g.get("object") for g in goals if g.get("type") in ("on", "in")]
                             + [a for k in places.keys for a in (places.env.get(k) or {}).get("aliases") or []])
            issues.append({"kind": "object", "what": o, **({"near": near} if near else {})})
    return [x for k, x in enumerate(issues) if x not in issues[:k]]


def vacuous_goal_issues(goals, english: str, places, user_words=frozenset()) -> list:
    """A goal set that only walks somewhere (every goal ``go``) for a request whose English reading names an
    object to handle in object position: the model read the request as "go to X" and dropped the rest, and the
    run would end in a success against its own goals. The English reading is the translation of a non-English
    request (the language layer's cached one; the object check cannot read other scripts). Returns object issues."""
    if not goals or any(g.get("type") != "go" for g in goals) or not english:
        return []
    return [{"kind": "object", "what": o} for o in _object_mentions(english, places, user_words)]


# an English request whose verb was lost to the microphone: "could you the book on the shelf?" (a modal, "you",
# then an article straight away): the goals are a guess, so the person is asked what to do
_NOVERB = re.compile(r"\b(?:can|could|would|will|shall)\s+you\s+(?:please\s+)?(?:the|a|an|my|this|that|these|those)\b", re.I)


def verb_lost(text: str) -> bool:
    """Is *text* a request with no verb after "can you ..." (the speech recogniser dropped it)?"""
    return bool(_NOVERB.search(str(text or "")))


_SAY = {"noverb": "I did not hear what to do", "place": "the {what} you mentioned is not in my plan", "unsaid": "you did not say the {what}",
        "verb": "you asked me to {what} something", "object": "the {what} is not in my plan"}


def describe(issue: dict) -> str:
    """The issue as a short sentence; the kinds with a robot phrase (``speech``) come as a LocalText, so they
    are said in the user's language without a translation call."""
    text = _SAY[issue["kind"]].format(**issue)
    key = {"place": "iss_place", "unsaid": "iss_unsaid", "object": "iss_object"}.get(issue["kind"])
    return LocalText(key, {"what": issue["what"]}, text=text) if key else text


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (characters; a Hangul syllable is one character)."""
    a, b = str(a or ""), str(b or "")
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def typo_distance(a: str, b: str) -> int:
    """``edit_distance`` where swapping two neighbouring characters costs ONE edit (a typing slip: 'toolobx' for 'toolbox'), not two
    (optimal string alignment). Used by ``fuzzy_place`` / ``fuzzy_room``; the rule is fixed a priori, not tuned on any data."""
    a, b = str(a or ""), str(b or "")
    rows = [list(range(len(b) + 1))]
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            v = min(rows[-1][j] + 1, cur[j - 1] + 1, rows[-1][j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                v = min(v, rows[-2][j - 2] + 1)
            cur.append(v)
        rows.append(cur)
        rows = rows[-2:] if len(rows) > 2 else rows
    return rows[-1][-1]


def near_distance(a: str, b: str) -> int:
    """``typo_distance``, but a word that is the whole name plus 1-2 more letters (a plural or an ending: 'workbenches' for
    'workbench') costs one edit; the name must have 4+ letters so a short name does not match a longer word."""
    d = typo_distance(a, b)
    if len(b) >= 4 and 1 <= len(a) - len(b) <= 2 and a.startswith(b):
        d = min(d, 1)
    return d


def near_name(word: str, names, max_edits: int = 2) -> str:
    """The one name of ``names`` within ``max_edits`` of ``word`` (not equal), '' when none or several."""
    w = _norm(word)
    c = {n for n in (_norm(x) for x in names if x) if n and n != w and len(n) >= 3 and edit_distance(w, n) <= max_edits}
    return next(iter(c)) if len(c) == 1 else ""


def _spans(text: str) -> set:
    """The word spans (1-3 words, with and without the language's answer tail) of an answer, normalised."""
    toks = _norm(re.sub(r"[.,!?]", " ", str(text or ""))).split()
    spans = {" ".join(toks[i:j]) for i in range(len(toks)) for j in range(i + 1, min(len(toks), i + 3) + 1)}
    tail = langdata.rx("answer_tail")
    if tail is not None:
        spans |= {tail.sub("", x) for x in spans}
    return {x for x in spans if x}


def room_names(places) -> dict:
    """{normalised name: ENV room}: the room as the ENV spells it, with / without a trailing 'room', and the names the language
    layer declares for it (``places.ext.rooms``); {} when the ENV has no rooms."""
    out = {}
    for rm in places.rooms():
        n = _norm(rm)
        out.update({n: rm, n + " room": rm})
        if n.endswith(" room"):
            out[n[:-5]] = rm
    for n, rm in ((getattr(places, "ext", None) and places.ext.rooms) or {}).items():
        out[_norm(n)] = rm
    return {n: rm for n, rm in out.items() if len(n) >= 2}


def fuzzy_room(places, text: str, threshold: float = 0.25):
    """The one ENV room whose name is within ``threshold`` normalised edit distance of a word span of ``text`` (ASR noise:
    'story room' for 'study room'), else None. Same measure as ``fuzzy_place``; two rooms equally near = None."""
    spans, best = _spans(text), {}
    for n, rm in room_names(places).items():
        a = _jamo(n)
        for sp in spans:
            b = _jamo(sp)
            d = near_distance(a, b) / max(len(a), len(b))
            if d <= threshold and (rm not in best or d < best[rm]):
                best[rm] = d
    top = sorted(best.items(), key=lambda kv: kv[1])
    if top and (len(top) == 1 or top[0][1] < top[1][1]):
        return top[0][0]
    return None


def fuzzy_place(places, text: str, threshold: float = 0.25):
    """(key, name) of the one ENV place whose name / alias is within ``threshold`` normalised edit distance of a
    word span of ``text`` (an answer with ASR noise: 식그탁 for 식탁), else (None, ''). The threshold is fixed a priori
    (one wrong unit in a four-unit name) and not tuned on any benchmark (SAGE-v2 F3.7)."""
    t = _norm(re.sub(r"[.,!?]", " ", str(text or "")))
    toks = t.split()
    spans = {" ".join(toks[i:j]) for i in range(len(toks)) for j in range(i + 1, min(len(toks), i + 3) + 1)}
    tail = langdata.rx("answer_tail")
    if tail is not None:
        spans |= {tail.sub("", x) for x in spans}
    best = {}
    for k in places.keys:
        if (places.env.get(k) or {}).get("person"):
            continue
        for n in {_norm(places.head(k))} | {_norm(a) for a in (places.env.get(k) or {}).get("aliases") or [] if a and "@" not in str(a)}:
            if len(n) < 2:
                continue
            for sp in spans:
                if not sp:
                    continue
                a, b = _jamo(sp), _jamo(n)
                d = near_distance(a, b) / max(len(a), len(b))
                if d <= threshold and (k not in best or d < best[k][0]):
                    best[k] = (d, n)
    if len(best) != 1:
        top = sorted(best.items(), key=lambda kv: kv[1][0])
        if len(top) >= 2 and top[0][1][0] < top[1][1][0]:
            return top[0][0], top[0][1][1]
        return None, ""
    k, (_d, n) = next(iter(best.items()))
    return k, n


def _jamo(s: str) -> str:
    """A Hangul syllable as its letters (initial, vowel, final), so a one-letter slip (식딱 / 식탁) is one edit of
    six, not one of two."""
    out = []
    for ch in s:
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out += [chr(0x1100 + c // 588), chr(0x1161 + (c % 588) // 28)] + ([chr(0x11A7 + c % 28)] if c % 28 else [])
        else:
            out.append(ch)
    return "".join(out)


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
        if i["kind"] == "unsaid" and not (multi or i.get("object")):
            continue                              # no empty-slot question ("Where should I put the ?")
        if i["kind"] == "unsaid":
            qs.append(LocalText("q_which_x", {"what": multi["what"], "options": list(multi["keys"])},
                                text=f"Which {multi['what']} do you mean: {' or '.join(nice(k) for k in multi['keys'])}?")
                      if multi else LocalText("q_where_put", {"object": i["object"]}))
        elif i["kind"] == "place" and i is not multi:
            qs.append(LocalText("q_mentioned", {"what": i["what"]}))
        elif i["kind"] == "verb":
            qs.append(f"What should I {i['what']}?")
        elif i["kind"] == "noverb":
            qs.append(LocalText("q_what_do"))
        elif i["kind"] == "object":
            near = i.get("near")
            qs.append(LocalText("q_did_you_mean", {"object": near, "place": near}) if near else LocalText("q_repeat"))
    if multi is not None and not any(i["kind"] == "unsaid" for i in issues):
        qs.insert(0, LocalText("q_mentioned", {"what": multi["what"]}))
    return LocalText.join(*list(dict.fromkeys(qs))[:2]) if qs else ""


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
def _mark(kind: str, profile=None):
    """The words that make a request ``kind`` (safety / capability) unsafe or beyond the robot: the robot's safety profile
    (``refusal_marks``, every language). None when it has none."""
    return profile.mark(kind) if profile is not None else None


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
                  answer_heard: bool, marks=None) -> Optional[Refusal]:
    """The refusal of one understanding, or None. ``status`` / ``reason``: the model's typed status and its
    sentence; ``missing``: the actions the goals need that the manifest lacks; ``words``: everything the
    person said; ``answer_heard``: the last thing in play is an answer heard through the robot's I/O (a
    typed one cannot be misheard); ``marks``: the robot's safety profile, whose ``refusal_marks`` make a refusal after a heard
    answer believable (none = the refusal stands). The model's free text never refuses by itself."""
    if status in ("unsafe", "infeasible"):
        kind = SAFETY if status == "unsafe" else CAPABILITY
        mark = _mark(kind, marks)
        if answer_heard and mark is not None and not mark.search(words or ""):
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
