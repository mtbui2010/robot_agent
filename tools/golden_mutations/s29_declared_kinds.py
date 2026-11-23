"""S29 mutations, a goal kind declared with no rules callable (checked by tests/golden/scenario_sets/s29_declared_kinds.py): one
behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def holds_list_always_true(mp):
    from robot_agent.core.pack import declared_kinds
    mp.setattr(declared_kinds, "holds", lambda pack, decl, goal, world, placed: True)


def plan_never_refuted(mp):
    from robot_agent.core.pack import declared_kinds
    mp.setattr(declared_kinds, "unmet", lambda pack, decl, goal, belief_after: [])


def unknown_is_ignored(mp):
    from robot_agent.core.pack import verifiers
    mp.setattr(verifiers, "unknown_fails", lambda pack, kind: False)


def phrase_key_ignored(mp):
    from robot_agent.core.pack import declared_kinds
    mp.setattr(declared_kinds, "phrase_key", lambda pack, kind: None)


MUTATIONS = {
    "declared_holds_always_true": ("a kind's holds list is always true in the belief (declared_kinds.holds)", holds_list_always_true),
    "declared_plan_never_refuted": ("a plan never refutes a kind's holds list (declared_kinds.unmet)", plan_never_refuted),
    "declared_unknown_is_ignored": ("unknown_is: failure is ignored, a cannot-tell stays on the belief (verifiers.unknown_fails)",
                                    unknown_is_ignored),
    "declared_phrase_key_ignored": ("the read-back ignores a kind's phrase key (declared_kinds.phrase_key)", phrase_key_ignored),
}
