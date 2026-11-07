output "subscription_id" {
  description = "Target Azure subscription identifier."
  value       = var.subscription_id
}

output "resource_group_name" {
  description = "Name of the resource group dedicated to this experiment."
  value       = azurerm_resource_group.experiment.name
}

output "location" {
  description = "Deployment region for the sandbox resources."
  value       = azurerm_resource_group.experiment.location
}

output "databricks_workspace_url" {
  description = "HTTPS URL for accessing the Azure Databricks workspace."
  value       = "https://${azurerm_databricks_workspace.experiment.workspace_url}"
}

output "databricks_workspace_id" {
  description = "Unique resource identifier of the Databricks workspace."
  value       = azurerm_databricks_workspace.experiment.workspace_id
}

output "storage_account_url" {
  description = "Primary blob endpoint URL for the ADLS Gen2 storage account."
  value       = azurerm_storage_account.experiment.primary_blob_endpoint
}

output "artifacts_container_name" {
  description = "Container name for MLflow artifacts and datasets."
  value       = azurerm_storage_container.artifacts.name
}
