"""S01 mutations, recovery ladders and the planner's inputs (checked by tests/golden/scenario_sets/s01_ladders.py and
s01_planner.py): one behaviour each, by monkeypatch; tools/golden_mutation_check.py loads this file."""


def help_move_changed(mp):
    from robot_agent.core.planning import step_ladder
    mp.setattr(step_ladder, "HELP_MOVE", step_ladder.HELP_MOVE + " X")


def ladder_choice(mp):
    """A failed Pick goes straight to the planner: no retry in place, no help question."""
    from robot_agent.core.planning.assist_loop import AssistLoop

    def recover_pick(self, r, st, reason, rule):
        return (None, "")
        yield                                                    # a generator, as the rung is
    mp.setattr(AssistLoop, "_recover_pick", recover_pick)


def planner_obs_empty(mp):
    """The planner is given an empty observation (generate_plan / replan)."""
    from robot_agent.core.planning.assist_loop import AssistLoop
    orig = AssistLoop._plan_call
    pos = {"generate_plan": 1, "replan": 4}

    def plan_call(self, r, name, *args, **kw):
        args = list(args)
        if name in pos and len(args) > pos[name]:
            args[pos[name]] = ""
        return orig(self, r, name, *args, **kw)
    mp.setattr(AssistLoop, "_plan_call", plan_call)


def sage_prompt_changed(mp):
    from robot_agent.core.planning import robot_prompt
    mp.setattr(robot_prompt, "ORDER", tuple(reversed(robot_prompt.ORDER)))
    mp.setattr(robot_prompt, "ACTION_TEXT", {k: ("thing", "X") for k in robot_prompt.ACTION_TEXT})


def place_arg_set_changed(mp):
    """The loop's set of actions whose argument is a place loses Place (assist_loop._LOC_ACTIONS)."""
    import robot_agent.core.planning.assist_loop as AL
    mp.setattr(AL, "_LOC_ACTIONS", set(AL._LOC_ACTIONS) - {"Place"})


def reconcile_not_run(mp):
    """The generic reconcile does nothing (core.planning.loop.reconcile_world: the run-start one and the adapter's)."""
    import robot_agent.core.planning.loop as LP
    mp.setattr(LP, "reconcile_world", lambda node, world, threshold=0.8: world)


MUTATIONS = {
    "help_move_changed": ("the MoveTo help question is reworded (step_ladder.HELP_MOVE)", help_move_changed),
    "ladder_choice": ("a failed Pick goes straight to the planner (StepLadder._recover_pick gives up at once)", ladder_choice),
    "planner_obs_empty": ("the planner gets an empty observation (AssistLoop._plan_call)", planner_obs_empty),
    "sage_prompt_changed": ("SAGE's robot prompt changes: the action order reversed, the action texts replaced (robot_prompt)",
                            sage_prompt_changed),
    "place_arg_set_changed": ("Place leaves the loop's set of place-argument actions (assist_loop._LOC_ACTIONS)", place_arg_set_changed),
    "reconcile_not_run": ("the generic reconcile does nothing (core.planning.loop.reconcile_world)", reconcile_not_run),
}
