# Domain profiles

A domain profile is the knowledge about *what a robot is asked to do*, kept as data instead of code. It answers
questions the core used to answer with household assumptions: which goal kinds exist, how a goal is read back,
which products must not go where, which words mean "put it inside" or "bring me", and which words make a refusal
genuine.

Base commit: `5d7ca38` (branch `sage-v2/homeloop`); the work is on branch `domain-profile`. The diff is mechanical:
the tables moved out of Python into YAML, and the functions that read them take an optional profile argument.

## Choosing a profile

A robot pack adds one key to its `manifest.yaml`:

```yaml
domain: delivery
```

Without the key the profile is `household`, so a pack that does not declare one behaves exactly as before. The
manifest check rejects a name that has no folder under `robot_agent/domains/`. `core/domain.py` is the loader:
`domain.load(name)`, `domain.of(robot)`, `domain.available()`. `AssistLoop.domain` is the profile of its robot.

## Files of a profile

`robot_agent/domains/<name>/`:

| file | holds | read by |
|---|---|---|
| `goals.yaml` | goal kinds (the actions each needs, the understand-prompt line), the manipulation / template kind lists, the read-back phrases of the template kinds | `core/planning/goals.py`, `assist_loop.py` |
| `hazards.yaml` | hazard classes (regex lists) and the rules (hazard class x place class -> refused, with what the robot says) | `core/safety/hazards.py` |
| `places.yaml` | place classes inferred from a place's names (first match wins) | `core/safety/place_class.py` |
| `lexicon.yaml` | put-in / on-top / hand-over / first-person / recipient / tell word lists, Korean particles | `core/planning/lexicon.py` |
| `refusal.yaml` | `safety` and `capability` regexes: words that make a refusal after a heard answer genuine | `understand.typed_refusal` |

The module-level names (`goals.GOAL_KINDS`, `hazards.HAZARDS`, `lexicon.PUTIN`, ...) are the household profile's
tables, loaded once at import. A function that must follow the robot takes `dom` (a `Domain`) and uses that
profile instead: `goals.kinds_for(actions, dom)`, `goals.summary(g, dom)`, `check_goal(..., dom)`,
`typed_refusal(..., dom)`. `lexicon.has / patterns` find the profile through the `robot` argument they already
had. A robot pack's own additions (manifest `safety.hazards`, i18n phrases) are applied on top of the profile as before.

## The two profiles

* `household`: what the core always knew. The four manipulation kinds (on, in, open, close) and the three template
  kinds (go, say, deliver); the cleaning-chemical rule for food places; the household lexicon. Its YAML is the old
  Python tables, value for value.
* `delivery`: for a base-only robot. No manipulation kinds, so the understand schema offers only `go`, `say` and
  `deliver`; no hazard classes, no place classes; empty put-in / on-top / hand-over lists; refusal words for a
  robot that cannot lift. Docking is a `go` to the dock place and waiting for a person to unload is the end of a
  `deliver`, so no extra kind is needed.

## What is still in code (not moved)

The understand prompt text ("You turn a household robot task into goals", the "It cannot" list), the i18n
read-back keys (`rb_*`) and the English texts next to them in `AssistLoop._goal_phrase`, and the `compile_steps` /
`reached` rules of the template kinds. A new template kind needs a code change there; a new profile that reuses
the existing kinds needs none.

## Behaviour pin

With the default profile SAGE behaves identically. The check (`homeloop_bench/results/r4a/pin.py`): a `--llm fake`
run of 12 dev tasks, EN and KO, for sage, sage_ask and sage_norag, byte-identical before and after; an offline
replay of the milestone-10 round-7 cache for the first 5 dev tasks with the same outcomes; the robot_agent suite
green. Result in `results/r4a/pin.json`. Tests: `tests/test_domain.py`.

## Adding a profile

Copy `domains/delivery/`, edit the five files, keep `goals.yaml`'s `template_kinds` within the kinds the code can
compile (go, say, deliver), and select it with `domain: <name>` in the pack's manifest. Write no benchmark text and
no task id into a profile.
