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

RecallEvents are durable append-only authority records: deletion, retargeting, and truncation are rejected. `completed_at` may be set once and is audit metadata only; a completed event continues to block pinning/freezing and quarantine matching released lots. An event must match at least one existing claim revision. In particular, issue a claim-wide recall only after at least one revision exists for that claim; zero-match events are rejected, not queued for future revisions.

The shared advisory transaction gate serializes release-lot and membership writes, every claim-revision status/recall update, every RecallEvent insertion, and human-dual review updates/deletes. This is intentionally global: unrelated lot operations can wait behind a RecallEvent that is itself waiting for a row-lock holder. These write paths fail closed unless the transaction uses READ COMMITTED. Approval, freeze, and recall jobs should set bounded transaction-local timeouts, for example `SET LOCAL lock_timeout='5s'` and `SET LOCAL statement_timeout='2min'`, then fail visibly and retry only after investigating a timeout; do not bypass the gate or fall back to an older snapshot. These are operational starting values, not production-qualified settings. Explicit row locks taken outside these guarded write paths are not globally ordered.

The `release_lot.snapshot_isolation` column is constrained to `REPEATABLE READ`, but this package contains no bundle-assembly reader or code that consumes the field. Its intended relationship to `registry_snapshot_at` is unresolved; it does not describe the isolation level used for freeze writes. Freeze-time eligibility is revalidated by a READ COMMITTED write transaction. Do not infer a read-only assembly contract from this metadata.

The D-0019 PostgreSQL 18 control/staging qualification at READ COMMITTED remains pending and requires separate owner authorization. No production or staging test is performed as part of this package qualification. Before any real freeze, the application role must be non-owner/non-superuser and unable to disable triggers or set `session_replication_role`; no grants/role model has been qualified here. `bundle_sha256` remains caller-supplied and is not cryptographically bound to the pinned membership rows.

The GateChange is a pending review proposal, not an approval receipt. The PR remains draft pending independent Claude review of the final head and trusted-base GateChange approval.

This proposal is GC-0008; GC-0007 is reserved separately by PR #12. D-0023 is cited only for the governance boundary; it is not approval of the ReleaseLot implementation. Implementation-specific owner and independent-review provenance remains pending.
