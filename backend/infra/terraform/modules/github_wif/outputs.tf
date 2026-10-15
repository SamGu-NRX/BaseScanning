output "pool_name" {
  value = google_iam_workload_identity_pool.github_actions.name
}

output "provider_name" {
  value = google_iam_workload_identity_pool_provider.github_actions.name
}

output "repository_principal_set" {
  value = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_actions.name}/attribute.repository/${var.repository}"
}
