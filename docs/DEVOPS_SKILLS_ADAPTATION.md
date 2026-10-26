# DevOps Skills Adaptation Register

This register tracks external/current DevOps skill patterns considered for KSP. KSP adapts patterns; it does not blindly vendor external agent prompts or workflows.

## Selection criteria

A skill/pattern is favored when it improves one or more of:

- autonomous diagnosis and repair;
- deterministic validation;
- least privilege and supply-chain safety;
- reproducibility and rollback;
- deployment reliability;
- infrastructure portability;
- operating cost;
- auditability.

It is rejected or downgraded when it assumes broad credentials, direct production mutation, unbounded loops, unpinned dependencies, always-on infrastructure, or model judgment as the final authority for destructive actions.

## Adapted patterns

### `akin-ozer/cc-devops-skills`

Useful pattern: paired **generator / validator** skills for GitHub Actions, Dockerfiles, Ansible, Terraform/Kubernetes-adjacent configuration, logging, and other DevOps assets.

KSP adaptation:
- keep generation and validation logically separate;
- validator must rerun after repair;
- classify unknown errors explicitly instead of fabricating a fix;
- verify action/tool versions online before claiming currency;
- use actionlint/zizmor/static validation before runtime tests;
- require deterministic post-fix acceptance.

KSP difference:
- generated infrastructure does not automatically become deployable;
- policy, security, cost, and rollback gates are added before side effects.

### Platform engineering / SRE skill collections

Useful pattern: separate capabilities for infrastructure planning, Kubernetes operations, GitOps/release engineering, observability, incident response, and cost optimization.

KSP adaptation:
- split DevOps responsibilities into narrow execution skills owned by one DevOps agent role;
- Governor coordinates but does not need to contain every implementation detail;
- skills emit structured evidence that Security/QA can independently inspect.

### GitHub Actions security skills / zizmor

Useful pattern: treat workflow files as executable supply-chain code.

KSP adaptation:
- pin third-party Actions to full commit SHAs;
- default workflow permissions to read-only;
- prohibit `permissions: write-all`;
- prohibit `pull_request_target` by default;
- disable checkout credential persistence;
- run workflow security analysis in PR CI;
- isolate write-capable jobs from untrusted pull-request code.

### OpenSSF Scorecard

Useful pattern: continuously evaluate repository supply-chain posture.

KSP adaptation:
- run as a scheduled/release-boundary deep scan rather than on every edit;
- publish SARIF/security evidence where supported;
- use results as governance signals, not as a single binary security verdict.

### Trivy

Useful pattern: one scanner for repository dependencies, filesystem vulnerabilities, secrets, and infrastructure/configuration misconfiguration.

KSP adaptation:
- high/critical findings block deep-security gates unless explicitly excepted;
- keep fast PR checks small; perform broader scanning on schedule and release boundaries;
- scan built container images again before production promotion.

### OPA / Conftest policy-as-code

Useful pattern: deterministic authorization for infrastructure and deployment rules.

KSP adaptation:
- policy engine has final authority for high-impact side effects;
- agent risk recommendations are advisory inputs, not policy decisions;
- policies cover destructive IaC changes, public exposure, privileged containers, unbounded resources, missing health checks, secret handling, and budget thresholds.

This will be activated in the canonical KSP repository when Terraform/Kubernetes/Compose deployment objects are present.

### OpenTofu / Terraform planning

Useful pattern: `fmt -> validate -> plan -> policy/cost review -> apply` with remote state locking and environment separation.

KSP adaptation:
- pull requests produce plans only;
- apply uses reviewed immutable plan/equivalent reviewed intent;
- destructive operations require stronger authorization;
- drift detection is observational and autonomous;
- production apply uses short-lived OIDC credentials.

### Renovate / Dependabot

Useful pattern: automated dependency currency.

KSP adaptation:
- start with GitHub-native Dependabot for zero extra service cost;
- group GitHub Actions updates weekly to reduce CI fan-out;
- low-risk updates may later auto-merge after all deterministic checks pass;
- major/security-sensitive updates remain reviewed.

### Artifact attestations / SLSA-style provenance

Useful pattern: bind promoted artifacts to the workflow and source commit that produced them.

KSP adaptation:
- build once and promote by immutable digest;
- attach SBOM and provenance at release boundaries;
- production deployment consumes the same artifact validated in staging.

## Skill execution contract

Every autonomous DevOps skill should follow this generic contract:

```text
INPUT
  scope + desired state + current evidence + budget/risk constraints

PRECHECK
  verify target, credentials/permissions, tool versions, and rollback ability

PLAN
  produce smallest safe change and classify its risk

VALIDATE
  static checks + policy + security + cost + tests

EXECUTE
  only if risk tier authorizes execution

VERIFY
  health/acceptance checks and expected-state comparison

REPAIR
  deterministic bounded loop; never infinite retries

OUTPUT
  structured evidence, changed resources, cost delta, rollback pointer, residual risk
```

## Current implementation status

Implemented in the DevOps umbrella:
- path-filtered PR/push CI;
- concurrency cancellation;
- immutable Action SHA policy;
- least-privilege permissions;
- workflow-security scanning;
- dependency review;
- weekly grouped dependency updates;
- scheduled Trivy and OpenSSF Scorecard deep scans;
- bounded risk-tier autonomy model;
- repository lifecycle catalog and promotion policy.

Deferred until the canonical KSP application/infrastructure repository exists:
- container build/sign/attest pipeline;
- OpenTofu/Terraform plan/apply;
- Conftest/OPA deployment policies;
- ephemeral preview environments;
- staging promotion;
- canary/rolling production promotion;
- automated health rollback;
- cloud OIDC federation;
- infrastructure cost-delta gates.
