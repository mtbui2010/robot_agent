"""Mutations from the review of S01 (2026-11-23): behaviours no cassette caught, now pinned by tests (the third element names
them) and, for the sub-goal phrase, by the set cassettes ``subgoals_*``. Kill criteria of the steps that move the code: S03
(words / Places / Memory), S06 (the tree conditions), S07 (goals.py's sub-goal phrases)."""

PLACES = "tests/test_places_memory_pins.py"
TREES = "tests/test_tree_condition_pins.py"
SUBGOALS = "tests/test_subgoal_phrases.py"


def article_my_your_kept(mp):
    import re
    from robot_agent.core.planning import assist_loop as AL
    mp.setattr(AL, "_strip_article", lambda s: re.sub(r"^(the|a|an)\s+", "", s or "").strip())


def one_word_fallback_lost(mp):
    import inspect
    import textwrap
    from robot_agent.core.planning import assist_loop as AL
    src = textwrap.dedent(inspect.getsource(AL.Places.candidates))
    assert src.count("if not hits and rest:") == 1
    ns = dict(vars(AL))
    exec(src.replace("if not hits and rest:", "if False:"), ns)   # noqa: S102 - a mutation of the real source, in a child process
    mp.setattr(AL.Places, "candidates", ns["candidates"])


def answers_window_nine(mp):
    from robot_agent.core.planning import assist_loop as AL
    mp.setattr(AL.Memory, "answers_text", lambda self: "\n".join(
        f"- task '{a['task']}': asked '{a['q']}' -> '{a['a']}'" for a in self.data["answers"][-9:]) or "(none)")


def hand_free_always_true(mp):
    from robot_agent.options import bt_goal
    orig = bt_goal.holds
    mp.setattr(bt_goal, "holds", lambda n, a, w, p: True if n == "hand_free" else orig(n, a, w, p))


def holding_any_object(mp):
    from robot_agent.options import bt_goal
    orig = bt_goal.holds
    mp.setattr(bt_goal, "holds", lambda n, a, w, p: (getattr(w, "holding", None) is not None and len(a) == 1) if n == "holding"
               else orig(n, a, w, p))


def subgoal_into_in(mp):
    from robot_agent.core.planning import goals
    orig = goals.subgoal_text
    mp.setattr(goals, "subgoal_text", lambda g, places=None: orig(g, places).replace(" into ", " in "))


MUTATIONS = {
    "article_my_your_kept": ("'my' / 'your' before a place name is no longer dropped (assist_loop._strip_article)", article_my_your_kept,
                             [PLACES]),
    "one_word_fallback_lost": ("a place said by one word of its name ('round table') is no longer found (Places.candidates)",
                               one_word_fallback_lost, [PLACES]),
    "answers_window_nine": ("the model sees the last nine remembered answers, not eight (Memory.answers_text)", answers_window_nine,
                            [PLACES]),
    "hand_free_always_true": ("tree condition hand_free is true with a full hand (bt_goal.holds)", hand_free_always_true, [TREES]),
    "holding_any_object": ("tree condition holding o is true while another object is held (bt_goal.holds)", holding_any_object,
                           [TREES]),
    "subgoal_into_in": ("the sub-goal phrase of an `in` goal says 'in', not 'into' (goals.subgoal_text)", subgoal_into_in, [SUBGOALS]),
}
