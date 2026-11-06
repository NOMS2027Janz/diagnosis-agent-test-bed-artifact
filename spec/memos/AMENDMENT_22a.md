# Amendment 22a: stale caches for the mismatch worlds (4 November 2026)

An audit of every experimental setting and cache after amendment 22 found two caches whose keys do not cover the fix:

1. **Ticket observations** (`.cache/observed_24_1_g1_cal_m1.json`, `_m2.json`, written 30 October). The key is `OBSERVED_VERSION` and a digest of the history's events and incidents; it covers neither the network's `nli_factor` and `mismatch` nor the code. After the fix the key still matched, and rebuilt observations differ in 182 of 1,757 tickets (level 1) and 332 of 1,757 (level 2). Only the experience store behind the analogy line reads them, so the manual-troubleshooting arms (B-tm1, B-tm2) are unaffected; the agent arms (A-full-cd-tm1, tm2) would have read them. Both files were moved to `.cache/stale_pre_amendment22/` before the agent arms of the rerun started, so they are rebuilt from the fixed code. The real-world file (`observed_24_1_g1_cal.json`) matches the fixed code in all 1,757 tickets.
2. **Fits** (`results/_fitcache`): `_fit_version` hashes the physics and simulator files but not `history.py`. The rerun uses the fresh `results/_fitcache_c4t`, as amendment 22 states.

No code is changed, so manifest C4T stands. Neither cache is shipped in the archive, where both are rebuilt on first use. Every other setting was verified to act where it should, in the simulated world or in the records.
