variable "control_staging_product_id" {
  type        = string
  description = "Qualified Contabo product ID for the control/staging VM."
}

variable "control_staging_region" {
  type        = string
  description = "Contabo region for control/staging."
  default     = "US-central"
}

variable "ubuntu_2404_image_id" {
  type        = string
  description = "Contabo image ID verified to be Ubuntu 24.04 LTS."
}

variable "bootstrap_ssh_key_secret_ids" {
  type        = list(number)
  description = "Contabo secret IDs containing approved SSH public keys."
}

variable "deployment_authorized" {
  type        = bool
  description = "Trusted HCP-only release flag. Keep false until A2/A3 qualification passes."
  default     = false
}
