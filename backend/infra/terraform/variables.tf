variable "project_id" {
  type    = string
  default = "house-scanning"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "product_id" {
  type    = string
  default = "house-scanning"
}

variable "environment" {
  type = string

  validation {
    condition     = var.environment == "dev"
    error_message = "environment must be dev. SD-77: one stack, and the live name stays dev."
  }
}

variable "owner" {
  type    = string
  default = "huntertcarver"
}

variable "manage_project_resources" {
  type    = bool
  default = false
}

variable "billing_account" {
  type    = string
  default = null
}

# False while house-scanning has no billing link. Gates Secret Manager,
# the fixtures bucket, and capture_bucket. The budget also needs
# monthly_budget_amount. See docs/components/deploy.md.
variable "billing_linked" {
  type    = bool
  default = false
}

variable "monthly_budget_amount" {
  type    = number
  default = null
}

variable "github_repository" {
  type    = string
  default = "huntertcarver/house-scanning-server"
}

variable "github_repository_id" {
  type        = string
  default     = null
  description = "Numeric GitHub repository id. Required on the live provider condition."
}

variable "github_repository_owner_id" {
  type        = string
  default     = null
  description = "Numeric GitHub repository owner id. Required on the live provider condition."
}

variable "github_wif_mode" {
  type    = string
  default = "shared"

  validation {
    condition     = contains(["shared", "own"], var.github_wif_mode)
    error_message = "github_wif_mode must be shared or own."
  }
}

variable "github_wif_pool_name" {
  type    = string
  default = null
}

variable "github_wif_provider_name" {
  type    = string
  default = null
}

variable "create_state_bucket" {
  type    = bool
  default = false
}

variable "state_bucket_name" {
  type    = string
  default = "hss-tfstate"
}

variable "fixtures_bucket_name" {
  type    = string
  default = "hss-fixtures"
}

variable "modal_workspace_id" {
  type    = string
  default = null
}
