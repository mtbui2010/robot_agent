"""S25a mutations, the opt-in keys of a bt_goal tree entry (checked by tests/golden/scenario_sets/s25a_tree_keys.py): one behaviour each,
by monkeypatch; tools/golden_mutation_check.py loads this file. Each must make at least one golden fail."""

from robot_agent.core.planning import tree_host


def _keys(ladder=None, events=None):
    """Patch ``tree_host.tree_keys`` so that the named key is forced (None: read from the entry as it is)."""
    def patch(mp):
        orig = tree_host.tree_keys
        mp.setattr(tree_host, "tree_keys", lambda e: {**orig(e), **({"ladder": ladder} if ladder is not None else {}),
                                                      **({"events": events} if events is not None else {})})
    return patch


def choice_words_unread(mp):
    mp.setattr(tree_host, "choice_words", lambda questions, ref, lang: [])


def choice_matches_any_answer(mp):
    orig = tree_host.choice_words
    mp.setattr(tree_host, "choice_words", lambda questions, ref, lang: orig(questions, ref, lang) + ["whatever"])


def pack_trees_unread(mp):
    mp.setattr(tree_host, "declared_trees", lambda configured, pack_trees=None: configured)


MUTATIONS = {
    "tree_ladder_key_ignored": ("the tree key ladder: false is ignored: every act runs the engine's ladder", _keys(ladder=True)),
    "tree_ladder_always_off": ("every tree runs its acts without the engine's ladder", _keys(ladder=False)),
    "tree_events_key_ignored": ("the tree key events: true is ignored: no tree_node event", _keys(events=False)),
    "tree_events_always_on": ("every tree sends a tree_node event for each node", _keys(events=True)),
    "choice_words_unread": ("the words of a pack question's choice are not read: the ask leaf never matches them", choice_words_unread),
    "choice_matches_any_answer": ("a choice also matches the word 'whatever': the unmatched answer goes on", choice_matches_any_answer),
    "pack_trees_unread": ("the pack's trees table is not read: a robot whose trees are in its pack has no tree", pack_trees_unread),
}
