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

output "cosmos_endpoint" {
  description = "HTTPS endpoint of the Azure Cosmos DB account."
  value       = azurerm_cosmosdb_account.experiment.endpoint
}

output "cosmos_database_name" {
  description = "Name of the Azure Cosmos DB database."
  value       = azurerm_cosmosdb_sql_database.experiment.name
}

output "cosmos_container_name" {
  description = "Name of the transactional Cosmos DB container."
  value       = azurerm_cosmosdb_sql_container.orders.name
}

output "storage_account_url" {
  description = "Primary Blob service endpoint URL for the storage account."
  value       = azurerm_storage_account.experiment.primary_blob_endpoint
}

output "table_endpoint" {
  description = "Primary Table service endpoint URL for the storage account."
  value       = azurerm_storage_account.experiment.primary_table_endpoint
}

output "table_name" {
  description = "Name of the storage table holding materialized views."
  value       = "ordersummaries"
}

output "blob_container_name" {
  description = "Name of the blob container holding analytical change snapshots."
  value       = azurerm_storage_container.analytics.name
}

output "openai_endpoint" {
  description = "HTTPS endpoint of the Azure OpenAI Cognitive Services account."
  value       = azurerm_cognitive_account.experiment.endpoint
}

output "chat_deployment_name" {
  description = "Deployment name for the GPT-6 Luna language model."
  value       = azurerm_cognitive_deployment.chat.name
}
