# Amendment 23: drift cases in a world that raises standing alarms (4 November 2026)

Approved by Chris Janz, 4 November 2026, 09:01.

**The fault.** A drift case is posed as an early warning, before any alarm. The case generator (`make_case2` in `src/adagent/harness/cases2.py`) enforced this by rejecting any draw in which the case's world raised any alarm at all. With amendment 22 in place, the level-2 world of the twin-mismatch test runs three lightpaths (och140, och177 and och218) below the signal-degrade threshold with no fault, so they raise standing OTU_SD alarms from the start of every case. No drift case could then be drawn at level 2: all 20 drift seeds of 2000 to 2159 failed with "no noticeable drift anomaly drawn".

**The fix.** A drift is rejected when the case's world raises any alarm that the same world with no fault does not raise. Alarms the network raises with no fault are standing alarms; they are not the drift's and do not bar the question. They stay in the case's data for both approaches to see. A drift is also not posed on a lightpath that already carries a standing alarm, since the question states that no alarm has been raised.

**What it changes.** Only drift cases in a world that raises standing alarms. At level 0 (every sealed, diagnosis, completion and generation case) and at level 1 the world with no fault raises no alarm, so the test is the same as before and every case is generated exactly as before. This is checked on drift seeds at levels 0 and 1 before any run.

**The runs.** Manifest `spec/CAMPAIGN_MANIFEST_C4U.json` (C4T with this change, `AMENDMENT_22a.md` and this memo). Under C4U: `A-full-cd-tm2` on seeds 2000 to 2159, and `B-tm2` on the 20 drift seeds (2003, 2011, ..., 2155), whose other 140 records under C4T stand unchanged because the change touches only drift cases. `A-full-cd-tm1` and `B-tm1` stand under C4T.
