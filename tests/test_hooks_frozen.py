"""The hook table and the option dispatch table are frozen (Step S01 of docs/proposals/generalize_core_design.md, for Steps S22
and S24): the hook names, their kinds (decide / transform / observe) and flow flags (core/options/spec.py ``HOOKS``), and each
registered option's name, dispatch order, hooks, enabling key and services, in registry order, equal the tables recorded on the
old code below. A step that adds a hook or an option adds a line here in a reviewed commit; a changed or dropped one fails.
The order the hooks fire in during a run is in the cassette ``hooks_order`` (option ``trace``, capture ``trace``)."""

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))

HOOKS = {
    'on_start': ('observe', False),
    'on_ground': ('transform', False),
    'on_plan': ('transform', False),
    'on_where_answer': ('decide', True),
    'on_understand_schema': ('transform', False),
    'on_understand_prompt': ('transform', False),
    'on_heard': ('decide', True),
    'on_goals': ('decide', True),
    'on_issues': ('transform', False),
    'on_goal_edit': ('observe', False),
    'on_goal_reread': ('observe', False),
    'on_goal_unapplied': ('decide', True),
    'on_run_goals': ('decide', True),
    'on_step': ('decide', True),
    'on_step_done': ('observe', False),
    'on_step_failed': ('decide', True),
    'on_ask_help': ('decide', True),
    'on_goal_check': ('decide', False),
    'on_translation_lost': ('decide', False),
    'on_second_look': ('decide', False),
    'on_final': ('decide', False),
    'on_goals_met': ('decide', False),
    'on_finish': ('observe', False),
    'on_planner_kwargs': ('transform', False),
    'on_goal_replan': ('decide', False),
    'on_replan_findings': ('transform', False),
    'on_plan_findings': ('transform', False),
    'on_listener_rule': ('decide', False),
    'on_event': ('observe', False),
    'on_lab': ('observe', False),
}

# (name, order, hooks, enabled key, services) in registry order
OPTIONS = [
    ('trace', 0, ('on_start', 'on_event', 'on_lab', 'on_step_failed'), 'enabled', ()),
    ('goals_only', 1, ('on_run_goals',), 'enabled', ('runs_goals', 'rank_first')),
    ('bt_goal', 5, ('on_run_goals', 'on_step_failed'), 'enabled', ('runs_goals', 'no_replan')),
    ('answer_align', 8, ('on_heard', 'on_goals', 'on_issues'), 'enabled', ()),
    ('hazard', 10, ('on_goals', 'on_goals_met', 'on_finish'), '', ('forbids', 'refused_goals', 'gate_off', 'goal_dropped')),
    ('heat_safety', 11, ('on_goals', 'on_goals_met', 'on_finish'), 'enabled', ('forbids', 'refused_goals')),
    ('cond_extract', 15, ('on_goals',), 'enabled', ('refused_goals', 'goal_dropped')),
    ('name_recover', 17, ('on_goals', 'on_issues'), 'enabled', ()),
    ('cond_goal', 20, ('on_understand_schema', 'on_understand_prompt', 'on_goals'), 'enabled', ('refused_goals', 'goal_dropped')),
    ('goal_ledger', 22, ('on_issues',), 'enabled', ('goal_keep',)),
    ('look_then_ask', 29, ('on_goals',), 'enabled', ()),
    ('ask_source', 30, ('on_goals', 'on_where_answer'), 'enabled', ()),
    ('reach_delivery', 30, ('on_start', 'on_ground', 'on_step', 'on_step_done', 'on_goal_check', 'on_step_failed'), 'enabled', ('no_refetch',)),
    ('reach_check', 32, ('on_step',), 'enabled', ()),
    ('fetch_guard', 32, ('on_plan_findings',), 'enabled', ('rank_first', 'failure_code')),
    ('drop_search', 36, ('on_step_failed',), 'enabled', ()),
    ('ask_next', 40, ('on_ask_help',), 'enabled', ()),
    ('asr_guard', 40, ('on_heard',), 'enabled', ()),
    ('unapplied_change', 40, ('on_goal_edit', 'on_goal_reread', 'on_goal_unapplied', 'on_final'), 'enabled', ()),
    ('confirm_look', 50, ('on_second_look',), 'enabled', ()),
    ('lang_words', 60, ('on_start',), 'enabled', ()),
    ('translate_retry', 60, ('on_translation_lost',), 'enabled', ()),
    ('goal_words', 70, ('on_goals',), 'enabled', ('goal_guard',)),
    ('search_budget', 70, ('on_start',), 'enabled', ('looks_made', 'search_limit', 'search_over')),
    ('search_per_object', 70, ('on_start',), 'enabled', ('looks_made',)),
    ('entity_cover', 75, ('on_issues',), 'enabled', ()),
    ('object_words', 75, ('on_start',), 'enabled', ('belief_table', 'belief_set')),
    ('bring_ask', 75, ('on_start',), 'enabled', ('person_where',)),
    ('ask_agree', 78, ('on_goals', 'on_issues'), 'enabled', ('unrefused',)),
    ('ask_focus', 80, ('on_issues',), 'enabled', ('confirm_limit', 'unrefused')),
    ('place_find', 80, ('on_plan',), 'enabled', ()),
    ('shared_instances', 81, ('on_issues',), 'enabled', ('shared_names',)),
    ('heard_check', 82, ('on_issues',), 'enabled', ()),
    ('gate_prune', 84, ('on_plan',), 'enabled', ()),
    ('precond_repair', 85, ('on_plan',), 'enabled', ()),
    ('dedupe_keep_end', 86, ('on_start',), 'enabled', ('keep_after_dedupe',)),
    ('plan_complete', 86, ('on_plan',), 'enabled', ()),
    ('plan_release', 87, ('on_start', 'on_plan'), 'enabled', ()),
    ('recipient_keep', 88, ('on_start', 'on_listener_rule', 'on_goals'), 'enabled', ()),
    ('hw_wait', 90, ('on_start',), 'enabled', ('wait_schedule', 'recovery_rule', 'more_tries')),
    ('reopen_on_miss', 90, ('on_step_failed',), 'enabled', ()),
    ('replan_guard', 90, ('on_planner_kwargs', 'on_goal_replan', 'on_replan_findings', 'on_plan'), 'enabled', ()),
    ('repair_sync', 91, ('on_replan_findings',), 'enabled', ('repair_kwargs',)),
    ('end_state', 95, ('on_final',), 'enabled', ()),
]


def test_the_hook_table_is_unchanged():
    from robot_agent.core.options.spec import HOOKS as now
    assert list(now) == list(HOOKS), "a hook was added, dropped or moved"
    assert {k: tuple(v) for k, v in now.items()} == HOOKS


def test_the_option_table_is_unchanged():
    from robot_agent.core.options.registry import REGISTRY
    now = [(s.name, s.order, tuple(s.hooks), s.enabled, tuple(s.services)) for s in REGISTRY.specs()]
    assert [n[0] for n in now] == [o[0] for o in OPTIONS], "an option was added, dropped or moved"
    assert now == OPTIONS


def test_the_hooks_order_cassette_records_the_trace_rows():
    import harness
    cas = harness.load(harness.cassette_path("hooks_order"))
    rows = cas["recorded"].get("trace_rows") or []
    assert rows and rows[0].get("k") == "header"
    assert {r.get("k") for r in rows} >= {"header", "event"}
