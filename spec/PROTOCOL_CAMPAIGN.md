# Protocol: the campaign to the NOMS 2027 paper

Plan: "The Diagnosis Agent: Campaign Plan to the NOMS 2027 Paper" (docs/campaign/campaign_plan.md). Decisions by
Chris, 2026-10-20 07:02: all five recommendations accepted. (1) The LLM-alone baseline in both forms, single shot on
the 320 diagnosis cases and with checks on 80 cases (10 of each kind). (2) Its model is the study's main model.
(3) Protection-switch event logs are built as a measured option and reported separately from the agent as built
unless they clearly earn their place. (4) The larger network has about 56 ROADM sites, run on 80 cases. (5) The
third case set is reserved now and left unopened until the record run.

## 1. The agent and its development sets

Development uses the diagnosis cases (seeds 2000 to 2319) and the fresh cases (seeds 3000 to 3319), day 2190. The
agent is frozen in Stage 4, with a manifest, before the record run.

## 2. The third case set

Seeds 4000 to 4319, day 2190, 40 of each kind (make_case2 cycles the kinds by seed). Reserved 2026-10-20. Cases are
generated from the seed when run, so reserving them needs no data on disk. scripts/exp2_run.py refuses these seeds
unless ADAGENT_RECORD_RUN=1, which is set only for the record run of the frozen agent. Nothing about these cases is
looked at before then.

## 3. Stage 1 experiments

### 3.1 The "can this decide?" step (arms A-full-cd, A-full-cd-fut, A-tr-cd, A-tr-cd-fut)

Before proposing a check whose estimated effort (time to result plus the travel estimate) is half an hour or more,
the advisor computes can_decide for it (src/adagent/tools/loop.py) and does not propose it when p_two < 0.05. It then
considers the next-best check. When no check is left the loop stops with "I don't know", recording the refused
checks (record "decide_refused") and the reason. Parameters as fixed for the estimate, which was set on the
diagnosis cases with peeling only (Results, Section 15.2); not retuned.

Runs: A-full-cd and A-full-cd-fut on the diagnosis cases, paired with A-full and A-full-fut; A-tr-cd and
A-tr-cd-fut on the fresh cases, paired with A-tr and A-tr-fut. Measures: outcomes by kind, effort and visits by
outcome, and the measured saving against the estimate. Success: no right diagnosis lost on either set, and effort
and visits within the estimate's range or lower.

### 3.2 A twin with structural error (arms A-full-tm1, A-full-tm2)

To be specified before the run: the twin's nonlinear noise from a closed-form model in place of the simulator's; an
amplifier gain tilt that drifts with age in the world and not in the twin; connector and splice losses in the world
from a wider distribution than the records state. Two levels. Measures: right, wrong commitments and effort by kind
against A-full. The question is whether wrong commitments stay rare as the twin departs from the world.

### 3.3 Baselines: the LLM decides alone (arms LLM-1, LLM-chk); alarm correlation alone (arm AC)

To be specified before the run, with the prompt frozen and the spend checked after the first 20 cases.

### 3.4 A realistic mix of kinds

Analysis only: reweighting of existing results by prevalences from published sources, with a sensitivity range.

Amendment (2026-10-20, 13:20). LLM-alone pilot (20 cases, prompt llm-alone-0.1): $1.48, $0.07 a case, a third of
the estimate. In three of the 20 cases the LLM committed to a place the documentation resolves to several locations
(the ports of a unit, the spans of a line), which the rule then scored as a set. The agent's ledger would have to
separate such locations before committing, so the LLM is given the same chance: prompt llm-alone-0.2 asks it once
to choose one of the locations or to say "I don't know". The pilot's rows are kept in results/campaign/llm_pilot_v01
and the arms run afresh under spec/CAMPAIGN_MANIFEST_C1L.json. Part 1 (no LLM) is unaffected.

Amendment (2026-10-20, 14:35; Chris approved 14:33). Part 1 measured at about 1.1 jobs a minute while sharing the
laptop, which puts it at 40 to 45 hours. Trimmed: alarm correlation alone runs on the diagnosis cases only (on the
fresh cases it would repeat the same mechanical result), and the structural-error arms run on 160 diagnosis cases, 20
of each kind (seeds 2000 to 2159), at each level. scripts/run_c1b.sh runs the remainder under
spec/CAMPAIGN_MANIFEST_C1B.json after the "can this decide?" arms of run_c1.sh.

## 4. Amendment: the capability ladder (2026-10-21; arms A-L1 to A-L6, A-Lall, B-L1, B-Lall)

The future instrumentation branch changed no outcome (Results, Section 14): the agent's 72 undiagnosed cases need
observables that no deployed tier has. The capability ladder adds them one at a time, each to the world (what is
observable) and to the agent (how it is used: by documented meaning or through the twin, never learned), so that
each one's effect on diagnosis and effort is measured. Every rung is opt-in (WorldSim.ladder, config key "ladder");
with none switched on the world, the agent and manual troubleshooting are as before. The rungs' documentation is
knowledge/ladder/telemetry.yaml (and procedures.yaml for manual troubleshooting), read only by the ladder's arms.

Rungs (world/ladder.py, tools/ladder.py), all on the future instrumentation branch:

- L1 stream liveness. Every sampled telemetry stream carries a sample counter recorded with each value. Stale
  telemetry repeats its last value and its counter stops; a misreporting instrument keeps counting. Agent: a
  generation line proposing TELEMETRY_STALE at every unit whose counter stopped, and only there; an evidence term of
  its own: a stopped counter not accounted for costs 8 nats (against every cause that needs the stream live or says
  nothing of it, and against a cause nobody proposed), and stale telemetry at a unit whose counter advances costs 8
  nats. Manual troubleshooting: the management system raises TELEMETRY_STREAM_LOST on a stopped counter (three
  sample periods and at least 120 s), and procedure P-TELEMETRY-LOST names the unit's instrument as the probable
  cause. The alarm is the counter read by its documented threshold, so both approaches have the same information.
- L2 per-span DGD. A DGD estimate for every span every 60 s (1 ps). Agent: through the twin, which predicts each
  span's DGD under each hypothesis.
- L3 per-port WSS monitoring. At each WSS output port, per channel, the power leaving the port (0.2 dB) and the
  offset of the filter's centre (0.5 GHz), every 60 s. Agent: by documented meaning (a filter offset above 2 GHz
  since the onset is a filter shift at that port; a port's power falling by more than 1 dB while the channel
  arriving at that degree holds within 0.5 dB is a loss in the port), as a generation line and an evidence term (6
  nats for a reading not accounted for, 3 for a shift or port loss hypothesised where the port shows none). The
  twin does not also compare these readings, so none counts twice. A first version read them through the twin
  only; it placed the fault (the class shrank to the truth) but could not raise it above "none of these", because
  the twin's allowance for an unconsidered cause is generous to any reading that changed.
- L4 located events. Per-span polarisation sensing and coordinated transient capture: every brief excursion on a
  span (polarisation faster than 5000 rad/s, or loss above 0.3 dB, over within 60 s) is captured with its time,
  duration and size; 2 per cent are missed. Agent: a generation line at the captured spans and an evidence term
  (a capture the hypothesis does not place costs 6 nats; a transient hypothesised on a span that captured nothing
  costs 3.9).
- L5 equipment event logs. Line protection switches and channel add/drop actions are logged against the lightpath
  or OMS, timestamped to the second, delivered after a delay (median 20 s), with 10 per cent of entries lost. Agent:
  a generation line and an evidence term (an entry not accounted for costs 6 nats; the logged action hypothesised
  with no entry costs 2.3).
- L6 instrument cross-check. A remote check (xcheck_reading, 5 minutes) compares the change an amplifier's or a
  receiver's power instrument reports since 15 minutes before the question with that of an independent instrument
  at the same point. Agent: the advisor offers it at the units of the contenders, and its answer is read by its
  documented meaning (disagreement: the instrument is at fault; agreement: no misreporting there).

The harness's equivalence classes see each rung's observable (harness/equivalence.py: sampled streams compared as
readings, event-like items as discrete data), so a rung can narrow a class only where its observable separates the
causes, and the separability of an "I don't know" counts the cross-check where L6 is deployed.

Arms (harness/exp2.py): A-Lk, the agent of A-full-cd-fut with rung k alone; A-Lall, every rung; B-L1, manual
troubleshooting under the future branch with L1's alarm and procedure; B-Lall, with every rung's world. Manual
troubleshooting's procedures read only L1's alarm (no procedure reads the other rungs' observables), so B-Lall
should equal B-L1 case for case; it is run as a control on that.

Case sets: A-L1, B-L1, A-Lall and B-Lall on the 320 diagnosis cases (seeds 2000 to 2319). Each further single rung
on the diagnosis cases of the kinds its observable can change (exp2.LADDER_KINDS; kinds cycle by seed): L2 soft,
two faults and maintenance (120 cases); L3 every kind but drift and monitoring (240); L4 and L5 transients (40); L6
all 320 (the cross-check can be asked of any unit a monitoring hypothesis names). Run by scripts/run_c2_ladder.sh
under spec/CAMPAIGN_MANIFEST_C2.json, results in results/campaign/ladder.

Measures: outcomes by kind; effort and site visits by outcome; each paired case for case with A-full-cd-fut (the
agent) and B-fut (manual troubleshooting) on the same cases. Success for a rung: the kinds it was built for (L1
frozen readings; L2 DGD rises; L3 filter shifts and port attenuation drifts; L4 polarisation transients and
mechanical disturbances; L5 protection-switch hits and channel-loading transients; L6 misreporting instruments)
move to right, no wrong commitment is added, and no other case's outcome changes. A change elsewhere is reported
case by case with its cause; a wrong commitment added is a failure of the rung's documented use, not of the
observable.

Notes. The rungs change the fits the harness memoises (results/_fitcache): the fit version changes for every arm,
so the first run after this amendment rebuilds the memo. The event-like rungs (L1, L4, L5) are checked on top of
the fits made without them, which every arm on a case shares. The manifests of stage 1 (C1, C1B) no longer verify
against this code; stage 1 must finish under its own manifests before this code is installed.

Amendment (2026-10-21, 09:45). To fit the ladder before Chris leaves on Friday: L6 (the instrument cross-check) runs on
the monitoring faults only, since it can only confirm a misreporting instrument and A-Lall covers every other kind;
and A-Lall/B-Lall run immediately after A-L1/B-L1, before the single rungs, so that the whole ladder's effect is
measured first if time runs short. The record run on the third case set moves to the first overnight batch after
Friday.

Amendment (2026-10-22, 09:40; terminology only, no change to any arm). Rung L1 is called measurement liveness in all
reports, not stream liveness. The fault it resolves (TELEMETRY_STALE) leaves the stream itself healthy: reports keep
arriving on schedule, carrying the last value measured. Delivery checks (message sequence numbers, heartbeats) pass
throughout. What L1 adds is a counter of measurements taken, kept by the measuring process and carried with every
reading; a counter that stops while reports continue is the only thing that separates a frozen instrument from a
steady one in the test bed as built.

## 5. Amendment: the knowledge graph degraded (2026-10-23; arms *-kg-none, -inst, -flat, -sig, -modes)

Question (from the team's discussion of whether the knowledge graph must be designed by hand): which parts of the
graph's quality matter to diagnosis, and how much. The world is untouched; only the agent's and the procedures'
knowledge is degraded, through a wrapper (scripts/kgx_run.py) that leaves the agent's code and the frozen manifest
unchanged. The world's side (checks, data lake, scoring) keeps the true graph. Each degradation is one fixed set of
errors (seed 20261023), as an operator's records carry one set of errors.
- inst: 5% of equipment with the wrong type (another type of the same kind); 30% of spans' cable and duct records,
  10% of protection records, 10% of instrument records and 5% of cross-connect records lost. The twin inherits them.
- flat: the layered dependency relations kept only as untyped, undirected connectivity without order, ends or role;
  node kinds kept. The twin keeps the true line-design model.
- sig: for 30% of (equipment type, fault mode) pairs, no documented effects and no alarm naming the mode as a probable
  cause.
- modes: 20% of (equipment type, fault mode) pairs removed (never all of a type's modes).
- none: the control; an undegraded copy through the same wrapper must reproduce the arms without it.
Arms: A-full-cd and B with each, status quo instrumentation, seeds 2000-2159 (diagnosis cases), against A-full-cd
(results/x3/exp2b) and B-kg-none. Measures: right, partial, wrong (and wrong at the right element), "I don't know",
effort and visits, by kind (scripts/analysis_kgx.py). Run as nightly portions 1 and 2 (scripts/run_night.sh), before
the rest of the record run, on Chris's direction. No API calls.

## 6. Amendment: the agent with the LLM proposer on the sealed cases (2026-10-24, 23:30 Madrid; before any run)

Decision by Chris, 2026-10-24 23:09: run the agent with its LLM proposer on the sealed cases. Arm A-full-cd-llm is
A-full-cd with one change: the LLM proposer (src/adagent/tools/proposer_llm.py, prompt proposer-0.5, the study's main
model gpt-5.6 under the provider's default sampling) is a first-pass generation line at weight 1.0, as each of the
six offline lines. No setting is changed or tuned; the planner is the advisor; the ledger alone commits. Built as a
wrapper (scripts/llmline_run.py) so the frozen code under the C2 manifest is unchanged. Before the run: the wrapper's
control (the proposer abstaining on every case) must reproduce A-full-cd exactly on development or diagnosis seeds,
and a replay of recorded proposals must run without failure; no test touches the sealed seeds. Cases: 4000 to 4319,
status quo instrumentation, day 2190; results under results/campaign/record/A-full-cd-llm. Measures as for A-full-cd
(Table 1 row: right, wrong, field and remote hours, visits; by kind), paired against A-full-cd and B, plus the LLM's
cost (tokens, dollars, latency). Reported whatever it shows.

## 7. Amendment: spectral monitoring and the C3 manifest (2026-10-25, 20:00 Madrid; before any run)

Design: docs/campaign/spectral_design.md (Chris, 23 Oct: two technologies, evaluated separately and together).
**World.** A spectral mode (ADAGENT_SPECTRAL=1) gives the world and the twin a passband cascade: raised-cosine
signals, super-Gaussian WSS passbands (order 4, 70 GHz in a 75 GHz slot) and a filtering penalty from the clipped
power and the asymmetry, calibrated so that one 5 GHz shift costs what the default model charges (0.5 dB). Two new
fault modes, WSS_FILTER_NARROWING (soft) and LASER_FREQ_DRIFT (incipient), are documented with twin scenarios and
alarm probable causes (knowledge/spectral/addenda.yaml). With the flag off, nothing changes.
**Instruments.** S: span-end channel spectra at the first span end of each OMS (peak power, centre offset, -3 dB
width), every 60 s. R: the receiver's view of the channel shape (offset, width, asymmetry) with a learned estimate of
the filtering penalty (0.2 dB model error); documented in knowledge/ladder/telemetry.yaml.
**Cases.** The filter-focused set, 160 cases, seeds 5000-5159 (development 5900-5915), single faults and pairs: filter
shift, filter narrowing, laser frequency drift, WSS attenuation drift, WSS port failure and the look-alikes (amplifier
noise-figure rise, receiver degradation, margin erosion). The 320 diagnosis cases in spectral mode are out of scope.
**Arms.** A-sp (the agent with the "can this decide?" step in spectral mode, no new instrument), A-S, A-R, A-R-nopm
(R without the penalty model), A-SR; manual troubleshooting B-sp and B-SR (instruments present; no procedure reads
them). Portions 6 to 9 of scripts/run_night.sh (scripts/run_spectral.sh 1 to 4). Measures as for Table 1: right,
partial, wrong, "I don't know" justified and unjustified, field and remote hours, visits, by kind; paired against
A-sp and B-sp. Settings fixed on the development cases before any of 5000-5159 was run; reported whatever they show.
**C3 manifest and small fixes.** The build lands with two small fixes: the switch-fabric alarm is renamed
OTN_FABRIC_FAIL, so that its knowledge-graph node no longer shares an id with the fault mode OTN_SWITCH_FAIL
(OBSERVED_VERSION 3 regenerates the cached ticket observations); and the L1 rung's title reads "measurement
liveness". Control before any spectral run: with the flag off, A-full-cd and B on 24 diagnosis cases (including five
switch failures) must reproduce results/x3/exp2b/A-full-cd and results/a42/orig/B. spec/CAMPAIGN_MANIFEST_C3.json is
then written and every later run checks it.
Control result (25 Oct, 20:25 Madrid, before C3 was written): 48 of 48 identical in outcome, effort, visits and the
sequence of checks (A-full-cd 24, including the five switch failures; B 24).

## 8. Amendment: a design fault, the C4 agent, and a new sealed set (2026-10-26, 06:15 Madrid; before any C4 run)

**The fault.** Spectral portion 6 (amendment 7) showed the agent committing to MARGIN_EROSION, a fault mode the
documentation describes as a symptom category ("slow decline of channel margin, typically ageing of components along
the path") with no twin scenario, in two-fault cases where the truth was a receiver degradation and a line-side
fault. The twin declines a mode it cannot simulate, and the ledger scores a declined hypothesis at the term's mean; a
specific, testable cause that leaves readings unexplained can then score below it, and peeling, which needs the
twin's fit of the leader, never seeks the second cause. The fault is in the design, so it may affect every result
produced so far.
**Decision by Chris (26 Oct, 05:57-06:10).** No result produced by an agent with this fault is reported in the paper
or the report. The agent is fixed and audited, frozen under a new manifest C4, and every agent result the paper and
report present is produced again under C4. The record of the earlier runs remains in the repository and the README's
daily log only.
**The fix (specification; to be completed before C4 is frozen).** From the independent audit (lab book 26 Oct):
(1) Twin: a cause for which no twin scenario is documented is fitted as predicting no change in transmission, not
declined (twin.py, UNTESTABLE_AS_NO_CHANGE; GraphTwin 0.3). Its twin score is then min(no-fault score, background),
as for causes of equipment behaviour only; its residual is every changed reading; checks and the planner predict for
it the no-fault answer. A compound keeps the fits of its testable causes.
(2) The operator's twin practice (knowledge/practice/twin.yaml, physics_scenarios; Chris, 26 Oct 11:33) gains
scenarios for AMP_GAIN_TILT (a tilt across the band, -5 to 5 dB) and MARGIN_EROSION (a small receiver-side penalty,
0.1 to 1 dB over the window; the twin has no ramp).
(3) A spare-port check (retune_spare) no longer reads margin erosion as a receiver fault (documented as ageing along
the path).
(4) The trend line proposes an ageing mode seen only by a health stream (pump current, laser bias) only where that
stream is reported (Chris, 26 Oct 11:33).
(5) Graph consistency: a single cause whose mode documents no effects for its element is judged by R2 (services
down count against it).
(6) A margin-level term (tools/margin.py; Chris, 26 Oct 15:48): before the onset, each receiver's pre-FEC bit error
rate against the twin's no-fault prediction from the records; a rate worse by more than 3 times the stated accuracy
(margin_db_1sigma x BER_DECADES_PER_DB) shows a loss of margin, one within 1 times shows none; an ageing mode the rate
sees (documented for the element or borrowed from another vendor's equipment of the class) is supported or
contradicted accordingly; any other hypothesis about that receiver or its instruments is charged for the loss as the
twin charges an unexplained reading.
(7) Commitment at a coarser grain (tools/grain.py; Chris, 26 Oct 14:47): when no cause is probable enough, the
posterior summed over the element, the OMS, the site, then the fault mode; the finest grain reaching the commitment
threshold is reported, and scored as its own outcome, right at a coarser grain or wrong, never as right.
Deferred: the GNN term's treatment of classes it does not know (GNN arms only, not in the paper's set).
**A new sealed set.** Seeds 7000 to 7319, 40 of each of the eight kinds, generated as the sealed set 4000-4319 was,
are reserved now, before any C4 development, for the record run of the C4 agent (code guard: ADAGENT_RECORD_RUN_C4=1).
Seeds 4000-4319 have been opened for the C2 agent and are not used for C4. Development and controls use the
development, diagnosis and spectral development seeds only.
**Arms under C4.** On 7000-7319: the agent (with the "can this decide?" step), with future instruments, with every
capability, with the LLM proposer (API, about $25 to $50); alarm correlation alone (status quo and future) and manual
troubleshooting (status quo, future, every capability). On the diagnosis cases: the agent and its future branch; the
capability ladder; the knowledge graph degraded (agent arms); the multiple-fault specialist. Manual troubleshooting
and alarm correlation results produced under C2 are reused only where a control under C4 reproduces them exactly.
Spectral monitoring is out of the NOMS paper (Chris, 06:10); it is rerun under C4 afterwards.

## 9. Amendment: the LLM orchestrator on the new sealed set (2026-10-27, 12:00 Madrid; before any C4 run)

The paper's headline configuration becomes the agent orchestrated by the LLM: arm L, in which the LLM orchestrator
(gpt-5.6-sol) plans and replans the checks in place of the purpose-built advisor, and nothing else changes. The
advisor's arm (A) is reported beside it as the control that reproduces exactly. Arm L calls the model only where the
ledger cannot commit after the data held, so it runs on the planned cases of the sealed set: those on which arm A had
to plan, taken from arm A's record run (exp2_run.py --only-planned --plan-from), with every other case identical to
arm A by construction. About 70 to 90 cases are expected; API cost about $40 to $100. The comparisons stated in
advance: right, partial, wrong and "I don't know" of L against A on the planned cases and on all 320; effort and
site visits; and whether L ever commits wrongly where A does not. Reason (Chris, 27 October 11:52): on the
confirmation set, the LLM orchestrator was right in 13 of the 63 planned cases against 11 for the advisor, with no
wrong commitment; replanning by the LLM has not been ruled out, and a paper on an agent should report the agent as
designed.

## 10. Amendment: two guards against commitments built on a weak leader (2026-10-28, 07:00 Madrid; before any C4 run)

Validation on the development cases 2000-2319 found two new wrong commitments, both from the C4 fixes. In one, every
proposal now scored, a weak single cause led the named candidates while "none of these" held 0.88, and pairing the two
leaders built a compound that reached the commitment threshold. In the other, the commitment at a coarser grain
committed a cause shared by every member of a set though that cause alone was negligible. Two guards are added
(tools/loop.py, tools/grain.py): the two leaders are paired only when the first holds at least 0.2
(COMPOUND_MIN_LEAD); the coarser grain commits a shared cause only when its single-cause candidate holds at least 0.2
(CAUSE_ALONE_MIN). Peeling is not guarded: a guard on it cost five two-fault answers in validation. With the guards,
on the 320 development cases: no wrong commitment (published: 1), 233 right (232), 35 right at a coarser grain;
one right answer lost to a partial (s2061, the price of the pairing guard), accepted with s2155 and s2187 (Chris,
28 October 06:58). The C4 code is frozen with these guards.

## 11. Amendment: today's practice as handed off (2026-10-28, 09:30 Madrid; before any C4 run)

Today's practice is measured at two points: alarm correlation alone (AC) and manual troubleshooting with the vendors'
procedures (B). In B the procedures choose their own starting alarm by an order of precedence over alarm types; in
operations the engineer starts from the correlation engine's root alarm (Chris, 28 October 09:03). Arm B-ac measures
that hand-off (tools/baseline_ac.py): the procedure for correlation's root alarm is started at its unit and followed
exactly as in B; where the library has no procedure for the root alarm, correlation's list is the outcome; where there
is no alarm, the procedures' own starting points apply. B-ac and B-ac-fut run on the sealed cases 7000-7319 under the
C4 manifest (scripts/run_c4_bac.sh), with the agent's code unchanged. B-ac is a comparison point, like AC and B, not a
version of the agent. Comparisons stated in advance: right, partial, wrong, effort and site visits of B-ac against B
and AC, overall and by kind; the cases where B-ac and B start from different alarms, and what that changes.

## 12. Amendment: a design fault in port monitoring, and the rerun of A-Lall (2026-10-29, 10:00 Madrid; after the C4 record run, before any result is reported)

The record run of 28-29 October (scripts/run_c4_record.sh, C4 manifest) completed A-full-cd, B, AC, AC-fut, A-Lall
and B-Lall on the sealed cases 7000-7319; B-ac and B-ac-fut had run the morning before. Two of A-Lall's three wrong
commitments (s7171, s7227, both slow drifts) came from a design fault in rung L3, the WSS port monitors
(tools/ladder.py, PortMonitor). Its documented meaning is a filter offset or a port loss "since the onset". A drift
has no onset in the record, so its frame puts the onset at the question time and the after window held one 60-second
sample taken before it; across about 1,000 ports, one sample of instrument noise beyond the 2 GHz threshold is
likely, and it was read as a filter shift that the evidence term then held against every other hypothesis. The
fix: only readings taken after the onset count, and with none, no port shows a change. In a case with an onset the
readings compared were already after it, so nothing else changes. Verified on the 40 development drift cases
(2003-2315, step 8): before the fix one false entry and one wrong commitment (s2235); after it no entry and 40 of 40
right. Test: tests/test_ladder.py, test_port_monitor_makes_no_entry_without_readings_after_the_onset. Of the 20 port
entries in the sealed A-Lall run, the 18 true ones were all in cases with an onset.

Scope. Only A-Lall reads the port monitors, so only A-Lall is rerun, on all 320 sealed cases; its earlier results are
kept in results/campaign/c4_superseded and not reported. Every other C4 arm stands. The third wrong commitment
(s7155, MARGIN_EROSION read as INSTRUMENT_MISREPORT of the same receiver's power) is not caused by the fault and
stands. The record run also skipped L-full through a fault of its script, not of the agent: on macOS `wc -l` pads its
count, so a smoke test with 0 errors read as failed. The script is corrected (tr -d " "); L-full, which does not
read the port monitors, runs under this amendment's manifest on the cases A-full-cd planned, as amendment 9
specifies. Code frozen as spec/CAMPAIGN_MANIFEST_C4E.json; run scripts/run_c4e.sh. Decision: Chris, 29 October 08:48.

## 13. Amendment: the LLM orchestrator under the "can this decide?" step (2026-10-29, 14:30 Madrid; before the run)

Amendment 9 set the LLM orchestrator (arm L-full) in place of the advisor "and nothing else changes". The step that
refuses a check of half an hour or more with under one chance in twenty of leading to a decision is implemented
inside the advisor (Advisor.decide_gate); in L-full the advisor is used only to list checks and predict answers, so
the step was not applied. L-full therefore differed from A-full-cd in two ways: who chooses, and whether costly
checks that cannot decide are admitted. Its results (29 October, 99 planned cases) are kept in
results/campaign/c4/L-full as a diagnostic and not reported: on those cases it was right in 31 against 31, wrong in 2
against 1, with 193.3 hours and 50 visits against 112.7 and 30, after 126 checks of half an hour or more.

Arm L-full-cd applies the step, as the advisor applies it, to every check offered to the LLM: a check the step
refuses is not offered, and the state the LLM reads says how many were withheld and why. The advisor's per-check
calculation is factored into Advisor._assess and Advisor._answer_groups, unchanged, so that the step can be applied
to a check another planner chooses (Advisor.gate_refuses); A-full-cd must reproduce its records exactly under the
change (checked on development cases against results/c4d before any run). Arm L-full-cd then differs from
A-full-cd only in who chooses among the admissible checks. It runs on the 99 sealed cases A-full-cd planned
(exp2_run.py --only-planned --plan-arm A-full-cd), after a smoke test on six development cases.

Comparisons stated in advance, of L-full-cd against A-full-cd on the 99 planned cases and on all 320: right, partial,
wrong and "I don't know"; hours of tests and travel and site visits; the cases on which they differ and, for each,
the checks each chose and the LLM's stated reasons; and whether L-full-cd commits wrongly where A-full-cd does not.

The question the comparison answers (Chris, 29 October 11:27): after each finding, does choosing the next step call
for judgment, or is a coded, contingent policy adequate? In this test bed every finding is scored by a fixed
interpretation, every check's answer under each candidate is predicted, the ledger holds an explicit posterior and
the goal is one with known prices, so the next step can be computed; the expectation is that the LLM matches the
advisor at best and that its occasional misjudgments cost effort or commitments. Code frozen as
spec/CAMPAIGN_MANIFEST_C4F.json (with amendment 14); run scripts/run_c4f.sh.

## 14. Amendment: the LLM proposer where fault knowledge is missing (2026-10-29, 14:30 Madrid; before the run)

In the generation study the LLM proposer was the strongest single line (class top-1 0.92 at day 2190), but pooled
with the six coded lines it added no coverage (the truth's class present in 97 and 100 per cent of cases with or
without it), and in the agent on the sealed cases it changed nothing. The coded lines read the same complete,
one-vocabulary documentation the proposer is grounded in. The test asks where the proposer should add: when the
documentation names a fault mode but gives none of its effects, the coded lines cannot connect symptoms to it, while
the LLM can propose it from what such a fault does, and grounding accepts it because the mode is documented. When a
mode is missing altogether, grounding rejects it too, and no gain is expected (the control of the argument).

Arms (scripts/kgx_llm_run.py), on the diagnosis cases 2000-2159 as in the degradation study, under the C4F code:
A-full-cd-kg-sig and A-full-cd-llm-kg-sig (30 per cent of fault signatures removed); A-full-cd-kg-modes and
A-full-cd-llm-kg-modes (20 per cent of fault modes removed). The proposer is built on the agent's side of each case
(the degraded graph and the frame built from it) and joins the six coded lines at the same weight. Control, before
any API call: with the proposer abstaining on every case (--fake-empty), A-full-cd-llm-kg-sig must reproduce
A-full-cd-kg-sig exactly. Comparisons stated in advance, with and without the proposer, per degradation: right,
partial, wrong and "I don't know"; effort and visits; the cases the proposer recovers and the cases it loses; and,
for the recovered cases, whether the proposer named the true mode. Expected: a gain under kg-sig, none under kg-modes.

Not in this amendment, to follow in their own before any run: the LLM framer end to end, the interpretation of
questions as operators put them, and the expression of the agent's answer for an operator.

## 15. Amendment: the LLM shown the advisor's own check groups (2026-10-29, 18:45 Madrid; before the run)

On the 99 planned cases L-full-cd and A-full-cd differ in 11. In four (s7033, s7118, s7121, s7169), every leading
candidate was a DGD rise on one of the spans of one section. The advisor made a two-minute coherent diagnostic,
whose value lies in confirming the candidates, which all predict the same answer, against a cause nobody proposed:
the advisor adds "none of these" as a group of its own when the leader's predicted answer differs from the no-fault
answer (Advisor._assess). The commitment at the coarser grain, the section, followed, and was right. The state the
LLM read grouped each check's answers over the named candidates only and, with one group, said the check "separates
nothing"; the LLM stopped and the agent reported "I don't know". This is a difference in what the two planners were
given, not in how they judge. Every other input of the advisor's choice was checked and is shown to the LLM: the
candidates and their probabilities, the grouped predicted answers, effort and budget, the "can this decide?" step
(amendment 13), the returns left, and the candidates with no prediction.

The fix (tools/orchestrator_llm.py, prompt orchestrator.v5): each check's groups shown to the LLM are the advisor's
own, from Advisor._assess, including the "none of these" group with its probability and its meaning, and a check is
said to separate nothing only when the advisor would find no group to separate. Each planner call records how many
"none of these" groups it was shown. L-full-cd's results are kept as a diagnostic and not reported. Arm L-full-cd2
(otherwise identical to L-full-cd) runs on the same 99 sealed planned cases after the C4F runs finish, under
spec/CAMPAIGN_MANIFEST_C4G.json (scripts/run_c4g.sh: the full test suite, the identity check of amendment 13, a smoke
test on six development cases in which at least one "none of these" group must be shown, then the freeze). The
comparisons stated in amendment 13 apply to L-full-cd2.

## 16. Amendment: the remaining LLM roles (2026-10-30, 13:00 Madrid; before any run)

Three roles of an LLM in the agent are measured, so that the table of roles in "Where Judgment Pays" has no
untested row. Each is compared with the coded alternative under the C4H code (spec/CAMPAIGN_MANIFEST_C4H.json,
scripts/run_c4h.sh, after the full test suite, the identity check of amendment 13 and a smoke test of each part).

**Framing.** Arm A-full-cd-lf is A-full-cd with the first case frame built by the LLM framer (tools/frame_llm.py,
query tools over the graph and the data lake) in place of the deterministic framer; return passes still widen the
frame with the deterministic framer. On the sealed cases 7000-7079 (ten of each kind), against A-full-cd on the same
cases: right, partial, wrong and "I don't know"; effort and visits; the frame's symptoms and quiet instruments. The
earlier measurement (generation study, 50 cases, code before the corrections, the proposer's first choice) found the
LLM's frames 22 to 24 points worse; this measures the effect on the final diagnosis.

**Interpreting the question** (scripts/qi_run.py, tools/question_llm.py, harness/rephrase.py). For each of the 320
sealed cases the templated question is the truth (kind, anchor, reported time). Two free-text versions: a
controlled rewrite by hand-written forms, and a free paraphrase written by a different model (DeepSeek), given only
the facts needed to keep it resolvable from the records and told to write as an operator or customer would. Each is
read by the LLM reader (the study's model, four record lookups) and by a coded reader. A reading is right when kind
and anchor match and the time is within 300 s. Expected: the coded reader adequate on the controlled set, short on
the free set; the LLM reader adequate on both. Where a reading is right the agent's diagnosis is the same by
construction (the structured question is what the agent starts from).

**Expressing the answer** (scripts/ae_run.py, tools/answer_llm.py). For the sealed cases 7000-7079 the agent's
record (A-full-cd) is summarised with the question; the LLM writes the operator's report and states its conclusion
and the facts it used with their fields. Code checks faithfulness: the conclusion must be the ledger's; every
identifier in the report must be in the summary; every cited fact must be at its field. A coded template writes the
same report from the same summary. Usefulness is rated by people, blind, on the pairs (results/campaign/c4/ae/
blind.json); that rating is not part of the run.

## 17. Amendment: completing the C4 reruns (2026-10-30, 12:40 Madrid; before any run)

**The omission.** Amendment 8 required every agent result the paper and report present to be produced again under
C4, and listed the arms. The sealed-set arms were run; the development-case arms and the future-instrument agent on
the sealed set were not. The paper and the note "Where Judgment Pays" still carry results from earlier code: the
capability table, the robustness tests, the future-instrument rows, the table of the LLM deciding alone, the
generation-study figures for the LLM proposer, and the traces of cases 2021 and 2071. None of these is reported until
it is produced under the current frozen code. Decision: Chris, 30 October 11:59.
**Runs** (under the C4H manifest frozen by scripts/run_c4h.sh, after its identity test of A-full-cd against
results/c4d; the agent's code is that of C4 for every arm below):
(1) Sealed 7000-7319 (ADAGENT_RECORD_RUN_C4=1): A-full-cd-fut, B-fut.
(2) Diagnosis cases 2000-2319: A-full-cd-fut, B, B-fut; the capability ladder A-L1 to A-L6 on their rung's cases
(exp2.ladder_seeds), A-Lall, B-L1, B-Lall; LLM-1 (the LLM deciding alone, prompt unchanged; API about $27).
(3) Diagnosis cases 2000-2159: the twin with structural error, A-full-cd-tm1, A-full-cd-tm2 (new arms: the reported
agent, with the "can this decide?" step), B-tm1, B-tm2; the graph degraded, A-full-cd-kg-none, A-full-cd-kg-inst,
A-full-cd-kg-flat, B-kg-none, B-kg-inst, B-kg-flat (scripts/kgx_run.py); the LLM proposer with full documentation,
A-full-cd-llm-kg-none (scripts/kgx_llm_run.py; API about $13), against A-full-cd-kg-none.
(4) Traces of cases 2021 and 2071 (A-full-cd) for the worked example and the Discussion.
(5) Alarm correlation (AC) on the diagnosis cases 2000-2319, for the comparisons on those cases.
(6) Measurements of the twin against the world and of the network's history, recomputed on the current world
(scripts/twin_fidelity.py; the history statistics of docs/builds/BUILD_01): no agent runs.
(7) The generation study repeated on the current code (spec/PROTOCOL_EXP1_GENERATION.md, seeds 1000-1099, days 730
and 2190): the offline lines and their equivalence classes (cloud workspace), the LLM proposer's reference arm L0,
one repeat (laptop; API about $15, measured from the first study's tokens). Measures as first specified, from one
repeat: each line's first candidate right to the class, and the coverage of the true class by the pooled lines with
and without the proposer. Stability across repeats is not measured again and not reported.
(8) Analyses recomputed from the C4 records (no runs): what would resolve the unresolved cases
(scripts/analysis_resolve.py on A-full-cd, diagnosis cases), the capability table, the graph-degradation table with
the wrong commitments that name the right element (scripts/analysis_kgx.py on kgx_c4 and the reruns), the LLM-alone
table, and the twin's error under each structural mismatch (both levels reported).
**Baselines named.** On the diagnosis cases the agent is A-full-cd (results/c4d) under the status quo and
A-full-cd-fut under future instruments; manual troubleshooting is B and B-fut. The LLM-alone table and the
robustness tables state which instrumentation they use (the status quo).
**Worked example.** The trace of case 2021 (docs/worked_example/s2021_frozen.json, frozen C4d code) is regenerated
under C4H with its figures; AC and B on case 2021 are in results/campaign/c4_trace.
**Machines.** Each arm runs whole on one machine: arms in (3) other than the API arm in the cloud workspace, the rest
on Chris's laptop. The machine is recorded with each arm.
**Measures** as in the amendments that first specified each arm (4, 5, 3.2, 3.3, 14), with the generation figures
(the proposer's first candidate right to the class; coverage of the true class with and without the proposer)
computed from the records of A-full-cd-llm-kg-none and A-full-cd-kg-none.
**Provenance check.** scripts/provenance_check.py lists every number the paper and reports state with the run it is
taken from, and fails if any run directory holds a record not produced under a C4-series manifest. It is run before
every push and every draft sent for review.
