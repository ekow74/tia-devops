# KSP Pack Builder — Release-Lot Integrity S02

Purpose: enforce release-lot state, identity, pinning, recall, and concurrency controls in the shipped PostgreSQL schema.

This layer gates the **first release-lot freeze**, not Phase-0 source registration. The separate D-0019 PostgreSQL 18 control/staging run still gates source registration.

Controls:
- membership INSERT/UPDATE/DELETE only while lot is `CANDIDATE`;
- membership must bind an exact `APPROVED`, non-recalled `(claim_revision_id, content_hash)` with no matching `RecallEvent`;
- empty lots cannot freeze/release;
- every pinned revision and matching `RecallEvent` is rechecked at freeze/release;
- `RELEASED` requires prior `FROZEN` state;
- explicit allowed state transitions; non-candidate lot identity is immutable. Candidate, frozen, and released lots may exit to `QUARANTINED` or `RECALLED`; both are terminal;
- `frozen_at` is set by the CANDIDATE-to-FROZEN transition;
- TRUNCATE cannot bypass release-lot or membership guards;
- a pinned revision becoming `STALE`/`RECALLED` quarantines its FROZEN/RELEASED lots in the same transaction.
- a RecallEvent quarantines matching FROZEN/RELEASED lots in the same transaction.

Qualification cases:
- RL1 unapproved revision add -> reject;
- RL2 pinned revision no longer approved at freeze -> reject;
- RL-REC-1 matching RecallEvent prevents pinning;
- RL-REC-2 matching RecallEvent after pinning prevents freeze;
- RL5 released-lot membership delete -> reject;
- all 25 status pairs and identity mutations in FROZEN/RELEASED -> exact outcomes;
- RL-CONC-A/B approval-withdrawal and RL-CONC-C/D RecallEvent/freeze orderings -> exact outcomes, with observed blockers;
- positive APPROVED -> FROZEN -> RELEASED -> pass;
- recall of released pinned revision -> lot becomes `QUARANTINED`.

PostgreSQL 18 CI reconstructs and loads the canonical shipped schema from `sql/phase0_schema.parts/` and `sql/release_lot_controls.sql`; it does not qualify against the minimal fixture schema. The advisory transaction gate serializes pinning, release-lot mutations, approval withdrawal, and RecallEvent insertion before tuple locks. Per-revision `FOR SHARE`/`FOR NO KEY UPDATE` locks remain in stable revision-id order. These operations require READ COMMITTED; stronger isolation aborts before mutation to avoid stale-snapshot approval.

The GateChange is a pending review proposal, not an approval receipt. The PR remains draft pending independent Claude review of the final head and trusted-base GateChange approval.
