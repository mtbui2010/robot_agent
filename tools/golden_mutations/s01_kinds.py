"""S01 mutations, goal kinds: each changes ONE rule of a goal kind by monkeypatch (the tree is never edited); the cassettes of
tests/golden/scenario_sets/s01_kinds.py must catch each (tools/golden_mutation_check.py loads this file)."""

import dataclasses


def _loop():
    from robot_agent.core.planning.assist_loop import AssistLoop
    return AssistLoop


def template_always_reached(mp):
    from robot_agent.core.planning import goals
    mp.setattr(goals, "reached", lambda g, steps, at, last=False: True)


def unknown_kind_not_reached(mp):
    """What the first design's sentence would implement: a goal of a kind no table knows is not reached / does not hold."""
    A = _loop()
    known = {"on", "in", "open", "close", "go", "say", "deliver"}
    orig_r, orig_h = A._reaches, A._goals_hold

    def reaches(steps, world, goals, loop=None):
        return False if any(g["type"] not in known for g in goals) else orig_r(steps, world, goals, loop)
    mp.setattr(A, "_reaches", staticmethod(reaches))
    mp.setattr(A, "_goals_hold", lambda self, goals, *a, **k: False if any(g["type"] not in known for g in goals)
               else orig_h(self, goals, *a, **k))


def on_goal_without_place(mp):
    """An ``on`` goal is reached by a Place (or a hand-over) anywhere: the destination is not compared."""
    A = _loop()
    mp.setattr(A, "_on_reached", lambda self, g, got: bool(got) and got[0] in ("Place", "release"))


def in_needs_no_pick(mp):
    from robot_agent.core.planning import goals
    mp.setitem(goals.GOAL_KINDS, "in", goals.GoalKind("in", (("PutIn", "Place"),)))


def deliver_prompt_changed(mp):
    from robot_agent.core.planning import goals
    k = goals.GOAL_KINDS["deliver"]
    mp.setitem(goals.GOAL_KINDS, "deliver", dataclasses.replace(k, prompt=k.prompt.replace("hand it over", "leave it")))


def readback_in_phrase(mp):
    A = _loop()
    orig = A._goal_phrase
    from robot_agent.core.planning import speech

    def phrase(g, places, nice):
        if g["type"] == "in":
            return speech.LocalText("rb_in", {"object": g.get("object"), "place": g["to"]},
                                    text=f"put the {g.get('object')} into the {nice(g['to'])}")
        return orig(g, places, nice)
    mp.setattr(A, "_goal_phrase", staticmethod(phrase))


def rb_go_phrase(mp):
    from robot_agent.core.planning import speech
    mp.setitem(speech.CORE_PHRASES, "rb_go", "head to the {place}")


def non_manip_goal_seen(mp):
    A = _loop()
    orig = A._observe_goal
    mp.setattr(A, "_observe_goal", lambda self, r, g: (True, "seen", None) if g.get("type") not in ("on", "in") else orig(self, r, g))


def end_check_trusts_belief(mp):
    mp.setattr(_loop(), "_observe_goal", lambda self, r, g: (True, "belief", None))


def manifest_drops_capabilities(mp):
    from robot_agent.api import robot_info
    orig = robot_info.manifest_of
    mp.setattr(robot_info, "manifest_of", lambda view, env, site: {k: v for k, v in orig(view, env, site).items() if k != "capabilities"})


MUTATIONS = {
    "template_always_reached": ("a template goal (go / say / deliver) is always reached (goals.reached)", template_always_reached),
    "unknown_kind_not_reached": ("a goal of an unknown kind is never reached nor holds (AssistLoop._reaches / _goals_hold)",
                                 unknown_kind_not_reached),
    "on_goal_without_place": ("an on goal is reached by a Place anywhere (AssistLoop._on_reached)", on_goal_without_place),
    "in_needs_no_pick": ("the in kind needs no Pick (goals.GOAL_KINDS['in'].needs)", in_needs_no_pick),
    "deliver_prompt_changed": ("the deliver kind's understand prompt line is reworded", deliver_prompt_changed),
    "readback_in_phrase": ("the read-back of an in goal says 'into' in other words (AssistLoop._goal_phrase)", readback_in_phrase),
    # no run reads CORE_PHRASES["rb_go"] today (speech.render reads the core English phrases only for lang "en", which the
    # language layer never renders: LANGS = ja, ko, vi, zh; the English read-back takes _goal_phrase's own text): the phrase
    # table snapshot pins it
    "rb_go_phrase": ("the read-back phrase of a go goal is reworded (speech.CORE_PHRASES['rb_go'])", rb_go_phrase,
                     ["tests/golden/test_planner_prompt_golden.py::test_the_core_phrase_table_is_unchanged"]),
    "manifest_drops_capabilities": ("GET /robot/manifest drops the capabilities (api.robot_info.manifest_of)",
                                    manifest_drops_capabilities, ["tests/test_manifest_snapshot.py"]),
    "non_manip_goal_seen": ("the end check says every goal other than on / in is seen (AssistLoop._observe_goal)",
                            non_manip_goal_seen),
    "end_check_trusts_belief": ("the end check trusts the belief for every goal (AssistLoop._observe_goal)", end_check_trusts_belief),
}
