"""Search route (default off): nearer places first among about equally likely ones; positions are ENV data; off = the old order."""
from robot_agent.core.planning.search import SearchBelief, SearchConfig
from robot_agent.core.planning.search_route import locs_from_env, route_scores

LOCS = {"a@r1": (0.0, 0.0), "b@r2": (10.0, 0.0), "c@r1": (1.0, 0.0), "d@r2": (9.0, 0.0)}


def belief(weight, locs=LOCS):
    b = SearchBelief(list(LOCS), SearchConfig(route_weight=weight, rank="prior"), locs=locs)
    b.ranked["x"] = ["b@r2", "a@r1", "d@r2", "c@r1"]      # the likelihood zig-zags across the home
    return b


def test_off_by_default_the_order_is_the_likelihood_order():
    assert SearchConfig().route_weight == 0
    assert belief(0.0).order("x", "a@r1") == ["b@r2", "a@r1", "d@r2", "c@r1"]


def test_with_a_weight_the_nearer_place_goes_first():
    order = belief(0.5).order("x", "a@r1")
    assert order.index("c@r1") < order.index("d@r2") and order.index("a@r1") < order.index("b@r2")


def test_no_position_for_here_or_no_locs_changes_nothing():
    assert belief(0.5).order("x", None) == belief(0.0).order("x")
    assert belief(0.5).order("x", "unknown") == belief(0.0).order("x")
    assert belief(0.5, locs={}).order("x", "a@r1") == belief(0.0).order("x")
    s = {"p": 1.0}
    assert route_scores(s, LOCS, "a@r1", 0.0) is s


def test_locs_from_env_reads_only_declared_positions():
    env = {"a": {"loc": {"x": 1, "y": 2, "rz": 0}}, "b": {}, "c": {"loc": {"x": "bad"}}}
    assert locs_from_env(env, ["a", "b", "c"]) == {"a": (1.0, 2.0)}


def test_a_clearly_more_likely_place_still_wins_over_a_near_one():
    b = belief(0.05)
    assert b.order("x", "a@r1")[0] == "b@r2"
