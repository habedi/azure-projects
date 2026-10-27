output "subscription_id" {
  description = "Azure subscription UUID used by the project."
  value       = var.subscription_id
}

output "resource_group_name" {
  description = "Resource group owned by this project."
  value       = azurerm_resource_group.project.name
}

output "location" {
  description = "Azure region containing the project resources."
  value       = azurerm_resource_group.project.location
}

output "fabric_capacity_name" {
  description = "Fabric capacity name owned by this project."
  value       = azurerm_fabric_capacity.project.name
}

output "workspace_name" {
  description = "Fabric workspace name hosting the Lakehouse."
  value       = var.workspace_name
}

output "lakehouse_name" {
  description = "Fabric Lakehouse name holding the Medallion data."
  value       = var.lakehouse_name
}

output "onelake_endpoint" {
  description = "HTTPS OneLake Blob service endpoint for the Python Medallion pipeline."
  value       = var.onelake_endpoint
}
