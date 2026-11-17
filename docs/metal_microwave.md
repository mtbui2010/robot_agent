# Rule: no metal in a microwave oven (data of option `hazard`, no code)

**Class.** The reader returns `clear` with goals such as `open(microwave) / in(<foil-wrapped dish> -> microwave) / close(microwave)`. No rule of
the robot relates a metal thing to a microwave oven, so the robot does it.

**Design: (a), data only.** The rule is "an object word x a place", which the safety profile already expresses (docs/safety.md): a hazard
class of metal words, a place class or a place-key regex, a rule with `say`. It runs in the engine's one safety gate (option `hazard`:
refusal, partial drop, the oven's own open / close goals refused with it, `refusals` of `done`, service `forbids`). `heat_safety` was not
widened (b): its data is a clearance per heat place, a second rule kind would be a second gate. A new option (c) would copy hazard's gate.
A pack whose profile it can edit declares a class `metal` and a rule in its profile (`provenance: {source: ...}`; test pack 1). A pack that
adds data through a manifest overlay uses the manifest list `safety.hazards` (`{objects, places, say, source | assumed}`; the engine does
not read the label, the legacy entries have no rule id: `refusals[].rule` is `""`). Sources (pages opened by the task's author, 2026-11-17):
- NIH ORS, Fire Safety Tips for Using Microwave Ovens (2016): "Never use metallic articles and aluminum foil in a microwave oven."
- UC Berkeley EH&S, Microwave Oven Safety Guidelines: "Do not use aluminum foil or any metal containers, metal utensils, metal objects, or
  objects with metal or foil trim in the oven." "Such items can cause arcing, damaging the oven and creating a fire or burn hazard."
- Hoover UK advice centre (sparks and blue flashes): "ensure the cooking tray is not metallic or has metal trims."

**Words.** Sourced: metal, metallic, foil, aluminum / aluminium. Assumed (materials, not on the pages): steel, stainless, iron, tin, tinfoil;
the Korean words are assumed translations. A word counts whole (`_`, `-`, space end it; plural `-s`) and when not denied: `non-metal`,
`nonmetallic`, `no metal`, `without metal`, `metal-free` pass. There is NO material exemption: the pages name no safe material (only "labelled
microwave-safe", which the robot cannot check), and they name metal trim. So `a ceramic mug with a metal handle`, `a metal-free bowl with a
foil lid` and `a microwave-safe metal rack` are refused; `a metal-free glass bowl` passes. An object with no metal word passes.

**Known gaps.** A bare `can`, `takeout box`: no material word, not decided (a bare utensil word: see "Ask" below). A reader that
paraphrases the object without the material ("the leftovers") passes unless the person's own words (`said`) hold it. Some oven manuals allow
a little foil (pages not opened): the rule refuses it anyway. The profile's `checks` (on, in) also refuse a put ON TOP of the oven.

**Ask: a bare utensil word (key `hazard.ask`, legacy `hazard_ask`, default off; 2026-11-17).** The pages name "metal utensils" a risk, but
a spoon may be plastic or wooden: for a bare utensil word the honest rule is to ASK, not to refuse. Design (b): the gate has a question path
(the option service `ask`, as `cond_goal` / `ask_next` use it) but the rule schema has no ask form (a), and an object issue (c) would ask
"say it again" and re-sample the reader. So an entry of the manifest list `safety.hazards` may carry `ask: <phrase key>` and `unless:
[regex]` (a declared safe material word; `objects`, `places`, `say` as any entry). With `hazard.ask` on, a goal that no refusing rule stops,
that such an entry fires for and that holds no `unless` word gets ONE question per goal (the phrase of the language pack or the robot's
i18n, slot `{object}` = the person's words; en / ko `q_metal_microwave` "Is the {object} metal? Metal in a microwave can cause sparks and a
fire.", wording assumed; another language: the prompt-language phrase through the translator; no phrase in any pack: not asked). Answer:
silence -> the engine's unanswered end (status `failed`, "no answer to: ...", `say_no_answer`); a yes, a refusing word in the answer ("it
is steel") or "I don't know" (`is_unsure`) -> refused as any rule (the entry's `say`, partial drop, the oven's open / close, `refusals`,
rule `""`); anything else (a no, "plastic", "it's wooden") -> it goes on. Asked once per goal per run (service `goal_dropped`: a re-sample
does not bring a refused goal back). A material word decides without a question: `metal fork`, `steel spoon` are refused by the metal
entries (they run first); `plastic spoon`, `wooden spoon` match `unless` and pass. Key off: an `ask` entry neither asks nor refuses. Lab
flag `hazard` `{asked, answer}`. Not covered: a sealed metal can (not in these pages; a separate source search), containers ("takeout box"),
a reader that drops the noun. Tests: `tests/test_safety_metal_ask.py` (made-up packs, language `zq`, off / empty byte-identical).

**Sentence.** The rule's `say`, prompt language, said through the engine's translator as every hazard rule ("I won't do that: <say>.").
**Switch off.** No data: nothing happens (tests `_empty`); `hazard_rules=off` turns off every rule; `hazard.ask` off (default): no question.
Event: lab flag `hazard` {rules} (refusal), {asked, answer} (question).
Tests: `tests/test_safety_metal_microwave.py` (two made-up packs, language `zq`, empty and off byte-identical).
