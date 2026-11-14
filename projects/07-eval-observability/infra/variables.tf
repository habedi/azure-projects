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
  description = "Azure region for resources supporting Azure OpenAI and Application Insights."
  type        = string
  default     = "swedencentral"
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]+$", var.location))
    error_message = "location must be a lowercase Azure region short name (for example, swedencentral)."
  }
}

variable "resource_group_name" {
  description = "Name of the resource group dedicated to this evaluation experiment."
  type        = string
  default     = "rg-azpg-07-sandbox"
  nullable    = false

  validation {
    condition     = can(regex("^rg-azpg-07-[a-z0-9-]+$", var.resource_group_name))
    error_message = "resource_group_name must begin with rg-azpg-07- using lowercase letters, numbers, and hyphens."
  }
}

variable "storage_account_name" {
  description = "Globally unique name for the storage account storing evaluation runs and golden datasets."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3 to 24 lowercase alphanumeric characters."
  }
}

variable "openai_account_name" {
  description = "Globally unique Cognitive Services OpenAI account name."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^azpg07[a-z0-9-]{0,56}[a-z0-9]$", var.openai_account_name))
    error_message = "openai_account_name must begin with azpg07 and use 8 to 64 lowercase letters, numbers, or internal hyphens."
  }
}

variable "log_analytics_workspace_name" {
  description = "Name of the Log Analytics workspace backing Application Insights."
  type        = string
  default     = "azpg07-sandbox-law"
  nullable    = false

  validation {
    condition     = can(regex("^azpg07-[a-z0-9-]*[a-z0-9]$", var.log_analytics_workspace_name)) && length(var.log_analytics_workspace_name) <= 63
    error_message = "log_analytics_workspace_name must begin with azpg07-, use lowercase letters, numbers, and hyphens, and be at most 63 characters."
  }
}

variable "app_insights_name" {
  description = "Name of the Application Insights component for OpenTelemetry evaluation traces and metrics."
  type        = string
  default     = "azpg07-sandbox-ai"
  nullable    = false

  validation {
    condition     = can(regex("^azpg07-[a-z0-9-]+$", var.app_insights_name))
    error_message = "app_insights_name must begin with azpg07- using lowercase letters, numbers, and hyphens."
  }
}

variable "chat_model_name" {
  description = "Azure OpenAI model name for candidate completions and LLM-as-a-judge evaluation."
  type        = string
  default     = "gpt-6-luna"
  nullable    = false

  validation {
    condition     = length(trimspace(var.chat_model_name)) > 0
    error_message = "chat_model_name must be a non-empty string."
  }
}

variable "chat_model_version" {
  description = "Model version for the chat deployment."
  type        = string
  default     = "2026-09-22"
  nullable    = false

  validation {
    condition     = can(regex("^[a-zA-Z0-9._-]+$", var.chat_model_version))
    error_message = "chat_model_version must be a non-empty version identifier."
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
