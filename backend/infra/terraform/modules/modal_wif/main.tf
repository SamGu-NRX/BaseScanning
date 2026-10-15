locals {
  modal_env = coalesce(var.modal_environment_name, var.environment)
  pool_id   = coalesce(var.pool_id, "modal-${var.environment}")
  sa_id     = coalesce(var.runtime_sa_id, "hss-runtime-${var.environment}")
}

resource "google_iam_workload_identity_pool" "pool" {
  project                   = var.project_id
  workload_identity_pool_id = local.pool_id
  display_name              = "modal-${var.environment}"
}

resource "google_iam_workload_identity_pool_provider" "modal" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.pool.workload_identity_pool_id
  workload_identity_pool_provider_id = var.provider_id
  display_name                       = "modal"
  attribute_condition                = "attribute.workspace_id == \"${var.modal_workspace_id}\" && attribute.environment_name == \"${local.modal_env}\""

  attribute_mapping = {
    "google.subject"             = "\"modal/\" + assertion.workspace_id + \"/\" + assertion.environment_name + \"/\" + assertion.app_name"
    "attribute.workspace_id"     = "assertion.workspace_id"
    "attribute.environment_name" = "assertion.environment_name"
    "attribute.app_name"         = "assertion.app_name"
  }

  oidc {
    issuer_uri        = "https://oidc.modal.com"
    allowed_audiences = ["oidc.modal.com"]
  }
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = local.sa_id
  display_name = "hss-runtime-${var.environment}"
}

resource "google_service_account_iam_member" "workload_identity_user" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${var.project_number}/locations/global/workloadIdentityPools/${local.pool_id}/attribute.environment_name/${local.modal_env}"
}

resource "google_service_account_iam_member" "token_creator" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.runtime.email}"
}
