terraform {
  # HCP Terraform is the authoritative state store. Organization/workspace are
  # supplied only at init time from the protected control-staging environment.
  # The credentialed workspace must be execution-mode=local, VCS-disconnected,
  # speculative-enabled=false, auto-apply=false before any credentialed plan.
  backend "remote" {}
}
