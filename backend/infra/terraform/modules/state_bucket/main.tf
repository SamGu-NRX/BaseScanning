resource "google_storage_bucket" "state" {
  project                     = var.project_id
  name                        = var.bucket_name
  location                    = var.location
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  labels                      = local.effective_labels

  versioning { enabled = var.versioning_enabled }
}

resource "google_storage_bucket_iam_member" "admin" {
  for_each = var.admin_members
  bucket   = google_storage_bucket.state.name
  role     = "roles/storage.admin"
  member   = each.value
}
