# Variable validation can only read its own variable, so the cross-field
# rule from the brief lives in a check block.
check "shared_wif_pool_name" {
  assert {
    condition     = var.github_wif_mode != "shared" || var.github_wif_pool_name != null
    error_message = "github_wif_pool_name is required when github_wif_mode is shared."
  }
}

locals {
  base_labels = {
    product    = var.product_id
    env        = var.environment
    owner      = var.owner
    data_class = "pii"
    managed_by = "terraform"
  }

  # SD-74. No serviceAccountAdmin, workloadIdentityPoolAdmin, or billing.costsManager.
  github_sa_roles = toset([
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/secretmanager.admin",
    "roles/iam.securityReviewer",
  ])

  # try() because module.github_wif has count 0 when github_wif_mode is shared.
  github_wif_pool_name     = var.github_wif_mode == "own" ? try(module.github_wif[0].pool_name, null) : var.github_wif_pool_name
  github_wif_provider_name = var.github_wif_mode == "own" ? try(module.github_wif[0].provider_name, null) : var.github_wif_provider_name

  # The eight APIs enabled on house-scanning. secretmanager is in this set.
  required_apis = toset([
    "storage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "secretmanager.googleapis.com",
  ])
}
