variable "project_id" {
  type = string
}

variable "project_number" {
  type        = string
  description = "Input, not a data source, so terraform test runs without credentials."
}

variable "environment" {
  type = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "modal_workspace_id" {
  type = string
}

variable "modal_environment_name" {
  type    = string
  default = null
}

variable "pool_id" {
  type    = string
  default = null
}

variable "provider_id" {
  type    = string
  default = "modal"
}

variable "runtime_sa_id" {
  type    = string
  default = null
}
