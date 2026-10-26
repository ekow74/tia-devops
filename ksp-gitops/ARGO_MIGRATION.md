# Argo CD Migration Boundary

Argo CD is intentionally deferred while the canonical runtime is Docker Compose on two low-cost Ubuntu VMs.

## Why defer

Argo CD is a Kubernetes GitOps controller. Installing Kubernetes solely to obtain Argo CD would add control-plane, networking, RBAC, upgrade, CRD and operational complexity before KSP needs multi-node orchestration.

## Trigger conditions for migration

Evaluate k3s + Argo CD when at least several of these are true:
- multiple production worker nodes;
- independent autoscaling pools;
- GPU/media workers;
- frequent progressive/canary delivery needs;
- multiple tenant/domain deployments;
- multi-region or multi-cluster operation;
- Compose reconciliation becomes a material operational bottleneck;
- Kubernetes-native scheduling materially reduces cost or manual operations.

## Interface preserved across migration

The following remain stable:
- signed ReleaseManifest;
- immutable image digests;
- Pharmacology/Med-Surg/Bridge Knowledge Pack digests;
- qualification bundle digest;
- signed revocation list;
- monotonic release sequence;
- build-signing identity separated from promotion identity;
- Git promotion PR as desired-state change;
- rollback/recall eligibility policy.

Only the reconciliation adapter changes:

Compose phase:
  Git -> KSP reconciler -> Docker Compose

Kubernetes phase:
  Git -> Argo CD -> k3s/Kubernetes

A future Argo Rollouts adapter may add canary/blue-green delivery, but it must not weaken clinical, rights, revocation, signature, or GateChange policy.

## Current upstream baseline observed during design

As of 2026-10-26, Argo CD 3.5.6 is the latest stable release while 3.6 is still in release-candidate status. No Argo version is pinned in the Compose phase; version selection occurs only when the Kubernetes migration gate is actually opened.
