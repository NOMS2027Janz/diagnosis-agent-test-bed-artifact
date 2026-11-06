# Amendment 22: the structural mismatch carried to the case-day network (4 November 2026)

## The fault

`History.network_at()` (src/adagent/world/history.py) builds the network in service on the case day as a new
`Network`, copying the elements and the gap but not the two attributes of the structural mismatch set by
`world/gap.apply_mismatch`: `nli_factor` (the world's nonlinear interference relative to the GN closed form) and
`mismatch` (the level). In the arms with a twin of wrong physics (A-full-cd-tm1, A-full-cd-tm2, B-tm1, B-tm2,
campaign C4H), the world therefore ran with the twin's own nonlinear interference. The per-element parts of the
mismatch (amplifier ageing, hidden gain tilt, connector losses redrawn, gains recommissioned) were applied, since they
live on the elements. Found by the provenance check of the NOMS 2027 paper: the described size of the mismatch did not
match what the world showed.

## The fix

`network_at()` copies `nli_factor` and `mismatch` to the case-day network. Without a mismatch these are the defaults
(1.0 and 0), so no other arm changes; this is confirmed by rerunning no-mismatch cases case for case.

With the fix, on the reference network at day 2190 (seed 2000), the mismatch shifts the world's GSNR at the receivers
by a mean of -0.55 dB (standard deviation 0.15) at level 1 and -1.16 dB (0.30) at level 2, against the world without
mismatch.

## What is rerun

The four arms, on the 160 diagnosis cases 2000 to 2159 under status-quo instruments, under manifest
`spec/CAMPAIGN_MANIFEST_C4T.json` (C4H with the corrected history.py, this memo and the run script):
`scripts/run_c4t.sh`, writing `results/campaign/c4t/`. The C4H records of these arms are superseded and not reported.
