locals {
  bucket_name = coalesce(var.bucket_name, "hss-captures-${var.environment}")
  labels = merge(
    {
      product    = "house-scanning"
      env        = var.environment
      owner      = var.owner
      data_class = "pii"
      managed_by = "terraform"
    },
    var.extra_labels,
  )
}

resource "google_storage_bucket" "captures" {
  name                        = local.bucket_name
  project                     = var.project_id
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy
  labels                      = local.labels

  versioning {
    enabled = false
  }

  soft_delete_policy {
    retention_duration_seconds = 0
  }

  # SD-77. One environment. Delete these prefixes after 30 days.
  dynamic "lifecycle_rule" {
    for_each = toset(["captures/", "work/", "uploads/"])
    content {
      action {
        type = "Delete"
      }
      condition {
        age            = 30
        matches_prefix = [lifecycle_rule.value]
      }
    }
  }

  lifecycle_rule {
    action {
      type = "AbortIncompleteMultipartUpload"
    }
    condition {
      age = 2
    }
  }

  dynamic "cors" {
    for_each = length(var.cors_origins) == 0 ? [] : [1]
    content {
      origin          = var.cors_origins
      method          = ["GET", "HEAD", "PUT"]
      response_header = ["Content-Type", "Content-MD5", "ETag"]
      max_age_seconds = 3600
    }
  }
}

resource "google_storage_bucket_iam_member" "object_admin" {
  for_each = toset(var.object_admin_members)
  bucket   = google_storage_bucket.captures.name
  role     = "roles/storage.objectAdmin"
  member   = each.value
}

resource "google_storage_bucket_iam_member" "object_viewer" {
  for_each = toset(var.object_viewer_members)
  bucket   = google_storage_bucket.captures.name
  role     = "roles/storage.objectViewer"
  member   = each.value
}
