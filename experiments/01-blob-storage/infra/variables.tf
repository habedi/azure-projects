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
  description = "Explicit Azure region name, such as eastus or westeurope."
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
    condition     = length(var.resource_group_name) <= 90 && can(regex("^rg-azpg-01-[a-z0-9]+(-[a-z0-9]+)*$", var.resource_group_name))
    error_message = "resource_group_name must start with rg-azpg-01-, have a lowercase alphanumeric suffix with optional hyphens, and contain at most 90 characters."
  }
}

variable "storage_account_name" {
  description = "Globally unique storage account name owned by this experiment."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.storage_account_name) <= 24 && can(regex("^azpg01[a-z0-9]+$", var.storage_account_name))
    error_message = "storage_account_name must start with azpg01, have a lowercase alphanumeric suffix, and contain at most 24 characters."
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
