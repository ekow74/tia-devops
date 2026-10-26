# KSP Hybrid GitOps + Resilience Baseline

Status: approved with minor implementation fold-ins by `KSP_Deployment_Architecture_Comparison_REVIEW_v2.0_2026-10-26`.
Base: ksp-s02-cicd-security.
Canonical direction: Terraform + Ansible + two-VM Docker Compose, with a small host timer-driven reconciler and staging resilience qualification. No Kubernetes/Argo CD yet.

## Single release authority

Production does **not** trust Git as desired-state authority.

1. The promotion service canonicalizes desired-state JSON and signs it with the dedicated promotion key.
2. The signed desired-state document is published as an OCI artifact/channel pointer.
3. The production reconciler verifies that promotion signature, then independently verifies the referenced ReleaseManifest under the pinned build key.
4. Git is optional audit/diff/history only. Production has no Git credential and the runtime trust path never reads Git.
5. A build key cannot authorize production desired state.
6. Private project-key signing uses no public Rekor upload; Cosign verification explicitly uses the matching no-public-tlog posture. Freshness is enforced by signed timestamps plus monotonic host state.

Runtime authority:

promotion service -> promotion-signed desired-state OCI artifact -> host reconciler -> Docker Compose

## Anti-reconciliation / recall safety

The reconciler is deliberately asymmetric.

1. Persisted or currently verified revocation of **running** content is checked first and returns `QUARANTINE`. This outranks freeze, desired-state signature failure, expiry and flap controls.
2. Revocation sequence never moves backward. `last_seen_revocation_sequence` is persisted by the reconciler account; an older list fails static. Persisted `quarantined_digests` prevents replay from reviving previously recalled content.
3. Promotion and ReleaseManifest build signatures must verify before normal reconciliation.
4. A valid signed freeze stops repair, but **does not stop quarantine of recalled content**.
5. `last_applied_sequence` is persisted; older desired-state documents are refused. A rollback uses a new channel sequence and a separately verified rollback record to target an older eligible release.
6. NTP must be synchronized. `issued_at` more than 5 minutes in the future fails static. Desired-state validity is capped at 48 hours, and expired state is never applied.
7. Authority/revocation transport failure is fail-static: keep the current verified non-revoked release and alert; never fall back to tags or lower sequences.
8. Three failed applies of one state stops reconciliation and escalates.
9. Environment, DB-schema compatibility, minimum reconciler version and expected ports are bound into the signed document.
10. The desired-state schema is closed (`additionalProperties: false`) and carries no commands/hooks/scripts.

## Drift classes

Auto-repair is restricted to known-safe runtime drift:
- stopped/crashed known container;
- running image digest mismatch;
- fixed-template, non-secret Compose configuration mismatch.

Never auto-repaired:
- revoked content -> quarantine;
- unknown container / unexpected port -> alert + contain, preserve evidence;
- host package/user/sshd/firewall drift -> report, controlled Ansible repair only;
- secret drift -> report only;
- database schema/data -> freeze deploys;
- volumes -> report only, never prune/delete/recreate.

Policy: `ksp-gitops/reconciler/drift-policy.json`.
Evaluator: `ksp-gitops/scripts/evaluate_reconcile_policy.py`.

## Reconciler privilege boundary

Production reconciler is a dedicated host `systemd` service/timer, not an application container with Docker socket access. It is unavailable to the Governor and has no inbound API, Git/cloud/Terraform credentials, build capability or self-update path.

Loop:

fetch signed OCI state -> verify promotion/build signatures -> verify revocation freshness/quarantine -> validate environment/sequence/time/DB/reconciler compatibility -> classify drift -> pull exact digests -> render fixed Compose template -> apply permitted repair -> health check -> persist evidence/counters.

## Resilience qualification

Staging-only mandatory first-production experiments:
- PostgreSQL outage through Toxiproxy wired to the staging application network;
- side-effecting worker termination;
- interrupted Knowledge Pack transfer;
- revoked digest at activation, rollback and reconciliation;
- signed OCI authority/revocation-source outage;
- disk-full on `/srv/ksp-staging-data` **only**, isolated from control-plane storage;
- shared-host reboot mid-deploy, including promotion service/Governor/review-ledger recovery;
- several-hours slow/fast clock skew and NTP-unsynchronized state.

Chaos never runs while `/run/ksp-promotion-active` exists. Toxiproxy is retained; Pumba is not required. Governor retry-storm behavior belongs in integration tests, not infrastructure chaos.

## Argo CD boundary

Argo CD, Argo Rollouts and LitmusChaos remain deferred. Reconsider k3s + Argo CD only when at least two evidence-based scale/operations triggers persist and Kubernetes upgrade/recovery ownership is funded. The release authority, revocation semantics, Knowledge Pack digests and rollback eligibility do not change; only the reconciliation adapter changes.
