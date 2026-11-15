# Safety profile: what is hazardous for ONE robot

Many things are dangerous for a household robot and not for a factory robot: bleach next to food, a knife near a child. So
the core does not know which product, place or person is a hazard. A robot pack declares it as data, its **safety profile**.
The core ships only the engine (`robot_agent/core/safety/`): the loader and validator (`profile.py`), the class matchers
(`place_class.py`, `receiver_class.py`), the rule matcher (`hazards.py`) and `EMPTY`, a profile with no class and no rule.
A robot that declares no profile gets no hazard refusal (fail open), the same as a language with no pack.

Rule 1 of [../CLAUDE.md](../CLAUDE.md) covers this: no domain facts in the engine. A test
(`tests/test_safety_profiles.py`) checks that no class or rule id of any shipped test profile appears in `core/safety/*.py` and
that `lang/*.json` holds none of the old hazard keys.

## Declaring a profile

In `manifest.yaml`:

```yaml
safety:
  profile: safety/household.yaml    # a path relative to manifest.yaml, or an inline mapping with the same schema
  hazards: [...]                    # legacy extra rules {objects | hazard, places, say}; still read (see below)
```

`Manifest.safety_profile` holds the loaded profile (`None` = none declared). `core.safety.profile_of(robot)` returns it or
`EMPTY`. A profile that does not validate makes the manifest invalid; the errors name the field path
(`safety.profile.rules[1].receivers: 'ghost' is not a declared receiver class`).

## Schema

```yaml
profile: household                  # name (required; the bench records name + sha256 of the file)
version: 1
description: free text              # optional
sources:                            # every provenance id used below
  chop_cleaners:
    url: https://...                # required
    opened: "2026-11-04"
    supports: "what the opened page says"      # or `quote:` (exact words)
    status: verify                  # optional; --strict fails while any source says `verify`
checks: ["on", "in"]                # goal kinds looked at (default on, in). Quote "on": YAML 1.1 reads a bare `on` as true
                                    # (the loader maps true back to "on")
hazard_classes:                     # ORDERED: the first class whose words match wins
  cleaning_chemical:
    words:                          # per language code: regexes on the lower-cased object text
      en: {sourced: ["\\bbleach\\b"], assumed: ["\\bdetergent\\b"]}
      ko: {sourced: ["표백제"], assumed: ["세제"]}
    sources: [chop_cleaners]        # required when any `sourced` word exists
place_classes:                      # ORDERED; matched on  key head | aliases | category   (never on ENV `tags`)
  food_storage: {words: {en: ["\\bfridge\\b"], ko: ["냉장고"]}, provenance: assumed}
receiver_classes:                   # person entries of the ENV only (the entry has a `person` field)
  vulnerable_person: {words: {en: ["\\b(?:child|kids?)\\b"]}, fields: [age_group, category], provenance: assumed}
rules:
  - id: chem_food                   # required, unique
    hazard: cleaning_chemical       # exactly one of `hazard` (a class) and `objects` (regexes on the object text)
    places: [food_storage]          # exactly one of `places` (a declared place class, or any other string = a regex on the
                                    #   place key) and `receivers` (declared receiver classes)
    say: "cleaning chemicals do not go with food"    # prompt language; spoken through the translator like any robot sentence
    provenance: assumed             # `assumed`, or {source: <id>} when an opened page states the rule itself
    within_m: 0                     # optional, metres (place rules only): also forbid a place LESS than this far from a place of
                                    #   the rule's classes on the map (ENV `loc`; same room when both keys name one); 0 = off
  - id: chem_child
    hazard: cleaning_chemical
    receivers: [vulnerable_person]
    say: "cleaning chemicals must not be handed to a child"
    enabled_by: hazard.receivers    # optional option key: the rule runs only when the run turns that option on
refusal_marks:                      # optional: words that make a typed refusal believable after an answer heard through the microphone
  safety:     {en: ["\\b(?:knife|fire)\\b"], ko: ["칼|불"]}
  capability: {en: ["\\b(?:cook|wash)\\b"]}
```

Language codes are free (`en`, `ko`, `xx`); the class words are the union over all languages of the profile, so a profile
for a language the core has never seen works. A language the profile has no words for is classified through the words of
the other languages the ENV also names (the English head of a key), and otherwise fails open.

`--strict` of `python -m robot_agent.contract.check_manifest` additionally fails on `status: verify` and prints the counts
(classes, rules, sourced vs assumed words, sha256 of the profile).

The validator rejects: unknown keys; a regex that does not compile; a language key that is not a code; a rule that names an
undeclared class; a rule with both or none of `places` / `receivers`, or both or none of `hazard` / `objects`; a rule with no
`id` or `say`; a sourced word list without `sources`; a source id that is not in `sources`; a bad `enabled_by`.

## Engine semantics

`find_rule(goal, places, profile, pack_rules, mode, enabled)` returns a `Verdict(say, rule_id)` or None;
`check_goal` returns the `say`.

1. `mode == "off"`, or a goal kind not in `profile.checks`: no check.
2. The object text is the goal's `object` and `said`, lower case.
3. Place rules first: the profile's rules in file order, then the legacy manifest list. Mode `pack` keeps the legacy list only.
   A place that is a declared place class is compared by class; any other string is a regex on the place key.
   A rule with `within_m` also fires when a place of its declared classes is less than `within_m` metres from the goal's place
   (`near_class`, the ENV's `loc`; persons and places with no `loc` take no part).
4. Receiver rules (the goal's `to` or `near_person` is a person entry of a named receiver class) run in every mode but `off`.
5. A rule with `enabled_by` runs only when that option key is in `enabled`. The assist loop passes `hazard.receivers`
   when the run option `hazard_receiver` is on.
6. The first rule that fires wins.

The assist loop reads the options in one place (`AssistLoop._verdict`, `_forbids`): `hazard_rules` = `core` (alias `profile`;
the profile's rules and the legacy list) | `pack` (legacy list only, deprecated) | `off` (the model's own `unsafe` only).
`typed_refusal` reads `profile.refusal_marks`; no marks means a refusal stands.
Option `safety_profile` (default off) declares a second profile in `planner.options.safety_profile.profile`; the `hazard` gate asks
its rules (service `safety_rule`) after the robot's own (docs/options.md).

## The `refusals` entry of the `done` event

When the safety rules refuse a goal, the `done` event of the run (every final status: success with a partial refusal, failed,
refused) carries `refusals`, next to the string list `refused_parts`:

```json
"refusals": [{"kind": "safety", "rule": "chem_child", "why": "cleaning chemicals must not be handed to a child",
              "goal": {"type": "on", "object": "bleach bottle", "said": "", "from": "", "to": "child", "near_person": ""}}]
```

`rule` is the profile rule id (`""` for a legacy rule). A benchmark judge reads this to credit a correct partial refusal. Nothing
the loop does or says depends on it. The key is absent when nothing was refused.

## Profiles in this repo

Only test fixtures (`tests/fixtures/safety/`), not shipped:

- `household_fixture.yaml`: a copy of the household robot's profile (English, Korean). `tests/golden/safety_household_r17.json`
  is the decision table of the r17 core tables (about 28 thousand goal x mode decisions); the engine with this fixture
  reproduces it exactly.
- `factory_illustrative.yaml`: illustrative, every entry `assumed`, not a safety assessment. Same engine, other decisions:
  bleach into a fridge is allowed, acetone onto a welding bay is refused.
- `xx_language.yaml`: a made-up language.

A robot pack ships its own profile (the homeloop household robot: `homeloop_robot/configs/safety/household.yaml`).

## Migration from r17

The core's household tables are gone: `HAZARD_RULES`, `RECEIVER_RULES`, `HAZARDS()`, `CLASSES()`, the pack keys
`hazard_cleaning_*`, `place_food_*`, `person_vulnerable`, `refusal_safety`, `refusal_capability` of `lang/*.json`. A pack that
relied on them must declare a profile or loses the food-place and child rules (and the heard-refusal marks). `check_goal`
takes the profile as its third argument and `enabled` (option keys) instead of `receivers=`. Behaviour of a pack that
declares the same data is byte-identical (pins in the CHANGELOG).
