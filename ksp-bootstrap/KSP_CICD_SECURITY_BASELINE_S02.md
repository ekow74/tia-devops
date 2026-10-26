# KSP CI/CD Security Baseline — S02

Status: implementation bootstrap; production Terraform apply remains disabled.

This baseline implements the production-pipeline corrections from Claude review v1.0. It is intentionally staged in `tia-devops` only as a reusable template because the dedicated private `ksp-platform` repository does not yet exist. Nothing in this directory is a production deployment authority.

## A1 — Gate integrity and Governor separation

Protected paths are listed in `policies/protected-paths.txt`. In the production repository:

- the Governor identity MUST NOT approve, merge, or bypass rules for protected paths;
- protected-path PRs require a `GateChange` record tied to a Decision Log ID;
- `GateChange` review must be performed by an independent reviewer class (`CLAUDE`, `RN`, or `OWNER` as applicable);
- clinical fixture expected-answer changes require RN review;
- lowering thresholds, deleting protected tests/rules, adding skip/xfail/fail-open constructs, or changing protected fixtures without an approved GateChange fails CI.

The repo ruleset is an external control and cannot be considered complete until it is applied to the private production repo and tested with the Governor identity.

## A2 — Terraform PR credential boundary

Untrusted PR CI is credential-free. It may perform formatting, parsing, validation that does not require provider credentials, and static policy checks only.

KSP does **not** run a Contabo-credentialed speculative plan on pull-request code. A credentialed HCP Terraform plan is created only from the accepted `main` commit. Merge is never equivalent to apply.

Static PR policy rejects:

- Terraform `external` and `http` data sources;
- `local-exec` and `remote-exec` provisioners;
- unpinned Git/GitHub module sources;
- provider sources outside the explicit allowlist.

Production resources must use `lifecycle { prevent_destroy = true }` where accidental deletion would cause outage/data loss. Contabo instance attributes documented to trigger reinstall (including `user_data`, `ssh_keys`, and `default_user` in the current provider docs) are classified destructive. Production instances use `ignore_changes = [user_data]`; host configuration after bootstrap belongs to Ansible.

No production apply occurs from `pull_request`.

## A3 — Container/network exposure

Compose files fail CI when a published port omits an explicit loopback/private host binding. Internet-facing ingress is an explicit named exception handled by the ingress service policy, not by arbitrary service `ports:` entries.

Ansible production hardening must manage the `DOCKER-USER` chain so Docker-published ports cannot bypass host firewall intent. Provider firewall rules form an additional boundary. Host qualification performs an external scan and compares observed ports to the release's expected-open set.

## A4 — Backup isolation and DR objectives

Primary durable backups are off-provider (Backblaze B2 or equivalent), not in the same Contabo failure domain.

Production credentials are append-only/write-only where the backend supports it. Production cannot prune or delete retained backup history. Retention is enforced server-side or by a separate trusted control-plane identity.

Initial targets:

- review/control PostgreSQL RPO: <= 6 hours;
- production metadata RPO: <= 6 hours;
- service RTO: <= 4 hours;
- restore qualification: scheduled restore into a disposable environment, with measured RPO/RTO recorded.

A backup is not qualified until a restore drill succeeds.

## A5 — Build versus promotion identity

KSP uses two independent signing authorities:

1. **Build identity** signs container/pack/release-manifest digests.
2. **Promotion identity** signs the staging/production channel pointer only after qualification.

Production requires both. A build identity alone cannot promote itself.

For a private low-cost repository, the initial signing mechanism is Cosign with project-controlled keys and no public Rekor upload. The build signing key is isolated to the trusted build workflow; the promotion key is held by the trusted control/deployment plane and is not available to ordinary CI.

The deployment agent verifies at minimum:

- artifact/release digest;
- allowed repository and workflow identity recorded in provenance;
- build signature;
- separate promotion signature;
- monotonic release sequence, except authenticated explicit rollback;
- channel expiry;
- revocation/recall status;
- schema compatibility;
- pull-only registry scope.

Refusals are recorded in the release evidence bundle.

## A6 — Repository visibility and zero-cost control set

Production `ksp-platform` is intended to be **private** because it will contain commercial implementation and knowledge-system metadata.

GitHub's current documentation states that private-repository artifact attestations require GitHub Enterprise Cloud, and private CodeQL/dependency-review/secret-protection features require paid GitHub security products/plans. KSP therefore does not depend on those paid controls for the initial production baseline.

Zero-cost/private-repo baseline:

- Gitleaks (secret scanning);
- Trivy and/or OSV-Scanner (dependency/container/IaC vulnerabilities);
- Semgrep CE (SAST where useful);
- zizmor (GitHub Actions security);
- CycloneDX/SPDX SBOM generation;
- Cosign project-key signatures;
- SHA-pinned GitHub Actions;
- dependency lockfiles and provider lockfiles.

Paid GitHub security products may be added later but are not a production prerequisite.

## Apply freeze

Terraform apply is prohibited until A2 and A3 are implemented in the target production repository and their named tests pass. Production promotion is prohibited until A1, A4, and A5 are also qualified.
