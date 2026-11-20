# ask_next: after a failure, ask the person what to do next

**Problem.** When a Find, Pick or MoveTo has used its own retries, the engine asks one fixed question ("I can't pick up the X. Could you
move it closer to the edge, then say yes?", "Where is it?", "Please clear the way and say yes") and reads only a yes or a place name.
"No", "give up", "not sure" or silence go to a blind replan or end the run; after a "yes" the robot retries once and fails the same way
(MuJoCo videos of the kcare house). No menu exists, "stop" is not understood, and a failed pick never goes to another place unless the
person names one.

**Trigger (hook).** New decide-flow hook `on_ask_help(run, step, kind)`, called where the engine is about to put its own help question
(`_recover_find` "where", `_recover_pick` "pick", `_recover_move` "move"), right after it marks that question as used (`r.helped`): once
per object and kind, only when the engine's `ask_help` is on and after the step's own retries. A result `(outcome, replan hint)` replaces
the engine's question and what follows it; None = the engine's own question, as today.

**Question.** One sentence of the language pack (`speech` phrases `q_ask_next_find` / `_pick` / `_move`, slot `object`): try again here,
look somewhere else, or stop (move: again or stop). **Answer** (pure `parse_answer(text, pack, place)`): the pack's declared words only
(block `ask_next` `{retry, elsewhere, anywhere, stop}` with `source` / `assumed`, plus `dialogue.stop`, `yes`, `no`, `answer_unsure`) and
the ENV place the answer names: stop word > place > anywhere > elsewhere > retry > unsure (unclear) > no (stop) > yes (retry) > unclear.

**Actions.** retry: the step once more in place (a Pick looks again first). elsewhere + place (or a room: its best unsearched place): the
engine's handling of a told place (memory, search belief) and its replan hint. elsewhere, no place: Find = the engine's own "Where is
it?" follows; Pick = one "Where should I look?" (place or nothing). anywhere: Find = the engine's search goes on (`_search_on`).
stop: the run ends `aborted` (a stop by the person, as a cancel) saying `say_stopped`. unclear: asked ONCE more naming the choices
(`q_ask_next_again`), then today's path. Silence, no ask skill, no labelled words, no phrase: today's path (fail open).

**Caps.** At most `max_menus` (2, assumed) menus per run, one per object and kind (the engine's `helped`), never after a stop.

**Overlaps.** The engine's pick / move / where questions: replaced only while the option is on, reused as the fallback; the where answer
path (`note_answer`, `_search_told`, `_search_on`) reused through services. `ask_source` (before the plan) and its `reask_on_miss`
move the where point earlier or mark it used ("I don't know"): the menu comes at that point or not at all, never twice. `on_step_failed`
runs BEFORE the engine's retries (it would have to copy their counters), `on_where_answer` only reads the where answer: not extended.
No option is retired.

**Switch off:** key `enabled` (run option `ask_next`), default off. **Event:** lab flag `ask_next` `{kind, object, answer, action}`.
**Measure.** Unit and loop tests on a made-up robot and language; the bench layer `ask_next` is selectable only (the symbolic person may
not understand a menu); to be measured in MuJoCo with a scripted person.
