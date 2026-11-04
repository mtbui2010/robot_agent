"""search.py (SAGE-v2 P1.13): the search belief alone, no loop, no robot."""

import pytest

from robot_agent.core.planning.search import (Leg, SearchBelief, SearchConfig, confidence, containers_from_env)

DR, DT, DS, CT, SK = "drawer@kitchen", "dining table@kitchen", "desk@study room", "counter@kitchen", "shelf@kitchen"
PLACES = [DS, DR, CT, SK, DT]


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def belief(can_open=True, **cfg):
    clock = Clock()
    b = SearchBelief(PLACES, SearchConfig(**cfg), openable=lambda k: k == DR, can_open=can_open, now=clock)
    return b, clock


def S(*items):
    out = []
    for it in items:
        a, _, o = it.partition(" ")
        out.append({"action": a, "object": o})
    return out


def plan(steps):
    return [f"{s['action']} {s['object']}" for s in steps]


# ── prior ──────────────────────────────────────────────────────────────
def test_confidence_halves_every_half_life():
    assert confidence(0, 100) == 1.0
    assert confidence(100, 100) == pytest.approx(0.5)
    assert confidence(300, 100) == pytest.approx(0.125)
    assert confidence(1e9, 0) == 1.0                 # half-life 0: never decays
    assert confidence(-5, 100) == 1.0                # a clock skew is not a future sighting


def test_config_from_env_and_overrides():
    env = {"ROBOT_AGENT_SEARCH": "0", "ROBOT_AGENT_SEARCH_RANK": "prior", "ROBOT_AGENT_SEARCH_HALF_LIFE_S": "60"}
    c = SearchConfig.from_env({}, env=env.get)
    assert (c.enabled, c.rank, c.half_life_s) == (False, "prior", 60.0)
    c = SearchConfig.from_env({"search": True, "search_rank": "llm"}, env=env.get)
    assert c.enabled and c.rank == "llm"
    assert SearchConfig.from_env({}, env={}.get) == SearchConfig()


def test_recent_sighting_first_old_one_outranked_after_decay():
    b, clock = belief(half_life_s=3600)
    b.see("apple", DS, ts=clock.t - 60, how="placed")
    b.rank("apple", lambda p: {"places": [SK, CT]})
    assert b.order("apple")[0] == DS                  # 1 min old: the prior wins over the ranking
    clock.t += 3 * 86400                              # three days later the same sighting has decayed
    assert b.order("apple")[:2] == [SK, CT]
    assert b.first_place("apple") == SK


def test_needs_rank_first_only_for_stale_sightings():
    b, clock = belief(half_life_s=3600, trust=0.5)
    assert not b.needs_rank_first("apple")            # no sighting: the planner's first guess
    b.see("apple", DS, ts=clock.t - 60)
    assert not b.needs_rank_first("apple")            # trusted
    clock.t += 2 * 3600
    assert b.needs_rank_first("apple")                # 0.25 < trust
    b.rank("apple", lambda p: None)
    assert not b.needs_rank_first("apple")            # asked once already


def test_unknown_places_are_ignored():
    b, _ = belief()
    b.see("apple", "robot"); b.see("apple", None); b.see("apple", "garage")
    assert b.prior("apple") == {} and b.first_place("apple") is None


# ── the ranking: once per object ──────────────────────────────────────
def test_rank_is_asked_once_and_cached():
    b, _ = belief()
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return {"places": ["the kitchen shelf", CT, "garage", CT]}
    out = b.rank("apple", ask, ground=lambda n: SK if "shelf" in n else None)
    assert out == [SK, CT]                            # grounded, unknown + duplicate dropped
    assert b.rank("apple", ask) == [SK, CT] and len(asked) == 1
    assert "Rank the places where the apple" in asked[0] and DT in asked[0]
    b.rank("mug", ask)
    assert len(asked) == 2 and b.rank_calls == 2


def test_a_failed_ranking_is_not_asked_again():
    b, _ = belief()
    calls = []
    assert b.rank("apple", lambda p: calls.append(p)) == []
    assert b.rank("apple", lambda p: calls.append(p)) == [] and len(calls) == 1
    assert not b.informed("apple")


def test_prior_mode_never_asks():
    b, _ = belief(rank="prior")
    assert b.rank("apple", lambda p: pytest.fail("asked")) == []


def test_rank_prompt_says_what_is_known():
    b, clock = belief()
    b.see("apple", DS, ts=clock.t - 3 * 86400, how="placed")
    b.missed("apple", CT, ())
    p = b.rank_prompt("apple")
    assert "placed at desk@study room 3 days ago" in p and "Already searched (not there): counter@kitchen" in p


# ── searched places: the hard constraint ──────────────────────────────
def test_searched_stays_searched_until_something_changes():
    b, _ = belief()
    b.missed("apple", CT, ())
    assert b.is_searched("apple", CT) and not b.is_searched("mug", CT)     # per object
    b.changed(DT)
    assert b.is_searched("apple", CT)
    b.changed(CT)                                     # the robot moved something there
    assert not b.is_searched("apple", CT)


def test_an_opened_container_voids_the_search_there():
    b, _ = belief()
    b.missed("spoon", DR, opened=())                  # looked while the drawer was closed
    assert b.is_searched("spoon", DR, opened=())
    assert not b.is_searched("spoon", DR, opened={DR})
    b.missed("spoon", DR, opened={DR})                # looked inside: searched for good
    assert b.is_searched("spoon", DR, opened={DR})


def test_a_miss_removes_the_sighting_and_found_ends_the_search():
    b, _ = belief()
    b.see("apple", DS)
    b.missed("apple", DS, ())
    assert DS not in b.prior("apple") and b.under_search() == ["apple"]
    b.found("apple", CT)
    assert b.under_search() == [] and b.first_place("apple") == CT


# ── the next look ─────────────────────────────────────────────────────
def test_closed_container_here_is_opened_before_moving_on():
    b, _ = belief()
    b.missed("spoon", DR, ())
    leg = b.next_leg("spoon", DR, ())
    assert (leg.place, leg.open) == (DR, [DR])
    assert plan(leg.steps(DR)) == [f"Open {DR}", "Find spoon"]
    assert plan(leg.steps(DT)) == [f"MoveTo {DR}", f"Open {DR}", "Find spoon"]


def test_without_open_a_closed_container_is_never_a_look():
    b, _ = belief(can_open=False)
    b.rank("spoon", lambda p: {"places": [DR, CT]})
    b.missed("spoon", DR, ())
    leg = b.next_leg("spoon", DR, ())
    assert leg.place == CT and leg.open == []
    for p in (CT, DS, SK, DT):
        b.missed("spoon", p, ())
    assert b.next_leg("spoon", DT, ()) is None        # nowhere left the robot can look


def test_next_leg_follows_the_order_and_skips_searched():
    b, _ = belief()
    b.rank("apple", lambda p: {"places": [CT, SK]})
    b.missed("apple", DS, ())
    assert b.next_leg("apple", DS).place == CT
    b.missed("apple", CT, ())
    assert b.next_leg("apple", CT).place == SK
    assert b.informed("apple")
    b.missed("apple", SK, ())
    assert not b.informed("apple")                    # the ranking is used up: no preference left


def test_leg_text_and_end():
    leg = Leg("spoon", DR, [DR])
    assert "Open it" in leg.text() and leg.end() == DR
    assert Leg("apple", CT).text() == CT


# ── steering a planner's suffix ───────────────────────────────────────
def test_steer_moves_a_look_away_from_a_searched_place():
    b, _ = belief()
    b.missed("apple", DS, ())
    steps, notes = b.steer(S(f"MoveTo {DS}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"), DS)
    assert plan(steps)[:2] == [f"MoveTo {DR}", f"Open {DR}"]      # ENV order, the drawer opens
    assert plan(steps)[2:] == ["Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"]
    assert notes and "instead of desk@study room" in notes[0]


def test_steer_keeps_an_unsearched_choice_when_the_belief_knows_nothing():
    b, _ = belief()
    b.missed("apple", DS, ())
    s = S(f"MoveTo {CT}", "Find apple", "Pick apple")
    assert b.steer(s, DS) == (s, [])


def test_steer_follows_the_ranking_and_restores_the_place_after():
    b, _ = belief()
    b.rank("apple", lambda p: {"places": [SK]})
    b.missed("apple", DS, ())
    # the planner looks at the dining table (the goal's place) and places there without a MoveTo
    steps, _n = b.steer(S(f"MoveTo {DT}", "Find apple", "Pick apple", f"Place {DT}"), DS)
    assert plan(steps) == [f"MoveTo {SK}", "Find apple", "Pick apple", f"MoveTo {DT}", f"Place {DT}"]


def test_steer_a_look_here_again_into_a_closed_container_opens_it():
    b, _ = belief()
    b.missed("spoon", DR, ())
    steps, _n = b.steer(S("Find spoon", "Pick spoon", f"MoveTo {DT}", f"Place {DT}"), DR)
    assert plan(steps) == [f"Open {DR}", "Find spoon", "Pick spoon", f"MoveTo {DT}", f"Place {DT}"]
    # the planner opened it itself: nothing to change
    s = S(f"Open {DR}", "Find spoon", "Pick spoon")
    assert b.steer(s, DR)[0] == s


def test_steer_without_open_never_adds_an_open():
    b, _ = belief(can_open=False)
    b.missed("spoon", CT, ())
    steps, _n = b.steer(S(f"MoveTo {CT}", "Find spoon", "Pick spoon"), CT)
    assert all(s["action"] != "Open" for s in steps)
    assert plan(steps)[0] == f"MoveTo {DS}"           # the drawer is skipped: it cannot look inside


def test_steer_leaves_objects_not_under_search_alone():
    b, _ = belief()
    s = S(f"MoveTo {DS}", "Find apple", "Pick apple")
    assert b.steer(s, None) == (s, [])


# ── a run's record / resume ───────────────────────────────────────────
def test_to_dict_and_load_keep_searched_and_rankings():
    b, _ = belief()
    b.rank("apple", lambda p: {"places": [CT]})
    b.missed("apple", DS, ()); b.missed("spoon", DR, ())
    b2, _ = belief()
    b2.load(b.to_dict())
    assert b2.is_searched("apple", DS) and b2.is_searched("spoon", DR)
    assert b2.ranked["apple"] == [CT] and not b2.wants_rank("apple") and b2.rank_calls == 1
    assert b2.next_leg("spoon", DR).open == [DR]


def test_containers_from_env():
    env = {CT: {"containers": [DR, "nope"]}, DR: {}, DS: {}}
    fn = containers_from_env(env, lambda k: k == DR)
    assert fn(CT) == [DR] and fn(DR) == [DR] and fn(DS) == []
    b = SearchBelief(list(env), openable=lambda k: k == DR, can_open=True, containers=fn)
    b.missed("spoon", CT, ())
    leg = b.next_leg("spoon", CT)
    assert (leg.place, leg.open) == (DR, [DR])        # the closed drawer under the counter first
