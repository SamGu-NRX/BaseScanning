variable "project_id" {
  type = string
}

variable "workload_identity_pool_id" {
  type    = string
  default = "github-actions"
}

variable "workload_identity_pool_display_name" {
  type    = string
  default = "GitHub Actions"
}

variable "workload_identity_provider_id" {
  type    = string
  default = "architecture-design-decisions"
}

variable "workload_identity_provider_display_name" {
  type    = string
  default = "Architecture Design Decisions"
}

variable "repository" {
  type = string
}

variable "repository_id" {
  type        = string
  default     = null
  description = "Numeric GitHub repository id. When set, the provider condition ANDs assertion.repository_id."
}

variable "repository_owner_id" {
  type        = string
  default     = null
  description = "Numeric GitHub repository owner id. When set, the provider condition ANDs assertion.repository_owner_id."
}

variable "allowed_refs" {
  type    = set(string)
  default = ["refs/heads/main"]
}

variable "additional_attribute_conditions" {
  type        = list(string)
  default     = []
  description = "Extra attribute_condition clauses OR-ed with the repository and ref clause. Empty leaves the SMP condition unchanged."
}
