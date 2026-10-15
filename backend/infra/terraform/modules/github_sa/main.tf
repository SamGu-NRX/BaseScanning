resource "google_service_account" "github_actions" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = var.display_name
  description  = var.description
}

resource "google_service_account_iam_member" "workload_identity_user" {
  service_account_id = google_service_account.github_actions.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${var.workload_identity_pool_name}/attribute.repository/${var.repository}"
}

resource "google_project_iam_member" "project_roles" {
  for_each = var.project_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_billing_account_iam_member" "billing_roles" {
  for_each = var.billing_account == null ? toset([]) : var.billing_account_roles

  billing_account_id = var.billing_account
  role               = each.value
  member             = "serviceAccount:${google_service_account.github_actions.email}"
}
