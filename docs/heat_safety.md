# Option `heat_safety`: nothing that can burn goes on a heat source

**Class.** The reader returns `clear` with a goal such as `on(lighter -> stove@kitchen)` for a request to put a thing that can burn on
a heat source. Nothing in the engine relates the object to the place, so the robot does it. The reader is not changed. The rule works on
the reader's GOALS, at the engine's safety gate, the way option `hazard` does.

**Rule** (pure function `heat_check(goal, rule)`), only for an `on` / `in` goal whose `to` is a declared heat-source place (no such goal:
nothing is read, no model call). The object phrase (`object`, else `said`; `_` read as a space) is read in three steps, first answer wins:
1. **Head noun.** The head is the last word of the phrase before a post-modifier word of the prompt language (en: `with`, `of`, ...; the
   language pack's list). Words before the head are modifiers: a `plastic-handled pot` and a `kettle with a plastic handle` are a pot and a
   kettle. A head in the declared `cookware` list (the stove's own things) passes.
2. **Burnable word** (the fast path): a declared burnable word anywhere in `object` or `said` (whole words, plurals folded) refuses.
3. **Judgement:** one call to the planner's model (service `ask_model`, in the run's LLM budget) with the language pack's `judge` prompt,
   the person's own request (the reader may rename the object: a delivery box read as `package`) and the object's full phrase. The JSON
   answer names the `material` first, then `burns` and `unsure` (booleans): can it catch fire, melt, char or give off fumes there?
   `burns` or `unsure` refuses (the safe side); otherwise it passes unless the material holds a declared burnable word (the model names
   materials well but said "no" to a cloth oven mitt and a fabric teddy bear in development). No model, an error, no JSON or a
   non-boolean answer passes (fail open) and is logged. One call per phrase and place in a run. `forbids` has no run, so it uses steps 1 and 2 only.

A refused goal: the whole request is refused when every goal is refused; otherwise those goals are dropped and named at the end ("I did
the rest, but ..."), and the `refusals` of the `done` event list them.

**Data** (`planner.options.heat_safety`, robot pack; each entry needs a `source` string or `assumed: true`, or it is not used):
- `places`: `{<ENV key, or a key head>: {clearance_m, source | assumed}}`: the heat-source places and the distance a thing that can burn
  must keep (FEMA, Heating Fire Safety: "at least 3 feet from all heat sources ...", 0.91 m). A clearance of 0 or less is no rule. No
  place: the option does nothing at all. A place is a heat place only when declared (an `oven` the pack does not declare is not one).
- `burnable`: `{words, source | assumed}`: words of things that can burn. `cookware`: `{words, source | assumed}`: heads of the things
  made to stand on the heat (a word may have several words, matched at the end of the head phrase). Either empty: that step is skipped.
- Language pack block `heat_safety`: `refuse` / `part` (slot `{m}`), in the run's language else the prompt language; `after_head` (the
  post-modifier words) and `judge` (slots `{request}`, `{object}`, `{place}`), in the prompt language (the reader's `object` is in it). No `judge`
  prompt: step 3 is skipped (fail open). No sentence: nothing is refused (lab part `no_phrase`).

**Not decided (limits).** "Near" a heat source (map centres cannot tell it; the deleted `within_m` is not rebuilt). A cookware head
passes whatever its material (`plastic pot`): the pack's list decides what stands on the heat. The judgement is the model's view, not a
cited fact; a reader that renames the object is judged on its word and the request together (the model may still follow the word).

**Wiring.** Hooks `on_goals` (order 11, after `hazard`), `on_goals_met`, `on_finish`. Services `forbids` and `refused_goals`. Keys
`enabled` (run option `heat_safety`, default off), `judge` (run option `heat_safety_judge`, default on: `0` turns step 3 off). Lab flag
`heat_safety` `{part: refuse | drop | no_phrase, places, refused, goals}` and `{part: judge, object, place, answer, material}`
(answer `burns` | `unsure` | `safe` | `none`: not answered, passed). A cache-only replay has no recorded answer for the new call: it fails open.
