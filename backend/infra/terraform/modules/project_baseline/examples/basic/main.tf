provider "google" {
  project = var.project_id
}
module "project_env" {
  source                = "../../"
  project_id            = var.project_id
  project_name          = "Shared Dev"
  environment           = "dev"
  create_project        = false
  billing_account       = var.billing_account
  monthly_budget_amount = 250
}
variable "project_id" {
  type = string
}
variable "billing_account" {
  type = string
}
