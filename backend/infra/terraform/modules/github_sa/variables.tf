variable "project_id" {
  type = string
}

variable "service_account_id" {
  type = string
}

variable "display_name" {
  type = string
}

variable "description" {
  type = string
}

variable "workload_identity_pool_name" {
  type = string
}

variable "repository" {
  type = string
}

variable "project_roles" {
  type    = set(string)
  default = []
}

variable "billing_account" {
  type    = string
  default = null
}

variable "billing_account_roles" {
  type    = set(string)
  default = []
}
