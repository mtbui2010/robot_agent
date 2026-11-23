"""Step S03 moved ``words``, ``_strip_article``, ``Places`` and ``Memory`` verbatim from assist_loop.py to places.py. These
tests prove the move changed nothing: the source of every moved function equals the reference copy (tests/legacy_ref/places.py,
the old code), the new and the old code give the same answers over a table of made-up names, hints and ENVs (a one-word
match, a name with "my", a room, an alias, a hint, nine remembered answers), and assist_loop still exports the same objects."""
import inspect
import json
import types

import pytest

from legacy_ref import places as old
from robot_agent.core.planning import assist_loop as AL
from robot_agent.core.planning import places as new
from robot_agent.core.planning.phases import PHASE_MIXINS

ENVS = {
    "house": {"dining table@kitchen": {"surface": True}, "desk@study room": {"surface": True},
              "shelf@kitchen": {"surface": True}, "shelf@restroom": {"surface": True, "aliases": ["bathroom shelf"]},
              "drawer@kitchen": {"openable": True}, "wine fridge@kitchen": {"openable": True},
              "fridge@kitchen": {"openable": True}, "round table": {"surface": True, "aliases": ["kitchen table"]},
              "counter": {"surface": True, "aliases": ["식탁"]}, "not a place": "x"},
    "tool crib": {"bench@tool_crib": {}, "rack@tool_crib": {"aliases": ["parts rack"]}, "dock": {}},
    "empty": {},
}
NAMES = ["dining table", "the dining table", "my desk", "your desk", "round table", "table", "shelf", "kitchen shelf",
         "shelf in the restroom", "bathroom shelf", "kitchen", "restroom", "garage", "", None, "fridge", "the wine fridge",
         "in the wine fridge, please", "It is on the kitchen counter.", "kitchen table", "TennisBall", "drawer",
         "식탁 위에 있어요", "tool crib", "bench", "parts rack", "the rack in the tool crib", "dock",
         "@kitchen", "study room", "Desk", "shelf@restroom", "a shelf", "an apple"]
HINTS = ["", "the shelf in the restroom", "put it on the kitchen shelf", "bathroom shelf please", "the kitchen drawer",
         "restroom", "kitchen's shelf"]


class Ext:
    """A made-up option extension (dialog_agree2): one room word of another language."""
    rooms = {"cocina": "kitchen"}

    @staticmethod
    def rewrite_rooms(w):
        return w.replace("cocina", "kitchen")


# ── the code is the same code ──
@pytest.mark.parametrize("name", ["words", "_strip_article", "Places", "Memory"])
def test_the_moved_source_equals_the_reference_copy(name):
    assert inspect.getsource(getattr(new, name)) == inspect.getsource(getattr(old, name))


def test_assist_loop_exports_the_moved_objects():
    assert (AL.words, AL._strip_article, AL.Places, AL.Memory) == (new.words, new._strip_article, new.Places, new.Memory)
    assert new.strip_article is new._strip_article


def test_the_class_line_puts_the_phase_mixins_first_and_keeps_the_old_bases():
    from robot_agent.core.planning.loop import ClosedLoop
    from robot_agent.core.planning.search_hooks import SearchHooks
    from robot_agent.core.planning.step_ladder import StepLadder
    from robot_agent.core.planning.which_one import WhichOne
    assert AL.AssistLoop.__bases__ == tuple(PHASE_MIXINS) + (WhichOne, SearchHooks, StepLadder, ClosedLoop)


# ── the same answers ──
@pytest.mark.parametrize("name", NAMES + ["coffee-mug", "HTTPServer", " Tennis  ball ", "the the cup", "myself"])
def test_words_and_the_article_give_the_old_answers(name):
    assert new.words(name) == old.words(name)
    assert new._strip_article(new.words(name)) == old._strip_article(old.words(name))


def _both(env, ext=False, openable=None):
    a, b = new.Places(env, openable), old.Places(env, openable)
    if ext:
        a.ext, b.ext = Ext(), Ext()
    return a, b


@pytest.mark.parametrize("env", list(ENVS))
@pytest.mark.parametrize("ext", [False, True])
def test_places_candidates_and_ground_give_the_old_answers(env, ext):
    a, b = _both(ENVS[env], ext)
    for name in NAMES + (["cocina", "shelf in the cocina"] if ext else []):
        for hint in HINTS:
            assert a.candidates(name, hint) == b.candidates(name, hint), (name, hint)
            assert a.ground(name, hint) == b.ground(name, hint), (name, hint)


@pytest.mark.parametrize("env", list(ENVS))
def test_places_tables_and_text_checks_give_the_old_answers(env):
    rule = lambda key, entry: "fridge" in key            # noqa: E731 - a made-up robot rule
    for opener in (None, rule):
        a, b = _both(ENVS[env], openable=opener)
        assert (a.env, a.keys, a.rooms(), a.describe()) == (b.env, b.keys, b.rooms(), b.describe())
        for k in a.keys:
            assert (a.head(k), a.room(k), a.openable(k), a.in_room(a.room(k))) == (b.head(k), b.room(k), b.openable(k),
                                                                                   b.in_room(b.room(k)))
            for text in NAMES + HINTS:
                assert a.said(k, text or "") == b.said(k, text or ""), (k, text)
                assert a.room_said(k, text or "") == b.room_said(k, text or ""), (k, text)
            assert a._alias_hits(k) == b._alias_hits(k)


def _drive(cls, path):
    m = cls(path, clock=iter(range(100)).__next__)
    trace = [m.objects_text(), m.answers_text(), m.where("cup")]
    m.see("", "desk", "found")
    m.see("TennisBall", "desk@study room", "found")
    m.see("red_cup", None, "lost")
    for i in range(9):                                   # nine answers: the prompt shows the last eight
        m.remember_answer(f"task {i}", f"q{i}", f"a{i}")
    m.remember_answer("task x", "q", "")
    trace += [m.objects_text(), m.answers_text(), m.where("tennis ball"), m.where("Red Cup"), m.where("chair")]
    for i in range(9, 40):
        m.remember_answer(f"task {i}", f"q{i}", f"a{i}")
    trace += [m.answers_text(), json.loads(path.read_text()), cls(path).data]
    return trace


def test_memory_gives_the_old_texts_and_writes_the_old_file(tmp_path):
    assert _drive(new.Memory, tmp_path / "new" / "m.json") == _drive(old.Memory, tmp_path / "old" / "m.json")


def test_memory_reads_a_broken_file_as_empty_like_the_old_code(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    assert new.Memory(p).data == old.Memory(p).data == {"objects": {}, "answers": []}


def test_memory_that_cannot_save_keeps_going_like_the_old_code(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    for cls in (new.Memory, old.Memory):
        m = cls(blocker / "sub" / "m.json", clock=lambda: 1.0)
        m.see("cup", "desk", "found")
        assert m.where("cup") == "desk"


def test_places_drops_entries_that_are_not_places_like_the_old_code():
    env = {"a": {}, "b": 3, "c": None, "d": types.SimpleNamespace()}
    assert new.Places(env).keys == old.Places(env).keys == ["a"]
