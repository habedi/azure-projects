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
    experiment = "07-eval-observability"
  }
}

resource "azurerm_log_analytics_workspace" "experiment" {
  name                = var.log_analytics_workspace_name
  resource_group_name = azurerm_resource_group.experiment.name
  location            = azurerm_resource_group.experiment.location
  sku                 = "PerGB2018"
  retention_in_days   = 30

  tags = azurerm_resource_group.experiment.tags
}

resource "azurerm_application_insights" "experiment" {
  name                = var.app_insights_name
  resource_group_name = azurerm_resource_group.experiment.name
  location            = azurerm_resource_group.experiment.location
  workspace_id        = azurerm_log_analytics_workspace.experiment.id
  application_type    = "web"

  tags = azurerm_resource_group.experiment.tags
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

resource "azurerm_storage_container" "evaluations" {
  name                  = "evaluations"
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
  name                 = var.chat_model_name
  cognitive_account_id = azurerm_cognitive_account.experiment.id

  model {
    format  = "OpenAI"
    name    = var.chat_model_name
    version = var.chat_model_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = 10
  }
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

resource "azurerm_role_assignment" "monitoring_metrics_publisher" {
  scope                = azurerm_application_insights.experiment.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = var.principal_object_id
}
