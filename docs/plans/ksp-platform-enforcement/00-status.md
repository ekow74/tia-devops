# Status: ksp-platform repository + enforcement controls

- Gate 1 — Product: APPROVED 2026-11-02
- Gate 2 — Architecture: APPROVED 2026-11-02 (re-approved with Gate 3 amendment 2026-11-02)
- Gate 3 — Program Design: APPROVED 2026-11-02
- Gate 4 — Slice plan: in progress

## Slices
- [ ] Slice 1 — tracer bullet: App-posted `KSP Gate` required by ruleset (Owner creates repo, App, environment)
- [ ] Slice 2 — real gate happy path: routine passes, protected-without-GateChange blocked; safety CI, CODEOWNERS
- [ ] Slice 3 — robot identity: `ekow74-bot`, ruleset v2 (reviews, restrict updates), D-0026; two-click merge timed
- [ ] Slice 4 — safety-rule changes: GateChange + `KSP-REVIEW:` attestation + re-check workflow
- [ ] Slice 5 — tamper detection
- [ ] Slice 6 — post-merge audit
- [ ] Slice 7 — ruleset drift check
- [ ] Slice 8 — bypass drill (target 0/8), docs/external, move plan docs to ksp-platform

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
