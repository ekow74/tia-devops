# Phase-0 Independent Review Qualification

This harness qualifies the `KSP Independent Review` workflow without a paid
provider call. Local tests use an offline adapter and mock GitHub APIs; they do
not modify GitHub checks or commit statuses. The check is not required by a
ruleset in this change.

## Q0 — Owner settings snapshot

Before qualification, record a timestamped screenshot or export showing:

- Default-branch `GITHUB_TOKEN` workflow permissions are **read-only**.
- **Require approval for workflow runs** is ON.
- Copilot pull-request approvals are OFF.
- `KSP Independent Review` is not yet a required check.
- No Phase-0 ruleset or provider credential is added by this PR.

If any expected value is not set, record the gap and stop; do not infer the
Owner-side configuration from workflow YAML.

## Offline test command

Run the deterministic qualification suite from the repository root:

```sh
python3 -m unittest ksp-bootstrap.tests.test_gc0007_independent_review -v
```

The fixture under `tests/fixtures/review_gate/readonly_token_forge.py` routes
passing-check and passing-status attempts through an offline read-only API
double. Both return 403. No network request is made by this fixture. A real
403 from the repository's read-only `GITHUB_TOKEN` remains an Owner-observed
qualification item; never run the fixture against an arbitrary repository.

## Q0–Q5 sequence

| Qualification | Procedure and expected evidence |
| --- | --- |
| Q0 | Capture the settings snapshot above. Stop if default token permissions are writable or workflow-run approval is off. |
| Q1 | Open a clean PR changing only an unprotected path. The required workflow job succeeds, emits `NOT_REQUIRED`, and its summary includes exactly `not reviewed: unprotected`. It must identify the trusted classifier, not Claude. |
| Q2 | Run the workflow-spoof permission tests and the non-workflow Checks/Statuses fixture. The structural checker rejects an unapproved workflow's `checks: write` or `statuses: write`; the offline API double returns 403 for both attempted writes. A successful head-executing job must not create authoritative review evidence. Record any separately observed GitHub 403 responses with the PR and run IDs. |
| Q3 | Run the prompt-injection test. The malicious title/body/diff remain data in the single user payload, the provider request exposes no tools, and no secret appears in the prompt or output. Keep the live provider credential absent until the Owner authorizes provider qualification and spend. |
| Q4 | Qualify a protected test PR at SHA-A with a passing offline verdict bound to SHA-A. Advance the PR to SHA-B. SHA-A evidence must not validate for SHA-B; the old run is cancelled/stale and the current-head check fails closed until SHA-B has new evidence. Record both SHAs and run IDs. |
| Q5 | Open a second clean qualification PR and repeat Q1/Q2/Q4 checks to rule out one-off behavior. Record its PR number, exact head SHA, and run URL. |

Run the whole deterministic suite whenever re-qualifying:

```sh
python3 -m unittest discover -s ksp-bootstrap/tests -p 'test_*.py' -v
```

## After Owner promotes the required check

Do not promote the check as part of this PR. After a separate Owner-approved
ruleset change, rerun Q2 and Q4 using clean qualification PRs. Record the
ruleset snapshot, PR numbers, head SHAs, run IDs/URLs, the two 403 results,
and the stale-SHA-B result. Confirm in GitHub that the required-check rule
blocks merge while the current head has no passing authoritative review.
Do not use a same-named status context or a PR-head workflow result as
substitute evidence.

## Review-ready handoff

The PR description must include `GateChange: GC-0007`, its base SHA and frozen
head SHA, exact changed-file list, CI run IDs/URLs, a PASS/FAIL test matrix,
and the explicit Owner-side pending items. It must state that this PR is **not
approved for merge** until Claude reviews the exact frozen head. Live provider
qualification and Owner settings remain pending; this document does not
authorize spend, promote a required check, or grant merge authority.
