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
    experiment = "05-change-data-capture"
  }
}

resource "azurerm_cosmosdb_account" "experiment" {
  name                = var.cosmos_account_name
  location            = azurerm_resource_group.experiment.location
  resource_group_name = azurerm_resource_group.experiment.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.experiment.location
    failover_priority = 0
  }

  capabilities {
    name = "EnableServerless"
  }

  tags = azurerm_resource_group.experiment.tags
}

resource "azurerm_cosmosdb_sql_database" "experiment" {
  name                = "azpg-05-cdc-db"
  resource_group_name = azurerm_resource_group.experiment.name
  account_name        = azurerm_cosmosdb_account.experiment.name
}

resource "azurerm_cosmosdb_sql_container" "orders" {
  name                = "orders"
  resource_group_name = azurerm_resource_group.experiment.name
  account_name        = azurerm_cosmosdb_account.experiment.name
  database_name       = azurerm_cosmosdb_sql_database.experiment.name
  partition_key_paths = ["/store_id"]
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

resource "azurerm_storage_container" "analytics" {
  name                  = "cdc-analytics"
  storage_account_id    = azurerm_storage_account.experiment.id
  container_access_type = "private"
}

resource "azurerm_cognitive_account" "experiment" {
  name                = var.openai_account_name
  resource_group_name = azurerm_resource_group.experiment.name
  location            = azurerm_resource_group.experiment.location
  kind                = "OpenAI"
  sku_name            = "S0"

  custom_subdomain_name = var.openai_account_name
  local_auth_enabled    = true

  tags = azurerm_resource_group.experiment.tags
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = "gpt-6-luna"
  cognitive_account_id = azurerm_cognitive_account.experiment.id

  model {
    format  = "OpenAI"
    name    = "gpt-6-luna"
    version = "2026-09-22"
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
}

resource "azurerm_cosmosdb_sql_role_assignment" "contributor" {
  resource_group_name = azurerm_resource_group.experiment.name
  account_name        = azurerm_cosmosdb_account.experiment.name
  role_definition_id  = "${azurerm_cosmosdb_account.experiment.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = var.principal_object_id
  scope               = azurerm_cosmosdb_account.experiment.id
}

resource "azurerm_role_assignment" "storage_table_contributor" {
  scope                = azurerm_storage_account.experiment.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = var.principal_object_id
}

resource "azurerm_role_assignment" "storage_blob_contributor" {
  scope                = azurerm_storage_account.experiment.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.principal_object_id
}

resource "azurerm_role_assignment" "openai_user" {
  scope                = azurerm_cognitive_account.experiment.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.principal_object_id
}
