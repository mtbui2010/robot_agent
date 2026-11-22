"""S25a mutations, the tree keys of bt_goal (checked by tests/golden/scenario_sets/s25a_tree_keys.py and the bt_goal cassettes): one
behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def _keys(**force):
    def patch(mp):
        from robot_agent.core.planning import tree_host
        orig = tree_host.tree_keys
        mp.setattr(tree_host, "tree_keys", lambda entry: {**orig(entry), **force})
    return patch


def pack_choice_words_ignored(mp):
    from robot_agent.core.planning import tree_host
    mp.setattr(tree_host, "choice_words", lambda questions, ref, lang: [])


def pack_choice_word_added(mp):
    from robot_agent.core.planning import tree_host
    orig = tree_host.choice_words
    mp.setattr(tree_host, "choice_words", lambda questions, ref, lang: orig(questions, ref, lang) + ["whatever"])


MUTATIONS = {
    "tree_ladder_key_ignored": ("a tree entry with `ladder: false` still runs the engine's ladder (tree_host.tree_keys)", _keys(ladder=True)),
    "tree_ladder_always_off": ("every tree entry runs without the engine's ladder, key or not (tree_host.tree_keys)", _keys(ladder=False)),
    "tree_events_key_ignored": ("a tree entry with `events: true` sends no tree_node event (tree_host.tree_keys)", _keys(events=False)),
    "tree_events_always_on": ("every tree entry sends tree_node events, key or not (tree_host.tree_keys)", _keys(events=True)),
    "pack_choice_words_ignored": ("the ask leaf reads no choice words of a pack question (tree_host.choice_words)", pack_choice_words_ignored),
    "pack_choice_word_added": ("a pack question's choice also matches a word its pack never declared (tree_host.choice_words)",
                               pack_choice_word_added),
}
