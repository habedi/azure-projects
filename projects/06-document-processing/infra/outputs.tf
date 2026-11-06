output "subscription_id" {
  description = "Explicit subscription UUID used by the experiment."
  value       = var.subscription_id
}

output "resource_group_name" {
  description = "Resource group name containing the experiment resources."
  value       = azurerm_resource_group.experiment.name
}

output "location" {
  description = "Azure region where the resources are provisioned."
  value       = azurerm_resource_group.experiment.location
}

output "storage_account_url" {
  description = "Primary Blob service endpoint URL for the storage account."
  value       = azurerm_storage_account.experiment.primary_blob_endpoint
}

output "drop_container_name" {
  description = "Name of the blob drop zone container for raw incoming documents."
  value       = azurerm_storage_container.documents.name
}

output "processed_container_name" {
  description = "Name of the blob container holding validated structured JSON records."
  value       = azurerm_storage_container.processed.name
}

output "document_intelligence_endpoint" {
  description = "HTTPS endpoint of the Azure AI Document Intelligence account."
  value       = azurerm_cognitive_account.experiment.endpoint
}

output "document_intelligence_model_id" {
  description = "Prebuilt model ID used for document analysis."
  value       = "prebuilt-invoice"
}
