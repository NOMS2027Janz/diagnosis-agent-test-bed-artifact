# Amendment 18: documentation completion by the language model

Date: 1 Nov 2026, 16:30 Paris. Written before any completion or run. Decision: Chris, 1 Nov 16:11 and 16:15.

## Why
The knowledge-graph degradation arms (kgx_c4) show the agent cannot fairly judge a cause its documentation does not
describe: with 30% of fault signatures removed it loses 29 of 122 cases and commits 9 wrong; with 20% of fault modes
removed it loses 45 and commits 16 wrong. The LLM proposer, as built, cannot compensate: it may name only documented
modes, and a mode with no documented effects is charged for every alarm and service it cannot explain (C4 audit F7,
which closed a free pass and stays). The LLM's knowledge has no way into the judgment. This amendment gives it one.

## Method
A documentation-completion step run once, when documentation is loaded, before any case. For each equipment type the
LLM reads the type's documentation as held (description, element class, notes, its fault modes with their
descriptions, effects, nature, rate and twin scenario) with the shared vocabularies (effects, scope and location terms
from knowledge/vocabulary.yaml and the documentation; the fault-mode names known to the graph; the twin's scenario
catalogue) and returns, in the documentation's own form:
- (a) gaps: effects for each of the type's modes whose documentation gives none (a visible gap);
- (b) additions: fault modes the type can suffer that its documentation lacks, each with description, effects,
  nature, rate and twin scenario.
It never sees a case. Output is validated against the vocabularies (invalid entries dropped and counted) and stored
(results/campaign/a18/completion_<kind>_<degseed>.json); the agent reads the completed graph. Every completed entry
carries provenance "llm". Trust, fixed now: an added mode's documented rate is half the rate the LLM states; supplied
effects are used as given. The agent's code is unchanged (C4H manifest); only the graph it reads differs.
Model: the study's OpenAI model, as supplied, one structured call per equipment type.

## Conditions on every other result (decided before any result)
1. Gap filling acts only on modes with no documented effects. On complete documentation there are none (checked: all
   90 type-mode pairs carry effects), so the completed graph is identical and no other result can change. Verified by
   a graph comparison and the identity check (scripts/identity_c4f.py) on the completed complete documentation.
2. Additions act on every type, gaps or not. Run on the complete documentation first and count what is added. If
   nothing: as (1). If something: run the agent with the additions on the diagnosis cases 2000-2319 under status-quo
   and future instrumentation and compare case by case with the existing records; every changed case is explained.
   The sealed cases are not reopened; the paper states that sealed results are for vendor documentation as supplied
   and reports additions as a measured option.

## Development (no API; completions fixed once generated)
Development cases 3000-3039 (fresh development cases), with degradations drawn from a different seed (DEVSEED) from the diagnosis arms', so no
choice can be tuned to the entries removed there. Changes allowed in development: the prompt, the validation, the
trust rule. Each change is logged; the final form is frozen (hashes of the scripts and the prompt version) before the
diagnosis arms.

## Measures
- Completion against the true documentation: gaps, effects matching exactly (effect, scope, location) and by effect
  only; additions, removed modes recovered (recall) and modes added that the true documentation lacks (spurious).
- Agent, on the diagnosis cases 2000-2159, status quo: right, wrong, partial, coarse, "I don't know", effort, visits,
  against the degraded arm and the control (A-full-cd-kg-none: 122 right). Recovery = (completed - degraded) /
  (control - degraded). Arms: A-full-cd-kg-sig-comp, A-full-cd-kg-modes-comp, A-full-cd-kg-none-comp.
- Cost: calls, tokens, time.

## Runs
Completion calls on the laptop (API key), a few minutes. Agent arms on the laptop or the cloud (no API).
Scripts: scripts/a18_complete.py (completion), scripts/a18_run.py (agent arms over degraded and completed graphs),
scripts/a18_score.py (completion against truth). Outputs under results/campaign/a18/.

## Development and freeze (2 Nov 2026, 05:30 Ottawa)
Development cases 3000-3039, development draw. Right / partial / wrong / "I don't know" of 40:
- Complete documentation: no completion 34/3/1/2; additions at half rate 32/4/1/3; sibling 34/3/1/2; graded 33/3/1/3.
- Signatures removed: none 31/5/1/3; oracle 34/3/1/2; gaps and additions at half rate 29/6/1/4; gaps only 34/3/1/2;
  sibling 33/4/1/2; graded 33/4/1/2.
- Modes removed: none 29/5/2/4; oracle 34/3/1/2; half rate 30/6/1/3; sibling 34/3/1/2; graded 33/4/1/2.
Completions against the true documentation (development draw): all 27 visible gaps filled, 13 exactly, 26 of 32 true
effects given; 18 of 18 removed modes recovered, with 44 modes the true documentation lacks (33 on complete
documentation). Every recovered mode is documented, as held, for another type of the same element class; 11 of the
44 others are.
Change made in development (the trust rule, as allowed): an added mode is kept only when the documentation as held
gives it for another type of the same element class (comparable equipment), at half the stated rate ("sibling").
Gap filling, the prompt (doc-completion-a18-0.1) and validation are unchanged. Chosen on the development figures: it
matches the oracle under mode removal and the control on complete documentation, and is one case short of gap
filling alone under signature removal.
Frozen: scripts/a18_run.py sha256 aa26cd8cbd3e... (--trust sibling; 3d7719c600cf before a wiring fix, below), scripts/a18_complete.py 28fcceb46ef3....
Condition 1 checked: gap filling on complete documentation leaves the graph identical (every edge and attribute).
Condition 2 applies: the frozen rule adds 11 modes to complete documentation, so the case-by-case comparison runs on
2000-2319 under both instrumentations (scripts/run_a18_diag.sh).

Wiring fix before any diagnosis result (2 Nov, 06:15): the first launch of run_a18_diag.sh failed on every case
(RecursionError): for the diagnosis draw the runner replaced kgx_run.degraded and then called it through
a18_complete.degrade. The runner now keeps the original function and calls it. The development draw never took this
path, so development results are unaffected; the method (completions, trust rule, rates) is unchanged. Failed
records moved to results/campaign/a18/_failed_0505. Checked end to end on one case of each base arm before relaunch.
