variable "subscription_id" {
  description = "Target Azure subscription identifier."
  type        = string
}

variable "location" {
  description = "Azure region for resources supporting Azure OpenAI and Application Insights."
  type        = string
  default     = "swedencentral"
}

variable "resource_group_name" {
  description = "Name of the resource group dedicated to this evaluation experiment."
  type        = string
  default     = "rg-azpg-07-sandbox"
}

variable "storage_account_name" {
  description = "Globally unique name for the storage account storing evaluation runs and golden datasets."
  type        = string
}

variable "openai_account_name" {
  description = "Globally unique Cognitive Services OpenAI account name."
  type        = string
}

variable "log_analytics_workspace_name" {
  description = "Name of the Log Analytics workspace backing Application Insights."
  type        = string
  default     = "azpg07-sandbox-law"
}

variable "app_insights_name" {
  description = "Name of the Application Insights component for OpenTelemetry evaluation traces and metrics."
  type        = string
  default     = "azpg07-sandbox-ai"
}

variable "chat_model_name" {
  description = "Azure OpenAI model name for candidate completions and LLM-as-a-judge evaluation."
  type        = string
  default     = "gpt-6-luna"
}

variable "chat_model_version" {
  description = "Model version for the chat deployment."
  type        = string
  default     = "2026-09-22"
}

variable "principal_object_id" {
  description = "Object ID of the signed-in user or service principal running the experiment."
  type        = string
}
