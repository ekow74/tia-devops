# Program Design: ksp-platform repository + enforcement controls

All paths below are in the new private repo `ekow74/ksp-platform`. Python is **stdlib only** (3.12) so the gate has no installable dependencies. Tests use `unittest`, matching `tia-devops`.

## Change to Gate 2 (needs re-approval)

Gate 2's Flow step 5 had the gate re-check every open PR on a **10-minute schedule** after the Owner approves. Replaced with:

- **Routine changes:** `KSP Gate` decides on the change alone, giving *passed* or *blocked*. The ruleset's own required-review rules enforce the Owner's approval: code-owner review, approval of the most recent push, stale approvals dismissed. Nothing has to re-run after you click Approve.
- **Safety-rule changes:** the gate must read your approval, because it contains the attestation line. Your approving review starts a re-check through a small `pull_request_review` workflow. That workflow holds no secrets and only dispatches the real gate on `main`.

Why:
- **Cost:** a 10-minute schedule is about 4,300 runs a month, more than the 3,000 Actions minutes GitHub Pro includes for private repos.
- **Guardrail:** it would have added a wait or an extra click after Approve.
- **Mockup:** state 1 ("waiting for Owner") is now GitHub's built-in *Review required* box, shown next to `KSP Gate: passed`.

## Files

**GitHub configuration** (all protected; CODEOWNERS = Owner):
- `.github/CODEOWNERS`: `* @ekow74`. Every file needs the Owner's review.
- `.github/rulesets/main.json`: the one `main` ruleset, imported by the Owner and used as the source of truth for drift checks.
- `.github/workflows/ksp-safety.yml`: `pull_request`, no secrets. Runs unit tests, zizmor, and the Terraform and Compose static checks (ported).
- `.github/workflows/ksp-gate.yml`: `pull_request_target` plus `workflow_dispatch(pr_number)`, environment `gate-authority`. Mints the App token, checks out `main` only, runs `tools/ksp_gate/run.py gate`, and posts `KSP Gate`.
- `.github/workflows/ksp-gate-recheck.yml`: `pull_request_review` (submitted), `actions: write` only, no environment. Dispatches `ksp-gate.yml` on `main` for that PR number. It holds no secret, and the gate it starts runs `main`'s code.
- `.github/workflows/ksp-post-merge-audit.yml`: on push to `main`. Runs `run.py audit` with `GITHUB_TOKEN` (`issues: write`, `checks: read`, `pull-requests: read`).
- `.github/workflows/ksp-ruleset-drift.yml`: weekly plus `workflow_dispatch`, on `main`, environment `gate-authority`. Runs `run.py drift` with the App token (`administration: read`).

**Gate code:**
- `tools/ksp_gate/run.py`: CLI entry point with subcommands `gate`, `audit`, `drift`. Sets `sys.path` from its own location, because it runs under `python3 -I`.
- `tools/ksp_gate/model.py`: dataclasses and enums only.
- `tools/ksp_gate/policy.py`: protected-path matching plus the tamper detectors, ported from `check_gate_integrity.py`.
- `tools/ksp_gate/approval.py`: finds the Owner's latest review and parses the attestation.
- `tools/ksp_gate/gate.py`: the pure `evaluate()`, plus building context from git and the API, plus publishing the check run.
- `tools/ksp_gate/github_api.py`: a minimal urllib REST client with pagination and error mapping.
- `tools/ksp_gate/audit.py`: post-merge audit.
- `tools/ksp_gate/drift.py`: ruleset normalization and diff.

**Policy and governance** (protected):
- `policies/protected-paths.txt`: the ported list, re-rooted for `ksp-platform`, plus `tools/ksp_gate/**`, `.github/rulesets/**`, `qa/enforcement/**`.
- `governance/decisions/D-0026.json`: records the `ksp-platform` approval model (GitHub reviews plus App check; receipts retired; robot account). Numbering continues from `tia-devops`.
- `governance/gate-changes/`: the GateChange format, unchanged. Its `review_reference` is what the attestation must name.

**Drill, evidence and docs:**
- `qa/enforcement/drill.py`: the 8-scenario bypass drill (the Gate 1 metric). It runs from a Claude session **as the robot** using the robot's token. It's not run in Actions.
- `ops/evidence/enforcement/`: one JSON per drill run, committed by PR.
- `tests/gate/test_*.py`: unit tests (the test plan below).
- `tests/gate/fixtures/`: recorded API payloads (reviews, check runs, rulesets).
- `docs/external/github.md`: lists the robot account, Gate App, environment and secret **names**, and the Owner's emergency procedure.
- `docs/plans/ksp-platform-enforcement/`: these plan docs, moved over from `tia-devops`.

## Types & signatures

```python
# model.py
OWNER_LOGIN: Final = "ekow74"            # in a protected file; not an Actions variable
CHECK_NAME: Final = "KSP Gate"
GATE_WORKFLOW_PATHS: Final = (".github/workflows/ksp-gate.yml",)

class GateState(Enum):
    PASSED = "passed"                    # check conclusion: success
    WAITING_FOR_REVIEW = "waiting"       # protected change; needs Owner attestation -> action_required
    BLOCKED = "blocked"                  # rule failure (no GateChange, wrong base, bad record) -> failure
    BLOCKED_TAMPER = "tamper"            # weakens the gate itself -> failure

@dataclass(frozen=True)
class ChangedFile:  status: str; path: str               # status: A/M/D (no rename detection)

@dataclass(frozen=True)
class Review:       login: str; state: str; commit_id: str; body: str; submitted_at: str

@dataclass(frozen=True)
class Attestation:  review_reference: str; head_sha: str  # "KSP-REVIEW: <ref> <40-hex sha>"

@dataclass(frozen=True)
class PRContext:
    number: int; base_ref: str; default_branch: str
    base_sha: str; head_sha: str
    changed: tuple[ChangedFile, ...]
    protected_patterns: tuple[str, ...]          # read from base_sha
    gate_changes: tuple[Mapping[str, Any], ...]  # read from head_sha (data only)
    protected_diff: str                          # unified=0 diff of protected paths
    reviews: tuple[Review, ...]

@dataclass(frozen=True)
class GateResult:
    state: GateState; title: str; summary: str; reasons: tuple[str, ...]

# policy.py
def is_protected(path: str, patterns: Sequence[str]) -> bool: ...
def protected_changes(changed: Sequence[ChangedFile], patterns: Sequence[str]) -> list[ChangedFile]: ...
def tamper_reasons(changed: Sequence[ChangedFile], protected_diff: str) -> list[str]: ...
    # gate workflow deleted/renamed; protected file deleted; skip/xfail/continue-on-error/|| true added;
    # numeric threshold decreased
def validate_gate_changes(records: Sequence[Mapping[str, Any]], protected: Sequence[str]) -> list[str]: ...
    # ported field/format rules; scope must cover every protected path; returns errors

# approval.py
def latest_owner_review(reviews: Sequence[Review], owner: str) -> Review | None: ...
    # latest APPROVED/CHANGES_REQUESTED/DISMISSED by owner; COMMENTED ignored
def parse_attestation(body: str) -> Attestation | None: ...
def attested_for(review: Review | None, head_sha: str, review_reference: str) -> bool: ...

# gate.py
def evaluate(ctx: PRContext, owner: str = OWNER_LOGIN) -> GateResult: ...          # pure, no I/O
def build_context(gh: GitHub, repo: str, pr_number: int, workspace: Path) -> PRContext: ...
def publish(gh: GitHub, repo: str, head_sha: str, result: GateResult) -> None: ...  # create/update CHECK_NAME

# github_api.py
class GitHubError(Exception): status: int; message: str
class GitHub:
    def __init__(self, token: str, api: str = "https://api.github.com") -> None: ...
    def get(self, path: str, **params: str) -> Any: ...
    def paginate(self, path: str, **params: str) -> list[Any]: ...
    def post(self, path: str, body: Mapping[str, Any]) -> Any: ...
    def patch(self, path: str, body: Mapping[str, Any]) -> Any: ...

# audit.py
def audit_merge(gh: GitHub, repo: str, merge_sha: str, owner: str, gate_app_id: int) -> list[str]: ...
def report(gh: GitHub, repo: str, merge_sha: str, findings: Sequence[str]) -> None: ...  # opens issue if any

# drift.py
def normalize_ruleset(raw: Mapping[str, Any]) -> dict[str, Any]: ...   # drop ids/timestamps/links; sort lists
def diff_rulesets(live: Mapping[str, Any], committed: Mapping[str, Any]) -> list[str]: ...

# run.py
def main(argv: Sequence[str] | None = None) -> int: ...   # gate | audit | drift; 0 ok, 1 finding, 2 error

# qa/enforcement/drill.py
@dataclass(frozen=True)
class Outcome: scenario: int; name: str; blocked: bool; evidence: Mapping[str, Any]
@dataclass(frozen=True)
class Scenario: number: int; name: str; run: Callable[["DrillEnv"], Outcome]
@dataclass
class DrillEnv: gh: GitHub; repo: str; workdir: Path; owner_assist: Callable[[str], None]
SCENARIOS: tuple[Scenario, ...]            # exactly the 8 from 01-product.md, plus 2a below
def run_drill(env: DrillEnv) -> list[Outcome]: ...
def write_evidence(outcomes: Sequence[Outcome], out_dir: Path) -> Path: ...
def cleanup(env: DrillEnv) -> None: ...    # close drill PRs, delete drill branches
```

Decision table inside `evaluate()`, first match wins:
1. `base_ref != default_branch` → BLOCKED.
2. No protected changes → PASSED. Owner approval is then enforced by the ruleset.
3. `tamper_reasons` is non-empty and there's no valid GateChange → BLOCKED_TAMPER.
4. `validate_gate_changes` returns errors → BLOCKED.
5. The Owner's latest review isn't APPROVED on `head_sha` with a matching attestation → WAITING_FOR_REVIEW.
6. Otherwise → PASSED. Any tamper reasons are listed as "approved exceptions".

## Call stack

**PR opened or updated:**
```
ksp-gate.yml (pull_request_target, from main)
  → actions/create-github-app-token (pinned SHA) → App installation token
  → checkout main (persist-credentials: false); git fetch refs/pull/N/head as data
  → python3 -I tools/ksp_gate/run.py gate --pr N
      → gate.build_context → git diff/show (base, head) + GitHub.paginate(reviews)
      → gate.evaluate → policy.protected_changes, policy.tamper_reasons,
                        policy.validate_gate_changes, approval.latest_owner_review, approval.attested_for
      → gate.publish → POST/PATCH /repos/{repo}/check-runs  (as the App)
```

**Owner submits a review:**
```
ksp-gate-recheck.yml (pull_request_review, no secrets)
  → POST /repos/{repo}/actions/workflows/ksp-gate.yml/dispatches {ref: main, inputs: {pr_number}}
      → same path as above (workflow_dispatch on main)
```

**Merge to main:**
```
ksp-post-merge-audit.yml (push: main)
  → run.py audit --sha <merge>
      → audit.audit_merge → GET commits/{sha}/pulls, check-runs?app_id=, pulls/{n}/reviews
      → audit.report → POST issues (only on findings)
```

**Weekly drift check:**
```
ksp-ruleset-drift.yml → App token → run.py drift
  → GET /repos/{repo}/rulesets/{id} → drift.normalize_ruleset → drift.diff_rulesets(.github/rulesets/main.json)
  → issue on any difference
```

**Bypass drill** (a Claude session acting as the robot):
```
drill.run_drill → for each Scenario: git push / gh API attempt → record HTTP status + message
  → write_evidence → PR with ops/evidence/enforcement/<date>.json → cleanup
```

## Test plan

**Unit tests, `tests/gate/`**, run offline in `ksp-safety.yml`:

*Gate decisions (`evaluate`):*
- `test_routine_change_passes`: no protected paths gives PASSED with no Owner review present.
- `test_base_not_default_blocked`: a PR into another branch gives BLOCKED.
- `test_protected_without_gatechange_blocked`: changing `policies/protected-paths.txt` with no record gives BLOCKED.
- `test_gatechange_scope_must_cover_all_protected`: an uncovered protected path gives BLOCKED and names the path.
- `test_protected_waits_for_attestation`: a valid GateChange with no Owner review gives WAITING_FOR_REVIEW.
- `test_attestation_passes_when_bound`: Owner APPROVED on the head SHA, with an attestation naming the record's `review_reference` and the head SHA, gives PASSED.
- `test_attestation_wrong_sha_rejected`: an attestation with an older SHA gives WAITING.
- `test_attestation_stale_review_commit_rejected`: a review `commit_id` that isn't the head gives WAITING.
- `test_attestation_from_non_owner_ignored`: the same text from the robot gives WAITING.
- `test_owner_changes_requested_after_approval`: the latest Owner state is CHANGES_REQUESTED, giving WAITING.
- `test_commented_review_does_not_override_approval`: a later COMMENTED review is ignored.

*Tamper detection (`policy`):*
- `test_gate_workflow_deleted_is_tamper`: status D on `ksp-gate.yml` gives BLOCKED_TAMPER.
- `test_gate_workflow_renamed_is_tamper`: D plus an A elsewhere gives BLOCKED_TAMPER.
- `test_skip_added_is_tamper` / `test_threshold_decrease_is_tamper`: ported detectors fire.

*Parsing, publishing, audit and drift:*
- `test_parse_attestation_formats`: accepts the exact line; rejects a short SHA, uppercase hex and extra tokens.
- `test_publish_check_name_and_conclusions`: the fake GitHub receives `KSP Gate` with success, action_required or failure per state.
- `test_audit_clean_merge_no_findings`, `test_audit_missing_app_check`, `test_audit_missing_owner_approval`, `test_audit_check_from_wrong_app`.
- `test_drift_ignores_server_fields`, `test_drift_detects_removed_rule`, `test_drift_detects_bypass_actor_added`.

*Ported:* the YAML trigger and `pull_request_target`-exception tests from `tia-devops/ksp-bootstrap/tests`, re-pointed at the new paths.

**Enforcement drill, `qa/enforcement/drill.py`** (the Gate 1 metric, live against GitHub). Each scenario is "blocked" only if GitHub refuses it:
1. `push_to_main`: `git push origin HEAD:main` is rejected.
2. `merge_while_blocked`: merging a PR whose `KSP Gate` failed returns 405.
   - 2a. `robot_merge_after_owner_approval`: even a fully approved, passed PR can't be merged by the robot (restrict updates).
3. `delete_gate_workflow`: a PR deleting `ksp-gate.yml` gives `KSP Gate` = tamper, and merge returns 405.
4. `rename_gate_workflow`: the same, for a rename.
5. `fake_same_name_check`: a PR adding a job named `KSP Gate` that passes. The Actions-sourced check is ignored and merge returns 405.
6. `force_push_or_delete_main`: both are rejected.
7. `change_rules_without_owner`: a PR editing `policies/protected-paths.txt` gives WAITING, and merge returns 405.
8. `self_or_stale_approval`:
   - the robot approving its own PR is refused (or doesn't count);
   - after the Owner approves (`owner_assist` prompts you once), the robot pushes a new commit; the approval is dismissed and merge returns 405.

The drill needs **one Owner action**: approving one drill PR in scenario 8.

## Least confident decisions

1. **The robot machine account.** You switch claude.ai's GitHub connection to `ekow74-bot`, and Claude sessions lose direct access to your other repos unless the bot is added to them. This is the only way to separate the two identities, but it changes how you work every day.
2. **The attestation line in your review body** replaces the receipt file for safety-rule changes. It's simple, but it relies on you pasting one line correctly. The gate rejects anything malformed, so it fails safe.
3. **Routine changes pass `KSP Gate` without the gate seeing your approval.** Only the ruleset enforces it. If the ruleset is weakened, the gate won't notice. The post-merge audit and the weekly drift check are the backstops.
4. **Waiting state uses `action_required`.** It shows as "Action required" rather than amber, and I haven't confirmed how GitHub's merge box renders it. `failure` is the fallback.
5. **The App key lives as a GitHub environment secret**, not on an external host. It's free and simple, but its safety depends on the `main`-only deployment branch policy, which is a Slice 1 verification item.
6. **The `pull_request_review` re-check workflow** runs PR-head workflow code with `actions: write`. A malicious PR could edit it to dispatch other workflows, but those all run from `main`, so the risk is limited to noise. Worth challenging.
7. **`OWNER_LOGIN` is hard-coded** in a protected file instead of a repo variable. That's safer against the robot, but changing owners then needs a reviewed change.
8. **A stdlib-only REST client** means some boilerplate, but no supply-chain surface in the gate itself.
