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
  description = "HTTPS Blob service endpoint for document storage."
  value       = azurerm_storage_account.experiment.primary_blob_endpoint
}

output "container_name" {
  description = "Target storage container name for raw documents."
  value       = azurerm_storage_container.documents.name
}

output "search_service_endpoint" {
  description = "HTTPS endpoint of the Azure AI Search service."
  value       = "https://${azurerm_search_service.experiment.name}.search.windows.net"
}

output "search_service_name" {
  description = "Name of the Azure AI Search service."
  value       = azurerm_search_service.experiment.name
}

output "search_index_name" {
  description = "Name of the target hybrid vector search index."
  value       = "azpg-04-rag-index"
}

output "openai_endpoint" {
  description = "HTTPS endpoint for the Azure OpenAI service."
  value       = azurerm_cognitive_account.experiment.endpoint
}

output "embedding_deployment_name" {
  description = "Deployment name for text embeddings."
  value       = azurerm_cognitive_deployment.embedding.name
}

output "chat_deployment_name" {
  description = "Deployment name for chat completions."
  value       = azurerm_cognitive_deployment.chat.name
}
