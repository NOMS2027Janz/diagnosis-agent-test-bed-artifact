# Decision D3: the effort of field tests (Baseline B and the winnowing loop)

For Chris, 2026-10-15. Needed before Experiment 2's field tests are built. Not needed for Experiment 1.

## The question

In Experiment 2 the agent and the baseline (the procedures executed verbatim) ask for checks. Some read data the
agent already has, at no cost; some are field tests the world must perform. To compare them fairly, each field
test needs an effort: how long it takes before its result is known, and whether it needs someone on site. The
figures shape the results, because the agent's advantage, if it has one, is to ask for fewer or cheaper tests.

## Proposed figures

Remote tests are performed from the network operations centre through the equipment's management interface; site
tests need a technician at a site, with travel. Travel time is drawn per case from a distribution (proposed: 1 to
4 hours, median 2, longer for remote sites), because it dominates and varies.

| Test | Where | Time to result | Notes |
|---|---|---|---|
| Read further existing data (history, other instruments) | Remote | 0 to 2 min | Free in effort terms; counted as queries |
| Embedded OTDR trace on a span | Remote, if the site has an embedded OTDR (full tier) | 5 to 10 min | Otherwise a portable OTDR: site test, 30 min plus travel |
| Transmit power at a line interface | Remote (card performance data) | 2 min | Already in the lake if the card reports it; a site power meter otherwise |
| Loopback of a client port | Remote | 10 min | Interrupts the service if it is not already down |
| Audit of cross-connects against provisioning | Remote | 15 min | |
| Retune a channel or move it to a spare receiver | Remote | 30 min | Needs a spare port; interrupts the channel |
| Span loss measurement | Site, both ends | 45 min plus travel to both ends | |
| Inspect and clean a connector | Site | 20 min plus travel | Often fixes a dirty connector as it tests it |
| Replace a card or amplifier to test the hypothesis | Site | 60 min plus travel | A repair as much as a test |

## Options for using them

1. **Time only:** each test costs its time to result; travel counted once per site visit, and tests at the same
   site during one visit share it.
2. **Time and a dispatch penalty:** as option 1, plus a fixed penalty per site visit (for example two hours'
   equivalent), reflecting that dispatch is the expensive decision in practice.
3. **Two measures reported side by side:** total time to diagnosis, and number of site visits.

## Recommendation

Option 3, with the figures above as the reference and a sensitivity run at half and double travel time. The
figures are judgement from general operations practice, not from operator data; if you have operator figures, or
want different values for any row, they should replace these before Experiment 2's protocol is frozen.
