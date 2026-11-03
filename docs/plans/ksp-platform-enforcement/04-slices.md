# Slices: ksp-platform repository + enforcement controls

Build order, one slice at a time. Each slice ends with something you can see working on GitHub. **You do:** marks the steps only the Owner can do (one-time GitHub clicks). Everything else is done by Claude.

**Revised 2026-11-03 (re-approval needed):** the old Slices 2 and 3 have swapped, and the old Slices 2 and 4 are merged.
- **Why:** once the real gate is on `main`, every change to gate code, workflows or rules is a safety-rule change. That needs the Owner's GitHub approval, and the Owner can't approve PRs authored as `ekow74`.
- **Old order:** the real gate before the robot account would have locked the build until the Owner overrode the rules.
- **New order:** the stub gate stays in place until the robot has its own account. The first real gate then lands complete with its safety-rule approval path.

1. ✅ **Tracer bullet: an App-posted check that's required.** *(Done 2026-11-02.)*

2. **Separate robot identity** (was Slice 3).
   - **You do:** create `ekow74-bot`, invite it as a collaborator on `ksp-platform`, and switch claude.ai's GitHub connection to it.
   - **Claude does:**
     - ruleset v2 adds required code-owner review, approval of the most recent push, dismissal of stale approvals, restrict updates, and Repository admin as the only bypass;
     - adds CODEOWNERS (`* @ekow74`) and D-0026.
   - **Proves:**
     - the first robot-authored PR is approved and merged by you in two clicks (the guardrail is timed);
     - the robot's own merge attempt is refused (verification item 3).

3. **Real gate, including safety-rule changes** (old Slices 2 and 4 combined).
   - Port `policy.py` and `evaluate()`; add GateChange validation, the `KSP-REVIEW:` attestation, `ksp-gate-recheck.yml`, safety CI and `protected-paths.txt`.
   - **Proves:**
     - a routine PR gets *passed*;
     - a protected PR with no GateChange gets *blocked*;
     - a protected PR with a GateChange shows *waiting*, then *passed* after your attested approval.
   - This PR still merges under the stub gate on `main`. From then on the real gate judges everything.

4. **Tamper detection.**
   - Deleting or renaming the gate workflow, adding skip/xfail/continue-on-error, or lowering a threshold gives *blocked: tampering*.

5. **Post-merge audit.**
   - Every merge to `main` is checked for an App-sourced *passed* check plus your approval on that commit. A deliberate emergency-bypass merge by you opens an issue.

6. **Ruleset drift check.**
   - A weekly comparison of the live ruleset against `.github/rulesets/main.json`. A test edit you make in the GitHub UI opens an issue, then you revert it.

7. **Bypass drill and handover.**
   - Run the 8-scenario drill (plus 2a) as the robot. Commit the evidence; the target is **0 out of 8 succeeded**.
   - Add `docs/external/github.md`, switch the token action to `client-id`, and move these plan docs into `ksp-platform`.
   - **You do:** approve one drill PR when the drill asks.

**What to expect from Slice 3 onward:** Slices 4 to 7 change gate code and workflows, so each one is a safety-rule change. Each needs a GateChange record, an independent Claude review, and your approval with the `KSP-REVIEW:` line. This is the process working as designed, and it's the only kind of change that needs the extra step. Routine changes stay at two clicks.

After each slice: show you the result, tick it off in `00-status.md`, and ask "Continue to slice N+1, or re-steer?"
