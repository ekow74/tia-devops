# Status: ksp-platform repository + enforcement controls

- Gate 1 — Product: APPROVED 2026-11-02
- Gate 2 — Architecture: APPROVED 2026-11-02 (re-approved with Gate 3 amendment 2026-11-02)
- Gate 3 — Program Design: APPROVED 2026-11-02
- Gate 4 — Slice plan: APPROVED 2026-11-02; revised 2026-11-03 (reorder) — re-approval pending

## Slices
- [x] Slice 1 — tracer bullet: App-posted `KSP Gate` required by ruleset — DONE 2026-11-02
- [ ] Slice 2 — robot identity: `ekow74-bot`, ruleset v2 (reviews, restrict updates, admin bypass), CODEOWNERS, D-0026; two-click merge timed
- [ ] Slice 3 — real gate incl. safety-rule path: policy + evaluate + GateChange + `KSP-REVIEW:` attestation + re-check workflow + safety CI
- [ ] Slice 4 — tamper detection
- [ ] Slice 5 — post-merge audit
- [ ] Slice 6 — ruleset drift check
- [ ] Slice 7 — bypass drill (target 0/8), docs/external, client-id switch, move plan docs to ksp-platform

## Notes for a fresh session

- Workflow: software-factory skill (4 gates, Owner approval at each). Owner = ekow74.
- Plan docs live in `tia-devops/docs/plans/ksp-platform-enforcement/` until `ksp-platform` exists; move them there once it does.
- Binding decisions already merged on `tia-devops` main:
  - `ksp-bootstrap/governance/decisions/D-0023.json` — the ten activation preconditions for `ksp-platform`; production stays blocked until all are implemented and evidenced.
  - `ksp-bootstrap/governance/decisions/D-0025.json` — `ksp-platform` is a private repo on the **ekow74 personal account** under **GitHub Pro**; the merge-authoritative gate is a **dedicated GitHub App** whose check is required with its source pinned to that App (Option B). No new spend. Must verify at Gate 2 that Pro supports ruleset enforcement on a private personal repo and App-pinned required checks.
  - `ksp-bootstrap/KSP_CICD_SECURITY_BASELINE_S02.md` — S02 baseline, including the Owner-decision approval path (new decision records may be Owner-approved; everything else protected needs independent Claude/RN review).
- Reference implementation to reuse at Gate 2: `ksp-bootstrap/` (gate checker, GateChange/approval ledger, tests) and `.github/workflows/ksp-gate-authority.yml`.
- Owner feedback (2026-11-02): the approval process felt like too much work. Keep Owner effort per routine change minimal — this is a product requirement, not a nice-to-have.
- Known gap: GC-0006 cites D-0024, which is not in `decisions/`.
- Gate 2 finding (2026-11-02): Claude sessions act on GitHub as `ekow74` (admin). Architecture therefore introduces a separate robot machine account; see `02-architecture.md`.
- Slice 1 (2026-11-02): `actions/create-github-app-token` pinned to v3.3.0 (`64ab729a6294000f5aa4a4bc0b73d1f32bc45510`), which authenticates by Client ID. Environment secret names are therefore `KSP_GATE_APP_CLIENT_ID` and `KSP_GATE_APP_PRIVATE_KEY` (Gate 3 said `KSP_GATE_APP_ID`). The App ID is still needed as the ruleset's `integration_id`.
- Slice 1 results (2026-11-02):
  - `ekow74/ksp-platform` created (private). Gate App `ksp-gate-authority-ekow74`, App ID 5587095, installed on ksp-platform only.
  - Environment `gate-authority`: deployment branch `main` only. Secrets `KSP_GATE_APP_ID` and `KSP_GATE_APP_PRIVATE_KEY`; the stub uses the deprecated `app-id` input. A `KSP_GATE_APP_CLIENT_ID` secret may also exist and is unused. TODO: switch to `client-id`.
  - Ruleset `main-protection` (id 26409443) is active and requires `KSP Gate` with integration_id 5587095. JSON is at `ksp-platform/.github/rulesets/main.json` (PR ekow74/ksp-platform#1).
  - Verified: verification items 1 (enforced on Pro private personal repo), 2 (App-pinned check), and 4 (environment works under `pull_request_target`). Item 3 waits for Slice 3.
  - Drill scenario 1 preview: direct push to main as ekow74 was rejected ("Required status check 'KSP Gate' is expected").
  - Finding: this Claude session's proxy blocks ruleset writes (HTTP 403), so the Owner must import rulesets in the UI.
- PR ekow74/ksp-platform#1 (ruleset v1 JSON) merged by ekow74 on 2026-11-02.
