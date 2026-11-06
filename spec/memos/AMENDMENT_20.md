# Amendment 20: documentation completion, second round

Date: 2 Nov 2026, 12:00 Ottawa. Written before any amendment-20 completion or run. Decision: Chris, 2 Nov 07:32
(option 2: a proper next round, developed on more development draws and tested once on fresh cases).

## Why
The test of amendment 18's frozen method on the diagnosis cases 2000-2159 (status quo) recovered 14 of 29 lost
diagnoses under signature removal (wrong conclusions 9, unchanged) and 7 of 45 under mode removal (wrong 16 -> 14).
Two causes, neither visible on the single development draw:
1. Signatures: the model filled the removed signature of an OMS-input connector fault with the right effect but added
   a location ("far_end_receiver") the documentation does not give; the true cause could then not explain the
   readings and an amplifier pump failure was committed instead (all 9 wrong conclusions). The alarm links (probable
   causes) removed with the signatures were not restored at all.
2. Modes: the model proposed 17 of 18 removed modes, but the trust rule (keep only modes documented for comparable
   equipment) dropped the three on one-of-a-kind outside-plant types (fibre cut and polarisation transient on spans,
   dirty connector at an OMS input), which no comparable type can attest. Fibre cuts are the most frequent cause.
The development draw removed no outside-plant documentation, so development could not see either.

## Method (changes from amendment 18)
- Templates: each mode documented without effects is shown with the effects documented for the same mode on
  comparable equipment (same element class; any other type if none). The location vocabulary is shown with the
  documentation's own uses of each term; a location is to be given only where the templates or conventions give one.
  Prompt version doc-completion-a20-0.1 (scripts/a20_complete.py).
- Alarm links: for each filled gap and each added mode, a probable cause is restored on an alarm the type raises where
  a comparable type lists the same mode as a probable cause of the same alarm (by analogy, in code).
- Trust rule for added modes: attested by comparable equipment, half the stated rate (as amendment 18);
  unattested on a one-of-a-kind type (no other type of its element class), admitted at a reduced rate (--single,
  0.5 or 0.1 of the stated rate, chosen in development); other unattested modes dropped.
The agent's code is unchanged (C4H manifest).

## Development (allowed changes: prompt, the one-of-a-kind rate, the alarm-link rule; each logged)
- Completions scored against the true documentation on six development draws (dev1, dev2, dev4, dev6, dev7, dev10:
  each removes outside-plant documentation), for signatures and modes, and on complete documentation
  (scripts/a20_score.py).
- The agent on development cases 3000-3079, draw dev4: completed and empty for each degradation and for complete
  documentation; modes also at --single 0.1 (scripts/run_a20_dev.sh).

## Test (once, after the freeze)
Fresh cases 3160-3319 (never used in amendments 18-20), a fresh draw ("test", seed 20261102, fixed now and not
examined before the freeze): control, signatures removed and modes removed, each degraded and completed. Condition 2
of amendment 18 (what the frozen completion adds to complete documentation) rerun on the diagnosis cases 2000-2319
under status quo and future instrumentation, compared case by case with the existing records.
The amendment-18 test result is kept and reported as the first round; the lessons it gave are reported.

## Development results and freeze (2 Nov 2026, 20:50 Ottawa)
Completions scored on six development draws (dev1, dev2, dev4, dev6, dev7, dev10): signature gaps filled exactly
25-27 of 27 (amendment 18: 13 of 27); no wrong location; removed modes kept 17-18 of 18, outside plant included;
alarm links missing after completion 0-2.
Agent, development cases 3000-3079, draw dev4 (right / wrong of 80): control (complete documentation) 63 / 1.
- Signatures removed: 58 / 1; completed (first form) 59 / 1; completed with the change below 61 / 1.
- Modes removed: 43 / 9; completed (first form) 62 / 1; with the change 64 / 1 (one case gained against control).
- Complete documentation with the completion's additions: first form 63 / 1; with the change 63 / 1, every case as
  the control.
Change made in development (logged 17:45, lab book): one fault, one home (scripts/a20b_run.py --nodup). In the
first form, the one-of-a-kind path admitted connector faults on fibre spans, duplicating the connector faults
documented on the OMS input; with the same fault explained in two places the agent concluded the true cause was
probably never proposed (cases 3013, 3037). The arm with the one-of-a-kind rate cut to 0.1 was not run: the change
removes the cause rather than diluting it.
Frozen: scripts/a20_complete.py 88e3f198445e (prompt doc-completion-a20-0.1), scripts/a20b_run.py 37283ffe281d
with --nodup, trust a20, one-of-a-kind rate 0.5; the test command scripts/run_a20_test.sh e3ad2888958a.

## Test result (3 Nov 2026, 05:51 Ottawa; run once; no failures)
Fresh cases 3160-3319, test draw (seed 20261102), frozen method. Right / wrong of 160:
- control (complete documentation): 122 / 1 (35.5 h, 7 visits);
- signatures removed: 103 / 13 (72.0 h, 17 visits); completed 119 / 1 (50.0 h, 12 visits); recovery 84 per cent;
- modes removed: 98 / 8 (68.5 h, 17 visits); completed 120 / 1 (51.2 h, 12 visits); recovery 92 per cent.
Cases ending differently from the control: 5 (signatures) and 6 (modes). Condition 2 running (2000-2319).
Condition 2 (finished 3 Nov 10:42): the frozen completion's additions on complete documentation, diagnosis cases
2000-2319, compared case by case with results/c4d/A-full-cd (status quo) and results/campaign/c4_dev/A-full-cd-fut
(future instruments). Status quo: 5 cases change group (2133 partial -> IDK; 2164 right -> wrong; 2179 and 2243 right
-> coarse; 2237 partial -> IDK); right 233 -> 230, wrong 0 -> 1. Future: the same 4 apart from 2133; right 233 -> 230,
wrong 0 -> 1. Other results stand for vendor documentation as supplied; additions are reported as a measured option
with this cost.
