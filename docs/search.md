# Search with a belief

SAGE-v2 roadmap P1.13. Code: `robot_agent/core/planning/search.py` (pure, no robot, no LLM
client), hooked into `AssistLoop` by the `SearchHooks` mixin (`core/planning/search_hooks.py`, the
`_search_*` methods) at a few call sites in `assist_loop.py`.
Tests: `tests/test_v2_search_belief.py` (the module), `tests/test_v2_search_loop.py` (the hooks),
scenarios `scenarios/tests/test_t1_v2_search.py` (SR01-SR05).

## Why

The SAGE bench lost most autonomous runs on search (report section 4: FIND1, MOVED, INDR1). The
robot had no prior of where an object is, the planner chose the next place on every replan (an
LLM call each time, and it went back to places already searched), and it never looked inside a
closed drawer it had just looked at.

## What the belief holds (one `SearchBelief` per run)

| part | source | rule |
|---|---|---|
| prior | object memory (`object_memory.<host>.json`: last place + time + how), the user's answers, a Find that saw it | confidence `0.5 ** (age / half_life_s)`; a miss at a place removes that sighting |
| ranking | one LLM call per object per run (`RANK_PROMPT`, JSON `{"places": [...]}`) | cached, also when it failed; never asked again on a replan |
| searched | each Find miss with an `absent` code (the recovery policy's `mark_searched`) | per object; a hard constraint (below) |
| experience | the planner's live memory (`live_path`): each episode's `Pick <obj>` at the place of the `MoveTo` before it (`search_experience.py`; records `verified: false` skipped, the last 2000 lines) | `habit_weight * share of the picks there`; no decay, never a source, in the ranking prompt as "In past tasks in this home the robot picked the X up at P (n times)" |

Order of the places for an object: `score = prior confidence + habit_weight * habit share +
rank_weight * (n - i) / n` for the i-th of n ranked places, ties in ENV order. A 1-minute-old sighting
(about 1.0) beats where the robot always picked it up (0.75), which beats the top of the ranking (0.5);
a 3-day-old sighting (half-life 6 h: about 0.0002) beats none of them. Why the experience: the ranking
is an LLM's commonsense ("an apple is in the kitchen"; T2 qwen3 ranked the desk last), while the planner
that it overrides retrieves the home's own episodes (the apple is kept on the desk): after the v2
search the robot searched the living room and gave up (T2 S06, H04, H07, M04).

## When the core acts

1. **Grounding** (`_remembered`): a goal without a source gets the object memory's place only when
   that sighting is still trusted (`confidence >= trust`). In `prior` mode the memory is used as
   before, however old.
2. **Plan start** (`_search_start`): the belief is built and seeded from the memory. An object whose
   sightings are all stale gets the ranking first (one call) and the best place as its `from`.
   Objects with no sighting at all cost nothing: the planner's first guess stands.
3. **A Find miss** (`_recover_find` -> `_search_missed`): the place is marked searched for that
   object, the ranking is asked if it was not yet, and the next leg is chosen:
   * a container at this place that was closed and that the robot can open (manifest `Open`):
     open it and look again before moving on;
   * else the best place not searched yet; a closed container there is opened first; a closed
     container the robot cannot open is not a place it can look at;
   * nowhere left: the person is asked where it is at once (else after `search_max` misses, as
     before); no answer ends the run `failed` with code `absent`.
   The replan reason says `Look next at <place>`; the observation the planner gets has a line
   `Search for the <object>: look next at <leg>`.
4. **Every replan suffix** (`_replan_suffix` -> `_search_steer`): the first Find of each object
   still searched for is put on the belief's leg when the planner's look goes to a place already
   searched (nothing changed there), into a closed container without an Open, or (when the belief
   has a preference) elsewhere than the belief's best place. The steps around it are kept; a Place
   that relied on the planner's place gets its MoveTo back. The `replan` event carries
   `search: [notes]` when a suffix was steered.
5. **After a step** (`_search_effect`): a Find that saw the object ends its search, and a look for it
   right after elsewhere before its Pick (`MoveTo Q`, Open / Close, `Find` it) is removed
   (`search.skip_relook`; a step acting at Q gets its MoveTo back: T2 S02 left the found banana on the
   dining table to look on the counter); a Pick / Place /
   PutIn at a place voids every search there (the robot moved something); an Open there makes a
   search made while it was closed void too (the `opened` set is compared).

The planner still writes every plan. The core only moves the search leg of a suffix, as the
policy above says; it adds no other steps.

## A miss is not proof of absence (the detector's false-negative rate)

An object detector does not see an object that is in view on some looks. The rate is the robot's own
number: manifest `planner.detector_miss_rate` (0 to 0.9, default 0.1), the option `detector_miss_rate`
or `ROBOT_AGENT_DETECTOR_MISS_RATE` (option > env > manifest > 0.1). The core never reads a simulator's or
a benchmark's profile for it; a pack that knows its detector sets it, a pack that does not gets the default.

`SearchBelief.posterior(obj)` is the place's score (sighting, experience, ranking, plus a small floor so no
place is impossible) times `miss_rate ** misses at that place`, normalised. A sighting that a miss removed
still counts in the prior, so a place where the object was seen a minute ago stays probable after one miss
(weight x0.1) and is looked at again before the unlikely rest:

* **Before the search is used up** (`relook_beats_next`): the look at a searched place whose posterior is higher
  than every place not looked at yet replaces the next leg. Never the place the robot is at.
* **When the search is used up** (`_second_look`): the searched places in order of posterior, at most
  `relook_budget()` of them: 0 for a rate of 0 (a perfect detector is believed), else `round(20 x rate)` from 1
  to 4 (2 at the default rate, as before). Ties go to the place looked at first.

Misses survive `unsearch` (the count is for the whole run) and `to_dict` / `load`.

## Searched places are a hard constraint

Kept on the run context: they survive every replan, a pause / resume and a task-stack resume of
the same run. `SearchBelief.to_dict()` / `load()` keep them for a run that is rebuilt. A place is
searched again only when something changed there: a container at it was opened since, or the
robot picked / placed an object there, or the person said the object is there.

## Robot-agnostic

The core knows no place or container names. Places are the ENV keys; "is it a container" is the
adapter's `is_openable(key, entry)` (`RobotView.openable`); containers at a place come from an
optional adapter method `containers(place, entry) -> [ENV keys]`, else the ENV entry's
`containers` list, else the place itself when it opens. "Can the robot open it" is whether its
manifest has an `Open` action. A robot without Open (base_robot, an arm without a drawer skill)
never gets an Open step from the search.

## Configuration

| env var | per-run option | default | meaning |
|---|---|---|---|
| `ROBOT_AGENT_SEARCH` | `search` | `1` | `0`: the old behaviour (no belief, no ranking, no steering) |
| `ROBOT_AGENT_SEARCH_RANK` | `search_rank` | `llm` | `prior`: no ranking call, the prior alone |
| `ROBOT_AGENT_SEARCH_HALF_LIFE_S` | `search_half_life_s` | `21600` | half-life of a sighting's confidence |
| `ROBOT_AGENT_SEARCH_TRUST` | | `0.5` | a sighting at least this confident is believed without a ranking |
| `ROBOT_AGENT_SEARCH_RANK_WEIGHT` | | `0.5` | weight of the ranking against the prior |
| `ROBOT_AGENT_SEARCH_HABIT_WEIGHT` | `search_habit_weight` | `0.75` | weight of the experience prior; `0`: off (the v2 search) |
| `ROBOT_AGENT_DETECTOR_MISS_RATE` | `detector_miss_rate` | manifest `planner.detector_miss_rate`, else `0.1` | the detector's false-negative rate (posterior of a place after a miss; second-look budget) |

The ranking counts against the run's LLM budget (`ROBOT_AGENT_LLM_BUDGET`); a failed or
over-budget ranking leaves the prior alone, it never ends the run.

## Cost

T1 scenarios (scripted LLM, oracle planner): the 35 command scenarios have no search, so their
LLM calls (37 in total) and skill calls (345) do not change. Fault scenarios with a Find miss
(FIND1-4, DROP1, MOVED, LLM_REPL) make one more LLM call (the ranking), with the same skill
calls. With the real SAGE planner every miss still costs its replan call; what the ranking saves
is the extra looks at places already searched and the wrong next guesses.

## Limits

* One ranking per object per run: a ranking made before the first look does not know the misses
  that follow (they are excluded by the hard constraint instead).
* The memory keeps one place per object; older sightings are lost when it is overwritten.
* `ClosedLoop` (planner `grace`) has no search belief; only the SAGE + assist loop has.
