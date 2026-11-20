"""``core/planning/word_match``: the loose word match shared by options ``heard_check`` and ``cond_extract`` (key ``heard_match``).

The functions were moved out of ``heard_check`` unchanged; the tables below were computed with the moved-out bodies (heard_check before the move) and
pin that heard_check's behaviour is the same. Made-up words only."""
import pytest

from robot_agent.core.planning import word_match as W
from robot_agent.options import heard_check as H


def test_heard_check_uses_the_shared_helpers_no_copy():
    assert (H.tokens, H.main_script, H.limits, H.token_found) == (W.tokens, W.main_script, W.limits, W.token_found)


@pytest.mark.parametrize("text, toks, script", [
    ("Put the Vumpa on the zorbik!", ["put", "the", "vumpa", "on", "the", "zorbik"], "LATIN"),
    ("", [], ""), ("12 !", ["12"], ""), ("카푸 를 갖다 줘", ["카푸", "를", "갖다", "줘"], "HANGUL"),
    ("TV вумпа", ["tv", "вумпа"], "CYRILLIC"), ("ka-vumpa lu_zorbik", ["ka", "vumpa", "lu_zorbik"], "LATIN")])
def test_tokens_and_main_script_as_before(text, toks, script):
    assert W.tokens(text) == toks and W.main_script(text) == script


@pytest.mark.parametrize("data, lim", [
    (None, None), ({}, None), ({"min_prefix": 2, "min_ratio": 0.5}, None), ({"min_prefix": 2, "min_ratio": 0.5, "assumed": True}, (2, 0.5)),
    ({"min_prefix": "x", "min_ratio": 0.5, "source": "s"}, None), ({"min_ratio": 0.5, "assumed": True}, None)])
def test_limits_empty_or_unlabelled_data_is_none_as_before(data, lim):
    assert W.limits(data) == lim


@pytest.mark.parametrize("q, heard, p, r, found", [
    ("vumpa", ["vumpo"], 3, 0.8, True), ("vumpa", ["vu"], 3, 0.8, False), ("zo", ["zorbik"], 3, 0.8, True),
    ("kaz", ["lip", "kab"], 3, 0.6, True), ("kaz", ["lip", "kab"], 3, 0.7, False), ("x", ["y"], 0, 0.5, False), ("", ["a"], 2, 0.5, True),
    ("카푸", ["카푸를"], 2, 0.5, True), ("카푸", ["타푸"], 2, 0.5, True)])
def test_token_found_as_before(q, heard, p, r, found):
    assert W.token_found(q, heard, p, r) is found
