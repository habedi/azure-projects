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
    project    = "azure-projects"
    experiment = "04-rag-ai-search"
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

resource "azurerm_storage_container" "documents" {
  name                  = "documents"
  storage_account_id    = azurerm_storage_account.experiment.id
  container_access_type = "private"
}

resource "azurerm_search_service" "experiment" {
  name                = var.search_service_name
  resource_group_name = azurerm_resource_group.experiment.name
  location            = azurerm_resource_group.experiment.location
  sku                 = var.search_service_sku

  local_authentication_enabled = true
  authentication_failure_mode  = "http401WithBearerChallenge"

  tags = azurerm_resource_group.experiment.tags
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

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = "text-embedding-3-small"
  cognitive_account_id = azurerm_cognitive_account.experiment.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }

  sku {
    name     = "Standard"
    capacity = 10
  }
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = "gpt-4o-mini"
  cognitive_account_id = azurerm_cognitive_account.experiment.id

  model {
    format  = "OpenAI"
    name    = "gpt-4o-mini"
    version = "2024-07-18"
  }

  sku {
    name     = "Standard"
    capacity = 10
  }
}

resource "azurerm_role_assignment" "search_index_contributor" {
  scope                = azurerm_search_service.experiment.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = var.principal_object_id
}

resource "azurerm_role_assignment" "openai_user" {
  scope                = azurerm_cognitive_account.experiment.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = var.principal_object_id
}

resource "azurerm_role_assignment" "storage_blob_contributor" {
  scope                = azurerm_storage_account.experiment.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.principal_object_id
}
