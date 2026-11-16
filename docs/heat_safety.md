# Option `heat_safety`: nothing that can burn goes on a heat source

**Class.** The reader returns `clear` with a goal such as `on(lighter -> stove@kitchen)` for a request to put a thing that can burn on
a heat source. Nothing in the engine relates the object to the place, so the robot does it. The reader is not changed. The rule works on
the reader's GOALS, at the engine's safety gate, the way option `hazard` does.

**Rule** (pure function `heat_verdict(goal, heat, burnable)`): an `on` / `in` goal whose `to` is a declared heat-source place and whose
object (`object` or `said`, whole words, plurals folded by the language packs' rules, `_` read as a space) has a declared burnable word
puts the thing at distance 0 from the heat source, which is inside any clearance. The goal is refused. The whole request is refused when
every goal is refused. Otherwise those goals are dropped and named at the end ("I did the rest, but ..."), and the `refusals` of the `done`
event list them. An object with no burnable word passes (fail open): a pot, a pan or a kettle on the stove is not refused.

**Data** (`planner.options.heat_safety`, robot pack; each entry needs a `source` string or `assumed: true`, or it is not used):
- `places`: `{<ENV key, or a key head>: {clearance_m, source | assumed}}`: the heat-source places and the distance a thing that can burn
  must keep (FEMA, Heating Fire Safety: "at least 3 feet from all heat sources including fireplaces, wood stoves, radiators, space heaters
  or candles", 0.91 m; FEMA, Candle Fire Safety: 12 inches, 0.30 m, for a candle). A clearance of 0 or less is no rule.
- `burnable`: `{words: [...], source | assumed}`: the words of things that can burn (FEMA: "anything that can burn"; the list itself is
  the pack's assumption).
- Language pack block `heat_safety` (`lang/<code>.json`): `refuse` and `part` sentences with slot `{m}` (the clearance), labelled. The
  run's language is used, else the prompt language (then translated). With neither, the option does nothing (lab part `no_phrase`).

**Not decided (limits).** "Near" a heat source: the robot's map gives the centre of each place, not where on a counter an object lands.
Neighbouring furniture centres are about one clearance apart, so a map distance cannot tell "next to the stove" from "at the far end of
the counter". Only a put on or in the heat place itself is refused. This is why the older `within_m` (option `safety_profile`, deleted)
is not rebuilt. Oven, toaster, kettle, iron and cooktop have no cited clearance: a pack that declares them labels them `assumed`.

**Wiring.** Hooks `on_goals` (order 11, after `hazard`), `on_goals_met`, `on_finish`. Services `forbids` (so the engine's
`safety_gate` and its re-reading checks see the rule) and `refused_goals` (the read-back counts a refused goal as covered). Key `enabled`
(run option `heat_safety`), default off. Lab flag `heat_safety` `{part: refuse | drop | no_phrase, places, refused, goals}`.
