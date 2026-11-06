# Decision D2: the gap between the world and the twin

For Chris, 2026-10-15. Needed before the twin is rebuilt from the graph for Experiment 2 (winnowing) and before
the GNN rankers' training data is generated. Not needed for Experiment 1.

## The question

The world simulator is the only holder of how the network truly behaves. The twin is the agent's model of it, built
only from what the agent has: the knowledge graph (inventory as recorded, configuration, instruments) and the
documentation (datasheets, fault-mode effects). How far apart should they be? If they are identical, the twin's
counterfactuals are perfect and Experiment 2 measures nothing real; if they are far apart, the twin is useless. The
gap must be physically motivated, of a size a real operator would face, and an experimental axis.

## Where the gap stands today

Much of it already exists, because the twin is built from records:
- **Recording gaps:** lightpaths and equipment recorded days late; four instrument deployments never registered;
  five shared-risk groups (shared ducts) not in the plant records. The twin inherits these exactly as the graph
  holds them.
- **Documentation grain:** the twin knows fault effects as documented (qualitative, with typical ranges); the world
  applies them physically. VB's amplifier documents omit noise-figure degradation and laser-bias monitoring.

Missing: the world's physical parameters are nominal everywhere (every amplifier's noise figure 5.5 dB, every
fibre 0.20 dB/km, every connector pair 1.0 dB). A twin built from datasheets would therefore match the world's
physics exactly, which no real twin does.

## Options

1. **Parameter dispersion in the world only (recommended as the base gap).** Each element gets hidden true values
   drawn once per network seed: amplifier noise figure within the datasheet spread (typical 5.5, maximum 6.5 dB),
   gain ripple and tilt of a few tenths of a dB, fibre attenuation 0.19 to 0.23 dB/km, connector and splice losses
   0.3 to 1.5 dB per span, PMD coefficient per span. The twin uses the datasheet typical values and recorded span
   lengths. Errors then accumulate along chains, as you specified for the GNN training data: small for one
   amplifier, growing with path length.
2. **Effects the twin does not model.** The world adds polarisation-dependent loss, WSS filter narrowing that
   accumulates over cascaded ROADMs, nonlinear penalty dependence on channel loading, and slow ageing (connector
   loss creeping up, amplifier pump current rising). The twin keeps the simpler model.
3. **Instrument imperfection.** The world's instruments already carry noise and can misreport or go stale; add
   per-instrument calibration offsets (for example ±0.5 dB on power monitors) that the twin does not know.
4. **A gap size as an axis.** Options 1 to 3 scaled together by one factor (0, 0.5, 1, 2), so Experiment 2 can show
   how counterfactual winnowing degrades as the twin drifts from the world.

## Consequence for Experiment 1

Any of these changes the world, and so the cases. Experiment 1 is frozen on the world as it is now (version 1 in
the report). The gap is introduced as world version 2 after Experiment 1 is complete; Experiment 1 can be re-run on
version 2 afterwards to show whether generation is sensitive to it (it should be little affected, since generation
reads changes from the baseline rather than absolute values).

## Recommendation

Options 1 and 3 as the base gap, both physically ordinary and both inevitable in practice; option 2 limited to two
effects (cascaded filter narrowing and connector ageing), since each adds a source of "the twin was wrong for a
reason it could not know"; and option 4's scale factor as the experimental axis, with 1 as the reference. The
dispersions are drawn from published typical ranges (datasheet spreads, ITU-T G.652 attenuation limits), not tuned
to make any tool look good, and are fixed before Experiment 2's protocol.

## Decision (Chris, 2026-10-16)

Options 1 and 3 adopted, with the scale factor of option 4 as the experimental axis. Option 2 (effects the twin does
not model) is not adopted. The errors must be cumulative: the twin's prediction error on OSNR and margin must grow with
the number of amplifiers on a path, so a long path through many EDFAs carries a larger error than a short one. Per-
element errors drawn independently give that growth; a test will confirm that the spread of twin-minus-world OSNR
grows with path length.
