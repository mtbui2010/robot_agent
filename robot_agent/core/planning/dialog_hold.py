"""Hold the reading across turns (solution-lab round 18, option ``dialog_hold``; default OFF, off = the earlier behaviour byte
for byte). Pure helpers: no LLM, no ENV mutation, no word list, no number of a simulator. ``AssistLoop`` (``dialog_hold_flow``)
keeps one ``Ledger`` per run and acts on what these functions say.

Failure class (dev 0..99, round 12-17 recordings): a person refuses a read-back with a bare "no" or "not sure", the robot asks
"what should I do?" and understands the task AGAIN from scratch. The model draws a new reading: a part the person never touched
changes (mug becomes cup), an invented part appears, a right reading turns wrong. The robot also asks the same question again
after a "not sure". The ledger below stops both.

* **Parts.** The parts of a reading are its goals. The slots of a part come from its goal kind (``goals.SLOTS``: the domain
  profile); a goal of a kind the table does not know gets the slots it holds (object, from, to). Slot GROUPS keep fields that
  belong together: object = object, said, name_local; from = from, from_any; to = to, hand, near_person, user_at.
* **Status** of a part: ``open``, ``settled`` or ``refused``, with provenance per slot (``reading``, ``yes``, ``answer``,
  ``edit``).
* **Settled** by a yes to a read-back that names the part (the whole reading, or one part), or by her own content answer to an
  open question about a slot that names the value. A garbled answer (``dialog_ext.Guard``) never settles anything.
* **Merge** of every later reading of the model, part by part (``merge``):
  - a settled slot keeps its value;
  - an unsettled slot changes only when her NEW words give positive evidence for the new value and less for the old one: the
    name's exact tokens after the pack's particle strip (containment of a shorter word in a longer one does not count), an ENV
    alias, or an English ``said`` span. On a tie the old value stays. The model drawing the same words again is not evidence;
  - the object label is never renamed by a reading alone;
  - a part the reading adds, that the first reading did not have and her words do not anchor, is dropped.
* **Two statements of hers that conflict** and neither is superseded (not an answer to a question about that slot and not a
  correction after a refusal): ONE two-way question that names both values. After it, her latest specific statement wins.

Evidence that is NOT used (offline, dev 0..99): an identical re-reading after a bare "no", and the nouns of "no, <words>"
agreeing with the read-back. Both are the model's own noise or the person's refusal, not new facts.

Language- and robot-agnostic. Particles, articles, ENV aliases and speaker forms are DATA (``lang/*.json``, the ENV). A language
without data gives no positive evidence, so a slot keeps its value and the robot asks (fail open toward asking).
"""

from __future__ import annotations

import re
from typing import Optional

from . import dialog_converge as DC, goals as goal_kinds, langdata, persons
from .understand import _norm, place_mentions

DEFAULT_MAX_OPTIONS = 3            # a spoken choice: at most this many options (human-factors bound; assumed, manifest may set)
GROUPS = {"object": ("object", "said", "name_local"), "from": ("from", "from_any"),
          "to": ("to", "hand", "near_person", "user_at")}
ORDER = ("object", "to", "from")
OPEN, SETTLED, REFUSED = "open", "settled", "refused"


# ── option parsing ────────────────────────────────────────────────────────────────────────────────────────────────────
def options(raw, declared=None) -> Optional[dict]:
    """The run's ``{hold, one_part, max_options, relabel}`` or None (off). ``raw``: the run option / environment value
    (``"1"`` = both parts, ``"hold"`` = H1 only, ``"0"`` = off, or a mapping); ``declared``: the manifest's
    ``planner.dialog_hold`` mapping. No value anywhere = off."""
    d = declared if isinstance(declared, dict) else {}
    if isinstance(raw, dict):
        d, mode = {**d, **raw}, None
    else:
        s = str(raw if raw is not None else "").strip().lower()
        mode = s if s else None
    if mode in ("0", "false", "no", "off"):
        return None
    if mode in ("1", "true", "yes", "on", "both"):
        hold, one = True, True
    elif mode == "hold":
        hold, one = True, False
    elif mode is None:
        hold, one = bool(d.get("hold")), bool(d.get("one_part"))
    else:
        return None
    if not hold and not one:
        return None
    mx = d.get("max_options")
    mx = int(mx) if isinstance(mx, int) and not isinstance(mx, bool) and mx >= 2 else DEFAULT_MAX_OPTIONS
    return {"hold": True, "one_part": one, "max_options": mx}      # H2 needs the ledger: one_part implies hold


def cap_options(opts, n: int) -> list:
    return [o for o in opts][:max(2, int(n))]


# ── slots ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def slots_of(goal) -> tuple:
    """The slot groups of a goal, from its kind (``goals.SLOTS``); an unknown kind: the groups it holds."""
    kind = goal.get("type")
    names = goal_kinds.SLOTS.get(kind)
    if names is None:
        names = tuple(s for s in ORDER if goal.get(s))
    return tuple(s for s in names if s in GROUPS)


def _group_value(goal, slot):
    return str(goal.get(slot) or "").strip()


def _set_group(goal, slot, src) -> None:
    for k in GROUPS[slot]:
        if src.get(k) in (None, "", False):
            goal.pop(k, None)
        else:
            goal[k] = src[k]
    if slot in ("object", "from", "to") and slot not in goal:
        goal[slot] = ""


# ── evidence in her words ─────────────────────────────────────────────────────────────────────────────────────────────
def _articles(lang) -> set:
    out = set()
    for lg in {(lang or "en")[:2], langdata.prompt_lang()}:
        out |= {str(a).lower() for a in (langdata.get(lg).get("articles") or [])}
    return out


def token_forms(text, lang) -> set:
    """The tokens of ``text`` and each token with a case particle of the pack taken off its end (``grammar.noun_particles``),
    and, after that, one copula / polite ending of the pack (``grammar.noun_endings``: "우유요" is the noun 우유):
    whole tokens only, so 컵 is not found inside 머그컵. A language without particles or endings: the tokens."""
    toks = re.findall(r"[^\W_]+", str(text or "").lower())
    out = set(toks)
    lg = (lang or "en")[:2]
    parts = sorted((str(p) for p in (langdata.grammar(lg, "noun_particles", []) or []) if p), key=len, reverse=True)
    ends = sorted((str(e) for e in (langdata.grammar(lg, "noun_endings", []) or []) if e), key=len, reverse=True)

    def strip_one(t, words):
        for w in words:
            if t.endswith(w) and len(t) > len(w):
                return t[:-len(w)]
        return None
    for t in toks:
        for p in parts:
            if t.endswith(p) and len(t) > len(p):
                out.add(t[:-len(p)])
        e = strip_one(t, ends)
        if e:
            out.add(e)
            for p in parts:                                # "우유를요": the ending, then the particle
                if e.endswith(p) and len(e) > len(p):
                    out.add(e[:-len(p)])
    return out


def strip_lead_word(text, lang_codes=None) -> str:
    """``text`` without ONE leading yes / no word of the packs (``dialogue.yes`` / ``dialogue.no``, every language with a pack
    unless ``lang_codes`` is given) and the punctuation after it. The text as it is when it starts with none."""
    words = sorted({str(w).lower() for k in ("yes", "no") for w in langdata.dialogue_all(k, lang_codes) if w}, key=len, reverse=True)
    t = str(text or "").strip()
    if not words:
        return t
    m = re.match(r"^(?:" + "|".join(re.escape(w) for w in words) + r")(?![^\W_])[\s,.!?;:-]*", t, flags=re.I)
    return t[m.end():].strip() if m else t


def _name_tokens(name, lang) -> list:
    art = _articles(lang)
    toks = [t for t in re.findall(r"[^\W_]+", str(name or "").lower().replace("_", " ")) if t not in art]
    return toks


def object_score(goal, texts, lang) -> int:
    """How many tokens of the goal's object name (its ``said`` span, its local name, its label: the best of them) her
    ``texts`` say exactly. 0 = not named."""
    forms = set()
    for t in texts:
        forms |= token_forms(t, lang)
        forms |= token_forms(t, langdata.prompt_lang())
    best = 0
    for key in ("said", "name_local", "object"):
        name = goal.get(key)
        if not name or (key == "object" and goal.get("type") in ("open", "close")):
            continue
        toks = _name_tokens(name, lang)
        best = max(best, sum(1 for t in dict.fromkeys(toks) if t in forms))
    return best


def named_keys(texts, places) -> set:
    """ENV keys her ``texts`` name (places, containers, persons; the speaker as ``@speaker`` and its key)."""
    keys = set()
    for t in texts:
        if not t:
            continue
        for _s, _e, _p, ks, _src in place_mentions(_norm(str(t)), places):
            keys |= {k for k in ks if not str(k).startswith("@")}
        try:
            keys |= {k for k in places.candidates(str(t), str(t)) if not str(k).startswith("@")}
        except Exception:                                              # noqa: BLE001 - a lookup that fails names nothing
            pass
    if DC.speaker_said(list(texts)):
        keys.add("@speaker")
        sk = persons.speaker_key(places)
        if sk:
            keys.add(sk)
    return keys


def place_score(value, texts, places) -> int:
    if not value:
        return 0
    keys = named_keys(texts, places)
    if str(value).lower() in ("me", "user", "@speaker"):
        return int("@speaker" in keys)
    return int(value in keys)


def slot_score(goal, slot, texts, places, lang) -> int:
    if slot == "object" and goal.get("type") in ("open", "close"):      # the object of an open / close goal is a container of the ENV
        return place_score(goal.get("object"), texts, places)
    return object_score(goal, texts, lang) if slot == "object" else place_score(goal.get(slot), texts, places)


def anchor_of(goal, texts, places, lang) -> dict:
    """{slot: bool}: is the slot's value in her words (any utterance so far)?"""
    return {s: slot_score(goal, s, texts, places, lang) > 0 for s in slots_of(goal) if s == "object" or goal.get(s)}


def goal_anchored(goal, texts, places, lang) -> bool:
    """Does anything of the goal show in her words: its object name, a place, or (a message) its own words?"""
    if object_score(goal, texts, lang) > 0:
        return True
    for s in ("to", "from"):
        if goal.get(s) and place_score(goal[s], texts, places) > 0:
            return True
    if goal.get("type") == "say" and goal.get("object"):
        low = " ".join(str(t).lower() for t in texts)
        return str(goal["object"]).lower() in low
    return False


# ── the ledger ────────────────────────────────────────────────────────────────────────────────────────────────────────
class Ledger:
    """The parts of the run's reading, with their status. ``seen``: how many of her turns the last reading had heard."""

    def __init__(self, goals, seen=0):
        self.parts = [{"goal": dict(g), "status": OPEN, "settled": set(), "first": True, "prov": {}, "stage": 0, "edits": 0, "empty": False}
                      for g in goals]
        self.seen = seen
        self.active = False             # H2: the read-back goes part by part after a content-free answer
        self.conflict_asked = False
        self.failed = set()             # (template, slots) of questions that got "not sure" / not understood
        self.asked_slot = None          # (part index, slot) an open question is waiting for the answer of
        self.supersede = set()          # {(part, slot) | "*"}: her next words answer / correct these (they replace older ones)

    def goals(self) -> list:
        return [dict(p["goal"]) for p in self.parts]

    def sync(self, goals) -> None:
        """Take ``goals`` (an edit made elsewhere, same parts in the same order) as the parts' values."""
        if len(goals) == len(self.parts):
            for p, g in zip(self.parts, goals):
                p["goal"] = dict(g)

    def settle(self, i, slots=None, why="yes") -> None:
        p = self.parts[i]
        for s in (slots if slots is not None else slots_of(p["goal"])):
            p["settled"].add(s)
            p["prov"][s] = why
        if why == "yes":
            p.pop("changed_since_yes", None)
        self._status(p)

    def _status(self, p) -> None:
        need = set(slots_of(p["goal"]))
        if p["status"] != REFUSED:
            p["status"] = SETTLED if need and need <= p["settled"] else OPEN

    def settle_all(self, why="yes") -> None:
        for i in range(len(self.parts)):
            self.settle(i, None, why)

    def refuse(self, i) -> None:
        self.parts[i]["status"] = REFUSED

    def open_parts(self) -> list:
        return [i for i, p in enumerate(self.parts) if p["status"] == OPEN]

    def all_settled(self) -> bool:
        return bool(self.parts) and all(p["status"] == SETTLED for p in self.parts)

    def next_open(self, texts, places, lang, unanchored_first=()) -> Optional[int]:
        """The part to ask about next: an open part with an unanchored slot first (the robot filled it, or her words do not
        name it; ``unanchored_first``: indexes the caller found unverified), else plan order."""
        todo = self.open_parts()
        if not todo:
            return None

        def rank(i):
            g = self.parts[i]["goal"]
            un = not all(anchor_of(g, texts, places, lang).values())
            return (0 if (i in unanchored_first or un) else 1, i)
        return min(todo, key=rank)


def least_anchored_slot(goal, texts, places, lang, settled=()) -> str:
    """The slot to ask about for ``goal``: the object when her words do not name it, else the destination, else the source;
    when everything is anchored, the object (or the destination of a goal without an object). A slot in ``settled`` is not
    asked about again."""
    an = anchor_of(goal, texts, places, lang)
    slots = [s for s in slots_of(goal) if s not in set(settled)]
    for s in ORDER:
        if s in slots and s != "from" and an.get(s) is False:
            return s
    for s in ORDER:
        if s in slots and s != "from":
            return s
    return "object"


# ── merging a later reading ───────────────────────────────────────────────────────────────────────────────────────────
def _label(g) -> str:
    return str(g.get("object") or "").strip().lower()


MOVE_KINDS = ("on", "in")               # one family: "on" vs "in" is a property of the destination, not another part


def _family(g):
    t = g.get("type")
    return "move" if t in MOVE_KINDS else t


def refines(old, new) -> bool:
    """Is the label ``new`` the label ``old`` made more specific (every token of ``old`` kept, at least one added:
    mug -> black mug)? Such a pair names one thing, so it is never a conflict."""
    a, b = _label_tokens(old), _label_tokens(new)
    return bool(a) and set(a) < set(b)


def _label_tokens(g) -> list:
    return re.findall(r"[^\W_]+", _label(g).replace("_", " "))


def _match(old, new) -> dict:
    """{new index: old index}: parts matched by (type, label), then (type, said), then (type, a destination or source), then
    by position among the parts of one type when both readings have the same number of them."""
    out, used = {}, set()

    def go(pred):
        for j, n in enumerate(new):
            if j in out:
                continue
            for i, o in enumerate(old):
                if i not in used and _family(o["goal"]) == _family(n) and pred(o["goal"], n):
                    out[j] = i
                    used.add(i)
                    break
    go(lambda o, n: _label(o) == _label(n) and (_label(o) or o.get("to") == n.get("to")))
    go(lambda o, n: bool(o.get("said")) and o.get("said") == n.get("said"))
    go(lambda o, n: refines(o, n) or refines(n, o))
    go(lambda o, n: bool(n.get("to")) and o.get("to") == n.get("to") or bool(n.get("from")) and o.get("from") == n.get("from"))
    for ty in {_family(n) for n in new}:
        js = [j for j, n in enumerate(new) if _family(n) == ty and j not in out]
        is_ = [i for i, o in enumerate(old) if _family(o["goal"]) == ty and i not in used]
        if js and len(js) == len(is_):
            for j, i in zip(js, is_):
                out[j] = i
                used.add(i)
    return out


def merge(ledger, new_goals, new_texts, earlier_texts, places, lang, supersede=()) -> tuple:
    """Merge a later reading ``new_goals`` into the ledger (``ledger.parts`` keep the held values).

    ``new_texts``: what she said since the last reading (utterances and their held English); ``earlier_texts``: everything
    before. ``supersede``: slots she answered or corrected in ``new_texts`` as ``{(old part index, slot)}`` or ``{"*"}`` for
    all (an answer to an open question, a correction after a refusal): her new words replace what she said before.

    Returns ``(goals, info)``: the merged goals (plan order of the ledger, then the added anchored parts) and
    ``info = {"changed": [...], "kept": [...], "dropped": [...], "added": [...], "conflicts": [{part, slot, old, new}]}``."""
    info = {"changed": [], "kept": [], "dropped": [], "added": [], "added_idx": [], "conflicts": []}
    match = _match(ledger.parts, new_goals)
    n_old = len(ledger.parts)
    sup_all = "*" in set(supersede)
    all_texts = list(earlier_texts) + list(new_texts)
    added_j = {}                                       # ledger index of an added part -> its index in the new reading
    for j, n in enumerate(new_goals):
        i = match.get(j)
        if i is None:
            if goal_anchored(n, all_texts, places, lang):
                ledger.parts.append({"goal": dict(n), "status": OPEN, "settled": set(), "first": False, "prov": {},
                                     "stage": 0, "edits": 0, "empty": False})
                info["added"].append(_label(n) or n.get("type"))
                info["added_idx"].append(len(ledger.parts) - 1)
                added_j[len(ledger.parts) - 1] = j
            else:
                info["dropped"].append(_label(n) or n.get("type"))
            continue
        p = ledger.parts[i]
        o = p["goal"]
        for s in slots_of(o):
            if s in p["settled"]:
                continue
            ov, nv = _group_value(o, s), _group_value(n, s)
            if ov == nv and (s != "object" or (o.get("said") or "") == (n.get("said") or "")):
                continue
            if not ov and nv and s != "object":           # a gap filled (a source the robot knows, a destination she gave): no drift
                _set_group(o, s, n)
                if s == "to" and o.get("type") in MOVE_KINDS and n.get("type") in MOVE_KINDS:
                    o["type"] = n["type"]
                info["changed"].append((i, s))
                continue
            sn, so = slot_score(n, s, new_texts, places, lang), slot_score(o, s, new_texts, places, lang)
            answered = sup_all or (i, s) in set(supersede)
            # her answer to a question about this part narrows the object (mug -> black mug): the narrower reading stands
            # even when her short answer ("the black one") does not repeat the noun
            narrower = s == "object" and answered and refines(o, n) and sn >= so
            if sn > so or narrower:
                before = slot_score(o, s, earlier_texts, places, lang)
                # a message ("say") is her words, not a name: her latest statement of it wins, no choice between two texts
                if before > 0 and not answered and not ledger.conflict_asked and not (s == "object" and o.get("type") == "say") \
                        and not (s == "object" and refines(o, n)):
                    info["conflicts"].append({"part": i, "slot": s, "old": ov, "new": nv, "goal_old": dict(o), "goal_new": dict(n)})
                    continue
                _set_group(o, s, n)
                if s == "to" and o.get("type") in MOVE_KINDS and n.get("type") in MOVE_KINDS:
                    o["type"] = n["type"]                  # on / in follows the destination
                info["changed"].append((i, s))
            else:
                info["kept"].append((i, s))
    info["remap"] = _clean_up(ledger, match, n_old, sup_all, all_texts, new_texts, places, lang, added_j, info)
    return ledger.goals(), info


def _clean_up(ledger, match, n_old, sup_all, all_texts, new_texts, places, lang, added_j, info) -> dict:
    """The last step of `merge`: drop the old parts the reading no longer has (unless kept), then put the parts in the order of
    the new reading. Returns ``remap`` (old index -> index after the clean-up)."""
    # a part the reading no longer has: kept when settled, or when the first reading had it and her words anchor it
    # (the model forgot it); else the reading's drop stands. ``remap``: old index -> index after the clean-up.
    seen = set(match.values())
    keep, remap = [], {}
    held = [ledger.parts[i]["goal"] for i in seen]

    def twin(g):                       # the reading folded this part into a part it kept (one object, one destination), or a
        #                                "go" part leads to a place another kept part goes to anyway
        return any(_family(h) == _family(g) and (_label(h) == _label(g) or (g.get("type") == "say" and h.get("to") == g.get("to")))
                   or (g.get("type") == "go" and g.get("to") and g.get("to") in (h.get("to"), h.get("from"))) for h in held)
    for i, p in enumerate(ledger.parts):
        gone = i < n_old and i not in seen
        # a part of the first reading that a later reading leaves out stays, unless she refused and restated the task
        # ("*": then the drop stands); a part her NEW words anchor stays unless she refused it (a corrected part the reading
        # lost); a reading after an answer to one question often holds only what the answer was about (the model forgot the rest)
        first_kept = (p["first"] and not sup_all) or (
            goal_anchored(p["goal"], new_texts or all_texts, places, lang) and p["status"] != REFUSED)
        if gone and (twin(p["goal"]) or not (p["status"] == SETTLED or first_kept)):
            info["dropped"].append(_label(p["goal"]) or p["goal"].get("type"))
            continue
        keep.append((i, p))
    # the order of the new reading: a matched or added part takes its index there; a kept part the reading does not have
    # goes just after its nearest earlier old part
    at_new = {i: j for j, i in match.items()}
    at_new.update(added_j)
    last, key = None, {}
    for i, _p in enumerate(ledger.parts):
        if i in at_new:
            key[i] = (at_new[i], 0, i)
            if i < n_old:
                last = at_new[i]
        else:
            key[i] = ((-1 if last is None else last) + 0.5, 1, i)
    keep.sort(key=lambda ip: key[ip[0]])
    for pos, (i, _p) in enumerate(keep):
        remap[i] = pos
    ledger.parts = [p for _i, p in keep]
    return remap


# ── a content correction edits the part it names ──────────────────────────────────────────────────────────────────────
def other_parts_named(goal, texts, lang, others=()) -> list:
    """Positions in ``others`` (goals) of the move parts whose object her ``texts`` name, when they are about another part and
    not about ``goal``: a part with no object of its own (a "go") or whose object she does not name."""
    t = [x for x in texts if x]
    if goal.get("type") in MOVE_KINDS and object_score(goal, t, lang) > 0:
        return []
    return [k for k, o in enumerate(others) if o.get("type") in MOVE_KINDS and object_score(o, t, lang) > 0]


def names_other_part(goal, texts, lang, others=()) -> bool:
    return bool(other_parts_named(goal, texts, lang, others))


def agrees_with(goal, texts, places, lang) -> bool:
    """Do her ``texts`` agree with the part ``goal``? They name its object and no place or person it does not have."""
    t = [x for x in texts if x]
    if object_score(goal, t, lang) == 0:
        return False
    keys = named_keys(t, places) - {"@speaker"}
    own = {goal.get(s) for s in ("to", "from", "object")} | {persons.speaker_key(places)}
    if "@speaker" in named_keys(t, places) and goal.get("to") not in ("me", "user", "@speaker", persons.speaker_key(places)):
        return False
    return not (keys - own)


def content_edit(goal, texts, places, lang, others=()) -> dict:
    """The change a content "no" asks for in ONE part (the part the caller read back): ``{slot: value}``. Destination or
    recipient: the one ENV key (place or person) her words name that the part does not have; open / close: the container.
    {} when the words name no single new value (the object is then left to the re-reading and its evidence rule), or when
    they name the object of another part of the reading (``others``) and not this part's: the correction is about that part.
    A person is never the destination of an ``in`` goal."""
    t = [x for x in texts if x]
    kind = goal.get("type")
    if names_other_part(goal, t, lang, others):
        return {}                       # (a part with no object of its own, e.g. "go", is never edited by words about another)
    keys = named_keys(t, places)
    sk = persons.speaker_key(places)
    named = {k for k in keys if k in places.env} - {"@speaker"}
    if kind in ("open", "close"):
        news = {k for k in named if k != goal.get("object") and not persons.is_person(places, k)}
        return {"object": next(iter(news))} if len(news) == 1 and goal.get("object") not in named else {}
    if kind not in ("on", "in", "go", "deliver", "say"):
        return {}
    cur = goal.get("to")
    dests = {k for k in named if k != cur and k != goal.get("from")}
    if kind == "in":
        dests = {k for k in dests if not persons.is_person(places, k)}
    if "@speaker" in keys and sk and cur not in (sk, "me") and not dests and kind != "in":
        return {"to": "@speaker"}
    if len(dests) == 1 and "@speaker" not in keys:
        return {"to": next(iter(dests))}
    return {}


def apply_edit(goal, edit) -> dict:
    g = dict(goal)
    for slot, v in edit.items():
        if slot == "object":
            g["object"] = v
        elif slot == "to":
            for k in GROUPS["to"]:
                g.pop(k, None)
            g["to"] = "me" if v == "@speaker" else v
    return g


# ── questions that name both values ───────────────────────────────────────────────────────────────────────────────────
def conflict_question(places, slot, old, new, goal, max_options=DEFAULT_MAX_OPTIONS):
    """ONE two-way question that names both values (existing templates only)."""
    from . import speech
    opts = cap_options([old, new], max_options)
    if slot == "object":
        names = [str(o).replace("_", " ") for o in opts]
        return speech.LocalText("q_which_x", {"what": "one", "options": names},
                                text=f"Which one do you mean: {' or '.join(names)}?",
                                ask={"intent": "choose", "options": names, "about": ""})
    nice = [speech.say_name(places, k, "en") for k in opts]
    return speech.LocalText("q_which_place", {"options": list(opts)}, text=f"Which place do you mean: {' or '.join(nice)}?",
                            ask={"intent": "choose_place", "options": list(opts), "about": ""})


def open_question(goal, slot, places):
    """The EXISTING open question for the slot of a part (no new phrase): what to bring / which container / what to say /
    where to put it / where to take it / where to go."""
    from . import speech
    L, kind = speech.LocalText, goal.get("type")
    nm = goal.get("said") or goal.get("object") or ""
    if slot == "object":
        if kind in ("open", "close"):
            return L("q_which_drawer", {"verb": kind}, text=f"Which drawer should I {kind}?")
        if kind == "say":
            return L("q_what_say")
        return L("q_what_bring")
    if kind == "go":
        return L("q_where_go")
    if kind in ("on", "in") and nm and not persons.is_person(places, goal.get("to")):
        return L("q_where_put", {"object": nm})
    return L("q_where_take")


# What a core question template asks about (the slot her answer settles). The templates that ask her to say the task again
# (q_repeat, q_what_do, q_what_exactly, q_unclear_task, q_mentioned, q_which_part, q_silence) ask about no slot: her answer is a
# restatement and can conflict with what she said before. A read-back (q_confirm) settles nothing here: a yes settles every
# part, a "no, <words>" goes through the correction path (which marks what her words supersede). A free question written by
# the model asks about the whole reading; so does a choice between named options (q_which_x, q_which_place: the options may be
# objects, sources or destinations) and a template not listed (fail open toward her latest words).
ASKS_SLOT = {"q_what_bring": "object", "q_which_x": "*", "q_which_one": "object", "q_which_known": "object",
             "q_did_you_mean": "object", "q_which_drawer": "object", "q_what_say": "object", "q_which_reading": "*",
             "q_where_put": "to", "q_which_place": "*", "q_where_take": "to", "q_where_go": "to",
             "q_where_are_you": "to", "q_where_is_person": "to", "q_where_is": "from"}
RESTATE_KEYS = frozenset({"q_repeat", "q_what_do", "q_what_exactly", "q_unclear_task", "q_mentioned", "q_which_part",
                          "q_silence", "q_confirm", "q_confirm_restated", "q_coverage"})


def asked_slots(q) -> set:
    """The slots the question ``q`` asks about: {"object"}, {"to"}, ..., {"*"} (the whole reading) or set() (a request to say
    the task again)."""
    key = getattr(q, "key", "") or ""
    if key == "_join":
        out = set()
        for p in (getattr(q, "parts", ()) or ()):
            out |= asked_slots(p)
        return out
    if key in RESTATE_KEYS:
        return set()
    return {ASKS_SLOT.get(key, "*")}


def question_key(q) -> tuple:
    """(template, slots) of a question: what the no-repeat gate compares."""
    key = getattr(q, "key", "") or ""
    if key == "_join" or not key:
        parts = getattr(q, "parts", ()) or ()
        return ("_join", tuple(question_key(p) for p in parts)) if parts else ("", str(q))
    return (key, tuple(sorted((str(k), str(v)) for k, v in (getattr(q, "slots", None) or {}).items())))


# ── the correction of a whole read-back: object, recipient, open / close (extends dialog_converge.correction_edit) ────────
def _is_recipient(places, v) -> bool:
    return bool(v) and (persons.is_person(places, v) or persons.is_speaker_word(v))


def extended_edit(goals, texts, places, lang) -> list:
    """``[(i, slot, value)]``: what a short correction asks for beyond ``dialog_converge.correction_edit`` (which sends a named
    object to a new destination). Only when her words name no object of the reading:

    * recipient: one person (or the speaker) named, exactly one goal that hands something to a person: its ``to``;
    * open / close: one container named that is no source or destination of another goal, exactly one open / close goal:
      its ``object``.

    [] when the words name nothing single (the old path stands)."""
    t = [x for x in texts if x]
    if not t or any(g.get("type") in ("on", "in") and object_score(g, t, lang) > 0 for g in goals):
        return []
    keys = named_keys(t, places)
    sk = persons.speaker_key(places)
    out = []
    people = {k for k in keys if k in places.env and persons.is_person(places, k)}
    if "@speaker" in keys and sk:
        people.discard(sk)
        people.add("@speaker")
    hand = [i for i, g in enumerate(goals) if g.get("type") in ("on", "in", "deliver") and _is_recipient(places, g.get("to"))]
    if len(hand) == 1 and len(people) == 1:
        want = next(iter(people))
        cur = goals[hand[0]].get("to")
        same = cur == want or (want == "@speaker" and (cur in ("me", "user") or cur == sk))
        if not same:
            out.append((hand[0], "to", want))
    oc = [i for i, g in enumerate(goals) if g.get("type") in ("open", "close")]
    if len(oc) == 1:
        used = {g.get(s) for g in goals for s in ("to", "from")}
        cont = {k for k in keys if k in places.env and not persons.is_person(places, k) and k not in used
                and k != goals[oc[0]].get("object")}
        if len(cont) == 1 and not people:
            out.append((oc[0], "object", next(iter(cont))))
    return out if len(out) == 1 else []


# ── the object span of an answer (option noun_relabel) ────────────────────────────────────────────────────────────────────
def _strip_particles(tok, parts, chain=3) -> str:
    for _ in range(chain):
        for p in parts:
            if tok.endswith(p) and len(tok) > len(p):
                tok = tok[:-len(p)]
                break
        else:
            break
    return tok


def object_span(text, places, lang) -> Optional[str]:
    """The ONE own-script word of ``text`` (case particles taken off) that names no ENV place or person and says neither
    yes, no nor "not sure" (the pack's phrases): verbatim in ``text``. None when there is none or more than one, or the
    language declares no script or no particles (fail open)."""
    letters = langdata.block(lang, "script").get("letters")
    parts = sorted((str(x) for x in (langdata.grammar(lang, "noun_particles", []) or []) if x), key=len, reverse=True)
    if not letters or not parts:
        return None
    dlg = langdata.block(lang, "dialogue")
    stop = [str(x) for k in ("yes", "no", "fillers") for x in (dlg.get(k) or []) if isinstance(x, str) and len(x) >= 2]
    stop += [str(x) for x in (langdata.get(lang).get("dialogue_ext") or {}).get("answer_unsure", []) if isinstance(x, str)]
    out = []
    for tok in re.findall(letters + "+", str(text or "")):
        if any(w in tok for w in stop) or named_keys([tok], places):
            continue
        base = _strip_particles(tok, parts)
        if base and not named_keys([base], places):
            out.append(base)
    return out[0] if len(set(out)) == 1 and out[0] in str(text) else None
