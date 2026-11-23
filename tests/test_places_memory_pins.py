"""The name words, the place vocabulary and the object memory of the loop keep today's answers (review of S01, 2026-11-23: no
cassette caught a change of the one-word fallback, of the "my / your" article or of the eight-answer window). Step S03 moves
``words``, ``Places`` and ``Memory`` out of assist_loop.py: these rows are its proof together with a differential test against
the reference copy in tests/legacy_ref, not the cassettes. Read through ``assist_loop`` (S03 keeps the names exported there)."""

import pytest

from robot_agent.core.planning import assist_loop as AL

ENV = {"dining table@kitchen": {"surface": True}, "desk@study room": {"surface": True},
       "shelf@kitchen": {"surface": True}, "shelf@restroom": {"surface": True, "aliases": ["bathroom shelf"]},
       "drawer@kitchen": {"openable": True}}


@pytest.mark.parametrize("name, want", [("TennisBall", "tennis ball"), ("tennis_ball", "tennis ball"), (" Tennis  ball ", "tennis ball"),
                                        ("coffee-mug", "coffee mug"), ("HTTPServer", "http server"), (None, "")])
def test_words_splits_case_underscores_and_dashes(name, want):
    assert AL.words(name) == want


@pytest.mark.parametrize("said, want", [("the cup", "cup"), ("a cup", "cup"), ("an apple", "apple"), ("my desk", "desk"),
                                        ("your cup", "cup"), ("myself", "myself"), ("the the cup", "the cup"), ("", "")])
def test_one_leading_article_or_possessive_is_dropped(said, want):
    assert AL._strip_article(said) == want


@pytest.mark.parametrize("name, hint, want", [
    ("dining table", "", ["dining table@kitchen"]),
    ("the dining table", "", ["dining table@kitchen"]),
    ("my desk", "", ["desk@study room"]),
    ("round table", "", ["dining table@kitchen"]),                    # one word of the name said: the one-word fallback
    ("shelf", "", ["shelf@kitchen", "shelf@restroom"]),
    ("shelf", "the shelf in the restroom", ["shelf@restroom"]),
    ("kitchen", "", ["@kitchen"]),
    ("garage", "", []),
])
def test_places_candidates_keep_their_answers(name, hint, want):
    assert AL.Places(ENV).candidates(name, hint) == want


def test_places_ground_needs_one_place_and_not_a_room():
    p = AL.Places(ENV)
    assert p.ground("round table") == "dining table@kitchen"
    assert p.ground("shelf") is None and p.ground("kitchen") is None


def test_memory_shows_the_last_eight_answers_and_keeps_thirty(tmp_path):
    m = AL.Memory(tmp_path / "memory.json", clock=lambda: 1.0)
    for i in range(9):
        m.remember_answer(f"task {i}", f"q{i}", f"a{i}")
    lines = m.answers_text().splitlines()
    assert len(lines) == 8 and lines[0] == "- task 'task 1': asked 'q1' -> 'a1'" and "task 0" not in m.answers_text()
    for i in range(9, 40):
        m.remember_answer(f"task {i}", f"q{i}", f"a{i}")
    assert len(m.data["answers"]) == 30 and m.data["answers"][0]["task"] == "task 10"
    assert AL.Memory(tmp_path / "memory.json").data["answers"] == m.data["answers"]


def test_memory_remembers_where_an_object_was_seen_by_its_words(tmp_path):
    m = AL.Memory(tmp_path / "memory.json", clock=lambda: 2.0)
    m.see("TennisBall", "desk@study room", "found")
    assert m.where("tennis ball") == "desk@study room" and m.where("tennis_ball") == "desk@study room"
    assert m.objects_text() == "- tennis ball: desk@study room"
    m.remember_answer("t", "q", "")
    assert m.answers_text() == "(none)"
