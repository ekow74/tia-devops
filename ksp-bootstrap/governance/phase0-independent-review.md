# Phase-0 independent review gate (draft)

This is a non-production bootstrap in `tia-devops` only. It is not qualified, is
not a Claude integration, and is not required for merge. No real provider
credential, spend, approval receipt, merge, promotion, deployment, or repository
settings/ruleset change is authorized by this draft.

## Trust and current behavior

- The reviewer workflow uses `pull_request_target`, checks out only the exact
  event base SHA, and treats the PR head and API file listing as data. It never
  checks out or executes PR-head content.
- The token is scoped to the one read-only GitHub API step. Workflow and job
  permissions are `contents: read` and `pull-requests: read`; no check, status,
  pull-request, or contents write is requested.
- The API listing is bounded to 30 pages of 100 files. Inconsistent links,
  malformed entries, or a full final page fail closed. Renames classify both
  filenames; deletions classify the deleted filename. The trusted base policy
  supplies protected-path patterns.
- An unprotected PR emits `NOT_REQUIRED: not reviewed`. It is never described
  as an independent approval. A protected PR currently emits `UNAVAILABLE` and
  fails closed because no provider integration is configured.
- The strict verdict schema binds repository, PR number, exact head and base
  SHAs, reviewer identity, verdict, run evidence reference, workflow blob
  version, run ID, and whether a review actually occurred. Stale API metadata
  or a malformed verdict fails closed. PR concurrency cancels stale work.
- The proposed archive cross-check is to compare the archived run record and
  verdict fields against GitHub's run/check metadata for the exact repository,
  PR, head SHA, run ID, and workflow blob version. This cross-check is not yet
  implemented or qualified.
- The `OfflineTestAdapter` is used only by local qualification tests; it cannot
  access the network, shell, credentials, or write APIs. Untrusted file text is
  passed to no model. Provider integration remains unqualified until an owner
  authorizes a provider, its bounded transport, and credential handling.
- The base-trusted Gate Authority reads its authority policy from base and
  checks workflow permissions in the PR-head Git tree. Checks/statuses writes
  outside the exact reviewed authority/reviewer workflow paths fail; any such
  write on a `pull_request` workflow fails even within an allowed path. The
  reviewed paths currently request no writes.

The `artifact_reference` is a link to the immutable workflow run record and its
job summary, not an approval receipt or downloadable provider artifact.

## Qualification findings-to-tests matrix

| Qualification | Current evidence | Status / blocker |
| --- | --- | --- |
| Q0 — settings evidence | Workflow permissions are explicitly read-only; repository default permission, Copilot approval, workflow-run approval, and owner-only merge/bypass settings are not visible here. | **Pending owner evidence.** Require default workflow permissions read-only, Copilot approvals disabled, workflow-run approval enabled, and owner-only merge/bypass. Do not infer settings from fixtures. |
| Q1 — low-risk Copilot PR | Local unit tests exercise classification and verdict handling. The new workflow is not live on the base branch. | **Not run.** Requires owner-approved live qualification after review. |
| Q2 — modified reviewer workflow spoof | Same-name head-controlled reviewer-workflow fixture is rejected by the base-authority permission policy; exact-path exception tests cover the trusted workflow. | **Fixture-only; live spoof not run.** |
| Q3 — head code attempts checks/statuses API forgery | Negative fixtures cover `checks: write` and `statuses: write`; all relevant workflow permissions are read-only. | **No live 403 observed.** A PR-head API-forgery attempt must receive HTTP 403 in an owner-authorized live qualification; fixtures do not prove this. |
| Q4 — credential isolation and injection | Static test confirms the GitHub token is exposed only to the API-read step; the injection fixture is tested against the offline adapter. | **Offline-only.** No provider credentials or model are present, so provider isolation is unqualified. |
| Q5 — old-SHA invalidation and clean runs | Event SHA, re-fetched PR metadata, verdict schema, and run ID are bound; concurrency cancels stale work. | **No live synchronize test or clean qualification PRs run.** Test old-SHA invalidation after a new commit and complete two clean qualification PRs before any promotion decision. |

After any eventual promotion, repeat the live API-forgery/403 and old-SHA
invalidation tests as merge-blocking qualification tests. Neither a fixture nor
this draft claims ruleset enforcement or a live 403.

## Risks, decisions, and integration plan

- R4 remains open: the repository's exact-path/rule exception validator supports
  another explicit `pull_request_target` entry, and this draft adds that exact
  entry. GitHub-side event policy support and owner settings were not verifiable
  from this environment.
- D1 remains open: a fixing author cannot independently verify their own fix.
  Any exception requires an Owner-approved contract amendment; this draft does
  not self-approve or publish an approval receipt.
- Q0 prerequisites are not assumed configured. Owner-only merge and bypass,
  Copilot approvals disabled, workflow-run approval enabled, and repository
  default workflow permissions read-only must be evidenced before live tests.
- Claude approved the design, not this implementation. An out-of-band review
  must receive the candidate commit SHA, complete diff, every changed path,
  exact blob hashes, test results, and failure evidence. It must bind approval
  to the final head SHA and exact GateChange scope through the existing process.
- After that review, the owner may decide whether to authorize a provider
  adapter and credential. Any adapter must have no shell, network, or write
  tools for untrusted input; only bounded provider API transport may use
  network. The trusted classifier remains separate from provider execution.
- Run Q0-Q5 and two clean qualification PRs only after owner authorization. Do
  not make the check required or change settings/rulesets as part of this draft.
  Ekow retains sole merge authority.

No live 403, repository setting, event-policy decision, or ruleset result is
claimed. The latest draft workflow runs were approval-gated with zero jobs, so
they provide no implementation qualification evidence.
