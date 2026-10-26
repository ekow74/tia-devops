resource "contabo_instance" "this" {
  display_name = var.display_name
  product_id   = var.product_id
  region       = var.region
  period       = var.period_months
  image_id     = var.image_id
  default_user = "admin"
  ssh_keys     = var.ssh_key_secret_ids
  user_data    = var.cloud_init

  lifecycle {
    prevent_destroy = true

    # Contabo documents that changing user_data or ssh_keys reinstalls the VM.
    # After bootstrap, Ansible owns host configuration and SSH authorized_keys.
    ignore_changes = [
      user_data,
      ssh_keys,
    ]
  }
}
