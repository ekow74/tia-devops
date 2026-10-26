# Credentials are supplied only in trusted HCP Terraform runs through:
# CNTB_OAUTH2_CLIENT_ID
# CNTB_OAUTH2_CLIENT_SECRET
# CNTB_OAUTH2_USER
# CNTB_OAUTH2_PASS
#
# Untrusted pull-request workflows never receive these variables.
provider "contabo" {}
