"""S01 mutations, tree conditions and effect predicates (checked by tests/golden/scenario_sets/s01_trees.py): one behaviour each,
by monkeypatch; tools/golden_mutation_check.py loads this file."""


def _invert(names):
    def patch(mp):
        from robot_agent.options import bt_goal
        orig = bt_goal.holds
        mp.setattr(bt_goal, "holds", lambda n, a, w, p: (not orig(n, a, w, p)) if n in names else orig(n, a, w, p))
    return patch


def bt_placed_ignores_hand(mp):
    from robot_agent.options import bt_goal
    orig = bt_goal.holds
    mp.setattr(bt_goal, "holds", lambda n, a, w, p: ((p or {}).get(a[0]) == a[1]) if n == "placed" else orig(n, a, w, p))


def effect_unknown_pred_fails(mp):
    from robot_agent.core.planning import verify
    orig = verify._holds

    def holds(world, pred, bind):
        v = orig(world, pred, bind)
        return False if v is None else v
    mp.setattr(verify, "_holds", holds)


def effect_on_inverted(mp):
    from robot_agent.core.planning import verify
    orig = verify._holds

    def holds(world, pred, bind):
        v = orig(world, pred, bind)
        return (not v) if v is not None and verify._PRED.match(pred.strip()).group(2) == "on" else v
    mp.setattr(verify, "_holds", holds)


MUTATIONS = {
    "bt_opened_inverted": ("the tree conditions opened / closed are inverted (options.bt_goal.holds)", _invert(("opened", "closed"))),
    "bt_at_inverted": ("the tree condition at is inverted (options.bt_goal.holds)", _invert(("at",))),
    "bt_placed_ignores_hand": ("the tree condition placed holds while the object is still in the hand (options.bt_goal.holds)",
                               bt_placed_ignores_hand),
    "effect_unknown_pred_fails": ("a declared effect the belief does not track fails the step (verify._holds None -> False)",
                                  effect_unknown_pred_fails),
    "effect_on_inverted": ("a declared effect on the on predicate is read inverted (verify._holds)", effect_on_inverted),
}
