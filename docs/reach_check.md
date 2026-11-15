# reach_check: TAMP-lite, a reachability check between the plan and the arm

**Problem.** The plan does not know geometry. In MuJoCo runs of the kcare house the plan "move to the table, find the bowl, pick
the bowl, ..." failed at the Pick: the Find saw the bowl 0.94 m to the side of the base (base frame, `ins[obj].pose_3d`) while the
arm reaches 0.80 m. The robot learnt it only from the pick skill ("beyond arm reach") and then asked the person to move the bowl
(`_recover_pick`, out of side reach). A base shift of about 0.2 m would have made the pick possible.

**Rule** (pure function `decide(dist, reach, margin, max_shift) -> shift | None`): `dist` = the side offset `|y|` of the point
the last Find at this place saw; `reach` = the robot's declared `geometry.reach_m` (the contract's side reach, `Geometry.reach_m`,
read from the manifest as declared: the contract default does not count). `dist <= reach`: nothing. Else `shift = dist - reach +
margin`; `shift <= max_shift`: a shift step of `shift` metres toward the object's side (sign of `y`) goes before the Pick; more:
nothing. Missing, negative or non-number values: nothing.

**Declared data** (robot pack, manifest): `geometry.reach_m` (metres); `planner.action_roles.pick`; `planner.options.reach_check.shift`
= `{skill: <the robot's skill that moves the base sideways>, param: <its distance argument, metres, + = base +y>, margin_m, max_shift_m}`
with `source: "<where the numbers come from>"` or `assumed: true`. Unlabelled data is not used.

**Does:** hook `on_step` (before a step runs): for a Pick whose object the belief has found here (`found`, cleared by any MoveTo) and
whose point `seen_at` holds, inserts ONE skill-only step `{action: "", skill, params: {param: ±shift}}` (no plan action: no symbolic
effect, the robot stays "arrived"); the Pick runs after it (the pick skill looks again itself). Once per sighting.
**Does not:** plan base poses, move forward / back (the pick skill's own `mforward`), change a plan before it runs, retry after a
failed Pick, or invent a skill: with no declared shift skill it is inert and today's path (fail at the Pick, ask the person) stands.
Keep `max_shift_m` below `geometry.arrive_tol_m`, or the arrival check may say the robot left its place.

**Switch off:** key `enabled` (run option `reach_check`, `$ROBOT_AGENT_REACH_CHECK`, `planner.options.reach_check.enabled`), off by
default. **Event:** lab flag `reach_check` `{object, dist_m, reach_m, shift_m}`, run event `recover` kind `reach_check`.

**Measure.** Tests (`tests/test_option_reach_check.py`): the rule on small tables; a made-up robot; through the real AssistLoop an
object seen 0.95 m to the side with reach 0.80: on, the shift runs before the Pick and the goal is reached; off, the Pick fails and the
robot asks as today. Bench: symbolic_robot layer `reach_check` (selectable, not in `all`); the mock pack declares no shift skill, so
the option is inert there (no lab event expected). On the robot: count of picks refused for side reach, with and without the option.
