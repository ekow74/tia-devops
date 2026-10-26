output "instance_id" {
  description = "Contabo instance identifier."
  value       = contabo_instance.this.id
}

output "ip_config" {
  description = "Provider-reported instance IP configuration."
  value       = contabo_instance.this.ip_config
}

output "status" {
  description = "Provider-reported instance status."
  value       = contabo_instance.this.status
}
