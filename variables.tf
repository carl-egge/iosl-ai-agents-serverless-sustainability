variable "project_id" {
  type    = string
  default = "iosl-faas-scheduling"
}

variable "region" {
  type    = string
  default = "us-east1"
}

variable "gemini_api_key" {
  type      = string
  sensitive = true
}

variable "electricitymaps_api_key" {
  type      = string
  sensitive = true
}

variable "mcp_api_key" {
  type      = string
  sensitive = true
}

variable "gcs_bucket" {
  type = string
}

variable "schedule_mode" {
  type = string
}

variable "schedule_location" {
  type = string
}

variable "function_metadata_path" {
  type = string
}

variable "static_config_path" {
  type = string
}
