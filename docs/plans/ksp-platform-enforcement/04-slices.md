# Slices: ksp-platform repository + enforcement controls

Build order, one slice at a time. Each slice ends with something you can see working on GitHub. **You do:** marks the steps only the Owner can do (one-time GitHub clicks). Everything else is done by Claude.

The ruleset grows slice by slice. Until Slice 3 creates the robot account, Claude still acts as `ekow74`, and you can't approve your own PRs. So the review rules switch on in Slice 3, not before.

1. **Tracer bullet: an App-posted check that's required.**
   - **You do:** create the private `ksp-platform` repo; create the KSP Gate Authority App and install it on that repo only; create environment `gate-authority` (branch `main` only) with its two secrets; import the ruleset.
   - **Claude does:** a stub `ksp-gate.yml` that always posts `KSP Gate: passed` as the App, and a ruleset that requires only that check, pinned to the App.
   - **Proves:** a test PR shows the App's check, the ruleset is enforced with no upgrade banner, and the environment works under `pull_request_target`. This covers verification items 1, 2 and 4.

2. **Real gate, happy path.**
   - Port `policy.py` and `evaluate()`.
   - A routine PR gets *passed*; a PR touching a protected path with no GateChange gets *blocked*.
   - Adds `ksp-safety.yml` with unit tests, CODEOWNERS and `protected-paths.txt`.

3. **Separate robot identity.**
   - **You do:** create `ekow74-bot`, invite it as a collaborator, and switch claude.ai's GitHub connection to it.
   - **Claude does:** ruleset v2 adds required code-owner review, approval of the most recent push, dismissal of stale approvals, restrict updates, and Repository admin as the only bypass. It also adds D-0026.
   - **Proves:** the first robot-authored PR, approved and merged by you in two clicks (the guardrail is timed). The robot's own merge attempt is refused (verification item 3).

4. **Safety-rule changes.**
   - GateChange validation, the `KSP-REVIEW:` attestation, and `ksp-gate-recheck.yml`.
   - **Proves:** a protected PR shows *waiting*, then *passed* after your attested approval, with no extra clicks.

5. **Tamper detection.**
   - Deleting or renaming the gate workflow, adding skip/xfail/continue-on-error, or lowering a threshold gives *blocked: tampering*.

6. **Post-merge audit.**
   - Every merge to `main` is checked for an App-sourced *passed* check plus your approval on that commit. A deliberate emergency-bypass merge by you opens an issue.

7. **Ruleset drift check.**
   - A weekly comparison of the live ruleset against `.github/rulesets/main.json`. A test edit you make in the GitHub UI opens an issue, then you revert it.

8. **Bypass drill and handover.**
   - Run the 8-scenario drill (plus 2a) as the robot. Commit the evidence; the target is **0 out of 8 succeeded**.
   - Add `docs/external/github.md`, and move these plan docs from `tia-devops` into `ksp-platform`.
   - **You do:** approve one drill PR when the drill asks.

After each slice: show you the result, tick it off in `00-status.md`, and ask "Continue to slice N+1, or re-steer?"
