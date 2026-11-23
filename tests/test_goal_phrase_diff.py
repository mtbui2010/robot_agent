"""Step S08 chose the read-back phrase by goal kind: ``AssistLoop.summary`` and ``AssistLoop._goal_phrase`` (still static
names on the class) now ask the ``phrase`` of the rules object each kind names in the pack
(robot_agent/core/pack/goal_semantics.py ``phrase``; the household bodies: robot_agent/packs/household/callables/goals.py).
These tests prove nothing changed for the standard pack: (1) the standard pack has a ``phrase`` for each of the seven
household kinds and none for any other kind; (2) a differential grid: the new methods and the reference copy of the old ones
(tests/legacy_ref/goal_phrase.py) give the same text AND the same key, slots, parts and type, or raise the same exception,
over made-up goals (every kind, with and without the optional fields, persons, speakers, kinds no pack declares) and
places (None, or an ENV); (3) an unknown kind keeps the plain text "<kind> the <object>"; (4) opt-in: a pack's own rules
object decides the phrase of its kind, and a kind without one reads as plain text."""
import itertools

import pytest

from legacy_ref.goal_phrase import Old
from robot_agent.core.pack import Registry, default_pack
from robot_agent.core.pack import goal_semantics as gs
from robot_agent.core.pack.loader import Pack
from robot_agent.core.planning import speech
from robot_agent.core.planning.assist_loop import AssistLoop, Places

DR, DT, CT, P, ME = "drawer@kitchen", "dining table@kitchen", "counter@kitchen", "auntie", "spot by you"
ENV = {DT: {"surface": True}, CT: {"surface": True}, DR: {"openable": True}, P: {"person": "p1"},
       ME: {"person": "p2", "speaker": True}, "grandpa": {"person": "p3"}}


def G(t, o="", to="", **kw):
    return dict({"type": t, "object": o, "from": "", "to": to}, **kw)


def shape(x):
    """Everything a LocalText carries (a plain text is just its type and value)."""
    if isinstance(x, speech.LocalText):
        return ("LocalText", str(x), x.key, sorted(x.slots.items(), key=repr), tuple(shape(p) for p in x.parts), x.ask)
    return (type(x).__name__, x)


def outcome(fn, *a):
    try:
        return ("value", shape(fn(*a)))
    except Exception as e:                     # noqa: BLE001
        return ("raises", type(e).__name__)


SINGLE = [
    G("on", "mug", CT), G("on", "mug", CT, said="cup", name_local="tasse"), G("on", "mug", CT, name_local="tasse"),
    G("on", "mug", CT, **{"from": DT}), G("on", "mug", CT, **{"from": P}), G("on", "mug", P), G("on", "mug", P, hand=True),
    G("on", "mug", ME), G("on", "mug", ME, hand=True), G("on", "mug", "grandpa", hand=True),
    G("on", "mug", CT, near_person=P), G("on", "mug", CT, near_person=ME), G("on", "mug", P, near_person=ME),
    G("on", "", CT), {"type": "on", "object": "mug"}, {"type": "on"},
    G("in", "mug", DR), G("in", "mug", DR, said="cup"), G("in", "", DR), {"type": "in", "object": "mug"},
    G("open", DR), G("close", DR), G("open", ""), {"type": "close"}, G("open", "drawer"),
    G("go", to=CT), G("go", to=P), G("go"), {"type": "go"},
    G("say", "hello", CT), G("say", "hello", P), G("say", "hello", ME), G("say", "hello"), G("say", "hello", person="auntie"),
    G("say", "", CT), G("say", "hi \"you\"", P), {"type": "say", "object": "x"},
    G("deliver", "mug", CT), G("deliver", "", CT), G("deliver", "mug", P), G("deliver"),
    G("polish", "apple", DT), G("polish"), G("give", "mug", P), {"type": "swim", "object": "pool@yard"}, {"type": None},
    {"object": "mug"}, {},
]
PLACES = {"none": None, "env": Places(ENV)}


def _nice(places):
    return (lambda k: speech.say_name(places, k, "en")) if places is not None else (lambda k: (k or "").replace("@", " in the "))


# ── (1) the data ──
def test_the_standard_pack_has_a_phrase_for_each_household_kind_and_for_no_other():
    p = default_pack()
    kinds = ["on", "in", "open", "close", "go", "say", "deliver"]
    assert list(p.table("goal_kinds")) == kinds
    assert all(callable(getattr(gs.rules_of(p, k), "phrase", None)) for k in kinds)
    assert all(gs.rules_of(p, k) is None for k in ["give", "polish", "", None, 3])


# ── (2) the differential grid ──
@pytest.mark.parametrize("pname", list(PLACES))
def test_goal_phrase_equals_the_old_code(pname):
    places = PLACES[pname]
    for g in SINGLE:
        for nice in (_nice(places), lambda k: f"<{k}>"):
            assert outcome(AssistLoop._goal_phrase, g, places, nice) == outcome(Old._goal_phrase, g, places, nice), (pname, g)


@pytest.mark.parametrize("pname", list(PLACES))
def test_summary_equals_the_old_code(pname):
    places = PLACES[pname]
    lists = [[]] + [[g] for g in SINGLE] + [list(c) for c in itertools.combinations(SINGLE[:40:3], 2)] + [SINGLE[:12]]
    for goals in lists:
        assert outcome(AssistLoop.summary, goals, places) == outcome(Old.summary, goals, places), (pname, goals)
    for goals in lists[:60]:
        assert outcome(AssistLoop.summary, goals) == outcome(Old.summary, goals), goals          # places defaults to None


# ── (3) an unknown kind ──
def test_an_unknown_kind_reads_as_the_plain_text_kind_the_object():
    nice = _nice(PLACES["env"])
    out = AssistLoop._goal_phrase(G("polish", DT), PLACES["env"], nice)
    assert type(out) is str and out == "polish the dining table in the kitchen"
    assert AssistLoop._goal_phrase(G("polish"), None, _nice(None)) == "polish the "


# ── (4) opt-in: the pack's own rules object decides ──
class NoPhrase:
    """A made-up kind's rules with the three questions every ``goal.rules`` object has and no ``phrase``."""

    def reached(self, goal, steps, world, ctx):
        return True

    def holds(self, goal, belief, ctx):
        return True

    def missing(self, goal, steps, world, ctx):
        return None


class Install(NoPhrase):
    """A made-up kind's rules: it phrases its goal with a key of its own."""

    def phrase(self, g, places, nice):
        return speech.LocalText("rb_install", {"object": g["object"], "place": g["to"]}, text=f"install the {g['object']}")


def _robot_pack(drop=()):
    reg, std = Registry(), default_pack()
    reg.register("goal.rules", "line.install", Install(), module="line_callables")
    reg.register("goal.rules", "line.bare", NoPhrase(), module="line_callables")
    for k in std.table("goal_kinds"):
        reg.register("goal.rules", f"std.{k}", gs.rules_of(std, k), module="std_callables")
    kinds = {k: e for k, e in std.table("goal_kinds").items() if k not in drop}
    kinds.update(install={"rules": "line.install"}, weld={"rules": "line.bare"})
    return Pack(schema=2, name="line", tables={"goal_kinds": kinds}, registry=reg)


def test_a_pack_s_rules_object_phrases_its_kind_and_a_kind_without_phrase_is_plain_text():
    pack = _robot_pack(drop=("in",))
    out = gs.phrase(G("install", "pipe", "clips"), None, _nice(None), pack=pack)
    assert shape(out) == shape(speech.LocalText("rb_install", {"object": "pipe", "place": "clips"}, text="install the pipe"))
    assert gs.phrase(G("weld", "seam"), None, _nice(None), pack=pack) == "weld the seam"
    assert gs.phrase(G("in", "mug", DR), None, _nice(None), pack=pack) == "in the mug"        # a removed kind: plain text
    assert shape(gs.phrase(G("open", DR), None, _nice(None), pack=pack)) == shape(AssistLoop._goal_phrase(G("open", DR), None, _nice(None)))
