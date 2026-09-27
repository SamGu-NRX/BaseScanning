data "google_project" "this" {
  project_id = var.project_id
}

module "project_baseline" {
  count  = var.manage_project_resources ? 1 : 0
  source = "./modules/project_baseline"

  create_project        = false
  project_id            = var.project_id
  project_name          = var.project_id
  environment           = var.environment
  billing_account       = var.billing_account
  monthly_budget_amount = var.monthly_budget_amount
  required_apis         = local.required_apis
  labels                = local.base_labels
}

module "github_wif" {
  count  = var.manage_project_resources && var.github_wif_mode == "own" ? 1 : 0
  source = "./modules/github_wif"

  project_id                              = var.project_id
  workload_identity_pool_id               = "github-actions"
  workload_identity_provider_id           = "house-scanning-server"
  workload_identity_pool_display_name     = "house-scanning-server"
  workload_identity_provider_display_name = "house-scanning-server"
  repository                              = var.github_repository
  repository_id                           = var.github_repository_id
  repository_owner_id                     = var.github_repository_owner_id
  allowed_refs                            = ["refs/heads/main"]
}

module "github_sa" {
  count  = var.manage_project_resources ? 1 : 0
  source = "./modules/github_sa"

  project_id                  = var.project_id
  service_account_id          = "hss-gha"
  display_name                = "house-scanning GitHub Actions"
  description                 = "Terraform plan/apply from huntertcarver/house-scanning-server via WIF"
  workload_identity_pool_name = local.github_wif_pool_name
  repository                  = var.github_repository
  project_roles               = local.github_sa_roles
  billing_account             = var.billing_account
  billing_account_roles       = toset([])
}

module "state_bucket" {
  count  = var.manage_project_resources && var.create_state_bucket ? 1 : 0
  source = "./modules/state_bucket"

  project_id    = var.project_id
  bucket_name   = var.state_bucket_name
  location      = "US"
  admin_members = toset(["serviceAccount:${module.github_sa[0].email}"])
  labels        = local.base_labels
}

# WS-01 PR 34 module, called with no count: the dev state address is
# module.modal_wif (not module.modal_wif[0]). Output name on the module
# is wif_audience. project_number is the module's string input; the value
# is this project's number.
module "modal_wif" {
  source             = "./modules/modal_wif"
  project_id         = var.project_id
  project_number     = data.google_project.this.number
  environment        = var.environment
  modal_workspace_id = var.modal_workspace_id
}

# No count: the dev state address is module.capture_bucket, not [0].
module "capture_bucket" {
  source               = "./modules/capture_bucket"
  project_id           = var.project_id
  environment          = var.environment
  cors_origins         = []
  object_admin_members = ["serviceAccount:${module.modal_wif.runtime_sa_email}"]
}
