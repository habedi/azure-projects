terraform {
  required_version = ">= 1.8.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }

  subscription_id                 = var.subscription_id
  storage_use_azuread             = true
  resource_provider_registrations = "none"
}

resource "azurerm_resource_group" "experiment" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    project    = "azure-projects"
    experiment = "08-mlops-databricks"
  }
}

resource "azurerm_databricks_workspace" "experiment" {
  name                        = var.databricks_workspace_name
  resource_group_name         = azurerm_resource_group.experiment.name
  location                    = azurerm_resource_group.experiment.location
  sku                         = "premium"
  managed_resource_group_name = "${var.resource_group_name}-managed"

  tags = azurerm_resource_group.experiment.tags
}

resource "azurerm_storage_account" "experiment" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.experiment.name
  location                 = azurerm_resource_group.experiment.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"
  is_hns_enabled           = true

  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true

  tags = azurerm_resource_group.experiment.tags
}

resource "azurerm_storage_container" "artifacts" {
  name                  = "mlops-artifacts"
  storage_account_id    = azurerm_storage_account.experiment.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "storage_blob_contributor" {
  scope                = azurerm_storage_account.experiment.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.principal_object_id
}

resource "azurerm_role_assignment" "databricks_contributor" {
  scope                = azurerm_databricks_workspace.experiment.id
  role_definition_name = "Contributor"
  principal_id         = var.principal_object_id
}
