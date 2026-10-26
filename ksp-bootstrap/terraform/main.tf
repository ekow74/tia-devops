locals {
  control_staging_cloud_init = templatefile(
    "${path.module}/cloud-init/bootstrap.yaml.tftpl",
    {}
  )
}

module "control_staging" {
  source = "./modules/protected_instance"

  display_name           = "ksp-control-staging"
  product_id             = var.control_staging_product_id
  region                 = var.control_staging_region
  period_months          = 1
  image_id               = var.ubuntu_2404_image_id
  ssh_key_secret_ids     = var.bootstrap_ssh_key_secret_ids
  cloud_init             = local.control_staging_cloud_init
  deployment_authorized  = var.deployment_authorized
}
