"""ASR2: an unclear answer to a yes / no question is asked again, a clear correction stays a correction."""

import pytest

from robot_agent.interact.dialog import Reply, ask_yes_no, read_yes_no, yes_no


@pytest.mark.parametrize("text, kind", [
    ("yes", "yes"), ("Yeah, go ahead", "yes"), ("네", "yes"),
    ("no", "no"), ("no, the sofa", "no"), ("아니요", "no"),
    ("yet", "unclear"), ("yea", "unclear"), ("yess", "unclear"), ("uh hmm", "unclear"), ("now", "unclear"),
    ("...", "unclear"), ("[noise]", "unclear"), ("내", "unclear"), ("네에", "unclear"), ("음", "unclear"),
    ("the one in the restroom", "correction"), ("on the kitchen counter", "correction"), ("sofa", "correction"),
    ("화장실에 있는 거", "correction"),
    ("", "silence"), ("   ", "silence"),
])
def test_read_yes_no(text, kind):
    assert read_yes_no(text).kind == kind


def test_reply_keeps_the_old_verdict():
    for t in ("yes", "no, the sofa", "the one in the restroom", "yet", ""):
        ok, _rest = yes_no(t)
        assert read_yes_no(t).ok == ok
    assert read_yes_no("no, the sofa").rest == "the sofa"
    assert read_yes_no("The restroom one").rest == "The restroom one"


def test_unclear_is_asked_again_once():
    asked, replies = [], iter(["yet", "yes"])
    ans, rep, n = ask_yes_no(lambda q: asked.append(q) or next(replies), "Is that right?", "Yes or no?")
    assert (ans, rep, n) == ("yes", Reply("yes"), 2) and asked == ["Is that right?", "Yes or no?"]


def test_still_unclear_is_returned_to_the_caller():
    asked = []
    ans, rep, n = ask_yes_no(lambda q: asked.append(q) or "yet", "Is that right?", "Yes or no?", reasks=1)
    assert rep.kind == "unclear" and n == 2 and len(asked) == 2


def test_correction_and_silence_are_not_reasked():
    for reply, kind in (("the sofa", "correction"), ("", "silence"), ("no", "no")):
        asked = []
        _a, rep, n = ask_yes_no(lambda q: asked.append(q) or reply, "Is that right?", "Yes or no?")
        assert rep.kind == kind and n == 1 and asked == ["Is that right?"]


def test_reasks_zero_and_stop():
    _a, rep, n = ask_yes_no(lambda q: "yet", "Q?", "Yes or no?", reasks=0)
    assert rep.kind == "unclear" and n == 1
    _a, rep, n = ask_yes_no(lambda q: "yet", "Q?", "Yes or no?", stopped=lambda: True)
    assert n == 1


def test_run_control_shim():
    from robot_agent.core import run_control
    ans, rep, n = run_control.ask_yes_no("Q?", lambda q: "yes" if q == "again" else "yet", "again")
    assert ans == "yes" and rep.kind == "yes" and n == 2
