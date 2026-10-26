# KSP Terraform Bootstrap

This directory is a credential-free bootstrap template for the future private `ksp-platform` production repository. It is not an authorized production deployment root.

## Pinned toolchain

- Terraform CLI: `1.16.6`
- Contabo provider: `contabo/contabo = 0.1.44`

The provider is configured from trusted runtime environment variables only:

- `CNTB_OAUTH2_CLIENT_ID`
- `CNTB_OAUTH2_CLIENT_SECRET`
- `CNTB_OAUTH2_USER`
- `CNTB_OAUTH2_PASS`

These variables are forbidden from untrusted PR workflows.

## Safety properties already encoded

- exact provider version pin;
- `prevent_destroy = true` on the protected instance;
- `user_data` and `ssh_keys` ignored after bootstrap because Contabo documents that changing them reinstalls the VM;
- no Terraform-generated private SSH key, so private key material never enters Terraform state;
- `deployment_authorized = false` by default and enforced by a resource precondition;
- cloud-init contains no long-lived secret and hands ongoing configuration to Ansible;
- initial contract period is one month for cost control.

## Apply freeze

Do **not** run `terraform apply` from this public umbrella repository.

Before the first trusted plan/apply:

1. Claude S02 independent review approves the GateChange.
2. Create the private `ksp-platform` repository and transplant the reviewed files.
3. Apply a GitHub ruleset that the Governor identity cannot approve, merge, or bypass for protected paths.
4. Configure HCP Terraform remote state/workspace.
5. Store Contabo API credentials only as sensitive HCP variables; do not put them in GitHub.
6. Resolve a verified Ubuntu 24.04 Contabo image ID, product ID, and existing SSH public-key secret ID.
7. Commit and review `.terraform.lock.hcl` in the private production repository.
8. Pass A2 credential-boundary and A3 network-exposure qualification.
9. Set `deployment_authorized=true` only in the trusted HCP workspace.

The first permitted target is **control/staging only**. Production remains a separate later environment/VM.
