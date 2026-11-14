variable "subscription_id" {
  description = "Target Azure subscription identifier."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be a valid UUID."
  }
}

variable "location" {
  description = "Azure region for resources supporting Azure Databricks."
  type        = string
  default     = "swedencentral"
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.location))
    error_message = "location must be a lowercase Azure region short name (for example, swedencentral)."
  }
}

variable "resource_group_name" {
  description = "Name of the resource group dedicated to this Databricks experiment."
  type        = string
  default     = "rg-azpg-08-sandbox"
  nullable    = false

  validation {
    condition     = can(regex("^rg-azpg-08-[a-z0-9-]+$", var.resource_group_name))
    error_message = "resource_group_name must begin with rg-azpg-08- using lowercase letters, numbers, and hyphens."
  }
}

variable "databricks_workspace_name" {
  description = "Name of the Azure Databricks workspace."
  type        = string
  default     = "azpg08-sandbox-dbw"
  nullable    = false

  validation {
    condition     = can(regex("^azpg08-[a-z0-9-]*[a-z0-9]$", var.databricks_workspace_name)) && length(var.databricks_workspace_name) <= 64
    error_message = "databricks_workspace_name must begin with azpg08-, use lowercase letters, numbers, and hyphens, and be at most 64 characters."
  }
}

variable "storage_account_name" {
  description = "Globally unique name for the storage account storing MLflow artifacts and datasets."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3 to 24 lowercase alphanumeric characters."
  }
}

variable "principal_object_id" {
  description = "Object ID of the signed-in user or service principal running the experiment."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.principal_object_id))
    error_message = "principal_object_id must be a valid Microsoft Entra object UUID."
  }
}
