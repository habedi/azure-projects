variable "subscription_id" {
  description = "Explicit UUID of the non-production Azure subscription for this experiment."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id)) && var.subscription_id != "00000000-0000-0000-0000-000000000000"
    error_message = "subscription_id must be an explicit, nonzero subscription UUID."
  }
}

variable "location" {
  description = "Explicit Azure region name, such as norwayeast or swedencentral."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9]+$", var.location))
    error_message = "location must be a lowercase Azure region name without spaces."
  }
}

variable "resource_group_name" {
  description = "Name of a new resource group owned only by this experiment. Do not reuse or import an existing group."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.resource_group_name) <= 90 && can(regex("^rg-azpg-04-[a-z0-9]+(-[a-z0-9]+)*$", var.resource_group_name))
    error_message = "resource_group_name must start with rg-azpg-04-, have a lowercase alphanumeric suffix with optional hyphens, and contain at most 90 characters."
  }
}

variable "storage_account_name" {
  description = "Globally unique storage account name owned by this experiment."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.storage_account_name) <= 24 && can(regex("^azpg04[a-z0-9]+$", var.storage_account_name))
    error_message = "storage_account_name must start with azpg04, have a lowercase alphanumeric suffix, and contain at most 24 characters."
  }
}

variable "search_service_name" {
  description = "Globally unique Azure AI Search service name owned by this experiment."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.search_service_name) <= 60 && can(regex("^azpg04[a-z0-9-]+[a-z0-9]$", var.search_service_name))
    error_message = "search_service_name must start with azpg04, have lowercase alphanumeric characters or hyphens, and contain at most 60 characters."
  }
}

variable "search_service_sku" {
  description = "Pricing tier of the Azure AI Search service. Azure permits only one free service per subscription."
  type        = string
  default     = "basic"

  validation {
    condition     = contains(["free", "basic"], var.search_service_sku)
    error_message = "search_service_sku must be either free or basic."
  }
}

variable "openai_account_name" {
  description = "Globally unique Azure OpenAI Cognitive Services account name owned by this experiment."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.openai_account_name) <= 64 && can(regex("^azpg04[a-z0-9-]+[a-z0-9]$", var.openai_account_name))
    error_message = "openai_account_name must start with azpg04, have lowercase alphanumeric characters or hyphens, and contain at most 64 characters."
  }
}

variable "principal_object_id" {
  description = "Explicit Microsoft Entra object UUID of the principal running the Python experiment, not an application or client ID."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.principal_object_id)) && var.principal_object_id != "00000000-0000-0000-0000-000000000000"
    error_message = "principal_object_id must be an explicit, nonzero principal object UUID."
  }
}
