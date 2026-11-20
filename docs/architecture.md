# Architecture: layers, options, size

How the code is organised, which rules are enforced by a test, and how the large old code shrinks. The rules are CLAUDE.md 13-15
(full text in [rules.md](rules.md)). The option interface is in [options.md](options.md).

## Layers

```
contract  <-  engine  <-  options  <-  robot profiles (robot packs: manifest, safety profile, language overlays; outside this repo)
                 ^
         interact, api  (above the engine; they never import an option)
```

| layer | paths | may import |
|---|---|---|
| `contract` | `robot_agent/contract` | nothing of the layers below |
| `engine` | `robot_agent/core` (planning, safety, options, run_control, ...) | `contract` |
| `options` | `robot_agent/options` | `contract`, `engine` |
| `interact` | `robot_agent/interact` | `contract`, `engine` |
| `api` | `robot_agent/api` | `contract`, `engine`, `interact` |

* The **engine** never imports an option. It reaches options through the registry (`core/options`), which loads the option layer by
  name (`importlib`, one place), and calls hooks through the dispatcher. A static import of `robot_agent.options` from the engine is
  an error.
* An **option** never imports another option. Code two options need goes DOWN into the engine as a public function.
* Nothing imports the tests, the benchmark (`symbolic_robot`: its `bench` suite and its `domains/home` simulator; formerly the repositories `homeloop_bench` and `homeloop_robot`; the bench variable is `SYMBOLIC_D1_LAYERS`, formerly `HOMELOOP_D1_LAYERS`, still accepted there) or a robot pack. `embodiplan` is imported only in
  `core/planning/backends/` and `core/planning/action_model.py` (rule 3).
* No module imports a `_private` name of another module.
* The **robot profile** is data outside the code: manifest, safety profile, language overlays. The engine and the options hold no fact of
  one robot, one language, one domain or one benchmark (rule 14; [safety.md](safety.md), [languages.md](languages.md)): missing data
  means the feature does nothing.

## What is enforced

`make lint-core` runs `tools/lint_arch.py`; `tests/test_architecture.py` runs the same checks in `make test`.

| check | rule | how |
|---|---|---|
| import layers | the table above; options do not import each other; no private-name import; no benchmark / test / pack import | `tools/arch_rules.json`; relative imports are resolved, imports inside functions count |
| size | engine and contract functions at most 80 lines, option functions 60; files 1200 lines, option files 400 | a baseline (`tools/arch_baseline.json`) records the old offenders at their size: they may not grow, a new one fails |
| option wiring | `assist_loop.py` reads no key of a registered option; the number of distinct option keys it reads from `self.cfg` and of `if` branches on `self.cfg` may not grow | baseline `wiring` |
| stale baseline | a baseline entry that is fixed must be removed | `python tools/lint_arch.py --update`; the diff is reviewed. The baseline is a ratchet |
| domain literals | `robot_agent/options`, `core/options`, `core/safety` hold no language or domain literal | `tools/lang_inventory.py`; the whole-core count is gated by `tests/test_lang_inventory.py` |
| options | every registered option has a spec with hooks and a doc line, an entry in [options.md](options.md), its own module and a test through `AssistLoop` | `tests/test_architecture.py` (`THROUGH_THE_LOOP`) |
| function size in planning | at most 80 lines in `core/planning` | `tests/test_wiring_function_size.py` (rule 10) |
| generality | the engine names no class or rule of any profile; an empty profile, a second (factory) profile and a made-up language are tested | `tests/test_safety_profiles.py` |

Run by hand: `python tools/lint_arch.py` (report; exit 1 on a new finding), `--top 15` (largest offenders), `--update` (rewrite the baseline).

## Known violations (the baseline) and the plan to remove them

Import findings in `tools/arch_baseline.json` (each is `file -> module`):

| group | count | plan |
|---|---|---|
| `contract -> core` (`legacy_adapter -> core.planning.loop`, `manifest -> core.safety.profile`) | 2 | the safety profile loader moves to `contract` (it is declared data and its validator is part of manifest loading); the legacy adapter moves above the engine |
| `core.run_control -> interact` (`dialog`, `run_manager`) | 2 | the dialog and run manager types the control module needs go down into the engine |
| private-name imports across modules | 36 | each becomes a public name (`loop._STOP/_NEXT/_DONE` are now `core.options.STOP/NEXT`; `_vr_list`, `understand._norm/_active/_find/...` next) |

Size offenders (`python tools/lint_arch.py --top 12`, at the end of round 19):

| lines | what | plan |
|---|---|---|
| 4211 | `core/planning/assist_loop.py` (file) | one option per round leaves the loop (below); the understand, dialog and recovery phases then become modules of their own |
| 240 | `DeviceManager.add_connect` | split by connection kind |
| 178 / 140 / 116 | `UnifiedAgent.run_direct`, `run`, `run._blocking` | the direct and blocking paths become classes with one phase per method |
| 91 | `run_plan_skill` | |
| 89 | `contract/manifest._planner` | one validator per section |
| 81 | `UnifiedAgent.run_direct._blocking` | |

The ratchet makes the plan visible: a change that moves code out of a baseline offender lowers its number, `--update` records the
lower number, and it can never go back up.

## Options still in the loop

Round 19 moved `hazard`, `reach_delivery` and `unapplied_change`. `assist_loop.py` still reads 66 distinct option keys in 67 `if`
branches on `self.cfg` (round 17: 70 and 77). The next ones, in order of how many methods they touch: `dialog_agree` / `dialog_agree2`,
`search_legs` / `search_total`, `dialog_unstick`, `understand_*`, `person_goals`, `relook`. One option per round, same recipe
([options.md](options.md#moving-an-option-out-of-the-loop-the-recipe)), each with a replay pin.

## Measured at round 19

| | r19a (before) | r19c (after) |
|---|---|---|
| `assist_loop.py` lines | 4342 | 4210 |
| `AssistLoop` methods / lines | 200 / 3986 | 194 / 3850 |
| distinct option keys read from `self.cfg` | 70 | 66 |
| `if` branches on `self.cfg` | 77 | 67 |
| `ASSIST_OPTIONS` | 88 literal names | 84 literal + the registry's (92 in all) |
| option modules | 0 | 3 (`hazard`, `reach_delivery`, `unapplied_change`) |
| dispatcher call sites in the loop | 0 | 18 (13 hook kinds) |
