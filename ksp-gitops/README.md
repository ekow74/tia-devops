# KSP GitOps + Chaos Architecture Candidate

Status: experimental alternative to the current S02 Compose pull-reconciler architecture.
Base: ksp-s02-cicd-security.
Goal: evaluate whether a fuller GitOps/resilience layer improves autonomy and safety without unacceptable cost/complexity.

## Architectural intent

1. Git is the declarative desired-state authority for environment release pointers.
2. CI builds and signs immutable software and knowledge artifacts but cannot directly mutate production.
3. A pull-side reconciler observes approved desired state, verifies signatures/digests/sequence/revocations, compares actual state, and applies only authorized differences.
4. Staging runs resilience qualification before a release can be promoted.
5. Chaos is staging-only by default and cannot target production without a distinct protected authorization path.
6. Argo CD is not required in the Compose phase. The reconciler interface is designed so a later k3s/Kubernetes runtime can replace the Compose reconciler with Argo CD without changing release semantics.

## OpenGitOps mapping

- Declarative: desired-state manifests describe exact release state.
- Versioned/immutable: release manifests and referenced artifacts are digest-addressed; Git retains history.
- Pulled automatically: the environment-side reconciler pulls approved state.
- Continuously reconciled: drift is detected continuously and either repaired or quarantined according to policy.

## Current phase

Compose runtime:

Git desired state -> KSP pull reconciler -> Docker Compose

Future Kubernetes phase:

Git desired state -> Argo CD -> k3s/Kubernetes

The upstream release manifest, Knowledge Pack digests, approval model, recall model, and promotion policy stay unchanged.

## Resilience qualification

Staging qualification adds deterministic chaos scenarios for:
- service/container termination;
- PostgreSQL latency/outage;
- registry/Git/object-store outage;
- interrupted Knowledge Pack fetch;
- invalid/revoked pack digest;
- worker retry storms;
- Governor bounded-recovery behavior.

No chaos tool is mounted into learner/runtime application containers. Container-runtime control is isolated to the staging chaos runner and is disabled by default.
