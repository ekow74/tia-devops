# KSP Pack Builder — Release-Lot Integrity S01

Purpose: close the new S06 review finding that release lots could freeze/release unapproved content or mutate after freeze.

This layer gates the **first release-lot freeze**, not Phase-0 source registration. The separate D-0019 PostgreSQL 18 control/staging run still gates source registration.

Controls:
- membership INSERT/UPDATE/DELETE only while lot is `CANDIDATE`;
- membership must bind an exact `APPROVED`, non-recalled `(claim_revision_id, content_hash)`;
- empty lots cannot freeze/release;
- every pinned revision is rechecked at freeze/release;
- `RELEASED` requires prior `FROZEN` state;
- FROZEN/RELEASED membership is immutable;
- a pinned revision becoming `STALE`/`RECALLED` quarantines its FROZEN/RELEASED lots in the same transaction.

Qualification cases:
- RL1 unapproved revision add -> reject;
- RL2 pinned revision no longer approved at freeze -> reject;
- RL4 recalled revision add -> reject;
- RL5 released-lot membership delete -> reject;
- positive APPROVED -> FROZEN -> RELEASED -> pass;
- recall of released pinned revision -> lot becomes `QUARANTINED`.

PostgreSQL 18 CI uses an S06-compatible minimal fixture schema to exercise the controls. The full S06 schema with the same controls is retained in the Claude review artifact; the eventual control/staging D-0019 qualification remains authoritative for the complete Phase-0 schema.
