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

output "eventhub_namespace_fqdn" {
  description = "Fully qualified domain name of the Event Hubs namespace."
  value       = "${azurerm_eventhub_namespace.experiment.name}.servicebus.windows.net"
}

output "eventhub_name" {
  description = "Name of the Event Hub stream."
  value       = azurerm_eventhub.telemetry.name
}

output "storage_account_url" {
  description = "HTTPS Blob service endpoint for the storage account."
  value       = azurerm_storage_account.experiment.primary_blob_endpoint
}

output "container_name" {
  description = "Name of the target telemetry storage container."
  value       = azurerm_storage_container.telemetry.name
}
