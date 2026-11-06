# Decision D1: the scoring rule

For Chris, 2026-10-15. Needed before the Experiment 1 protocol is frozen.

## The question

When the data cannot distinguish the true cause from another cause, how should a line be scored for naming the
other one? The specification (§9.2) defines the truth's equivalence class: every single cause that some value of
its free parameter makes indistinguishable from the truth on the data the agent could have had, within two
combined standard deviations, with the same alarms. §9.3 then scores a commitment to a class member other than
the truth as a confident error (NOTES_FOR_V0.2 item 10). Generation needs the same choice in a milder form:
does a top-ranked class member count as a hit?

## What the classes look like (development cases, results/equivalence/)

Most cases have a class of one: hard faults with alarms are unambiguous. Ten of fifty do not:
- a fibre cut: the two fibres of the cut cable (2);
- an SOP transient: the four spans of the line (4);
- a cross-connect misconnection at day 730: the four switches on the path (4; one at day 2190, where more ports
  report and the trace mismatch points to one switch);
- a noise-figure rise: the five amplifiers of the section (5);
- WSS filter shifts: receiver degradation at the receiver and the filters at the other ROADMs on the channel's
  path (4 and 5 at day 2190; 18 at day 730, where DGD rises on the path's spans also fit);
- one WSS filter shift that left nothing visible on day-730 instruments: a class of 188.

## What the choice changes (development cases, results/class_scores/20261015-093408.json)

| Line | Day 730 top-1 strict / by class | Day 2190 top-1 strict / by class |
|---|---|---|
| LLM proposer (three runs) | 0.85 to 0.86 / 0.92 to 1.00 | 0.85 to 0.88 / 0.92 to 0.93 |
| Procedures | 0.84 / 0.94 | 0.86 / 0.94 |
| Set cover | 0.84 / 0.88 | 0.84 / 0.84 |
| Abduction walk | 0.80 / 0.88 | 0.78 / 0.80 |
| Alarm correlation | 0.64 / 0.65 | 0.52 / 0.52 |
| Pooled, offline lines | 0.85 / 0.96 | 0.84 / 0.92 |
| Pooled, with the LLM proposer | 0.84 / 0.96 | 0.88 / 0.96 |

The class rule raises every line that names plausible alternatives the data cannot rule out, and leaves alarm
correlation and set cover almost unchanged (they rarely name such alternatives). Strict scoring penalises a line
for the data's ambiguity, not for its reasoning: in the WSS cases no line could do better than a coin toss among
the class.

## Options

1. **Strict only.** Simple; but it measures luck within the class as much as skill, and it would make a line look
   worse for naming a cause it had every reason to name.
2. **Class only.** Measures what the data allow; but it hides how often a line commits to one member of an
   ambiguous class, which matters for winnowing (that is when a check is needed).
3. **Class as primary, strict alongside, and in Experiment 2 a separate outcome for class-ambiguous commitments.**
   Generation is scored by class (did the line put something the data cannot rule out first, in the top five,
   with how much probability), with strict figures reported in every table. In winnowing, a commitment to a class
   member is neither correct nor a confident error but its own category, "committed where the data were
   ambiguous", reported with its rate, because the right behaviour there is to ask for a check, not to commit.

## Recommendation

Option 3. It scores each line on what it could know, keeps the strict figures visible, and turns the §9.3 problem
into a measurement (how often the agent commits when it should ask) rather than a penalty. Two details go with it:
the class is computed over every location any line named for the case (so it covers whatever a line could
propose), and a class of more than about ten members is reported as "unlocalisable on the data" and excluded from
top-1 and top-5 means (counted separately), since naming any one of 188 indistinguishable causes says nothing
about a line.
