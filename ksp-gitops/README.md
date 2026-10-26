# KSP Hybrid GitOps + Resilience Candidate

Status: S02 response candidate after Claude architecture review v1.0.
Base: ksp-s02-cicd-security.
Canonical direction: S02 Terraform + Ansible + two-VM Docker Compose, with a small timer-driven reconciler and staging resilience qualification. No Kubernetes/Argo CD yet.

## Single release authority (F1)

Production does **not** trust Git as desired-state authority.

1. The promotion service canonicalizes the desired-state JSON and signs it with the dedicated promotion key.
2. The signed desired-state document is published as an OCI artifact/channel pointer.
3. The production reconciler fetches that OCI document, verifies its promotion signature against the pinned promotion public key, and independently verifies the referenced ReleaseManifest under the pinned build public key.
4. Git may receive a copy for audit/diff/history only. The reconciler never reads Git and production holds no Git credential.
5. A build signature alone cannot authorize production.
6. The Governor may request promotion but cannot read the promotion key or write an authoritative desired-state document.

Runtime authority flow:

promotion service -> promotion-signed desired-state OCI artifact -> host reconciler -> Docker Compose

Audit-only mirror:

promotion service -> Git mirror (optional; never read by production)

## Anti-reconciliation controls (F2)

Every reconcile cycle performs these refusal checks before any repair:

1. Verify promotion signature and ReleaseManifest build signature.
2. Verify current signed revocation list and require its monotonic sequence to meet `revocation_min_sequence`.
3. Revocation outranks desired state. Revoked desired/running digests are quarantined, never re-applied.
4. A valid local signed freeze marker stops all repair actions even when the desired state remains valid.
5. Channel `sequence` only increases. Moving the target release backwards requires a separately verified rollback record that passed rollback-eligibility policy.
6. Expired desired state is not applied. The current verified release remains active unless it is revoked.
7. Registry/signature/revocation-source outage is fail-static: keep current verified state and alert; never use unverified/tag fallback.
8. Three consecutive failed applies of one state reaches the flap limit: stop applying and escalate.
9. Environment, database-schema compatibility, minimum reconciler version and expected-open-port policy are bound into the signed document.
10. The desired-state schema has `additionalProperties: false`; it cannot carry hooks, commands, scripts or other executable content.

## Drift classes

Only known-safe runtime drift is auto-repaired:

- stopped/crashed known container: restart with backoff;
- running image digest mismatch: repair to the promotion-signed digest after full verification;
- Compose/non-secret configuration mismatch: render from fixed template and repair.

Drift that is **not** automatically repaired:

- revoked content: quarantine;
- unknown container or unexpected open port: alert/contain, preserve evidence, do not delete;
- host package/user/sshd/firewall drift: report; controlled Ansible handles host repair;
- secrets: report only;
- database schema/data: never auto-repair; freeze further deploys;
- volumes: never prune/delete/recreate automatically.

The executable mapping is `ksp-gitops/reconciler/drift-policy.json` and the side-effect-free evaluator is `ksp-gitops/scripts/evaluate_reconcile_policy.py`.

## Reconciler privilege boundary (F3)

The production reconciler is a small host `systemd` service/timer, not an application container with the Docker socket mounted. It runs under a dedicated account unavailable to the Governor.

Smallest safe loop:

fetch signed OCI document -> verify promotion/build signatures -> validate schema/environment/sequence/expiry/revocation/DB/reconciler compatibility -> evaluate drift class -> pull exact digests -> render fixed Compose template -> apply only permitted repairs -> health check -> write evidence.

It never:

- builds artifacts or pulls by tag;
- runs contract-phase database migrations;
- executes document-supplied commands/hooks;
- runs `docker compose down -v` or prunes volumes;
- edits host firewall/users/sshd/packages;
- holds Terraform/cloud/Git-write credentials;
- exposes an inbound control API;
- self-updates outside a signed software release.

## Resilience qualification (F4)

Staging-only qualification includes:

- PostgreSQL outage/timeout;
- side-effecting worker termination;
- interrupted Knowledge Pack transfer;
- revoked digest at activation, rollback and reconciliation;
- signed OCI authority/revocation-source outage;
- disk-full condition;
- host reboot mid-deploy;
- clock skew / NTP failure.

Governor retry-storm testing is moved to Governor integration tests because it exercises budget/permission policy rather than infrastructure chaos.

Toxiproxy remains acceptable for staging network/dependency faults. Pumba is not required; guarded `docker`/`tc`/`fallocate` host commands give us the needed tests with less privileged third-party surface.

## Argo CD boundary

Argo CD, Argo Rollouts and LitmusChaos are deferred. A move to k3s/Kubernetes requires evidence that at least two scale/operational triggers persist (multi-node capacity/availability, independent worker pools, multiple installations, delivery SLO pressure, or repeated Compose-reconciler toil) and that cluster upgrades have an explicit operator/budget owner.

A future Kubernetes migration replaces only the reconciliation adapter. The promotion-signed ReleaseManifest/desired-state authority, revocation semantics, Knowledge Pack digests and rollback eligibility remain unchanged.
