output "runtime_sa_email" {
  value = google_service_account.runtime.email
}

output "wif_audience" {
  value = "//iam.googleapis.com/projects/${var.project_number}/locations/global/workloadIdentityPools/${local.pool_id}/providers/${var.provider_id}"
}

output "impersonation_url" {
  value = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${google_service_account.runtime.email}:generateAccessToken"
}

output "pool_name" {
  value = google_iam_workload_identity_pool.pool.name
}

output "provider_name" {
  value = google_iam_workload_identity_pool_provider.modal.name
}
