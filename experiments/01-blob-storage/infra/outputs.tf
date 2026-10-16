output "subscription_id" {
  description = "Azure subscription UUID used by the experiment."
  value       = var.subscription_id
}

output "resource_group_name" {
  description = "Resource group owned by this experiment."
  value       = azurerm_resource_group.experiment.name
}

output "location" {
  description = "Azure region containing the experiment resources."
  value       = azurerm_resource_group.experiment.location
}

output "storage_account_url" {
  description = "HTTPS Blob service endpoint for the Python round-trip experiment."
  value       = azurerm_storage_account.experiment.primary_blob_endpoint
}
