# Amendment 24: the margin levels judged against the fleet (4 November 2026, report-only refinement)

Approved by Chris Janz, 4 November 2026, 22:16. A refinement made after the freeze; its results are labelled as such.

**What it addresses.** The margin levels compare each receiver's pre-FEC bit error rate before the onset with the twin's prediction of it with no fault. A shift of the mean common to the whole network (the twin biased everywhere, as in the level-1 test of a twin with structural error, about 0.55 dB) does not cancel in that comparison, as it does in a change from before the onset to after. At level 1 it made 166 of 365 receivers read as short of margin, and all three wrong commitments of that arm took an instrument's misreport for margin erosion.

**The change.** With `ADAGENT_MARGIN_FLEET=1`, the margin levels take the median of every receiver's deviation from the twin as a shift common to the twin and subtract it before the thresholds apply, so that a receiver is judged against the fleet, as the health levels judge pumps and lasers. The thresholds (three and one times the stated accuracy) are unchanged. The removed offset is recorded in each case's record (`margin_offset_decades`). With the variable unset the code behaves exactly as under manifest C4U.

**The runs.** Manifest `spec/CAMPAIGN_MANIFEST_C4V.json`. With the variable set, on diagnosis cases 2000 to 2159: `A-full-cd` (the accurate twin, to show the change costs nothing without a shift) and `A-full-cd-tm1` (level 1). Output `results/campaign/c4v/`.
