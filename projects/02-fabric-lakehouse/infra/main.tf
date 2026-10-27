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
  resource_provider_registrations = "none"
}

resource "azurerm_resource_group" "project" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    project = "azure-projects"
    module  = "02-fabric-lakehouse"
  }
}

resource "azurerm_fabric_capacity" "project" {
  name                = var.fabric_capacity_name
  resource_group_name = azurerm_resource_group.project.name
  location            = azurerm_resource_group.project.location

  administration_members = [
    var.principal_object_id
  ]

  sku {
    name = "F2"
    tier = "Fabric"
  }

  tags = azurerm_resource_group.project.tags
}
