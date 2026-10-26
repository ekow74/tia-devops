# KSP DevOps Umbrella

This repository is the control/index umbrella for legacy DevOps, cloud, CI/CD, infrastructure-as-code, reliability, and training repositories owned by `ekow74`.

It is **not** the Knowledge System Platform (KSP) production application repository and it must not become a monorepo containing copies of the legacy repositories.

## Purpose

The umbrella exists to:

1. inventory older repositories without destroying their history;
2. separate reusable engineering patterns from training/reference material;
3. prevent legacy/course code from becoming an accidental KSP production dependency;
4. identify material that may be extracted into clean, reviewed KSP DevOps modules;
5. provide one governance point for CI/CD, infrastructure, security, observability, release engineering, and operational references.

## Canonical KSP development boundary

KSP production development should live in a separate private canonical repository (recommended name: `ksp-platform`). The production repository should own its own application source, tests, CI workflows, infrastructure modules, deployment manifests, security policy, release metadata, and documentation.

Legacy repositories listed in `catalog/repositories.yaml` are inputs for study and selective extraction only. They are not direct dependencies.

## Lifecycle classes

### `candidate-extract`
Contains potentially reusable infrastructure, automation, CI/CD, shell, security, or deployment patterns. Before any reuse, extract only the needed pattern into new KSP-owned code, then run license/provenance review, secret scanning, dependency review, security review, and tests.

### `reference`
Useful for learning, comparison, implementation ideas, or historical context. Do not import wholesale into KSP.

### `archive`
Certification material, PDFs, upstream/community snapshots, unrelated training projects, or low-value legacy material. Preserve history but keep outside the KSP build and dependency graph.

## KSP DevOps target structure

The future canonical KSP repository should keep DevOps concerns explicit and independent from domain content:

```text
ksp-platform/
├── .github/
│   ├── workflows/
│   ├── CODEOWNERS
│   └── dependabot.yml
├── apps/
├── services/
├── packages/
├── platform/
│   ├── compose/
│   ├── containers/
│   ├── kubernetes/
│   ├── terraform/
│   ├── ansible/
│   └── environments/
├── ops/
│   ├── observability/
│   ├── runbooks/
│   ├── backup-restore/
│   └── incident-response/
├── security/
│   ├── policies/
│   ├── threat-models/
│   ├── sbom/
│   └── supply-chain/
├── qa/
├── scripts/
├── docs/
└── archive/
```

## Rules for legacy repositories

- Preserve original Git history.
- Do not delete or flatten legacy repositories merely to make the account look cleaner.
- Do not make KSP production builds depend on these repositories by Git URL, submodule, copied binary, or mutable branch.
- Do not copy credentials, environment files, state files, generated artifacts, private keys, or machine-specific configuration.
- Treat third-party/course repositories as reference material unless provenance and license permit reuse.
- Prefer clean-room extraction: reproduce the useful pattern in a new KSP module with current versions, tests, documentation, and security controls.
- Record every promoted pattern in the KSP repository with source provenance and an adoption decision.

## Promotion gate

A legacy asset may move from `reference` or `candidate-extract` into KSP-owned production code only after:

1. provenance/license review;
2. secret and sensitive-data scan;
3. dependency and version review;
4. security review;
5. conversion to KSP naming/layout standards;
6. automated tests and linting;
7. reproducible build/deploy validation;
8. documentation and rollback procedure;
9. pull-request review.

## Current audit summary

The current GitHub estate contains 30 repositories. Most are DevOps/cloud training, Kubernetes certification/reference material, Docker/Terraform/Ansible labs, Jenkins examples, PDFs, or course repositories. The strongest candidates for pattern extraction are the Ansible repository, the freelance/job-support infrastructure examples, shell/CI material, selected Terraform examples, and selected Jenkins/container patterns. Broad training repositories and certification/PDF repositories remain reference or archive material.

The catalog in `catalog/repositories.yaml` is the machine-readable source of truth for this umbrella.
