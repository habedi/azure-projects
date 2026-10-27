variable "subscription_id" {
  description = "Explicit UUID of the non-production Azure subscription for this project."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id)) && var.subscription_id != "00000000-0000-0000-0000-000000000000"
    error_message = "subscription_id must be an explicit, nonzero subscription UUID."
  }
}

variable "location" {
  description = "Explicit Azure region name supporting Microsoft Fabric, such as westeurope or eastus."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z][a-z0-9]+$", var.location))
    error_message = "location must be a lowercase Azure region name without spaces."
  }
}

variable "resource_group_name" {
  description = "Name of a new resource group owned only by this project. Do not reuse or import an existing group."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.resource_group_name) <= 90 && can(regex("^rg-azpg-02-[a-z0-9]+(-[a-z0-9]+)*$", var.resource_group_name))
    error_message = "resource_group_name must start with rg-azpg-02-, have a lowercase alphanumeric suffix with optional hyphens, and contain at most 90 characters."
  }
}

variable "fabric_capacity_name" {
  description = "Globally unique Microsoft Fabric capacity name owned by this project."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.fabric_capacity_name) <= 63 && can(regex("^azpg02[a-z0-9]+$", var.fabric_capacity_name))
    error_message = "fabric_capacity_name must start with azpg02, have a lowercase alphanumeric suffix, and contain at most 63 characters."
  }
}

variable "principal_object_id" {
  description = "Explicit Microsoft Entra object UUID of the principal designated as capacity administrator."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.principal_object_id)) && var.principal_object_id != "00000000-0000-0000-0000-000000000000"
    error_message = "principal_object_id must be an explicit, nonzero principal object UUID."
  }
}

variable "workspace_name" {
  description = "Fabric workspace name assigned to the capacity."
  type        = string
  default     = "ws-azpg-02-sandbox"

  validation {
    condition     = length(var.workspace_name) <= 100 && can(regex("^ws-azpg-02-[a-z0-9-]+$", var.workspace_name))
    error_message = "workspace_name must start with ws-azpg-02- and contain lowercase alphanumeric characters or hyphens."
  }
}

variable "lakehouse_name" {
  description = "Fabric Lakehouse name holding the Medallion data."
  type        = string
  default     = "lh_azpg_02_sandbox"

  validation {
    condition     = length(var.lakehouse_name) <= 100 && can(regex("^[a-zA-Z0-9_]+$", var.lakehouse_name))
    error_message = "lakehouse_name must contain only alphanumeric characters and underscores."
  }
}

variable "onelake_endpoint" {
  description = "HTTPS OneLake Blob service endpoint."
  type        = string
  default     = "https://onelake.blob.fabric.microsoft.com"

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+\\.fabric\\.microsoft\\.com/?$", var.onelake_endpoint))
    error_message = "onelake_endpoint must be a valid HTTPS fabric.microsoft.com endpoint."
  }
}
