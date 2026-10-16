terraform {
  required_version = ">= 1.8.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  backend "local" {}
}

provider "azurerm" {
  features {}

  subscription_id                 = var.subscription_id
  storage_use_azuread             = true
  resource_provider_registrations = "none"
}

resource "azurerm_resource_group" "experiment" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    project    = "azure-playground"
    experiment = "01-blob-storage"
  }
}

resource "azurerm_storage_account" "experiment" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.experiment.name
  location                 = azurerm_resource_group.experiment.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true

  tags = azurerm_resource_group.experiment.tags
}

resource "azurerm_role_assignment" "blob_data_contributor" {
  scope                = azurerm_storage_account.experiment.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.principal_object_id
}
