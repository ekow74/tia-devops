# Argo CD Migration Boundary

Argo CD is intentionally deferred while the canonical runtime is Docker Compose on two low-cost Ubuntu VMs.

## Why defer

Argo CD is a Kubernetes GitOps controller. Installing Kubernetes solely to obtain Argo CD would add control-plane, networking, RBAC, upgrade, CRD and operational complexity before KSP needs multi-node orchestration.

## Trigger conditions for migration

Open a k3s + Argo CD architecture gate only when **at least two** of the following persist for more than one quarter:
- production needs three or more nodes for capacity or availability;
- zero-downtime deployment SLOs cannot be met with the Compose reconciler;
- independent worker pools (for example GPU/media) require separate scaling;
- three or more separate tenant/institution installations exist beyond staging and production;
- reconciler defects or operational toil cause repeated incidents.

Precondition: an explicit owner/budget exists for Kubernetes upgrades and recovery (managed Kubernetes or a second qualified operator).

## Release authority is unchanged

Argo CD never becomes KSP's clinical/release authority. The stable authority remains:
- promotion-signed desired-state/channel pointer;
- independently build-signed ReleaseManifest;
- immutable image and Knowledge Pack digests;
- qualification attestations;
- signed monotonic revocation list;
- rollback/recall eligibility policy;
- build identity separated from promotion/recall identities.

Git may transport Kubernetes manifests, but Git commit validity alone is insufficient. A future Kubernetes adapter must enforce the same signed-artifact/revocation policy through admission controls with pinned keys. Argo self-heal must obey the same freeze, revocation, expiry, sequence, flap and drift-class refusal rules used by the Compose reconciler.

## Adapter-only migration

Compose phase:
  signed OCI desired state -> KSP host reconciler -> Docker Compose

Kubernetes phase:
  signed release authority -> admission/signature/revocation policy -> Argo CD sync adapter -> k3s/Kubernetes

The upstream release model and Knowledge Pack safety semantics do not change.

Argo Rollouts is not justified now. Reconsider it only if runtime latency/error-rate regressions become a recurring deployment risk; it does not substitute for clinical K-gates or RN review.

LitmusChaos remains deferred to the Kubernetes phase. Current Compose staging uses Toxiproxy plus guarded host-native Docker/tc/fallocate/reboot procedures.

## Current upstream baseline observed during design

As of 2026-10-26, Argo CD 3.5.6 was the latest stable release observed during the design review. No Argo version is pinned in the Compose phase; version selection occurs only when the Kubernetes migration gate is opened.
