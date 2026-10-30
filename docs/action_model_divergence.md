# Action rules: divergence matrix (before) and one action model (after)

Roadmap step 3 ("one action model"). Before this change the symbolic action rules existed in five copies
that had drifted apart:

| # | copy | role |
|---|---|---|
| 1 | `embodiplan/verifier.py` (`verify_step`, `_apply`, `simulate`) | SAGE's plan gate, SayCan affordance, evaluation gate |
| 2 | `robot_agent/core/planning/verify.py` (`symbolic_check`) | post-step symbolic verify (`StepVerifier`) and, in `AssistLoop`, the pre-step guard |
| 3 | `robot_agent/core/planning/mapper.py` (`ActionMapper.apply_effect`) | world effects in `ClosedLoop` |
| 4 | `robot_agent/core/planning/assist_loop.py` (`AssistLoop._check`, `_effect`, the inline guard in `run_blocking`) | plan checker that drives SAGE edits, world effects in `AssistLoop` |
| 5 | `kcare_robot/configs/grace_namemap.apply_skill_effect` | effects of raw (open-loop / direct) kcare skills, keyed on skill names |

Every row below was produced by calling each copy on the same world and step (no reimplementation): the
world is given as the state before the step, and `_check` is called on a one-step plan with the goals shown
(goal-level findings such as "no goal needs it" are left out, they are not action rules). Places are robot
ENV keys: `table@kitchen`, `shelf@living room`, `drawer@bedroom` (openable). The regression tests that keep
this table true are `tests/test_action_model_agreement.py` (robot_agent) and `tests/test_action_model.py`
(embodiplan).

## Before (robot_agent 04f08a5, embodiplan d9a8074)

### Preconditions

| id | case | world | step | embodiplan verifier | verify.symbolic_check | AssistLoop._check |
|---|---|---|---|---|---|---|
| C01 | MoveTo without destination | (empty) | MoveTo | ERR empty_object | ok | ok |
| C02 | MoveTo where already there | arrived=table@kitchen | MoveTo table@kitchen | ok | ok | warn the robot is already there |
| C03 | Find without object | arrived=table@kitchen | Find | ERR empty_object | ERR Find requires an object | ok |
| C04 | Find at a place the object is not (believed on the table, robot at the shelf) | arrived=shelf@living room | Find cup | ok | ok | ERR the user said it is on table in the kitchen; MoveTo table@kitchen first |
| C05 | Find before any MoveTo | (empty) | Find cup | ok | ok | ok |
| C06 | Find the object already in the hand | arrived=table@kitchen holding=cup | Find cup | ok | ok | ERR already done (the cup is in the hand); delete this step |
| C07 | Pick without Find | arrived=table@kitchen | Pick cup | ERR no_find_before_pick | ERR Pick requires a found object | ERR needs 'Find cup' just before it, at the same place |
| C08 | Pick while holding another object | arrived=table@kitchen found=apple holding=cup | Pick apple | ERR already_holding | ERR Pick requires empty gripper, holding 'cup' | ERR the hand already holds the cup; Place it first |
| C09 | Pick an object other than the found one | arrived=table@kitchen found=apple | Pick cup | ok | ok | ERR needs 'Find cup' just before it, at the same place |
| C10 | Pick (no object) after Find | arrived=table@kitchen found=cup | Pick | ok | ok | ERR needs 'Find ' just before it, at the same place |
| C11 | Pick the object already held | arrived=table@kitchen holding=cup | Pick cup | ERR already_holding | ERR Pick requires a found object | ERR already done by an earlier step (the cup is in the hand); delete this step |
| C12 | Place without holding | arrived=shelf@living room | Place shelf@living room | ERR place_without_holding | ERR Place requires holding an object | ERR nothing is in the hand |
| C13 | Place at another place than the robot is at | arrived=table@kitchen holding=cup | Place shelf@living room | ERR place_wrong_location | ok | ERR the robot must MoveTo shelf@living room first |
| C14 | Place where the robot is | arrived=shelf@living room holding=cup | Place shelf@living room | ok | ok | ok |
| C15 | Place on a closed drawer (its top is a surface) | arrived=drawer@bedroom holding=cup | Place drawer@bedroom | ok | ok | ok |
| C16 | PutIn into a closed drawer | arrived=drawer@bedroom holding=cup | PutIn drawer@bedroom | ok | ERR PutIn requires 'drawer@bedroom' to be open | ERR drawer in the bedroom is closed; Open it before the Pick |
| C17 | PutIn into an open drawer | arrived=drawer@bedroom holding=cup opened=['drawer@bedroom'] | PutIn drawer@bedroom | ok | ok | ok |
| C18 | PutIn into a non-container (shelf) | arrived=shelf@living room holding=cup | PutIn shelf@living room | ok | ERR PutIn requires 'shelf@living room' to be open | ERR shelf in the living room is closed; Open it before the Pick |
| C19 | PutIn into an open drawer the robot is not at | arrived=table@kitchen holding=cup opened=['drawer@bedroom'] | PutIn drawer@bedroom | ok | ok | ERR the robot must MoveTo drawer@bedroom first |
| C20 | Open a drawer twice | arrived=drawer@bedroom opened=['drawer@bedroom'] | Open drawer@bedroom | ok | ERR 'drawer@bedroom' is already open | ok |
| C21 | Open while holding (single hand) | arrived=drawer@bedroom holding=cup | Open drawer@bedroom | ok | ok | ERR the robot cannot open it while holding the cup; do it before the Pick |
| C22 | Open a drawer the robot is not at (nor found) | arrived=table@kitchen | Open drawer@bedroom | ERR interact_without_find | ok | ERR the robot must MoveTo drawer@bedroom first |
| C23 | Open a found drawer the robot is not at (Find only looks) | arrived=table@kitchen found=drawer@bedroom | Open drawer@bedroom | ok | ok | ERR the robot must MoveTo drawer@bedroom first |
| C24 | Open a closed drawer the robot is at | arrived=drawer@bedroom | Open drawer@bedroom | ok | ok | ok |
| C25 | Close an already closed drawer | arrived=drawer@bedroom | Close drawer@bedroom | ERR close_not_opened | ERR 'drawer@bedroom' is not open | ok |
| C26 | Close while holding (single hand) | arrived=drawer@bedroom holding=cup opened=['drawer@bedroom'] | Close drawer@bedroom | ok | ok | ERR the robot cannot close it while holding the cup; do it before the Pick |
| C27 | Open something not openable (shelf) | arrived=shelf@living room | Open shelf@living room | ok | ok | ERR shelf in the living room cannot be opened or closed |
| C28 | TurnOn an appliance already on | arrived=lamp found=lamp on=['lamp'] | TurnOn lamp | ok | ERR 'lamp' is already on | ok |
| C29 | TurnOff an appliance that is off | arrived=lamp found=lamp | TurnOff lamp | ERR turnoff_not_on | ERR 'lamp' is not on | ok |
| C30 | Wash an object neither held, found nor at | arrived=table@kitchen | Wash cup | ERR interact_without_find | ERR Wash requires 'cup' to be held or found | ok |
| C31 | Serve with an empty hand | (empty) | Serve cup | ok | ERR Serve requires holding an object | ok |
| C32 | Unknown action | (empty) | Fly cup | ERR unknown_action | ok | ERR the robot has no action Fly (use MoveTo, Find, Pick, Place, PutIn, Open, Close) |

C04 used the goal `cup: from table@kitchen to shelf@living room`.

23 of 32 cases got different verdicts (ok / warning / error) from the three checkers. Examples: "Open a
drawer twice" (C20) is an error only for `symbolic_check`; "Open while holding" (C21) only for `_check`;
"Close an already closed drawer" (C25) is an error for embodiplan and `symbolic_check` but not for `_check`;
"Place at another place" (C13) passes `symbolic_check`; "PutIn into a closed drawer" (C16) passes
embodiplan because robot place keys (`drawer@bedroom`) never matched its container names (`Drawer`).

### Effects

| id | case | world | step | embodiplan _apply | mapper.apply_effect | AssistLoop._effect | kcare apply_skill_effect |
|---|---|---|---|---|---|---|---|
| E01 | MoveTo away after Find (found reset) | arrived=table@kitchen found=cup | MoveTo shelf@living room | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=table@kitchen found=cup hold=None open=[] on=[] |
| E02 | MoveTo the found handle | arrived=table@kitchen found=drawer@bedroom | MoveTo drawer@bedroom | arr=drawer@bedroom found=drawer@bedroom hold=None open=[] on=[] | arr=drawer@bedroom found=drawer@bedroom hold=None open=[] on=[] | arr=drawer@bedroom found=None hold=None open=[] on=[] | arr=table@kitchen found=drawer@bedroom hold=None open=[] on=[] |
| E03 | Pick (no object) after Find | arrived=table@kitchen found=cup | Pick | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold= open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] |
| E04 | Pick naming another object than found | arrived=table@kitchen found=apple | Pick cup | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=apple open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=apple open=[] on=[] |
| E05 | Place | arrived=shelf@living room holding=cup | Place shelf@living room | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] |
| E06 | Open | arrived=drawer@bedroom | Open drawer@bedroom | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] |
| E07 | Close | arrived=drawer@bedroom opened=['drawer@bedroom'] | Close drawer@bedroom | arr=drawer@bedroom found=None hold=None open=[] on=[] | arr=drawer@bedroom found=None hold=None open=[] on=[] | arr=drawer@bedroom found=None hold=None open=[] on=[] | arr=drawer@bedroom found=None hold=None open=[] on=[] |
| E08 | TurnOn | found=lamp | TurnOn lamp | arr=None found=lamp hold=None open=[] on=['lamp'] | arr=None found=lamp hold=None open=[] on=['lamp'] | arr=None found=lamp hold=None open=[] on=[] | n/a |

Effects disagreed on E02 (`_effect` always clears `found` on MoveTo), E03 (`_effect` holds `""` after a
Pick without an object), E04 (`apply_effect` holds the found object, ignoring the Pick's object) and E08
(`_effect` ignores TurnOn). kcare's `apply_skill_effect` leaves `arrived` to the sensor reconcile on
purpose (E01, E02).

## Decided semantics (action model 1.0)

The rules now live once in `embodiplan.action_model` (`check`, `apply`, `simulate`,
`ACTION_MODEL_VERSION = "1.0"`, see embodiplan `CHANGELOG.md`). The strictest sensible rule was taken
for each disagreement:

| case | rule | code |
|---|---|---|
| C01, C03 | MoveTo / Find / Open / Close / TurnOn / TurnOff need an object | `empty_object` |
| C02 | MoveTo where the robot already is: warning only | `already_there` |
| C04 | Find away from where the object is believed to be: error, when the caller passes `object_places` (the plan checker passes the user's "from") | `find_wrong_place` |
| C05 | Find before any MoveTo: allowed (the robot may see it from where it stands) | |
| C06 | Find the object already in the hand | `find_held` |
| C07, C09 | Pick needs the last Find to be that object | `no_find_before_pick`, `pick_not_found` |
| C08, C11 | Pick with a full hand | `already_holding` |
| C10 | Pick without an object takes the found one | |
| C12 | Place / PutIn with an empty hand | `place_without_holding` |
| C13, C19 | Place / PutIn need the robot at the receptacle (PutIn: or the container found, when Find reaches) | `place_wrong_location` |
| C15 | Place puts the object *on* the receptacle; a closed drawer is not an error (kcare_bucheon's `drawer` is "on top of the chest of drawers") | |
| C16, C18 | PutIn needs a *container* to be open; a non-container (shelf) does not | `putin_closed_container` |
| C20, C28 | Open what is open / TurnOn what is on | `already_open`, `already_on` |
| C25, C29 | Close what is not open / TurnOff what is off | `close_not_opened`, `turnoff_not_on` |
| C21, C26 | Open / Close with a full hand: error only with one hand (`Capabilities.hands < 2`; default one hand) | `hand_full` |
| C22, C23, C30 | Open / Close / TurnOn / TurnOff / Wash need the robot at the target, or the target found when `find_reaches` (Wash: or held). robot_agent uses `find_reaches=False`: its Find only looks | `interact_without_find` |
| C27 | Open / Close of a non-openable target: error when the caller passes `openable` (the plan checker and the step guard pass the ENV) | `not_openable` |
| C31 | Serve with an empty hand: warning only | `serve_without_holding` |
| C32 | Action outside the vocabulary (`actions`, default embodiplan's `ROBOT_ACTIONS`) | `unknown_action` |

Effects: MoveTo sets `arrived` and clears `found` unless the destination is the found handle; Find sets
`found`; Pick holds the step's object (or the found one) and clears `found`; Place / PutIn empty the hand;
Open / Close / TurnOn / TurnOff toggle `opened` / `on`.

Capabilities used by robot_agent: `action_model.ROBOT_CAPABILITIES = Capabilities(hands=1,
find_reaches=False)` (`robot_agent/core/planning/action_model.py`); per-robot values move to the manifest
with the RobotAdapter (step 4). Context rules (C04, C27, C32's robot vocabulary) need goals / ENV / the
skill map: the plan checker passes all of them, the step guard passes the ENV (`openable`), the post-step
verify has none and lets them through (the mapper rejects actions the robot has no skill for).

## After (branch arch/action-model)

### Preconditions

| id | case | world | step | action_model.check (defaults) | action_model.check (robot caps + ENV/goal context) | embodiplan verifier | verify.symbolic_check | AssistLoop._check |
|---|---|---|---|---|---|---|---|---|
| C01 | MoveTo without destination | (empty) | MoveTo | ERR empty_object | ERR empty_object | ERR empty_object | ERR MoveTo requires a destination object | ERR MoveTo requires a destination object |
| C02 | MoveTo where already there | arrived=table@kitchen | MoveTo table@kitchen | warn already_there | warn already_there | ok | ok | warn the robot is already there |
| C03 | Find without object | arrived=table@kitchen | Find | ERR empty_object | ERR empty_object | ERR empty_object | ERR Find requires a target object | ERR Find requires a target object |
| C04 | Find at a place the object is not (believed on the table, robot at the shelf) | arrived=shelf@living room | Find cup | ok | ERR find_wrong_place | ok | ok | ERR the user said it is on table in the kitchen; MoveTo table@kitchen first |
| C05 | Find before any MoveTo | (empty) | Find cup | ok | ok | ok | ok | ok |
| C06 | Find the object already in the hand | arrived=table@kitchen holding=cup | Find cup | ERR find_held | ERR find_held | ERR find_held | ERR 'cup' is already in the hand | ERR already done (the cup is in the hand); delete this step |
| C07 | Pick without Find | arrived=table@kitchen | Pick cup | ERR no_find_before_pick | ERR no_find_before_pick | ERR no_find_before_pick | ERR Pick requires a preceding Find of the target object | ERR needs 'Find cup' just before it, at the same place |
| C08 | Pick while holding another object | arrived=table@kitchen found=apple holding=cup | Pick apple | ERR already_holding | ERR already_holding | ERR already_holding | ERR cannot Pick while already holding 'cup' | ERR the hand already holds the cup; Place it first |
| C09 | Pick an object other than the found one | arrived=table@kitchen found=apple | Pick cup | ERR pick_not_found | ERR pick_not_found | ERR pick_not_found | ERR Pick 'cup' but the last Find was 'apple' | ERR needs 'Find cup' just before it, at the same place |
| C10 | Pick (no object) after Find | arrived=table@kitchen found=cup | Pick | ok | ok | ok | ok | ok |
| C11 | Pick the object already held | arrived=table@kitchen holding=cup | Pick cup | ERR already_holding,no_find_before_pick | ERR already_holding,no_find_before_pick | ERR already_holding | ERR cannot Pick while already holding 'cup' | ERR already done by an earlier step (the cup is in the hand); delete this step |
| C12 | Place without holding | arrived=shelf@living room | Place shelf@living room | ERR place_without_holding | ERR place_without_holding | ERR place_without_holding | ERR Place requires holding an object | ERR nothing is in the hand |
| C13 | Place at another place than the robot is at | arrived=table@kitchen holding=cup | Place shelf@living room | ERR place_wrong_location | ERR place_wrong_location | ERR place_wrong_location | ERR Place requires MoveTo 'shelf@living room' first (currently at 'table@kitchen') | ERR the robot must MoveTo shelf@living room first |
| C14 | Place where the robot is | arrived=shelf@living room holding=cup | Place shelf@living room | ok | ok | ok | ok | ok |
| C15 | Place on a closed drawer (its top is a surface) | arrived=drawer@bedroom holding=cup | Place drawer@bedroom | ok | ok | ok | ok | ok |
| C16 | PutIn into a closed drawer | arrived=drawer@bedroom holding=cup | PutIn drawer@bedroom | ERR putin_closed_container | ERR putin_closed_container | ERR putin_closed_container | ERR PutIn requires container 'drawer@bedroom' to be Open | ERR drawer in the bedroom is closed; Open it before the Pick |
| C17 | PutIn into an open drawer | arrived=drawer@bedroom holding=cup opened=['drawer@bedroom'] | PutIn drawer@bedroom | ok | ok | ok | ok | ok |
| C18 | PutIn into a non-container (shelf) | arrived=shelf@living room holding=cup | PutIn shelf@living room | ok | ok | ok | ok | ok |
| C19 | PutIn into an open drawer the robot is not at | arrived=table@kitchen holding=cup opened=['drawer@bedroom'] | PutIn drawer@bedroom | ERR place_wrong_location | ERR place_wrong_location | ERR place_wrong_location | ERR PutIn requires MoveTo 'drawer@bedroom' first (currently at 'table@kitchen') | ERR the robot must MoveTo drawer@bedroom first |
| C20 | Open a drawer twice | arrived=drawer@bedroom opened=['drawer@bedroom'] | Open drawer@bedroom | ERR already_open | ERR already_open | ERR already_open | ERR Open 'drawer@bedroom' but it is already Open | ERR drawer in the bedroom is already open; remove this step |
| C21 | Open while holding (single hand) | arrived=drawer@bedroom holding=cup | Open drawer@bedroom | ERR hand_full | ERR hand_full | ERR hand_full | ERR cannot Open 'drawer@bedroom' while holding 'cup' (one hand) | ERR the robot cannot open it while holding the cup; do it before the Pick |
| C22 | Open a drawer the robot is not at (nor found) | arrived=table@kitchen | Open drawer@bedroom | ERR interact_without_find | ERR interact_without_find | ERR interact_without_find | ERR Open requires a preceding Find of (or MoveTo) 'drawer@bedroom' | ERR the robot must MoveTo drawer@bedroom first |
| C23 | Open a found drawer the robot is not at (Find only looks) | arrived=table@kitchen found=drawer@bedroom | Open drawer@bedroom | ok | ERR interact_without_find | ok | ERR Open requires a preceding Find of (or MoveTo) 'drawer@bedroom' | ERR the robot must MoveTo drawer@bedroom first |
| C24 | Open a closed drawer the robot is at | arrived=drawer@bedroom | Open drawer@bedroom | ok | ok | ok | ok | ok |
| C25 | Close an already closed drawer | arrived=drawer@bedroom | Close drawer@bedroom | ERR close_not_opened | ERR close_not_opened | ERR close_not_opened | ERR Close 'drawer@bedroom' but it is not Open | ERR drawer in the bedroom is not open; remove this step |
| C26 | Close while holding (single hand) | arrived=drawer@bedroom holding=cup opened=['drawer@bedroom'] | Close drawer@bedroom | ERR hand_full | ERR hand_full | ERR hand_full | ERR cannot Close 'drawer@bedroom' while holding 'cup' (one hand) | ERR the robot cannot close it while holding the cup; do it before the Pick |
| C27 | Open something not openable (shelf) | arrived=shelf@living room | Open shelf@living room | ok | ERR not_openable | ok | ok | ERR shelf in the living room cannot be opened or closed |
| C28 | TurnOn an appliance already on | arrived=lamp found=lamp on=['lamp'] | TurnOn lamp | ERR already_on | ERR already_on | ERR already_on | ERR TurnOn 'lamp' but it is already On | ERR TurnOn 'lamp' but it is already On |
| C29 | TurnOff an appliance that is off | arrived=lamp found=lamp | TurnOff lamp | ERR turnoff_not_on | ERR turnoff_not_on | ERR turnoff_not_on | ERR TurnOff 'lamp' but it is not On | ERR TurnOff 'lamp' but it is not On |
| C30 | Wash an object neither held, found nor at | arrived=table@kitchen | Wash cup | ERR interact_without_find | ERR interact_without_find | ERR interact_without_find | ERR Wash requires a preceding Find of (or MoveTo) 'cup' | ERR Wash requires a preceding Find of (or MoveTo) 'cup' |
| C31 | Serve with an empty hand | (empty) | Serve cup | warn serve_without_holding | warn serve_without_holding | ok | ok | warn Serve with an empty hand |
| C32 | Unknown action | (empty) | Fly cup | ERR unknown_action | ERR unknown_action | ERR unknown_action | ok | ERR the robot has no action Fly (use MoveTo, Find, Pick, Place, PutIn, Open, Close) |

All paths agree. `action_model.check (defaults)` differs from the robot column only where the robot's
capabilities or context change the rule (C04, C23, C27). `symbolic_check` passes C04, C27 and C32 because
it has no goals, ENV or skill map (see above); with `openable=` (the step guard) it rejects C27.

### Effects

| id | case | world | step | action_model.apply | embodiplan _apply | mapper.apply_effect | AssistLoop._effect | kcare apply_skill_effect |
|---|---|---|---|---|---|---|---|---|
| E01 | MoveTo away after Find (found reset) | arrived=table@kitchen found=cup | MoveTo shelf@living room | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=table@kitchen found=cup hold=None open=[] on=[] |
| E02 | MoveTo the found handle | arrived=table@kitchen found=drawer@bedroom | MoveTo drawer@bedroom | arr=drawer@bedroom found=drawer@bedroom hold=None open=[] on=[] | arr=drawer@bedroom found=drawer@bedroom hold=None open=[] on=[] | arr=drawer@bedroom found=drawer@bedroom hold=None open=[] on=[] | arr=drawer@bedroom found=drawer@bedroom hold=None open=[] on=[] | arr=table@kitchen found=drawer@bedroom hold=None open=[] on=[] |
| E03 | Pick (no object) after Find | arrived=table@kitchen found=cup | Pick | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] |
| E04 | Pick naming another object than found | arrived=table@kitchen found=apple | Pick cup | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=cup open=[] on=[] | arr=table@kitchen found=None hold=apple open=[] on=[] |
| E05 | Place | arrived=shelf@living room holding=cup | Place shelf@living room | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] | arr=shelf@living room found=None hold=None open=[] on=[] |
| E06 | Open | arrived=drawer@bedroom | Open drawer@bedroom | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] | arr=drawer@bedroom found=None hold=None open=['drawer@bedroom'] on=[] |
| E07 | Close | arrived=drawer@bedroom opened=['drawer@bedroom'] | Close drawer@bedroom | arr=drawer@bedroom found=None hold=None open=[] on=[] | arr=drawer@bedroom found=None hold=None open=[] on=[] | arr=drawer@bedroom found=None hold=None open=[] on=[] | arr=drawer@bedroom found=None hold=None open=[] on=[] | arr=drawer@bedroom found=None hold=None open=[] on=[] |
| E08 | TurnOn | found=lamp | TurnOn lamp | arr=None found=lamp hold=None open=[] on=['lamp'] | arr=None found=lamp hold=None open=[] on=['lamp'] | arr=None found=lamp hold=None open=[] on=['lamp'] | arr=None found=lamp hold=None open=[] on=['lamp'] | n/a |

`action_model.apply`, embodiplan `_apply`, `ActionMapper.apply_effect` and `AssistLoop._effect` are the same
function now (the last two add only robot extras: `holding_since`, `holding_pose`, object memory).

## Summary

| | before | after |
|---|---|---|
| precondition cases with disagreeing verdicts (of 32) | 23 | 0 |
| effect cases with disagreeing results (of 8, kcare excluded) | 4 | 0 |
| copies of the rules | 5 (+ the inline guard) | 1 (`embodiplan.action_model`) + kcare (below) |
| robot_agent access to embodiplan internals | `_refine`, `_last_blocks`, `_retrieve_block`, `_flatten` | none (`edit_block`, `last_blocks`, `retrieve_examples`, `sage.flatten`) |

The inline guard in `AssistLoop.run_blocking` still checks "at the place" and "hand full" before calling
`symbolic_check` (only to keep its messages for SAGE's replan prompt); both are the model's rules, so it
cannot disagree with it.

## kcare `apply_skill_effect` (now delegates)

It is keyed on kcare skill names and runs for raw open-loop / direct skills. It now delegates like this
(robot_agent's public API only; branch arch/wiring):

```python
from robot_agent.core.planning import action_model     # robot_agent's facade of embodiplan.action_model

_ACTION_OF = {"find": "Find", "find_grasp": "Find", "find_once": "Find", "pick": "Pick", "grasp": "Pick",
              "place": "Place", "put": "Place", "putin": "PutIn", "put_in": "PutIn", "give": "Place",
              "open_drawer": "Open", "open": "Open", "close_drawer": "Close", "close": "Close"}

def apply_skill_effect(world, skill, params=None, result=None, node=None):
    ...  # unchanged: failed skill -> return; obj = _arg_object(...); containers -> _container_key(...)
    action = _ACTION_OF.get(s)
    if action:                                   # 'move' stays with the sensor reconcile (arrived)
        prior_fp = world.found_pose
        action_model.apply({"action": action, "object": obj}, world, inplace=True)
        ...  # kcare extras unchanged: found_pose on Find, holding_since / holding_pose on Pick / Place
```

It passes the found object to the Pick (`world.found or obj`), so E04 keeps kcare's behaviour: the grasp
holds what find found (`_arg_object` keeps only the first word of the arg, `water bottle` -> `water`).
With that, the kcare column equals `action_model.apply` on every row except `arrived` (E01, E02), which
stays with the sensor reconcile on purpose.

## Post-step verify (decided)

After a skill reports success, the post-step check no longer re-runs the preconditions
(`symbolic_check` on the pre-step world): it applies the effects and checks the skill's declared
`effects` plus its VerifySpecs (see docs/contract.md). Preconditions are only for the pre-step guard and
plan certification. In `ClosedLoop`, which has no pre-step guard, a successful skill whose preconditions
did not hold in the belief (e.g. a Pick the belief has no Find for) used to be rejected and replanned;
it now stands.
