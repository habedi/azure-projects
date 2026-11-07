variable "subscription_id" {
  description = "Target Azure subscription identifier."
  type        = string
}

variable "location" {
  description = "Azure region for resources supporting Azure Databricks."
  type        = string
  default     = "swedencentral"
}

variable "resource_group_name" {
  description = "Name of the resource group dedicated to this Databricks experiment."
  type        = string
  default     = "rg-azpg-08-sandbox"
}

variable "databricks_workspace_name" {
  description = "Name of the Azure Databricks workspace."
  type        = string
  default     = "azpg08-sandbox-dbw"
}

variable "storage_account_name" {
  description = "Globally unique name for the storage account storing MLflow artifacts and datasets."
  type        = string
}

variable "principal_object_id" {
  description = "Object ID of the signed-in user or service principal running the experiment."
  type        = string
}
