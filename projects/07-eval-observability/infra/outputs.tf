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

output "openai_endpoint" {
  description = "HTTPS endpoint for the Azure OpenAI Service."
  value       = azurerm_cognitive_account.experiment.endpoint
}

output "chat_deployment_name" {
  description = "Deployment name for the chat and judge model."
  value       = azurerm_cognitive_deployment.chat.name
}

output "app_insights_connection_string" {
  description = "Connection string for Application Insights OpenTelemetry telemetry ingestion."
  value       = azurerm_application_insights.experiment.connection_string
  sensitive   = true
}

output "storage_account_url" {
  description = "Primary blob endpoint URL for the storage account."
  value       = azurerm_storage_account.experiment.primary_blob_endpoint
}

output "evaluations_container_name" {
  description = "Container name for evaluation reports and benchmark runs."
  value       = azurerm_storage_container.evaluations.name
}
