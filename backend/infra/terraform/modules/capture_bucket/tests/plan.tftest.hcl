mock_provider "google" {}

variables {
  project_id  = "house-scanning"
  environment = "dev"
  owner       = "huntertcarver"
}

run "lifecycle" {
  command = plan

  assert {
    condition     = google_storage_bucket.captures.uniform_bucket_level_access == true
    error_message = "uniform bucket level access must be on"
  }

  assert {
    condition     = google_storage_bucket.captures.public_access_prevention == "enforced"
    error_message = "public access prevention must be enforced"
  }

  assert {
    condition     = google_storage_bucket.captures.versioning[0].enabled == false
    error_message = "versioning must be off"
  }

  assert {
    condition     = google_storage_bucket.captures.soft_delete_policy[0].retention_duration_seconds == 0
    error_message = "soft delete retention must be 0"
  }

  assert {
    condition     = google_storage_bucket.captures.labels["data_class"] == "pii"
    error_message = "data_class label must be pii"
  }

  assert {
    condition     = google_storage_bucket.captures.labels["product"] == "house-scanning"
    error_message = "product label must be house-scanning"
  }

  assert {
    condition     = google_storage_bucket.captures.labels["env"] == "dev"
    error_message = "env label must be the environment"
  }

  assert {
    condition     = google_storage_bucket.captures.labels["owner"] == "huntertcarver"
    error_message = "owner label must be set"
  }

  assert {
    condition     = google_storage_bucket.captures.labels["managed_by"] == "terraform"
    error_message = "managed_by label must be terraform"
  }

  assert {
    condition = length([
      for rule in google_storage_bucket.captures.lifecycle_rule : rule
      if alltrue([for action in rule.action : action.type == "Delete"]) && alltrue([
        for condition in rule.condition : condition.age == 7 && (condition.matches_prefix != null ? contains(condition.matches_prefix, "uploads/") : false)
      ])
    ]) == 1
    error_message = "uploads/ must be deleted after 7 days"
  }

  assert {
    condition = length([
      for rule in google_storage_bucket.captures.lifecycle_rule : rule
      if alltrue([for action in rule.action : action.type == "SetStorageClass" && action.storage_class == "NEARLINE"]) && alltrue([
        for condition in rule.condition : condition.age == 30 && (condition.matches_prefix != null ? contains(condition.matches_prefix, "captures/") : false)
      ])
    ]) == 1
    error_message = "captures/ must move to NEARLINE after 30 days"
  }

  assert {
    condition = length([
      for rule in google_storage_bucket.captures.lifecycle_rule : rule
      if alltrue([for action in rule.action : action.type == "AbortIncompleteMultipartUpload"]) && alltrue([
        for condition in rule.condition : condition.age == 1
      ])
    ]) == 1
    error_message = "incomplete multipart uploads must abort after 1 day"
  }
}
