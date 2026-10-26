variable "display_name" {
  type        = string
  description = "Human-readable Contabo instance name."
}

variable "product_id" {
  type        = string
  description = "Contabo VPS/VDS product ID selected for the environment."
}

variable "region" {
  type        = string
  description = "Contabo region."
  validation {
    condition     = contains(["EU", "US-central", "US-east", "US-west", "SIN"], var.region)
    error_message = "region must be a Contabo-supported region."
  }
}

variable "period_months" {
  type        = number
  description = "Initial contract period in months."
  default     = 1
  validation {
    condition     = contains([1, 3, 6, 12], var.period_months)
    error_message = "period_months must be 1, 3, 6, or 12."
  }
}

variable "image_id" {
  type        = string
  description = "Qualified Ubuntu 24.04 image ID. Changing image_id is destructive/reinstalling."
}

variable "ssh_key_secret_ids" {
  type        = list(number)
  description = "Existing Contabo SSH public-key secret IDs. Private keys never enter Terraform state."
  validation {
    condition     = length(var.ssh_key_secret_ids) > 0
    error_message = "At least one bootstrap SSH public-key secret ID is required."
  }
}

variable "cloud_init" {
  type        = string
  description = "Minimal first-boot cloud-init. Changes after creation are intentionally ignored."
}
