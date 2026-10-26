# KSP Autonomous DevOps Model

## Objective

Operate the Knowledge System Platform with high automation, bounded blast radius, strong evidence, and low recurring cost.

The DevOps system may autonomously inspect, test, lint, scan, classify, propose, patch, open pull requests, rerun failed checks, and promote low-risk changes when deterministic policy allows it. High-impact side effects remain behind explicit policy gates.

## Operating principle

**Autonomy is proportional to reversibility.**

The more difficult an action is to reverse, the stronger the evidence and authorization required before execution.

## Risk tiers

### Tier 0 — Observe

Autonomous without approval:
- inventory repositories and dependencies;
- inspect logs and metrics;
- run linters, unit tests, static analysis, policy checks, secret scans, SBOM generation, vulnerability scans, drift detection, and cost estimation;
- collect deployment evidence and produce remediation plans.

### Tier 1 — Safe repair

Autonomous through a pull request:
- formatting and deterministic lint fixes;
- dependency updates that pass policy;
- workflow version/SHA updates;
- documentation/runbook corrections;
- generated lock-file refreshes;
- non-semantic IaC normalization;
- test and validation repairs whose effect is confined to CI.

Requirements: branch change only, full CI rerun, auditable diff, rollback by revert.

### Tier 2 — Controlled change

Autonomous only when policy and environment gates pass:
- deploy to ephemeral/test environments;
- deploy to staging;
- infrastructure plans with no destructive operations;
- rolling/canary application changes with health checks and automated rollback;
- scaling within budget and resource ceilings.

Requirements: immutable artifact, provenance, policy pass, health criteria, timeout, rollback plan, budget check.

### Tier 3 — High-impact change

Human/policy authority required:
- production infrastructure deletion/replacement;
- database destructive migrations;
- secrets/identity/permission changes;
- public networking changes that expand exposure;
- payment or billing-account changes;
- disabling security controls;
- production rollback when data-loss risk exists;
- changes exceeding budget guardrails.

The autonomous system may diagnose and prepare the change, but must not execute it solely on model judgment.

## Pipeline control loop

```text
change/event
    |
    v
classify risk + affected paths
    |
    +--> Tier 0/1 --> fast deterministic validation --> repair loop --> PR
    |
    +--> Tier 2 --> build once --> scan/attest --> ephemeral/staging --> verify --> promote
    |
    +--> Tier 3 --> prepare plan/evidence --> approval/policy gate --> controlled execution

Every execution --> evidence + audit record + rollback pointer
```

## Core design patterns

1. **Build once, promote many** — never rebuild different binaries/images for staging and production.
2. **Immutable artifacts** — deployments use content-addressed image/artifact digests.
3. **Policy as code** — deterministic rules outrank agents/models for security and high-impact actions.
4. **Least privilege** — workflow-level permissions; cloud access through short-lived OIDC credentials rather than long-lived keys.
5. **Progressive delivery** — ephemeral -> staging -> canary/rolling production.
6. **Automated rollback** — promotion is paired with measurable health criteria and a known previous-good release.
7. **Change isolation** — PRs and short-lived branches; no autonomous direct commits to protected production branches.
8. **Generator + validator separation** — the component proposing a configuration is not the only component validating it.
9. **Evidence before action** — tests, scans, plans, diffs, cost estimates, and health checks are attached to the decision.
10. **Fail closed for destructive ambiguity** — uncertain high-impact actions stop; observational actions continue.

## Cost controls

- Path filters prevent unrelated CI runs.
- Concurrency groups cancel superseded workflow runs.
- Fast deterministic tests run on PRs; deep scans run on schedule and release boundaries.
- Dependency updates are grouped weekly to reduce CI fan-out.
- Ephemeral environments have TTL cleanup.
- Expensive integration/e2e suites run only when relevant components or release candidates change.
- Prefer hosted CI for burst work; avoid always-on self-hosted runners until utilization justifies them.
- Reuse dependency/build caches only when cache trust boundaries are safe.
- Infrastructure changes require estimated monthly-cost deltas once cost tooling is connected.
- Production resource autoscaling must have explicit minimum, maximum, and budget ceilings.

## Agent/automation roles

- **Governor:** classifies risk, checks budgets/evidence, coordinates workflow, and decides whether policy permits autonomous progression.
- **SWE agent:** proposes implementation changes.
- **DevOps agent:** owns CI/CD, packaging, environment promotion, IaC, deployment, rollback, and operational automation.
- **Security agent:** validates permissions, dependencies, supply chain, secrets, vulnerabilities, threat controls, and policy exceptions.
- **QA agent:** independently validates behavior and release evidence.
- **Tool gateway:** only path for real external side effects; enforces deterministic permissions and audit logging.

No agent may bypass the tool gateway or policy engine for production side effects.

## Required production gates

Before a production promotion:

1. source commit identified;
2. required PR checks pass;
3. artifact digest fixed;
4. vulnerability threshold passes or documented exception exists;
5. SBOM/provenance available;
6. deployment policy passes;
7. infrastructure plan contains no unapproved destructive changes;
8. cost delta within guardrail;
9. staging health criteria pass;
10. rollback target is known and validated.

## Failure handling

The default autonomous response to a failure is:

```text
observe -> classify -> reproduce -> smallest safe repair -> rerun affected checks -> rerun acceptance gate -> promote only if green
```

Repeated repair attempts must stop when the system cannot make demonstrably safe progress. It should then preserve all evidence and escalate rather than continuously mutate the environment.
