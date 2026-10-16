mock_provider "google" {}

variables {
  project_id          = "house-scanning"
  repository          = "huntertcarver/house-scanning-server"
  repository_id       = "1389499780"
  repository_owner_id = "43165591"
  allowed_refs        = ["refs/heads/main"]
}

run "condition" {
  command = plan

  assert {
    condition     = google_iam_workload_identity_pool_provider.github_actions.attribute_condition == "assertion.repository == \"huntertcarver/house-scanning-server\" && assertion.repository_id == \"1389499780\" && assertion.repository_owner_id == \"43165591\" && (assertion.ref == \"refs/heads/main\")"
    error_message = "GitHub provider condition string drifted from the live provider"
  }
}
