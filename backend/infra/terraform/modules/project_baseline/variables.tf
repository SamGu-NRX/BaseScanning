variable "project_id" {
  type = string
}
variable "project_name" {
  type = string
}
variable "environment" {
  type = string
}
variable "billing_account" {
  type    = string
  default = null
}
variable "folder_id" {
  type    = string
  default = null
}
variable "org_id" {
  type    = string
  default = null
}
variable "create_project" {
  type    = bool
  default = false
}
variable "labels" {
  type    = map(string)
  default = {}
}
variable "required_apis" {
  type = set(string)
  default = [
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ]
}
variable "monthly_budget_amount" {
  type    = number
  default = null
}
variable "manage_budget" {
  type    = bool
  default = true
}
variable "budget_currency_code" {
  type    = string
  default = "USD"
}
