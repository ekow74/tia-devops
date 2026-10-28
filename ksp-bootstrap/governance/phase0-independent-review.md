# Phase-0 independent review gate (draft)

This is a non-production bootstrap in `tia-devops` only. It is not qualified, is
not a Claude integration, and is not required for merge. No real provider
credential, spend, approval receipt, merge, promotion, deployment, or repository
settings/ruleset change is authorized by this draft.

## Trust and current behavior

- The reviewer workflow uses `pull_request_target` and checks out only
  `github.workflow_sha`, the commit associated with the executed workflow file.
  At runtime `GITHUB_WORKFLOW_SHA` must equal the workflow-provided
  `KSP_TRUSTED_SHA`, and checked-out `HEAD` must equal that same SHA. PR head
  content and API file listings remain data only; PR-head code is never checked
  out or executed.
- The PR event's `base_sha` remains a separate binding. The trusted
  `policy_sha` is `GITHUB_WORKFLOW_SHA`; workflow blob version and protected-path
  policy are read from that commit, never from PR head or an assumed-equal event
  base. GitHub documents `GITHUB_SHA` for `pull_request_target` as the last
  commit on the default branch and `GITHUB_WORKFLOW_SHA` as the workflow-file
  commit. Whether those values and the payload's `pull_request.base.sha` differ
  in a particular run is event-dependent and was not verified live; local
  end-to-end tests deliberately use distinct PR-base and policy SHAs.
  Primary sources: [workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request_target)
  and [workflow variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables).
- Reviewer workflow permissions are `contents: read` and `pull-requests: read`;
  Gate Authority has `contents: read`. Neither gate requests check, status,
  pull-request, or contents writes.
  The read-only `GITHUB_TOKEN` is passed as an environment variable only to the
  API-read step, but `actions/checkout` also receives a token for the trusted
  fetch. `persist-credentials: false` prevents checkout from persisting it in
  Git configuration. Workflow permissions remain read-only.
- The GitHub pull-request-files endpoint permits at most 100 results per page
  and up to 3,000 files. The client constructs each page URL locally and ignores
  `Link` targets, so pagination never follows an arbitrary URL; an empty page
  completes exact multiples of 100 below the API maximum. It rejects duplicate
  filenames, counts over 3,000, incomplete or inconsistent listings, and
  compares the duplicate-free listing count with `changed_files` from PR metadata
  both before and after listing. Stable head/base SHA and file count are
  required. Renames classify both filenames; deletions classify the deleted
  filename. The trusted policy commit supplies protected-path patterns. The
  documented endpoint cap is in the [REST files endpoint reference](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests-files).
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
  safely decodes workflow YAML in the PR-head Git tree using the existing
  hash-pinned PyYAML dependency. It validates workflow-level and every
  job-level permission map, including inherited permissions, flow mappings,
  aliases, merges, and `write-all`. A missing explicit workflow permission
  declaration fails closed because repository defaults are not assumed.
  The strict base policy reserves the reviewer/authority job names; a matching
  job display name (or fallback job ID) is allowed only at an exact reviewed
  path with `pull_request_target` as its sole trigger. Computed job names fail
  closed. Checks/statuses writes are limited to exact reviewed paths and that
  same target-only trigger. Other write scopes fail except for the exact
  pre-existing Scorecard job exception: `.github/workflows/security-deep.yml`,
  job `scorecard`, triggers `schedule` and `workflow_dispatch`, with only
  `id-token` and `security-events` writes. Unknown scopes (including `models`)
  and unexpected files/extensions under `.github/workflows` fail closed.
  `urllib` redirects are refused so an Authorization header cannot be carried
  to another origin. The trusted authority workflow installs the pinned parser
  before invoking its base checker; the main PR CI already has the same
  hash-pinned install command before its umbrella validator.

The `artifact_reference` is a link to the immutable workflow run record and its
job summary, not an approval receipt or downloadable provider artifact.

## Qualification findings-to-tests matrix

| Qualification | Current evidence | Status / blocker |
| --- | --- | --- |
| Q0 — settings evidence | Workflow permissions are explicitly read-only; repository default permission, Copilot approval, workflow-run approval, and owner-only merge/bypass settings are not visible here. | **Pending owner evidence.** Require default workflow permissions read-only, Copilot approvals disabled, workflow-run approval enabled, and owner-only merge/bypass. Do not infer settings from fixtures. |
| Q1 — low-risk Copilot PR | Local unit tests exercise classification and verdict handling. The new workflow is not live on the base branch. | **Not run.** Requires owner-approved live qualification after review. |
| Q2 — workflow/check-name spoof | Read-only fixtures spoof each reserved check name; a Git-tree test also verifies head-controlled policy changes cannot suppress the base policy's reserved-name finding. | **Local fixture/unit evidence only; live spoof not run.** |
| Q3 — checks/statuses forgery | Static workflow permission requests for unreviewed writes are rejected by trusted base code. This is a merge/gate-policy failure, not proof that a live API request received 403. | **No live 403 observed.** A separate head-code attempt inside an existing read-only workflow must receive HTTP 403 in owner-authorized qualification; fixtures do not prove this. |
| Q4 — credential isolation and injection | Local `run_gate` tests use a stub API; the unconfigured provider makes no provider request. Injection fixture remains offline-only. | **Offline-only.** No provider credentials or model are present, so provider isolation is unqualified. Checkout also receives the read-only workflow token; no claim that only the API step can access the token is made. |
| Q5 — old-SHA invalidation and clean runs | End-to-end tests exercise event/environment mismatch, checked-out workflow-source mismatch, changed PR head/base, and changed file counts. Verdicts bind PR `base_sha`, trusted `policy_sha`, workflow blob version, and run ID. | **Local harness only; no live synchronize test or clean qualification PRs.** Test old-SHA invalidation after a new commit and complete two clean qualification PRs before any promotion decision. |

## Claude finding response (local evidence only)

| Finding | Repair and regression evidence | Disposition |
| --- | --- | --- |
| F1 — reserved-name forgery | Base policy reserves the three check names; read-only fixtures cover each and a Git-tree test proves the base policy is used. Computed names fail closed. | Repaired locally; no hosted spoof run. |
| F2 — source/base binding | Checkout, policy, and workflow blob now use `GITHUB_WORKFLOW_SHA`; verdict separately binds event `base_sha` and `policy_sha`. End-to-end test uses distinct values and checkout mismatch. | Repaired locally. GitHub docs define `GITHUB_SHA` as the last default-branch commit for `pull_request_target` and `GITHUB_WORKFLOW_SHA` as the workflow-file commit; equality with payload base SHA is not assumed or claimed. |
| F3 — pagination | Locally constructed pages, endpoint bound of 100/page and 3,000 total, duplicate rejection, pre/post `changed_files` comparison, stable head/base checks, and safe ignoring of Link targets. | Repaired locally; tests cover 99/100/101/200/3,000, over-bound, duplicate, inconsistent counts, numeric-repository Link form, and races. |
| F4 — base policy mutant | Target-only status-write head fixture changes the head policy to allow itself; assertion requires the exact base-policy denial. The same test includes a read-only spoof where the head changes the reserved-name list. | Repaired locally; mutating the checker to load head policy causes these assertions to fail. |
| F5 — unreviewed write scopes | All writes need the narrow gate allowlist or the one exact Scorecard exception; tests reject `actions`, `issues`, `id-token`, and `security-events` writes on unreviewed workflows/events. | Repaired locally; the existing scheduled Scorecard write permissions remain explicitly scoped. |
| F6 — run_gate coverage | Temporary Git repository plus stub API tests exercise event/env mismatch, non-default base, checkout mismatch, changed head/base/count, unprotected result, protected `UNAVAILABLE`, and distinct policy/base SHAs. | Repaired locally; no provider behavior is qualified. |
| F7 — hosted parser install | `.github/workflows/ci.yml` is a `pull_request` workflow and already executes the exact hash-pinned install command from `ksp-bootstrap/requirements-ci.txt` before the umbrella validator. | **Owner-approval blocked.** Current GitHub runs are `action_required` with zero jobs. Do not bypass approval; PEP 668/hosted execution remains unverified. |

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
  The packet must also include the unchanged parser input
  `ksp-bootstrap/requirements-ci.txt` and the main `pull_request` installer
  `.github/workflows/ci.yml`; their presence is not hosted execution evidence.
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
