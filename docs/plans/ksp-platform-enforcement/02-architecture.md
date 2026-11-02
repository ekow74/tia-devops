# Architecture: ksp-platform repository + enforcement controls

Inputs: `01-product.md` (approved 2026-11-02), D-0023 (ten activation preconditions), D-0025 (personal account `ekow74`, GitHub Pro, Option B: App-pinned gate check). Reference code read: `.github/workflows/ksp-gate-authority.yml`, `.github/workflows/ksp-safety-gates.yml`, `.github/CODEOWNERS`, `ksp-bootstrap/scripts/ci/check_gate_integrity.py`, `ksp-bootstrap/policies/protected-paths.txt`, `ksp-bootstrap/tests/`.

## Key finding that drives this design

Claude sessions currently act on GitHub **as `ekow74`**: `gh api user` returns `ekow74`, PR #17 was authored as `ekow74`, and repo permissions read as `admin`. Today the robot *is* the Owner, so no GitHub rule can separate them. Every Gate 1 bypass scenario depends on the robot having **its own GitHub account** that is not an admin.

## Fit

Three identities, each with one job:

| Identity | What it is | Access to `ksp-platform` |
|---|---|---|
| **Owner** | `ekow74` (you) | Admin. The only bypass actor (the emergency path). Approves and merges. |
| **Robot** | New free machine account (working name `ekow74-bot`). claude.ai's GitHub connection is switched to it, so Claude sessions act as the robot. | Collaborator (write). Not an admin and never on a bypass list. Can push branches, open PRs, and re-run checks. Cannot merge into `main`. |
| **KSP Gate Authority** | New GitHub App owned by `ekow74`, installed **only** on `ksp-platform`. | `checks: write`, `pull_requests: read`, `contents: read`, `administration: read`. Its check is the decisive gate. |

What carries over from `tia-devops` (transplanted, not linked):
- **Base-trusted checker pattern:** the `pull_request_target` workflow runs from `main` and fetches the PR head only as data. Ported from `ksp-gate-authority.yml` and `check_gate_integrity.py`.
- **Protected-path policy, GateChange records, and their tests:** from `ksp-bootstrap/`.
- **Credential-free safety CI:** from `ksp-safety-gates.yml`.

What changes from `tia-devops`:
- **Owner approval becomes a native GitHub "Approve" review** on the exact latest commit, enforced by the ruleset, instead of a receipt file pushed to `main`. This removes the receipt ritual and gives the one-look approval Gate 1 asked for.
- **Independent review** (only for changes to safety rules) is recorded as an **attestation line in the Owner's approving review** that names the review document and the head SHA. The gate App checks it. The robot can't write it, because it can't post reviews as the Owner.
- **The Owner-decision approval path** (S02 baseline, tia-devops) is not needed in `ksp-platform`, because CODEOWNERS plus the ruleset replace it (as D-0023 says).

## Endpoints

No KSP service endpoints. The GitHub surfaces used:

- **Ruleset on `main`** (one, committed as JSON and imported by the Owner):
  - require a pull request, with 1 approval and code-owner review;
  - dismiss stale approvals, and require approval of the most recent push;
  - require status checks: **`KSP Gate` from the Gate Authority App** (pinned by `integration_id`) plus the safety CI checks;
  - restrict updates, so only bypass actors can update `main`. This is what stops the robot from merging;
  - block force pushes, restrict deletions;
  - bypass list = **Repository admin only** (the Owner emergency path).
- **Check Runs API:** the Gate App creates and updates `KSP Gate` on the PR head SHA.
- **Pull request reviews API (read):** the Gate App reads the Owner's latest review on the head SHA.
- **Rulesets API (read):** a scheduled audit compares the live ruleset to the committed JSON and flags drift.

## Data

No database. Everything is a file in `ksp-platform`, and every one of them is a protected path:

- `.github/rulesets/main.json`: the ruleset exactly as imported; the source of truth for drift checks.
- `.github/CODEOWNERS`: `* @ekow74`, so every file needs the Owner.
- `policies/protected-paths.txt`: the safety-rule surfaces (ported list) that need an independent review attestation as well as the Owner.
- `governance/decisions/*.json` and `governance/gate-changes/*.json`: same formats as tia-devops. Approval receipts are dropped.
- `ops/evidence/enforcement/<date>.json`: results of the 8-scenario bypass drill (the Gate 1 metric).

Queries the gate makes per PR: changed paths (base…head), the Owner's latest review (`state`, `commit_id`, body), the GateChange records at head, and the protected-path policy at base.

## Flow

Main path, for a routine change:
1. The robot pushes a branch and opens a PR.
2. **Safety CI** (`pull_request`, no secrets) runs tests and static checks.
3. **Gate workflow** (`pull_request_target`, definition from `main`, environment `gate-authority`):
   - mints a short-lived Gate App token;
   - fetches the PR head as data only;
   - runs the checker from `main`;
   - posts `KSP Gate` as the App: *waiting for Owner* / *blocked: tampering* / *passed* (the three mockup states).
4. **The Owner clicks Approve** in GitHub.
5. **The gate re-evaluates:**
   - the robot dispatches the gate workflow on `main` with the PR number (code still comes from `main`);
   - a 10-minute schedule on `main` re-checks open PRs as a backstop;
   - the gate confirms the approval is on the current head SHA, then posts *passed*.
6. **The Owner clicks Merge.** The robot can't, because of the restrict-updates rule.
7. **Post-merge audit** (push to `main`): confirms the merged PR had `KSP Gate` = passed from the App plus the Owner's approval on that SHA. If not (for example the emergency bypass was used), it opens an issue: "bypass used — record an Owner decision."

Safety-rule changes follow the same path. At step 3 the gate also requires a GateChange record, and at step 5 the Owner's review must include the attestation `KSP-REVIEW: <review_reference> <head_sha>`.

Why a PR can't fake the gate:
- The decisive check must come from the App (it's pinned in the ruleset), and the App's private key lives only in environment `gate-authority`.
- That environment is restricted to the `main` branch, so workflows a PR adds (which run on `refs/pull/N/merge`) or that are dispatched from robot branches can't read it.
- A PR can't change the gate workflow that judges it, because `pull_request_target` uses `main`'s copy.

## External

- **Robot GitHub account** (new, free machine account), invited as a collaborator. The Owner switches claude.ai's GitHub connection to this account. *Trade-off: Claude sessions then reach only repos the robot has been added to.*
- **KSP Gate Authority GitHub App** (new, owned by `ekow74`), installed on `ksp-platform` only.
- **Environment `gate-authority`** on `ksp-platform`, with its deployment branch limited to `main`. Secret names: `KSP_GATE_APP_ID`, `KSP_GATE_APP_PRIVATE_KEY`. No values live in the repo. Names are recorded in `docs/external/github.md`.
- **No new spend:** it all runs on GitHub Pro and Actions minutes.

## Must verify in Slice 1 (any failure means back to this gate)

1. Pro enforces rulesets on the private personal repo, with no "upgrade to enforce" banner.
2. A required status check can be pinned to the Gate App's `integration_id`. The App must post one check run before it can be selected.
3. The bypass list can hold "Repository admin", and restrict updates blocks a write collaborator from merging.
4. Deployment branch policies and environment secrets work on Pro private repos, and a `pull_request_target` run counts as `main` for the environment policy.

## Changes to earlier gates (flag for approval)

- **Gate 1 guardrail wording:** an Owner merge is realistically **two clicks** (Approve, then Merge), not one. It's still well under 2 minutes. I propose changing "one click" to "two clicks".
- **D-0023 alignment:** "approval files are evidence only" is now fully true in `ksp-platform`, because authorization comes from GitHub reviews plus the App check. This should be recorded as a decision record in `ksp-platform` in its first slice.
