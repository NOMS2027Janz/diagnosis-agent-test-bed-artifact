# Amendment 19: question interpretation with the reported time defined

Date: 1 Nov 2026, 16:45 Paris. Written before any run. Decision: Chris, 1 Nov 16:31 (option B). Replaces the
question-interpretation results of amendments 16 and 17, which are not reported.

## Finding that motivates it
The structured question's reported_at_s is the stated start for an event (a loss of service, an alarm, a reading
changed or frozen since a time) and the time of asking for a gradual trend (rephrase.py and the case generator).
Amendment 16 asked the LLM reader for "when the problem was noticed or began" and it gave trends' onsets; amendment 17
asked for "reported or noticed" and it gave tickets' raise times. The field is a convention, not something the text
states, so no instruction to the reader can define it for both. A design pitfall, found and fixed.

## Method
The LLM reader extracts only what the text states: kind (customer or operations), anchor, problem ("event" or
"trend"), and an event's start time (started_at_s; null for a trend or when no time is given). A fixed rule in code
forms reported_at_s: the stated start for an event, else the time of asking. The coded reader (which already applies
the convention), the controlled rewrites, the paraphraser (DeepSeek, amendment 16's prompt), the four lookups and the
scoring (kind, anchor, and time within 300 s) are unchanged; scoring now also records each component.
Script: scripts/a19_qi_run.py (frozen modules imported unchanged; C4H manifest unchanged).

## Cases
The sealed cases' question texts were examined in diagnosing amendments 16 and 17, so they are not the test.
Development: cases 3000-3079 (only the reader's prompt wording may change, each change logged). Test, run once after
the prompt is frozen: the diagnosis cases 2000-2319, never used for this test. Only their questions are used.

## Measures
Right readings (all three components) and each component, by reader (coded, LLM) and text (controlled, free), by
controlled form and question kind; lookups per reading; failures listed. No agent result depends on this test.

## Development result and freeze (1 Nov 2026, 18:30 Paris)
Development, cases 3000-3079, first prompt, no change: controlled texts coded 78/80, LLM 80/80; free texts coded
78/80, LLM 77/80. Every time component right for the LLM on both texts. The three LLM failures are the question's
kind on paraphrases that read as either (a ticket on a service's degraded quality, from operations, read as a
customer's). No prompt change is warranted. Frozen: scripts/a19_qi_run.py sha256 4cd843d3a8a3..., prompt version
question-reader-a19. Test on 2000-2319 next, once.

## Test result (1 Nov 2026, 19:15 Paris), diagnosis cases 2000-2319, run once, 25 min, no failures
Right readings (kind, anchor and time within 300 s) out of 320:
- controlled texts: coded reader 308, LLM 313. Misses are all anchors: amplifiers named by position ("amplifier 2
  counting from S09", amp1 in the records) read one place off (coded 12, LLM 7).
- free texts: coded reader 298, LLM 299. LLM misses: kind 16 (operations tickets on degraded quality read as a
  customer's; the coded reader misses 18 the same way), time 5 (an alarm's raise time in a ticket not taken as the
  start, and one frozen-reading start read as 14:00).
By question kind, LLM: customer 174/174 controlled and 171/174 free; operations 139/146 and 128/146.
Lookups per reading: 1.16 on average.
