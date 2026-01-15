variable "project_id" {
  type = string
  default = "iosl-faas-scheduling"
}

variable "agent_region" {
  type = string
  default = "us-east1"
}

variable "gemini_api_key" {
  type = string
  sensitive = true
}


variable "electricitymaps_api_key" {
  type = string
  sensitive = true
}