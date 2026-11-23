"""S25 mutations, typed ``act`` arguments and pack questions in a bt_goal tree (caught by tests/golden/scenario_sets/s25_typed_ask.py): one
behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def act_args_dropped(mp):
    from robot_agent.options import bt_goal
    orig = bt_goal._Leaves.act
    mp.setattr(bt_goal._Leaves, "act", lambda self, action, obj, args=None: orig(self, action, obj))


def mapping_makes_a_tree_invalid(mp):
    from robot_agent.core.planning import behavior_tree as bt
    orig = bt.valid

    def has_map(node):
        return isinstance(node, dict) or isinstance(node, list) and any(has_map(x) for x in node)
    mp.setattr(bt, "valid", lambda node: orig(node) and not has_map(node))


def answer_fact_not_kept(mp):
    from robot_agent.core.planning import tree_host
    mp.setattr(tree_host, "keep_answer", lambda facts, question, choice, text, keep_text: None)


def answer_text_not_kept(mp):
    from robot_agent.core.planning import tree_host
    orig = tree_host.keep_answer
    mp.setattr(tree_host, "keep_answer", lambda facts, question, choice, text, keep_text: orig(facts, question, choice, text, False))


def choice_phrase_not_read(mp):
    from robot_agent.core.planning import names, tree_host
    mp.setattr(tree_host, "_said", lambda text, words, lang: names.says([text], [w for w in words if len(w.split()) == 1], (lang,)))


def last_choice_wins(mp):
    from robot_agent.core.planning import tree_host
    orig = tree_host.read_choice

    def last(spec, text, lang):
        choices = (spec.get("answer") or {}).get("choices") or {}
        found = [c for c, v in choices.items() if orig({"answer": {"choices": {c: v}}}, text, lang)]
        return found[-1] if found else None
    mp.setattr(tree_host, "read_choice", last)


def answered_never_holds(mp):
    from robot_agent.core.planning import tree_host
    mp.setattr(tree_host, "answered", lambda facts, args: False)


def question_kind_ignored(mp):
    from robot_agent.options import bt_goal
    orig = bt_goal._Leaves.put
    mp.setattr(bt_goal._Leaves, "put", lambda self, q, kind=bt_goal.FLAG: orig(self, q, bt_goal.FLAG))


MUTATIONS = {
    "tree_act_args_dropped": ("an act leaf's mapping does not reach the step (options/bt_goal.py _Leaves.act)", act_args_dropped),
    "tree_mapping_invalid": ("a tree with an act mapping is refused as invalid (core/planning/behavior_tree.py valid)", mapping_makes_a_tree_invalid),
    "answer_fact_not_kept": ("the answer of a pack question is not kept as a fact (tree_host.keep_answer)", answer_fact_not_kept),
    "answer_text_not_kept": ("keep_text is ignored: the raw answer is not kept (tree_host.keep_answer)", answer_text_not_kept),
    "choice_phrase_not_read": ("a choice word of several words is never said by an answer (tree_host._said)", choice_phrase_not_read),
    "choice_last_wins": ("the LAST matching choice is read, not the first in declaration order (tree_host.read_choice)", last_choice_wins),
    "answered_never_holds": ("the condition answered is never true (tree_host.answered)", answered_never_holds),
    "question_kind_ignored": ("the clarify event of a pack question carries the host's kind, not the question's (options/bt_goal.py put)",
                              question_kind_ignored),
}
