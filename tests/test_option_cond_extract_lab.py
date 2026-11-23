"""Option ``cond_extract``: its lab entry carries the condition's ``present`` (and ``kind`` with key ``kinds``), so a flip of the polarity
by the extraction (a presence condition read as absent: the if branch dropped) shows in the events. Made-up requests (an urn, a hall)."""
from test_option_cond_schema import KINDS, answer, go, make

BASE = ("state", "object", "at", "if", "else", "drop")


def test_cond_extract_lab_carries_present():
    for present in (True, False):
        opt, run, log = make(answer(present=present))
        go(opt, run)
        assert log.labs[-1]["state"] == "ready" and log.labs[-1]["present"] is present and "kind" not in log.labs[-1]


def test_cond_extract_lab_carries_present_and_kind_with_key_kinds():
    opt, run, log = make(answer(present=False), **KINDS)
    go(opt, run)
    assert log.labs[-1]["present"] is False and log.labs[-1]["kind"] == "presence"


def test_cond_extract_lab_off_no_condition_read_no_entry_and_empty_no_present_on_a_none_plan():
    opt, run, log = make(answer(), task="put the urn on the shelf")             # no condition word: no call, no entry (_off)
    go(opt, run)
    assert log.labs == [] and log.calls == []
    opt, run, log = make({"conditional": False})                                  # nothing read: the entry as before (_empty)
    go(opt, run)
    assert set(log.labs[-1]) == set(BASE)


def test_cond_extract_lab_regression_a_flipped_presence_condition_is_visible():
    opt, run, log = make(answer(present=False))                                   # "if the urn is in the hall ..." read as absent
    go(opt, run)
    assert run.goals[0]["when"]["present"] is False and log.labs[-1]["present"] is False
