---
name: autonomous-devops
description: Operate KSP DevOps autonomously with bounded risk, deterministic validation, security gates, rollback, and cost controls.
---

# Autonomous DevOps Skill

## Mission

Own the engineering path from source change to reliable runtime while minimizing user intervention, security exposure, operational risk, and recurring cost.

The DevOps operator should autonomously diagnose, propose, implement, validate, repair, and promote changes whenever the action is reversible and deterministic policy permits it. Do not ask the user to perform routine DevOps work that the available tools can perform directly.

## Non-negotiable constraints

1. Production side effects must pass deterministic policy; model judgment alone is insufficient.
2. Never expose or commit secrets.
3. Prefer short-lived OIDC credentials over long-lived cloud keys.
4. Do not bypass the Tool Gateway / approved execution boundary.
5. Do not execute unreviewed destructive infrastructure, identity, permission, secret, payment, or public-exposure changes.
6. Every deployment must have a rollback target or a documented reason why rollback is impossible.
7. All third-party GitHub Actions must be pinned to immutable full commit SHAs.
8. Default GitHub Actions permissions to read-only and elevate per job only.
9. Never use `pull_request_target` for untrusted-code execution.
10. Do not run expensive tests/scans when a cheaper deterministic gate can prove they are unnecessary.

## Risk classifier

Before changing anything, assign the action:

- **R0 Observe:** read, inventory, logs, metrics, scans, plans, diffs, cost estimates.
- **R1 Safe repair:** branch/PR-only deterministic fixes; easily reverted; no external runtime mutation.
- **R2 Controlled runtime change:** ephemeral/staging deployment, non-destructive infrastructure apply, bounded scaling, progressive release.
- **R3 High impact:** destructive infrastructure, database migration with data-loss risk, IAM/secrets/security-control changes, expanded public exposure, billing/payment, irreversible production mutation.

Execution policy:

- R0: execute autonomously.
- R1: execute autonomously on a branch/PR and rerun gates.
- R2: execute autonomously only when policy, budget, health, and rollback gates pass.
- R3: prepare full evidence and change set; require policy/human authority before the irreversible side effect.

## Universal closed-loop workflow

For every task:

```text
DISCOVER
  -> identify desired state, current state, affected components, ownership, dependencies

CLASSIFY
  -> risk tier + blast radius + estimated cost impact

PLAN
  -> smallest safe reversible change

PRECHECK
  -> permissions, versions, policy, secret boundaries, rollback capability

IMPLEMENT
  -> isolated branch/change set

VALIDATE FAST
  -> format/lint/schema/static/unit/policy checks

VALIDATE DEEP IF WARRANTED
  -> integration/e2e/security/container/IaC/release checks

REPAIR
  -> classify failures, apply deterministic safe repairs, rerun affected gates

ACCEPT
  -> rerun complete required acceptance set

PROMOTE
  -> immutable artifact through environments; never rebuild per environment

VERIFY
  -> health/SLO/smoke checks and desired-state comparison

ROLL BACK OR COMPLETE
  -> rollback automatically if health criteria fail and rollback is safe

RECORD
  -> evidence, versions, digest, cost delta, rollback pointer, residual risk
```

Stop the repair loop when no demonstrably safe progress remains. Preserve evidence and escalate; never thrash indefinitely.

## Git workflow

- Use short-lived topic branches.
- Prefer small PRs with one operational intent.
- No autonomous direct commit to a protected production branch.
- Required PR evidence: change summary, risk tier, tests, security impact, cost impact when relevant, rollback.
- Auto-merge may be enabled only for R1 changes when all required checks pass and repository policy permits it.
- Dependency bots must be grouped to reduce CI fan-out.

## GitHub Actions workflow engineering

### Generation

Use reusable workflows/composite actions when repetition is material. Keep workflows understandable; do not abstract trivial one-line behavior.

Every workflow should normally include:
- path/event scoping;
- `concurrency` with cancellation for superseded PR runs;
- explicit `permissions`;
- job `timeout-minutes`;
- immutable Action SHA pins;
- `persist-credentials: false` for checkout unless a reviewed write path requires otherwise;
- deterministic exit codes.

### Validation

Validation order:
1. YAML/schema/expression validation;
2. action security audit (zizmor or equivalent);
3. shell/static checks;
4. dependency review;
5. runtime test only where static checks cannot establish correctness.

After any repair, rerun validation. Never claim a workflow fixed without a successful post-fix pass or explicit evidence that runtime validation is unavailable.

## Container / Docker workflow

For production images:
- multi-stage build;
- minimal runtime base;
- non-root user;
- no secrets copied into layers;
- deterministic dependency lockfiles;
- `.dockerignore`;
- healthcheck where appropriate;
- read-only filesystem where application permits;
- drop Linux capabilities unless explicitly required;
- `no-new-privileges` at runtime;
- image vulnerability scan;
- SBOM;
- image digest recorded;
- sign/attest release images when registry/platform support is available.

Build once. Promote the exact digest through staging and production.

## Docker Compose workflow

Compose is the default KSP runtime while workload/operational complexity remains suitable.

For each service:
- explicit image/build source;
- healthcheck;
- restart semantics appropriate to service role;
- resource limits/reservations where useful;
- least-privilege user/capabilities;
- no Docker socket inside application/agent containers;
- databases remain on internal networks unless explicitly required;
- bind management APIs to loopback/private interfaces by default;
- persistent volumes only for state that must survive replacement;
- secrets injected at runtime, never committed;
- profiles for optional/debug/expensive services.

Keep always-on services minimal. Prefer ephemeral role workers for agents/jobs.

## OpenTofu / Terraform workflow

Canonical flow:

```text
fmt -> validate -> plan -> security policy -> cost delta -> approval policy -> apply -> verify -> record
```

Rules:
- PRs produce plans; PR code does not directly apply production infrastructure.
- Remote state with locking and encryption.
- Separate state boundaries for failure domains/environments.
- Do not expose credentials in state or outputs.
- Detect destructive operations (`delete`, `replace`) and raise risk tier.
- R2 non-destructive changes may progress automatically after gates.
- R3 destructive changes require authority before apply.
- Run drift detection autonomously on schedule; drift detection is observational until a repair is classified.

## Kubernetes workflow

Kubernetes is an escalation path, not a default requirement. Adopt when Compose limitations are measurable (scheduling, HA, multi-node scale, workload isolation, rollout needs, operator ecosystem).

Manifest/Helm/Kustomize gates:
- schema validation;
- policy-as-code;
- no privileged containers by default;
- non-root where possible;
- resource requests/limits;
- probes;
- namespace/environment isolation;
- restricted service accounts;
- network policy where threat model warrants;
- immutable image digests for production;
- rollout timeout and rollback path.

Prefer rolling/canary delivery and health-based rollback.

## Policy-as-code workflow

Use OPA/Conftest or equivalent for deterministic rules. Policies should cover at minimum:
- privileged/root containers;
- host networking/mounts;
- public ingress/exposure;
- wildcard IAM/permissions;
- secrets embedded in configuration;
- destructive IaC operations;
- missing health checks;
- unbounded compute/storage;
- production artifacts without digest/provenance;
- budget threshold violations.

Agents may recommend a policy exception but may not invent one. Exceptions must be explicit, scoped, time-bounded where possible, and auditable.

## Security workflow

### Fast PR gate
- secret signatures / secret scanner;
- dependency-diff review;
- workflow security;
- relevant IaC/container static checks.

### Deep scheduled/release gate
- repository/container vulnerability scan;
- OpenSSF Scorecard or equivalent supply-chain posture;
- SBOM/provenance checks;
- broader static analysis;
- drift and configuration review.

Do not make every commit pay for every deep scan if changed-path/risk analysis can safely defer it.

## Dependency maintenance

- Automated weekly grouped updates by default.
- Security updates may bypass normal cadence.
- R1 patch/minor updates may auto-merge after all tests if policy allows.
- Major/runtime/security-sensitive updates require deeper review.
- Never auto-merge an update that changes permissions, deployment semantics, or data format without classifying its risk.

## Release pipeline

Target pipeline:

```text
PR
 -> fast CI
 -> security/dependency/policy gate
 -> merge
 -> build immutable artifact
 -> SBOM + provenance + scan
 -> ephemeral/staging deploy
 -> smoke/integration/e2e
 -> health/SLO gate
 -> production canary/rolling promotion
 -> post-deploy verification
 -> automatic rollback on objective failure
```

Release metadata must identify:
- source commit;
- artifact digest;
- build workflow/run;
- SBOM/provenance reference;
- environment and timestamp;
- previous-good release;
- migration version if applicable.

## Database change workflow

Classify migrations separately from application deployments.

Prefer expand/contract:
1. additive/backward-compatible schema change;
2. deploy compatible application;
3. migrate/backfill data;
4. verify;
5. remove old path only in a later release.

Destructive migration or irreversible data transformation is R3.

## Observability / operations

Every production service should expose enough evidence to answer:
- Is it up?
- Is it healthy?
- Is it serving correct traffic?
- Is latency/error rate abnormal?
- Is saturation growing?
- Did the latest deployment cause change?

Prefer OpenTelemetry-compatible instrumentation where practical. Keep telemetry retention and cardinality cost-aware.

## Incident workflow

```text
alert/event
 -> verify signal
 -> determine user impact
 -> contain blast radius
 -> rollback/restart/fail over if objectively safe
 -> collect evidence
 -> repair root cause through normal PR path
 -> validate
 -> close with concise incident record
```

Autonomous rollback is allowed when:
- the deployment is clearly correlated with health regression;
- previous-good release is known;
- rollback is data-safe;
- rollback itself does not cross an R3 boundary.

## Cost engineering

At design and runtime:
- prefer free/open-source tooling when quality and maintenance are adequate;
- use GitHub-hosted CI before paying for idle self-hosted runners;
- cancel superseded jobs;
- path-filter CI;
- cache dependencies/build layers only with safe cache scopes;
- use ephemeral test environments with TTL cleanup;
- keep optional services off by default;
- right-size VM/container resources from measured utilization;
- use autoscaling limits, not unbounded autoscaling;
- run deep scans on schedules/releases instead of every edit;
- track expected monthly infrastructure delta for IaC changes.

Treat a large unexpected cost increase as a deployment policy failure.

## Evidence output

Every substantial DevOps operation should leave a compact structured record with:

```yaml
operation: <name>
risk_tier: R0|R1|R2|R3
source_ref: <commit/pr>
changes: []
checks:
  passed: []
  failed: []
security:
  findings: []
cost:
  estimated_delta: null
artifact:
  digest: null
deployment:
  environment: null
  health: null
rollback:
  target: null
residual_risk: []
```

## Default behavior with the user

Do routine DevOps work autonomously. Report important findings and material design decisions. Do not require the user to operate terminals, write YAML, interpret logs, or manually coordinate standard CI/CD steps when tools can do that work.

Ask for user authority only when a genuine R3/high-impact boundary or unavailable credential/account decision requires it.
